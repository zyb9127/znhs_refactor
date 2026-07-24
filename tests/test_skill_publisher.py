"""
test_skill_publisher — services/skill_publisher.py 单元测试

覆盖：
  - dev 模式：publish_config 只写本地文件（tmp 目录替换 SKILLS_ROOT）、
    原子写（无 .tmp 残留）、ensure_ascii=False、PublishResult 返回结构、reload 委托
  - production 分支：注入假 es_config_store / redis_config_bus 对象，
    验证 ES 成功/失败（诚实失败）、Redis 失败仅 warnings
  - publish_package 批量发布只 reload 一次
  - rollback_config 委托链（ES 回滚 -> 取数据 -> 本地文件 -> Redis -> reload）
不调用任何真实网络/ES/Redis/LLM。

运行：cd ROOT && python -m unittest tests.test_skill_publisher -v
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services.skill_publisher import (
    PublishResult,
    publish_config,
    publish_package,
    rollback_config,
)
from utils.skill_runtime import SkillRuntimeLoader, skill_registry

P = "testprov"
I = "套餐推荐"  # 故意用非 ASCII 目录名，验证路径与编码处理


class FakeESStore:
    """假 es_config_store 单例"""

    def __init__(self, save_ok=True, new_version=3,
                 rollback_ok=True, published_data=None):
        self.save_ok = save_ok
        self.new_version = new_version
        self.rollback_ok = rollback_ok
        self.published_data = published_data
        self.save_calls = []
        self.rollback_calls = []

    def save_and_publish(self, province, intent, config_type, data,
                         operator="system", comment="", notify=True):
        self.save_calls.append(
            (province, intent, config_type, data, operator, comment, notify)
        )
        if self.save_ok:
            return True, f"发布成功，版本 v{self.new_version}", self.new_version
        return False, "ES 不可用", 0

    def rollback(self, province, intent, config_type, target_version, operator="system"):
        self.rollback_calls.append((province, intent, config_type, target_version, operator))
        if self.rollback_ok:
            return True, f"已回滚到版本 {target_version}"
        return False, "目标版本不存在或已被清理"

    def get_published(self, province, intent, config_type):
        return self.published_data


class FakeRedisBus:
    """假 redis_config_bus 单例"""

    def __init__(self, enabled=True, set_raises=False):
        self.enabled = enabled
        self.set_raises = set_raises
        self.set_calls = []
        self.delete_calls = []
        self.publish_calls = []

    def set_config(self, province, intent, config_type, data):
        if self.set_raises:
            raise RuntimeError("redis down")
        self.set_calls.append((province, intent, config_type, data))
        return True

    def delete_config(self, province, intent, config_type):
        self.delete_calls.append((province, intent, config_type))

    def publish_change(self, province, intent):
        self.publish_calls.append((province, intent))
        return True


class PublisherTestBase(unittest.TestCase):
    """公共环境：tmp 目录替换 SKILLS_ROOT + mock skill_registry.reload"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="znhs_pub_test_"))
        self._p_root = mock.patch.object(SkillRuntimeLoader, "SKILLS_ROOT", self.tmp)
        self._p_reload = mock.patch.object(skill_registry, "reload")
        self._p_root.start()
        self.mock_reload = self._p_reload.start()

    def tearDown(self):
        self._p_reload.stop()
        self._p_root.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg_path(self, config_type="biz_config"):
        return self.tmp / P / I / "config" / f"{config_type}.json"


