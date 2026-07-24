"""
Redis 配置总线

职责：
  1. 热缓存：缓存已发布配置，减少 ES 读压力
  2. Pub/Sub：跨实例通知，发布/回滚后广播让所有实例 reload

Redis Key 设计（生产隔离，znhs:agent: 前缀）：
  热缓存：znhs:agent:cfg:{province}:{intent}:{config_type}   TTL 2h
  Channel：znhs:agent:config:changed
  消息格式："{province}:{intent}"

支持 Redis Cluster 模式（生产环境 6 节点集群）。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

# ── 常量 ──────────────────────────────────────────────────────
KEY_PREFIX    = "znhs:agent:cfg:"
PUBSUB_CHANNEL = "znhs:agent:config:changed"
CACHE_TTL     = 86400  # 24 小时（配合 Pub/Sub 主动失效，TTL 仅作最终兜底）


class RedisConfigBus:
    """Redis 配置总线（单例）"""

    _instance: Optional["RedisConfigBus"] = None
    _client = None
    _pubsub_client = None   # 专用订阅连接
    _enabled: bool = False
    _is_cluster: bool = False

    def __new__(cls) -> "RedisConfigBus":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ── 初始化 ──────────────────────────────────────────────────

    def init(self, cfg: Dict[str, Any]) -> None:
        """
        cfg 示例（集群模式）：
        {
          "cluster_nodes": [
            {"host": "ha-znkf-0.ha-znkf.zjjpt-redis.svc.huaarmcore.hpc", "port": 3158},
            ...
          ],
          "password": "znkf0724",
          "key_prefix": "znhs:agent:",          # 可覆盖
          "cache_ttl": 7200,
          "pubsub_channel": "znhs:agent:config:changed"
        }

        也支持单机模式：
        {
          "host": "localhost",
          "port": 6379,
          "password": "",
          "db": 0
        }
        """
        global KEY_PREFIX, PUBSUB_CHANNEL, CACHE_TTL

        KEY_PREFIX     = cfg.get("key_prefix", KEY_PREFIX)
        PUBSUB_CHANNEL = cfg.get("pubsub_channel", PUBSUB_CHANNEL)
        CACHE_TTL      = int(cfg.get("cache_ttl", CACHE_TTL))

        cluster_nodes: List[Dict] = cfg.get("cluster_nodes", [])
        password = cfg.get("password", "")

        try:
            if cluster_nodes:
                self._init_cluster(cluster_nodes, password)
            else:
                self._init_standalone(cfg)
        except Exception as e:
            logger.warning(f"[RedisConfigBus] 初始化失败（不影响主服务）: {e}")

    def _init_cluster(self, nodes: List[Dict], password: str) -> None:
        try:
            from redis.cluster import RedisCluster, ClusterNode
            startup_nodes = [ClusterNode(n["host"], n["port"]) for n in nodes]
            kwargs: Dict[str, Any] = {
                "startup_nodes": startup_nodes,
                "decode_responses": True,
                "skip_full_coverage_check": True,
            }
            if password:
                kwargs["password"] = password
            self._client = RedisCluster(**kwargs)
            self._client.ping()
            self._is_cluster = True
            self._enabled = True
            logger.info(f"[RedisConfigBus] 已连接 Redis Cluster（{len(nodes)} 节点）")
        except ImportError:
            logger.warning("[RedisConfigBus] redis-py 未安装，Redis 总线不可用")
        except Exception as e:
            logger.warning(f"[RedisConfigBus] Redis Cluster 连接失败: {e}")

    def _init_standalone(self, cfg: Dict[str, Any]) -> None:
        try:
            import redis
            self._client = redis.Redis(
                host=cfg.get("host", "localhost"),
                port=int(cfg.get("port", 6379)),
                db=int(cfg.get("db", 0)),
                password=cfg.get("password") or None,
                decode_responses=True,
            )
            self._client.ping()
            self._is_cluster = False
            self._enabled = True
            logger.info(f"[RedisConfigBus] 已连接 Redis standalone: {cfg.get('host')}:{cfg.get('port')}")
        except ImportError:
            logger.warning("[RedisConfigBus] redis-py 未安装，Redis 总线不可用")
        except Exception as e:
            logger.warning(f"[RedisConfigBus] Redis standalone 连接失败: {e}")

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    # ── 缓存操作 ──────────────────────────────────────────────────

    def _cache_key(self, province: str, intent: str, config_type: str) -> str:
        return f"{KEY_PREFIX}{province}:{intent}:{config_type}"

    def set_config(
        self, province: str, intent: str, config_type: str, data: Dict[str, Any]
    ) -> bool:
        """写入热缓存"""
        if not self.enabled:
            return False
        try:
            key = self._cache_key(province, intent, config_type)
            self._client.setex(key, CACHE_TTL, json.dumps(data, ensure_ascii=False))
            return True
        except Exception as e:
            logger.warning(f"[RedisConfigBus] 写缓存失败 {province}/{intent}/{config_type}: {e}")
            return False

    def get_config(
        self, province: str, intent: str, config_type: str
    ) -> Optional[Dict[str, Any]]:
        """读取热缓存，未命中返回 None"""
        if not self.enabled:
            return None
        try:
            key = self._cache_key(province, intent, config_type)
            val = self._client.get(key)
            if val:
                return json.loads(val)
            return None
        except Exception as e:
            logger.warning(f"[RedisConfigBus] 读缓存失败 {province}/{intent}/{config_type}: {e}")
            return None

    def delete_config(self, province: str, intent: str, config_type: str) -> None:
        """删除缓存（发布/回滚时先删，让服务重新从 ES 加载）"""
        if not self.enabled:
            return
        try:
            key = self._cache_key(province, intent, config_type)
            self._client.delete(key)
        except Exception as e:
            logger.warning(f"[RedisConfigBus] 删除缓存失败: {e}")

    # ── Pub/Sub 发布 ──────────────────────────────────────────────

    def publish_change(self, province: str, intent: str) -> bool:
        """
        通知所有实例：某个技能包配置已更新，需要 reload。
        消息格式："{province}:{intent}"
        """
        if not self.enabled:
            return False
        try:
            message = f"{province}:{intent}"
            self._client.publish(PUBSUB_CHANNEL, message)
            logger.info(f"[RedisConfigBus] 已广播配置变更通知: {message}")
            return True
        except Exception as e:
            logger.warning(f"[RedisConfigBus] 发布变更通知失败: {e}")
            return False

    # ── Pub/Sub 订阅（后台线程）──────────────────────────────────

    def start_subscriber(self, on_change: Callable[[str, str], None]) -> None:
        """
        启动后台线程监听配置变更消息。

        Args:
            on_change: 回调函数，签名 on_change(province, intent)
        """
        if not self.enabled:
            logger.info("[RedisConfigBus] Redis 不可用，跳过 Pub/Sub 订阅")
            return

        t = threading.Thread(
            target=self._subscribe_loop,
            args=(on_change,),
            name="redis-config-subscriber",
            daemon=True,
        )
        t.start()
        logger.info(f"[RedisConfigBus] Pub/Sub 订阅线程已启动，Channel={PUBSUB_CHANNEL}")

    def _subscribe_loop(self, on_change: Callable[[str, str], None]) -> None:
        """订阅循环，断线自动重连。
        重连成功后触发全量 reload（on_change("*", "*")），避免断线期间遗漏的配置变更。
        """
        _first_connect = True
        while True:
            try:
                pubsub = self._client.pubsub()
                pubsub.subscribe(PUBSUB_CHANNEL)
                logger.info(f"[RedisConfigBus] 已订阅 Channel: {PUBSUB_CHANNEL}")

                # 非首次连接（即断线重连）：触发全量 reload，补偿断线期间遗漏的变更
                if not _first_connect:
                    logger.info("[RedisConfigBus] 断线重连成功，触发全量 reload")
                    try:
                        on_change("*", "*")
                    except Exception as e:
                        logger.error(f"[RedisConfigBus] 重连全量 reload 异常: {e}")
                _first_connect = False

                for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    data = message.get("data", "")
                    if not data or ":" not in data:
                        continue
                    parts = data.split(":", 1)
                    province, intent = parts[0], parts[1]
                    logger.info(f"[RedisConfigBus] 收到配置变更通知: {province}/{intent}")
                    try:
                        on_change(province, intent)
                    except Exception as e:
                        logger.error(f"[RedisConfigBus] 处理变更通知异常: {e}")

            except Exception as e:
                logger.warning(f"[RedisConfigBus] 订阅循环异常，5s 后重连: {e}")
                time.sleep(5)


# 全局单例
redis_config_bus = RedisConfigBus()
