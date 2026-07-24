"""
test_config_sync_dispatch — utils/skill_runtime.SkillRuntimeRegistry.start_config_sync 单元测试

覆盖 sync_mode（pubsub/poll/hybrid/未知值回退/development 跳过）到底调用了
redis_config_bus.start_subscriber 与 config_poller.start 中的哪一个/哪几个，
以及 poll_interval_seconds 是否被正确传递、旧入口 start_config_subscriber 的兼容行为。
全程 monkeypatch，不接触真实 Redis/ES。

运行：cd ROOT && python -m unittest tests.test_config_sync_dispatch -v
"""
import unittest

import utils.skill_runtime as skill_runtime_mod
from utils.skill_runtime import skill_registry


class TestStartConfigSync(unittest.TestCase):

    def setUp(self):
        self._old_is_dev = skill_runtime_mod.IS_DEV
        skill_runtime_mod.IS_DEV = False  # 强制走 production 分支

        import services.redis_config_bus as redis_bus_mod
        import services.config_poller as poller_mod
        self._old_start_subscriber = redis_bus_mod.redis_config_bus.start_subscriber
        self._old_poller_start = poller_mod.config_poller.start

        self.subscriber_calls = []
        self.poller_calls = []
        redis_bus_mod.redis_config_bus.start_subscriber = (
            lambda on_change: self.subscriber_calls.append(on_change)
        )
        poller_mod.config_poller.start = (
            lambda on_change, interval_seconds=None:
                self.poller_calls.append((on_change, interval_seconds))
        )

    def tearDown(self):
        skill_runtime_mod.IS_DEV = self._old_is_dev
        import services.redis_config_bus as redis_bus_mod
        import services.config_poller as poller_mod
        redis_bus_mod.redis_config_bus.start_subscriber = self._old_start_subscriber
        poller_mod.config_poller.start = self._old_poller_start

    def test_development_mode_skips_everything(self):
        skill_runtime_mod.IS_DEV = True
        skill_registry.start_config_sync({"sync_mode": "hybrid"})
        self.assertEqual(self.subscriber_calls, [])
        self.assertEqual(self.poller_calls, [])

    def test_pubsub_mode_only_starts_subscriber(self):
        skill_registry.start_config_sync({"sync_mode": "pubsub"})
        self.assertEqual(len(self.subscriber_calls), 1)
        self.assertEqual(self.poller_calls, [])

    def test_poll_mode_only_starts_poller(self):
        skill_registry.start_config_sync(
            {"sync_mode": "poll", "poll_interval_seconds": 120}
        )
        self.assertEqual(self.subscriber_calls, [])
        self.assertEqual(len(self.poller_calls), 1)
        self.assertEqual(self.poller_calls[0][1], 120)

    def test_hybrid_mode_starts_both(self):
        skill_registry.start_config_sync(
            {"sync_mode": "hybrid", "poll_interval_seconds": 60}
        )
        self.assertEqual(len(self.subscriber_calls), 1)
        self.assertEqual(len(self.poller_calls), 1)
        self.assertEqual(self.poller_calls[0][1], 60)

    def test_default_mode_is_hybrid_when_unspecified(self):
        skill_registry.start_config_sync({})
        self.assertEqual(len(self.subscriber_calls), 1)
        self.assertEqual(len(self.poller_calls), 1)
        self.assertEqual(self.poller_calls[0][1], 300)  # 默认 5 分钟

    def test_none_sync_cfg_falls_back_to_hybrid_defaults(self):
        skill_registry.start_config_sync(None)
        self.assertEqual(len(self.subscriber_calls), 1)
        self.assertEqual(len(self.poller_calls), 1)
        self.assertEqual(self.poller_calls[0][1], 300)

    def test_unknown_mode_falls_back_to_hybrid(self):
        skill_registry.start_config_sync({"sync_mode": "kafka"})
        self.assertEqual(len(self.subscriber_calls), 1)
        self.assertEqual(len(self.poller_calls), 1)

    def test_legacy_start_config_subscriber_maps_to_hybrid(self):
        """旧入口兼容：start_config_subscriber() 等价 hybrid 默认行为。"""
        skill_registry.start_config_subscriber()
        self.assertEqual(len(self.subscriber_calls), 1)
        self.assertEqual(len(self.poller_calls), 1)

    def test_on_change_callback_reloads_specific_skill(self):
        """透传的回调应精确 reload 对应 (province, intent)；"*" 触发全量。"""
        skill_registry.start_config_sync({"sync_mode": "poll"})
        on_change = self.poller_calls[0][0]

        reload_calls = []
        old_reload = skill_registry.reload
        skill_registry.reload = (
            lambda province=None, intent=None: reload_calls.append((province, intent))
        )
        try:
            on_change("shandong", "intent_x")
            on_change("*", "*")
        finally:
            skill_registry.reload = old_reload

        self.assertEqual(reload_calls, [("shandong", "intent_x"), (None, None)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