class TestPublishConfigDev(PublisherTestBase):
    """dev 模式：只写本地文件 + reload"""

    def test_dev_writes_file_and_result_structure(self):
        data = {"strategy": {"name": "中文策略"}, "script_templates_v2": []}
        with mock.patch("utils.skill_runtime.IS_DEV", True):
            res = publish_config(P, I, "biz_config", data, reload=False)

        self.assertIsInstance(res, PublishResult)
        self.assertTrue(res.success)
        self.assertTrue(res.file_written)
        self.assertFalse(res.es_written)
        self.assertFalse(res.redis_written)
        self.assertIsNone(res.version)
        self.assertIn("development", res.message)

        path = self._cfg_path()
        self.assertTrue(path.exists())
        # 内容等价 + ensure_ascii=False（中文原样落盘）
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), data)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("中文策略", raw)
        # 原子写：无 .tmp 残留
        leftovers = list(path.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])
        # reload=False 时不触发热更新
        self.mock_reload.assert_not_called()

    def test_dev_reload_called(self):
        with mock.patch("utils.skill_runtime.IS_DEV", True):
            res = publish_config(P, I, "api_nodes", {"nodeA": {}}, reload=True)
        self.assertTrue(res.success)
        self.mock_reload.assert_called_once_with(P, I)

    def test_dev_overwrite_atomic(self):
        """二次发布覆盖旧内容，仍无 .tmp 残留。"""
        with mock.patch("utils.skill_runtime.IS_DEV", True):
            publish_config(P, I, "biz_config", {"v": 1}, reload=False)
            publish_config(P, I, "biz_config", {"v": 2}, reload=False)
        with open(self._cfg_path(), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"v": 2})
        self.assertEqual(list(self._cfg_path().parent.glob("*.tmp")), [])

    def test_invalid_config_type(self):
        res = publish_config(P, I, "pipeline", {}, reload=False)
        self.assertFalse(res.success)
        self.assertIn("不支持的配置类型", res.message)

    def test_invalid_data_type(self):
        res = publish_config(P, I, "biz_config", ["not-a-dict"], reload=False)
        self.assertFalse(res.success)

    def test_invalid_path_segments_rejected(self):
        """province/intent/config_type 必须是单一安全路径段（防路径穿越）。"""
        bad_cases = [
            ("../evil", I), ("a/b", I), ("a\\b", I), (".hidden", I),
            ("", I), ("  ", I), (P, ".."), (P, "x/../y"), (None, I),
        ]
        for prov, itt in bad_cases:
            res = publish_config(prov, itt, "biz_config", {}, reload=False)
            self.assertFalse(res.success, f"{prov!r}/{itt!r} 应被拒绝")
            self.assertEqual(res.message, "非法的省份/意图标识")
        # 不产生任何目录/文件
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_rollback_invalid_segments_rejected(self):
        for prov, itt, ct in [("..", I, "biz_config"), (P, "a/b", "biz_config"),
                              (P, I, "../biz_config")]:
            res = rollback_config(prov, itt, ct, 1)
            self.assertFalse(res.success)
            self.assertEqual(res.message, "非法的省份/意图标识")

    def test_env_skills_runtime_path_respected(self):
        """F4：SKILLS_RUNTIME_PATH 环境变量优先于 SkillRuntimeLoader.SKILLS_ROOT。"""
        alt = Path(tempfile.mkdtemp(prefix="znhs_pub_env_"))
        try:
            with mock.patch.dict(os.environ, {"SKILLS_RUNTIME_PATH": str(alt)}), \
                 mock.patch("utils.skill_runtime.IS_DEV", True):
                res = publish_config(P, I, "biz_config", {"v": 1}, reload=False)
            self.assertTrue(res.success)
            self.assertTrue((alt / P / I / "config" / "biz_config.json").exists())
            self.assertFalse(self._cfg_path().exists())
        finally:
            shutil.rmtree(alt, ignore_errors=True)


