"""SkillRuntimeLoader._load_config_from_external 逐类型兜底回归测试

锁定「编辑接口保存后话术模板被冲掉」的根因修复：
publish_config 保存 api_nodes 时只回写了 api_nodes 的 Redis 缓存并触发 reload，
此刻 biz_config 缓存 miss(None)。修复前旧逻辑会把 biz_config 当空返回、冲掉内存里的
话术模板；修复后应逐类型回落 ES 取回 biz_config，绝不返回空。
"""
from __future__ import annotations

import sys
import unittest
import unittest.mock as mock
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import utils.skill_runtime as sr  # noqa: E402
from utils.skill_runtime import SkillRuntimeLoader  # noqa: E402


class _FakeRedis:
    def __init__(self, cache):
        self.cache = dict(cache)   # {(p,i,type): data}
        self.sets = []

    def get_config(self, p, i, ct):
        return self.cache.get((p, i, ct))

    def set_config(self, p, i, ct, data):
        self.sets.append((p, i, ct))
        self.cache[(p, i, ct)] = data
        return True


class _FakeES:
    def __init__(self, published):
        self.published = dict(published)   # {(p,i,type): data}

    def get_published(self, p, i, ct):
        return self.published.get((p, i, ct))


class TestPartialCacheFallback(unittest.TestCase):
    def _call(self, redis, es):
        with mock.patch.object(sr, "IS_DEV", False), \
             mock.patch("services.redis_config_bus.redis_config_bus", redis), \
             mock.patch("services.es_config_store.es_config_store", es):
            return SkillRuntimeLoader._load_config_from_external("beijing", "营销推荐")

    def test_redis_has_only_api_nodes_biz_falls_back_to_es(self):
        """核心场景：Redis 只缓存 api_nodes，biz_config 必须从 ES 取回而非返回空。"""
        redis = _FakeRedis({("beijing", "营销推荐", "api_nodes"): {"n": 1}})
        es = _FakeES({("beijing", "营销推荐", "biz_config"):
                      {"script_templates_v2": [{"product_id": "1"}]}})
        api_nodes, biz_config, source = self._call(redis, es)
        self.assertEqual(api_nodes, {"n": 1})
        self.assertTrue(biz_config.get("script_templates_v2"))   # 模板没被冲掉
        self.assertEqual(source, "redis")
        # biz_config 已回写 Redis 缓存
        self.assertIn(("beijing", "营销推荐", "biz_config"), redis.sets)

    def test_redis_has_only_biz_api_falls_back_to_es(self):
        """对称场景：只缓存 biz_config 时 api_nodes 回落 ES。"""
        redis = _FakeRedis({("beijing", "营销推荐", "biz_config"): {"script_templates_v2": [{}]}})
        es = _FakeES({("beijing", "营销推荐", "api_nodes"): {"n_api": {}}})
        api_nodes, biz_config, source = self._call(redis, es)
        self.assertEqual(api_nodes, {"n_api": {}})
        self.assertTrue(biz_config.get("script_templates_v2"))
        self.assertEqual(source, "redis")

    def test_both_cached_returns_both(self):
        redis = _FakeRedis({
            ("beijing", "营销推荐", "api_nodes"): {"n": 1},
            ("beijing", "营销推荐", "biz_config"): {"script_templates_v2": [{}]},
        })
        es = _FakeES({})
        api_nodes, biz_config, source = self._call(redis, es)
        self.assertEqual(api_nodes, {"n": 1})
        self.assertTrue(biz_config.get("script_templates_v2"))
        self.assertEqual(source, "redis")

    def test_redis_miss_both_from_es(self):
        redis = _FakeRedis({})
        es = _FakeES({
            ("beijing", "营销推荐", "api_nodes"): {"n": 1},
            ("beijing", "营销推荐", "biz_config"): {"script_templates_v2": [{}]},
        })
        api_nodes, biz_config, source = self._call(redis, es)
        self.assertEqual(source, "es")
        self.assertEqual(api_nodes, {"n": 1})
        self.assertTrue(biz_config.get("script_templates_v2"))

    def test_all_empty_falls_back_local(self):
        api_nodes, biz_config, source = self._call(_FakeRedis({}), _FakeES({}))
        self.assertEqual((api_nodes, biz_config, source), ({}, {}, "local"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
