"""
轻量缓存服务

当前实现：
- 进程内 TTL 缓存
- 单飞（singleflight）防并发击穿

后续可扩展：
- Redis 二级缓存
"""
import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from utils.logger import app_logger as logger


class CacheService:
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._inflight: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def _is_expired(self, expires_at: float) -> bool:
        return expires_at <= time.time()

    def get(self, key: str) -> Optional[Any]:
        cached = self._cache.get(key)
        if not cached:
            return None

        if self._is_expired(cached["expires_at"]):
            self._cache.pop(key, None)
            return None

        return cached["value"]

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return

        self._cache[key] = {
            "value": value,
            "expires_at": time.time() + ttl_seconds,
        }

    def delete(self, key: str) -> None:
        self._cache.pop(key, None)

    def delete_prefix(self, prefix: str) -> int:
        matched_keys = [key for key in self._cache.keys() if key.startswith(prefix)]
        for key in matched_keys:
            self._cache.pop(key, None)
        return len(matched_keys)

    def delete_matching(self, matcher: Callable[[str], bool]) -> int:
        matched_keys = [key for key in self._cache.keys() if matcher(key)]
        for key in matched_keys:
            self._cache.pop(key, None)
        return len(matched_keys)

    def clear(self) -> None:
        self._cache.clear()

    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[Any]],
        ttl_seconds: int,
    ) -> Tuple[Any, bool]:
        cached = self.get(key)
        if cached is not None:
            return cached, True

        async with self._lock:
            cached = self.get(key)
            if cached is not None:
                return cached, True

            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(loader())
                self._inflight[key] = task
                owner = True
            else:
                owner = False

        try:
            value = await task
            if owner and value is not None:
                self.set(key, value, ttl_seconds)
            return value, False
        finally:
            if owner:
                async with self._lock:
                    self._inflight.pop(key, None)


cache_service = CacheService()