class TestPublishConfigProd(PublisherTestBase):
    """production 分支：假 ES / Redis 对象注入"""

    def _run(self, fake_es, fake_redis, data=None, reload=False, broadcast=True):
        data = data if data is not None else {"strategy": {}}
        with mock.patch("utils.skill_runtime.IS_DEV", False), \
             mock.patch("services.es_config_store.es_config_store", fake_es), \
             mock.patch("services.redis_config_bus.redis_config_bus", fake_redis):
            return publish_config(P, I, "biz_config", data,
                                  operator="op_x", comment="c1", reload=reload,
                                  broadcast=broadcast)

    def test_prod_success_full_chain(self):
        fake_es = FakeESStore(save_ok=True, new_version=7)
        fake_redis = FakeRedisBus(enabled=True)
        res = self._run(fake_es, fake_redis, reload=True)

        self.assertTrue(res.success)
        self.assertTrue(res.file_written)
        self.assertTrue(res.es_written)
        self.assertTrue(res.redis_written)
        self.assertEqual(res.version, 7)
        self.assertIn("v7", res.message)
        # ES 收到 operator/comment，且 notify=False（广播由 publisher 统一发出，避免重复）
        self.assertEqual(fake_es.save_calls[0][4], "op_x")
        self.assertEqual(fake_es.save_calls[0][5], "c1")
        self.assertFalse(fake_es.save_calls[0][6])
        # Redis 缓存 + 广播（publisher 只广播一次）
        self.assertEqual(len(fake_redis.set_calls), 1)
        self.assertEqual(fake_redis.publish_calls, [(P, I)])
        # 本地文件也写了（ES 成功后的快照）
        self.assertTrue(self._cfg_path().exists())
        self.mock_reload.assert_called_once_with(P, I)

    def test_prod_es_failure_is_honest_failure(self):
        """生产模式 ES 先写且失败 => success=False，不写本地文件、不写 Redis、不 reload。"""
        fake_es = FakeESStore(save_ok=False)
        fake_redis = FakeRedisBus(enabled=True)
        res = self._run(fake_es, fake_redis, reload=True)

        self.assertFalse(res.success)
        self.assertFalse(res.es_written)
        self.assertIsNone(res.version)
        self.assertIn("ES", res.message)
        self.assertEqual(fake_redis.set_calls, [])
        self.assertEqual(fake_redis.publish_calls, [])
        self.mock_reload.assert_not_called()
        # 新顺序：ES 失败立即返回，本地文件不写（失败后系统状态完全不变）
        self.assertFalse(res.file_written)
        self.assertFalse(self._cfg_path().exists())

    def test_prod_broadcast_false_skips_all_publish_change(self):
        """broadcast=False：ES notify=False 且 publisher 自身也不广播，但仍写缓存。"""
        fake_es = FakeESStore(save_ok=True, new_version=5)
        fake_redis = FakeRedisBus(enabled=True)
        res = self._run(fake_es, fake_redis, broadcast=False)

        self.assertTrue(res.success)
        self.assertFalse(fake_es.save_calls[0][6])          # notify=False
        self.assertEqual(len(fake_redis.set_calls), 1)      # 缓存照写
        self.assertEqual(fake_redis.publish_calls, [])      # 不广播

    def test_prod_redis_failure_only_warns(self):
        """Redis 失败只记 warnings，不影响 success（ES 已成功）。"""
        fake_es = FakeESStore(save_ok=True, new_version=2)
        fake_redis = FakeRedisBus(enabled=True, set_raises=True)
        res = self._run(fake_es, fake_redis)

        self.assertTrue(res.success)
        self.assertTrue(res.es_written)
        self.assertFalse(res.redis_written)
        self.assertTrue(any("Redis" in w for w in res.warnings))

    def test_prod_redis_disabled_only_warns(self):
        fake_es = FakeESStore(save_ok=True, new_version=2)
        fake_redis = FakeRedisBus(enabled=False)
        res = self._run(fake_es, fake_redis)
        self.assertTrue(res.success)
        self.assertFalse(res.redis_written)
        self.assertTrue(any("Redis" in w for w in res.warnings))


