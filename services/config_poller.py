"""
ES 配置定时轮询器

背景：多实例配置同步此前只有 Redis Pub/Sub（services/redis_config_bus.py）一条路径——
某实例发布/回滚后广播变更消息，其他实例订阅到消息后 reload。这条路径延迟低，但存在盲区：
Redis 重启/网络抖动窗口期内错过的广播只能靠"断线重连触发全量 reload"兜底，若重连恰好也
失败过一轮，该窗口期的变更就会被漏掉，直到下一次配置变更或重启才会被动刷新。

本模块提供第二条独立路径：定时读取 ES 版本号摘要（es_config_store.get_all_published_versions，
一次 search，不逐个 get），与本地缓存的版本快照比较，只对发生变化的技能包触发 reload。
不依赖 Redis 是否健康，保证生产环境最终一定能读到 ES 中的最新配置。

同步模式由 config/config.json 的 redis_bus.sync_mode 决定（utils/skill_runtime.start_config_sync）：
  pubsub  — 仅 Redis Pub/Sub（不启用本轮询器，等价于原有行为）
  poll    — 仅本轮询器（不订阅 Pub/Sub，适用于不具备 Redis 条件的部署）
  hybrid  — 两者并存：Pub/Sub 负责低延迟，轮询负责自愈兜底（推荐，默认）

轮询间隔由 redis_bus.poll_interval_seconds 配置，默认 300 秒（5 分钟）。
"""
from __future__ import annotations

import threading
from typing import Callable, Dict, Optional

from loguru import logger

DEFAULT_POLL_INTERVAL = 300


class ConfigPoller:
    """定时轮询 ES 配置版本号，检测变更并触发 reload（单例，__new__ 单例模式与
    RedisConfigBus/ESConfigStore 保持一致，不用 __init__ 避免重复调用 ConfigPoller() 时状态被重置）。
    """

    _instance: Optional["ConfigPoller"] = None
    _baseline: Dict[str, int] = {}
    _thread: Optional[threading.Thread] = None
    _stop_event: threading.Event = threading.Event()
    _interval: int = DEFAULT_POLL_INTERVAL

    def __new__(cls) -> "ConfigPoller":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ── 启动 / 停止 ──────────────────────────────────────────────

    def start(
        self,
        on_change: Callable[[str, str], None],
        interval_seconds: Optional[int] = None,
    ) -> None:
        """启动后台轮询线程。

        首次调用只建立版本基线（不触发 reload）：初始化阶段 skill_registry.initialize()
        已经从 ES/Redis 加载过一次最新配置，此处基线与之天然一致，避免启动瞬间被误判为
        "全部技能包都变了"而触发一次没必要的全量 reload 风暴。
        """
        from services.es_config_store import es_config_store

        if not es_config_store.enabled:
            logger.info("[ConfigPoller] ES 不可用，跳过定时轮询")
            return

        self._interval = int(interval_seconds or DEFAULT_POLL_INTERVAL)
        self._baseline = es_config_store.get_all_published_versions()
        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._loop, args=(on_change,), name="config-poller", daemon=True,
        )
        self._thread.start()
        logger.info(
            f"[ConfigPoller] 已启动定时轮询（基线 {len(self._baseline)} 项，间隔 {self._interval}s）"
        )

    def stop(self) -> None:
        self._stop_event.set()

    # ── 轮询循环 ──────────────────────────────────────────────────

    def _loop(self, on_change: Callable[[str, str], None]) -> None:
        # wait() 超时返回 False 才继续轮询；stop() 触发 set() 后立即退出循环
        while not self._stop_event.wait(self._interval):
            self._poll_once(on_change)

    def _poll_once(self, on_change: Callable[[str, str], None]) -> None:
        """执行一次轮询比对。拆成独立方法方便单测直接调用，不依赖真实线程/sleep。"""
        from services.es_config_store import es_config_store
        try:
            current = es_config_store.get_all_published_versions()
        except Exception as e:
            logger.warning(f"[ConfigPoller] 轮询获取版本摘要异常（不影响下次轮询）: {e}")
            return

        if not current:
            # ES 暂不可用或搜索异常返回空：不覆盖基线，避免下次误判为"全部技能包被删除"
            return

        changed_keys = {k for k, v in current.items() if self._baseline.get(k) != v}
        self._baseline = current
        if not changed_keys:
            return

        changed_pairs = set()
        for key in changed_keys:
            parts = key.split(":", 2)
            if len(parts) >= 2:
                changed_pairs.add((parts[0], parts[1]))

        logger.info(f"[ConfigPoller] 检测到 {len(changed_pairs)} 个技能包配置变更: {sorted(changed_pairs)}")
        for province, intent in changed_pairs:
            try:
                on_change(province, intent)
            except Exception as e:
                logger.error(f"[ConfigPoller] 处理变更 {province}/{intent} 异常: {e}")


# 全局单例
config_poller = ConfigPoller()
