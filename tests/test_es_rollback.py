"""
test_es_rollback — services/es_config_store.py rollback / get_current_version_info 单元测试

用自写 FakeES（实现 get/index/update/delete/search 的最小行为）注入
es_config_store 单例的 _client 属性，覆盖：
  - 正常回滚（状态交换 / meta 交换 / comment 追加）
  - 回滚到当前版本
  - 目标版本不存在（不在 versions 列表 / 文档被清理）
  - ES 未启用
  - get_current_version_info 各分支
不调用任何真实网络/ES/Redis/LLM。

运行：cd ROOT && python -m unittest tests.test_es_rollback -v
"""
import unittest

from services import es_config_store as es_mod
from services.es_config_store import es_config_store


class FakeES:
    """最小 ES 假客户端：文档存在内存字典 {(index, id): source}"""

    def __init__(self):
        self.docs = {}

    def get(self, index, id, ignore=None, **kwargs):
        src = self.docs.get((index, id))
        if src is None:
            return {"found": False}
        return {"found": True, "_source": dict(src)}

    def index(self, index, id, body, **kwargs):
        self.docs[(index, id)] = dict(body)
        return {"result": "created"}

    def update(self, index, id, body, ignore=None, **kwargs):
        src = self.docs.get((index, id))
        if src is None:
            if ignore and 404 in ignore:
                return {"result": "not_found"}
            raise KeyError(f"doc not found: {index}/{id}")
        src.update(body.get("doc", {}))
        return {"result": "updated"}

    def delete(self, index, id, ignore=None, **kwargs):
        if (index, id) not in self.docs:
            if ignore and 404 in ignore:
                return {"result": "not_found"}
            raise KeyError(f"doc not found: {index}/{id}")
        del self.docs[(index, id)]
        return {"result": "deleted"}

    def search(self, index, body=None, **kwargs):
        hits = [
            {"_id": _id, "_source": dict(src)}
            for (idx, _id), src in self.docs.items()
            if idx == index
        ]
        return {"hits": {"hits": hits}}


P, I, T = "shandong", "intent_x", "biz_config"


class ESRollbackTestBase(unittest.TestCase):
    """公共 setUp / tearDown：注入 FakeES，恢复单例原状。"""

    def setUp(self):
        self._old_client = es_config_store._client
        self._old_enabled = es_config_store._enabled
        self.fake = FakeES()
        es_config_store._client = self.fake
        es_config_store._enabled = True

    def tearDown(self):
        es_config_store._client = self._old_client
        es_config_store._enabled = self._old_enabled

    def _publish(self, data, comment=""):
        ok, msg, ver = es_config_store.save_and_publish(
            P, I, T, data, operator="tester", comment=comment
        )
        self.assertTrue(ok, msg)
        return ver

    def _meta(self):
        return self.fake.docs[(es_mod.INDEX_META, f"{P}:{I}:{T}")]

    def _doc(self, version):
        return self.fake.docs.get((es_mod.INDEX_CONFIGS, f"{P}:{I}:{T}:{version}"))