class TestPublishPackage(PublisherTestBase):

    def test_publish_package_only_non_none_and_single_reload(self):
        with mock.patch("utils.skill_runtime.IS_DEV", True):
            results = publish_package(
                P, I,
                api_nodes={"nodeA": {}},
                biz_config={"strategy": {}},
                reload=True,
            )
        self.assertEqual(set(results.keys()), {"api_nodes", "biz_config"})
        self.assertTrue(all(r.success for r in results.values()))
        self.assertTrue(self._cfg_path("api_nodes").exists())
        self.assertTrue(self._cfg_path("biz_config").exists())
        # 批量发布只 reload 一次
        self.mock_reload.assert_called_once_with(P, I)

    def test_publish_package_partial(self):
        with mock.patch("utils.skill_runtime.IS_DEV", True):
            results = publish_package(P, I, biz_config={"strategy": {}}, reload=False)
        self.assertEqual(set(results.keys()), {"biz_config"})
        self.assertFalse(self._cfg_path("api_nodes").exists())
        self.mock_reload.assert_not_called()


class TestRollbackConfig(PublisherTestBase):

    def test_rollback_success_syncs_file_redis_reload(self):
        rolled_data = {"strategy": {"name": "回滚后配置"}}
        fake_es = FakeESStore(rollback_ok=True, published_data=rolled_data)
        fake_redis = FakeRedisBus(enabled=True)
        with mock.patch("services.es_config_store.es_config_store", fake_es), \
             mock.patch("services.redis_config_bus.redis_config_bus", fake_redis):
            res = rollback_config(P, I, "biz_config", 2, operator="op_r")

        self.assertTrue(res.success)
        self.assertTrue(res.es_written)
        self.assertEqual(res.version, 2)
        self.assertIn("已回滚到版本 2", res.message)
        self.assertEqual(fake_es.rollback_calls,
                         [(P, I, "biz_config", 2, "op_r")])
        # 本地文件同步为回滚后数据
        with open(self._cfg_path(), encoding="utf-8") as f:
            self.assertEqual(json.load(f), rolled_data)
        # Redis：删旧缓存 + 写新缓存 + 广播
        self.assertEqual(fake_redis.delete_calls, [(P, I, "biz_config")])
        self.assertEqual(len(fake_redis.set_calls), 1)
        self.assertEqual(fake_redis.publish_calls, [(P, I)])
        # 本实例热更新
        self.mock_reload.assert_called_once_with(P, I)

    def test_rollback_es_failure(self):
        fake_es = FakeESStore(rollback_ok=False)
        fake_redis = FakeRedisBus(enabled=True)
        with mock.patch("services.es_config_store.es_config_store", fake_es), \
             mock.patch("services.redis_config_bus.redis_config_bus", fake_redis):
            res = rollback_config(P, I, "biz_config", 9)

        self.assertFalse(res.success)
        self.assertIn("目标版本不存在", res.message)
        self.assertFalse(self._cfg_path().exists())
        self.assertEqual(fake_redis.publish_calls, [])
        self.mock_reload.assert_not_called()

    def test_rollback_success_but_no_published_data(self):
        """回滚成功但读不到数据：整体仍成功，warnings 说明未同步。"""
        fake_es = FakeESStore(rollback_ok=True, published_data=None)
        fake_redis = FakeRedisBus(enabled=True)
        with mock.patch("services.es_config_store.es_config_store", fake_es), \
             mock.patch("services.redis_config_bus.redis_config_bus", fake_redis):
            res = rollback_config(P, I, "biz_config", 2)

        self.assertTrue(res.success)
        self.assertFalse(res.file_written)
        self.assertTrue(any("未读取到" in w for w in res.warnings))
        # 仍然广播（其他实例可从 ES 读到回滚结果）
        self.assertEqual(fake_redis.publish_calls, [(P, I)])


