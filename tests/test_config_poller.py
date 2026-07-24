"""
test_config_poller — services/config_poller.py 单元测试

覆盖：_poll_once 的版本比对（变更检测/无变更去重/新增技能包）、ES 返回空时
不覆盖基线、on_change 异常吞掉不中断、start 在 ES 禁用时跳过、stop 停止线程。
全程注入假 es_config_store，不调真实网络/ES。

运行：cd ROOT && python -m unittest tests.test_config_poller -v
"""
import threading
import unittest

import services.config_poller as poller_mod
from services.config_poller import ConfigPoller, config_poller
from services.es_config_store import es_config_store


class PollerTestBase(unittest.TestCase):
    """公共环境：接管 es_config_store 的 enabled 状态与版本摘要返回值。"""

    def setUp(self):
        self._old_enabled = es_config_store._enabled
        self._old_client = es_config_store._client
        es_config_store._enabled = True
        es_config_store._client = object()  # 只需非 None，实际调用被下面的替身拦截

        self.versions = {}
        self._old_get_all = es_config_store.get_all_published_versions
        es_config_store.get_all_published_versions = lambda: dict(self.versions)

        # 重置单例状态
        config_poller._baseline = {}
        config_poller._stop_event = threading.Event()
        config_poller._thread = None

        self.changes = []
        self.on_change = lambda p, i: self.changes.append((p, i))

    def tearDown(self):
        config_poller.stop()
        es_config_store.get_all_published_versions = self._old_get_all
        es_config_store._enabled = self._old_enabled
        es_config_store._client = self._old_client


class TestPollOnce(PollerTestBase):

    def test_no_change_no_callback(self):
        self.versions = {"beijing:套餐推荐:biz_config": 3}
        config_poller._baseline = {"beijing:套餐推荐:biz_config": 3}
        config_poller._poll_once(self.on_change)
        self.assertEqual(self.changes, [])

    def test_version_bump_triggers_change(self):
        config_poller._baseline = {"beijing:套餐推荐:biz_config": 3}
        self.versions = {"beijing:套餐推荐:biz_config": 4}
        config_poller._poll_once(self.on_change)
        self.assertEqual(self.changes, [("beijing", "套餐推荐")])

    def test_new_package_triggers_change(self):
        config_poller._baseline = {}
        self.versions = {"shandong:营销推荐:api_nodes": 1}
        config_poller._poll_once(self.on_change)
        self.assertEqual(self.changes, [("shandong", "营销推荐")])

    def test_two_config_types_same_package_dedup(self):
        """同一技能包 biz_config 与 api_nodes 同时变更，只触发一次 reload。"""
        config_poller._baseline = {
            "beijing:套餐推荐:biz_config": 1,
            "beijing:套餐推荐:api_nodes": 1,
        }
        self.versions = {
            "beijing:套餐推荐:biz_config": 2,
            "beijing:套餐推荐:api_nodes": 2,
        }
        config_poller._poll_once(self.on_change)
        self.assertEqual(self.changes, [("beijing", "套餐推荐")])

    def test_baseline_updated_after_poll(self):
        config_poller._baseline = {"a:b:biz_config": 1}
        self.versions = {"a:b:biz_config": 2}
        config_poller._poll_once(self.on_change)
        self.assertEqual(config_poller._baseline, {"a:b:biz_config": 2})
        # 再轮询一次不再触发
        config_poller._poll_once(self.on_change)
        self.assertEqual(len(self.changes), 1)

    def test_empty_result_keeps_baseline(self):
        """ES 返回空（不可用/异常）时不覆盖基线，避免下轮误判全部变更。"""
        config_poller._baseline = {"a:b:biz_config": 1}
        self.versions = {}
        config_poller._poll_once(self.on_change)
        self.assertEqual(self.changes, [])
        self.assertEqual(config_poller._baseline, {"a:b:biz_config": 1})

    def test_on_change_exception_swallowed(self):
        """单个技能包 reload 异常不中断其余变更处理，也不抛出。"""
        config_poller._baseline = {"a:b:biz_config": 1, "c:d:biz_config": 1}
        self.versions = {"a:b:biz_config": 2, "c:d:biz_config": 2}

        def _boom(p, i):
            raise RuntimeError("模拟 reload 失败")
        config_poller._poll_once(_boom)  # 不应抛异常
        self.assertEqual(config_poller._baseline["a:b:biz_config"], 2)

    def test_get_versions_exception_swallowed(self):
        es_config_store.get_all_published_versions = self._raise
        config_poller._baseline = {"a:b:biz_config": 1}
        config_poller._poll_once(self.on_change)  # 不应抛异常
        self.assertEqual(self.changes, [])
        self.assertEqual(config_poller._baseline, {"a:b:biz_config": 1})

    @staticmethod
    def _raise():
        raise RuntimeError("es down")


class TestStartStop(PollerTestBase):

    def test_start_builds_baseline_without_callback(self):
        """start 只建基线不触发 reload（初始化已加载过最新配置）。"""
        self.versions = {"beijing:套餐推荐:biz_config": 5}
        config_poller.start(self.on_change, interval_seconds=3600)
        try:
            self.assertEqual(config_poller._baseline, {"beijing:套餐推荐:biz_config": 5})
            self.assertEqual(self.changes, [])
            self.assertIsNotNone(config_poller._thread)
            self.assertTrue(config_poller._thread.is_alive())
        finally:
            config_poller.stop()
            config_poller._thread.join(timeout=2)

    def test_start_skipped_when_es_disabled(self):
        es_config_store._enabled = False
        config_poller.start(self.on_change, interval_seconds=3600)
        self.assertIsNone(config_poller._thread)

    def test_interval_defaults(self):
        self.versions = {}
        config_poller.start(self.on_change, interval_seconds=None)
        try:
            self.assertEqual(config_poller._interval, poller_mod.DEFAULT_POLL_INTERVAL)
        finally:
            config_poller.stop()
            if config_poller._thread:
                config_poller._thread.join(timeout=2)

    def test_singleton(self):
        self.assertIs(ConfigPoller(), config_poller)


if __name__ == "__main__":
    unittest.main(verbosity=2)