class TestRollback(ESRollbackTestBase):

    def test_rollback_success(self):
        """正常回滚：v2 归档、v1 重新发布、meta 交换、comment 追加来源。"""
        self._publish({"k": "v1"}, comment="first")
        self._publish({"k": "v2"})

        ok, msg = es_config_store.rollback(P, I, T, 1, operator="op_a")
        self.assertTrue(ok)
        self.assertEqual(msg, "已回滚到版本 1")

        # 状态交换
        self.assertEqual(self._doc(1)["status"], "published")
        self.assertEqual(self._doc(2)["status"], "archived")
        # comment 追加回滚来源（原 comment 保留）
        self.assertIn("rollback from v2", self._doc(1)["comment"])
        self.assertIn("first", self._doc(1)["comment"])
        self.assertEqual(self._doc(1)["published_by"], "op_a")
        # meta 交换
        meta = self._meta()
        self.assertEqual(meta["published_version"], 1)
        self.assertEqual(list(meta["versions"]), [2])
        # get_published 读到回滚后的数据
        self.assertEqual(es_config_store.get_published(P, I, T), {"k": "v1"})

    def test_rollback_to_current_version(self):
        """回滚到当前 published 版本应拒绝。"""
        self._publish({"k": "v1"})
        ok, msg = es_config_store.rollback(P, I, T, 1)
        self.assertFalse(ok)
        self.assertEqual(msg, "已是当前版本")

    def test_rollback_target_not_in_versions(self):
        """目标版本不在归档列表：拒绝。"""
        self._publish({"k": "v1"})
        self._publish({"k": "v2"})
        ok, msg = es_config_store.rollback(P, I, T, 99)
        self.assertFalse(ok)
        self.assertEqual(msg, "目标版本不存在或已被清理")

    def test_rollback_target_doc_missing(self):
        """目标版本在 meta 里但文档已被清理：拒绝。"""
        self._publish({"k": "v1"})
        self._publish({"k": "v2"})
        # 手工删掉 v1 文档，模拟被清理
        del self.fake.docs[(es_mod.INDEX_CONFIGS, f"{P}:{I}:{T}:1")]
        ok, msg = es_config_store.rollback(P, I, T, 1)
        self.assertFalse(ok)
        self.assertIn("文档不存在", msg)

    def test_rollback_no_meta(self):
        """从未发布过的配置：拒绝。"""
        ok, msg = es_config_store.rollback(P, I, T, 1)
        self.assertFalse(ok)
        self.assertIn("无法回滚", msg)

    def test_rollback_disabled(self):
        """ES 未启用：直接返回 (False, 'ES 未启用')。"""
        es_config_store._enabled = False
        ok, msg = es_config_store.rollback(P, I, T, 1)
        self.assertFalse(ok)
        self.assertEqual(msg, "ES 未启用")

    def test_rollback_then_publish_again(self):
        """回滚后再次发布，版本号在回滚目标基础上 +1（与 save_and_publish 语义一致）。"""
        self._publish({"k": "v1"})
        self._publish({"k": "v2"})
        ok, _ = es_config_store.rollback(P, I, T, 1)
        self.assertTrue(ok)
        ver = self._publish({"k": "v3"})
        self.assertEqual(ver, 2)  # published_version 回到 1，再发布即 2
        meta = self._meta()
        self.assertEqual(meta["published_version"], 2)


class TestGetCurrentVersionInfo(ESRollbackTestBase):

    def test_version_info_normal(self):
        self._publish({"k": "v1"})
        self._publish({"k": "v2"})
        info = es_config_store.get_current_version_info(P, I, T)
        self.assertIsNotNone(info)
        self.assertEqual(info["published_version"], 2)
        self.assertEqual(list(info["archived_versions"]), [1])
        self.assertIn("updated_at", info)

    def test_version_info_no_meta(self):
        """meta 不存在时返回默认指针结构（published_version=None）。"""
        info = es_config_store.get_current_version_info(P, I, T)
        self.assertIsNotNone(info)
        self.assertIsNone(info["published_version"])
        self.assertEqual(info["archived_versions"], [])

    def test_version_info_disabled(self):
        es_config_store._enabled = False
        self.assertIsNone(es_config_store.get_current_version_info(P, I, T))


class TestGetAllPublishedVersions(ESRollbackTestBase):
    """get_all_published_versions：定时轮询用的版本号摘要接口"""

    def test_empty_when_no_docs(self):
        self.assertEqual(es_config_store.get_all_published_versions(), {})

    def test_returns_meta_id_to_version(self):
        self._publish({"k": "v1"})
        self._publish({"k": "v2"})
        result = es_config_store.get_all_published_versions()
        self.assertEqual(result, {f"{P}:{I}:{T}": 2})

    def test_multiple_packages(self):
        self._publish({"k": "a"})
        ok, msg, _ = es_config_store.save_and_publish(
            "guangdong", "营销活动", "api_nodes", {"n": {}}, operator="tester"
        )
        self.assertTrue(ok, msg)
        result = es_config_store.get_all_published_versions()
        self.assertEqual(result, {
            f"{P}:{I}:{T}": 1,
            "guangdong:营销活动:api_nodes": 1,
        })

    def test_disabled_returns_empty(self):
        es_config_store._enabled = False
        self.assertEqual(es_config_store.get_all_published_versions(), {})

    def test_search_exception_returns_empty(self):
        def _boom(**kwargs):
            raise RuntimeError("es down")
        self.fake.search = _boom
        self.assertEqual(es_config_store.get_all_published_versions(), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