class TestSkillRuntimeDelegation(PublisherTestBase):
    """skill_runtime save_* / rollback_config 委托 publisher 的回归验证"""

    def test_save_biz_config_returns_publisher_success(self):
        with mock.patch("utils.skill_runtime.IS_DEV", True):
            ok = skill_registry.save_biz_config(P, I, {"strategy": {}}, skip_reload=True)
        self.assertTrue(ok)
        self.assertTrue(self._cfg_path().exists())
        self.mock_reload.assert_not_called()

    def test_save_biz_config_honest_failure(self):
        """生产模式 ES 失败时 save_biz_config 返回 False（不再假成功）。"""
        fake_es = FakeESStore(save_ok=False)
        fake_redis = FakeRedisBus(enabled=True)
        with mock.patch("utils.skill_runtime.IS_DEV", False), \
             mock.patch("services.es_config_store.es_config_store", fake_es), \
             mock.patch("services.redis_config_bus.redis_config_bus", fake_redis):
            ok = skill_registry.save_biz_config(P, I, {"strategy": {}})
        self.assertFalse(ok)

    def test_save_biz_config_failure_keeps_memory_unchanged(self):
        """发布失败时不更新内存 pkg.config（诚实失败=状态不变，重试不产生重复模板）。"""
        from utils.skill_runtime import SkillScenarioPackage
        old_cfg = {"strategy": {"v": "old"}, "script_templates_v2": [{"template_id": "t1"}]}
        pkg = SkillScenarioPackage(
            province=P, intent=I,
            config={"biz_config": json.loads(json.dumps(old_cfg)), "api_nodes": {}},
        )
        key = f"{P}:{I}"
        skill_registry._packages[key] = pkg
        try:
            fake_es = FakeESStore(save_ok=False)
            fake_redis = FakeRedisBus(enabled=True)
            with mock.patch("utils.skill_runtime.IS_DEV", False), \
                 mock.patch("services.es_config_store.es_config_store", fake_es), \
                 mock.patch("services.redis_config_bus.redis_config_bus", fake_redis):
                ok = skill_registry.save_biz_config(P, I, {"strategy": {"v": "new"}})
            self.assertFalse(ok)
            self.assertEqual(pkg.config["biz_config"], old_cfg)
        finally:
            skill_registry._packages.pop(key, None)

    def test_save_biz_config_skip_reload_skips_broadcast(self):
        """skip_reload=True 映射 reload=False + broadcast=False（批量导入防广播风暴）。"""
        fake_es = FakeESStore(save_ok=True, new_version=4)
        fake_redis = FakeRedisBus(enabled=True)
        with mock.patch("utils.skill_runtime.IS_DEV", False), \
             mock.patch("services.es_config_store.es_config_store", fake_es), \
             mock.patch("services.redis_config_bus.redis_config_bus", fake_redis):
            ok = skill_registry.save_biz_config(P, I, {"strategy": {}}, skip_reload=True)
        self.assertTrue(ok)
        self.assertFalse(fake_es.save_calls[0][6])      # ES notify=False
        self.assertEqual(fake_redis.publish_calls, [])  # publisher 也不广播
        self.mock_reload.assert_not_called()

    def test_save_api_nodes_delegates(self):
        with mock.patch("utils.skill_runtime.IS_DEV", True):
            ok = skill_registry.save_api_nodes(P, I, {"nodeA": {}})
        self.assertTrue(ok)
        self.assertTrue(self._cfg_path("api_nodes").exists())
        self.mock_reload.assert_called_once_with(P, I)

    def test_rollback_config_delegates(self):
        fake_es = FakeESStore(rollback_ok=True, published_data={"k": "v"})
        fake_redis = FakeRedisBus(enabled=True)
        with mock.patch("services.es_config_store.es_config_store", fake_es), \
             mock.patch("services.redis_config_bus.redis_config_bus", fake_redis):
            ok, msg = skill_registry.rollback_config(P, I, "biz_config", 3)
        self.assertTrue(ok)
        self.assertEqual(msg, "已回滚到版本 3")


if __name__ == "__main__":
    unittest.main(verbosity=2)
