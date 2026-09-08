"""
test_llm_stats — 分省模型调用量统计模块（一期：Redis 计数器）测试

覆盖：
 1. record 聚合：success/fail/token/elapsed 累加、意图细分、剔除 province=test、
    空省份→unknown、禁用时 no-op、缓冲上限丢弃；
 2. flush_once（FakeRedis dict 模拟 HINCRBY/SADD/EXPIRE）：key 名（{yyyymmdd} hash tag）、
    field、TTL、失败回合并、swap 后 record 写新缓冲；
 3. query_daily / list_provinces：行结构、avg_elapsed_ms、intents 细分、分省过滤、日期遍历、
    Redis 异常返回 []；
 4. llm_service 插桩：mock httpx 客户端（含 usage），断言成功/失败分支 record 计数正确。

运行：cd ROOT && python -m pytest tests/test_llm_stats.py -q
约束：不调真实网络/ES/Redis/LLM。
"""
from __future__ import annotations

import asyncio
import json
import unittest

from services.llm_stats import (
    LlmStats,
    TTL_SECONDS,
    _hash_key,
    _provinces_key,
)


# ── Fake Redis ──────────────────────────────────────────────────

class FakeRedis:
    """dict 模拟 Redis 的 HINCRBY / SADD / SMEMBERS / HGETALL / EXPIRE。"""

    def __init__(self) -> None:
        self.hashes = {}      # key → {field: int}
        self.sets = {}        # key → set
        self.expires = {}     # key → ttl

    def hincrby(self, key, field, amount=1):
        h = self.hashes.setdefault(key, {})
        h[field] = int(h.get(field, 0)) + int(amount)
        return h[field]

    def sadd(self, key, *members):
        s = self.sets.setdefault(key, set())
        for m in members:
            s.add(m)
        return len(members)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def expire(self, key, ttl):
        self.expires[key] = ttl
        return True


class FailingRedis(FakeRedis):
    """第一次 hincrby 必抛异常，模拟 Redis 故障。"""

    def hincrby(self, key, field, amount=1):
        raise ConnectionError("redis down")


def _make_stats(client=None) -> LlmStats:
    s = LlmStats()
    s.init(client if client is not None else FakeRedis())
    return s


class TestRecord(unittest.TestCase):
    """record：纯内存聚合语义。"""

    def test_disabled_noop(self):
        s = LlmStats()           # 未 init → disabled
        s.record("henan", "语音共享", "m", True, 100)
        self.assertEqual(s._buffer, {})

    def test_aggregate_success_fail_tokens_elapsed(self):
        s = _make_stats()
        s.record("henan", "语音共享", "m", True, 100, prompt_tokens=10, completion_tokens=5)
        s.record("henan", "语音共享", "m", False, 300, prompt_tokens=20, completion_tokens=0)
        self.assertEqual(len(s._buffer), 1)
        buf = next(iter(s._buffer.values()))
        self.assertEqual(buf["calls"], 2)
        self.assertEqual(buf["success"], 1)
        self.assertEqual(buf["fail"], 1)
        self.assertEqual(buf["prompt_tokens"], 30)
        self.assertEqual(buf["completion_tokens"], 5)
        self.assertEqual(buf["elapsed_ms_sum"], 400)
        # 意图细分
        self.assertEqual(buf["calls@语音共享"], 2)
        self.assertEqual(buf["fail@语音共享"], 1)
        self.assertEqual(buf["prompt_tokens@语音共享"], 30)

    def test_test_province_excluded(self):
        s = _make_stats()
        s.record("test", "语音共享", "m", True, 100)
        self.assertEqual(s._buffer, {})

    def test_empty_province_goes_unknown(self):
        s = _make_stats()
        s.record("", "i", "m", True, 100)
        s.record(None, "i", "m", True, 100)
        (day, prov), = s._buffer.keys()
        self.assertEqual(prov, "unknown")
        self.assertEqual(s._buffer[(day, prov)]["calls"], 2)

    def test_empty_intent_no_subfield(self):
        s = _make_stats()
        s.record("henan", "", "m", True, 100)
        buf = next(iter(s._buffer.values()))
        self.assertFalse(any("@" in f for f in buf))

    def test_buffer_cap_drops_and_warns(self):
        s = _make_stats()
        import services.llm_stats as mod
        old = mod._BUFFER_MAX_KEYS
        mod._BUFFER_MAX_KEYS = 2
        try:
            s.record("p1", "i", "m", True, 1)
            s.record("p2", "i", "m", True, 1)
            s.record("p3", "i", "m", True, 1)   # 超限丢弃
        finally:
            mod._BUFFER_MAX_KEYS = old
        self.assertEqual(len(s._buffer), 2)


class TestFlushOnce(unittest.TestCase):
    """flush_once：key 命名 / field / TTL / 失败回合并。"""

    def test_flush_writes_hash_and_index_with_ttl(self):
        redis = FakeRedis()
        s = _make_stats(redis)
        s.record("henan", "语音共享", "m", True, 100, prompt_tokens=10, completion_tokens=5)
        s.record("henan", "语音共享", "m", False, 200)
        s.record("beijing", "流量加装", "m", True, 50)
        self.assertTrue(s.flush_once())
        self.assertEqual(s._buffer, {})

        # key 形如 znhs:agent:stats:llm:{yyyymmdd}:henan（日期为 hash tag）
        henan_keys = [k for k in redis.hashes if k.endswith(":henan")]
        self.assertEqual(len(henan_keys), 1)
        key = henan_keys[0]
        self.assertRegex(key, r"^znhs:agent:stats:llm:\{\d{8}\}:henan$")
        h = redis.hashes[key]
        self.assertEqual(h["calls"], 2)
        self.assertEqual(h["success"], 1)
        self.assertEqual(h["fail"], 1)
        self.assertEqual(h["prompt_tokens"], 10)
        self.assertEqual(h["completion_tokens"], 5)
        self.assertEqual(h["elapsed_ms_sum"], 300)
        self.assertEqual(h["calls@语音共享"], 2)
        self.assertEqual(h["fail@语音共享"], 1)
        # 省份索引 + TTL
        day = key.split("{")[1].split("}")[0]
        self.assertEqual(redis.smembers(_provinces_key(day)), {"henan", "beijing"})
        self.assertEqual(redis.expires[key], TTL_SECONDS)
        self.assertEqual(redis.expires[_provinces_key(day)], TTL_SECONDS)

    def test_flush_empty_buffer_is_noop(self):
        redis = FakeRedis()
        s = _make_stats(redis)
        self.assertTrue(s.flush_once())
        self.assertEqual(redis.hashes, {})

    def test_flush_failure_merges_back(self):
        redis = FailingRedis()
        s = _make_stats(redis)
        s.record("henan", "i", "m", True, 100)
        s.record("henan", "i", "m", True, 100)
        self.assertFalse(s.flush_once())
        # 数据合并回缓冲，下轮可重试
        self.assertEqual(len(s._buffer), 1)
        buf = next(iter(s._buffer.values()))
        self.assertEqual(buf["calls"], 2)

    def test_record_during_flush_goes_to_new_buffer(self):
        """swap 语义：flush 处理的是快照，flush 期间的 record 写新缓冲不丢。"""
        redis = FakeRedis()
        s = _make_stats(redis)
        s.record("henan", "i", "m", True, 100)
        # 模拟 swap 后立即 record（等价于 flush 进行中发生 record）
        with s._lock:
            pending, s._buffer = s._buffer, {}
        s.record("beijing", "i", "m", True, 100)
        s._buffer, old = pending, s._buffer   # 还原现场：重新走一次完整 flush
        for k, fields in old.items():
            s._buffer.setdefault(k, {}).update(fields)
        self.assertTrue(s.flush_once())
        total = sum(h.get("calls", 0) for h in redis.hashes.values())
        self.assertEqual(total, 2)


class TestQueryDaily(unittest.TestCase):
    """query_daily / list_provinces：行结构、过滤、日期遍历、异常兜底。"""

    def _seed(self, redis: FakeRedis):
        for day, prov, calls, fail, pt, ct, elapsed in (
            ("20260801", "henan", 10, 2, 1000, 500, 3000),
            ("20260801", "beijing", 5, 0, 400, 200, 1000),
            ("20260803", "henan", 3, 1, 300, 150, 900),
        ):
            key = _hash_key(day, prov)
            redis.hincrby(key, "calls", calls)
            redis.hincrby(key, "success", calls - fail)
            redis.hincrby(key, "fail", fail)
            redis.hincrby(key, "prompt_tokens", pt)
            redis.hincrby(key, "completion_tokens", ct)
            redis.hincrby(key, "elapsed_ms_sum", elapsed)
            redis.hincrby(key, "calls@语音共享", calls)
            redis.hincrby(key, "fail@语音共享", fail)
            redis.sadd(_provinces_key(day), prov)

    def test_rows_structure_and_order(self):
        redis = FakeRedis()
        self._seed(redis)
        s = _make_stats(redis)
        rows = s.query_daily("2026-08-01", "2026-08-03")
        self.assertEqual([(r["date"], r["province"]) for r in rows], [
            ("2026-08-01", "beijing"),
            ("2026-08-01", "henan"),
            ("2026-08-03", "henan"),
        ])
        r = rows[1]
        self.assertEqual(r["calls"], 10)
        self.assertEqual(r["success"], 8)
        self.assertEqual(r["fail"], 2)
        self.assertEqual(r["prompt_tokens"], 1000)
        self.assertEqual(r["completion_tokens"], 500)
        self.assertEqual(r["avg_elapsed_ms"], 300.0)   # 3000/10
        self.assertEqual(r["intents"]["语音共享"]["calls"], 10)
        self.assertEqual(r["intents"]["语音共享"]["fail"], 2)

    def test_province_filter(self):
        redis = FakeRedis()
        self._seed(redis)
        s = _make_stats(redis)
        rows = s.query_daily("2026-08-01", "2026-08-03", province="henan")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["province"] == "henan" for r in rows))

    def test_list_provinces(self):
        redis = FakeRedis()
        self._seed(redis)
        s = _make_stats(redis)
        self.assertEqual(s.list_provinces("2026-08-01", "2026-08-03"),
                         ["beijing", "henan"])
        self.assertEqual(s.list_provinces("2026-08-02", "2026-08-02"), [])

    def test_disabled_returns_empty(self):
        s = LlmStats()
        self.assertEqual(s.query_daily("2026-08-01", "2026-08-03"), [])
        self.assertEqual(s.list_provinces("2026-08-01", "2026-08-03"), [])

    def test_bad_dates_return_empty(self):
        s = _make_stats(FakeRedis())
        self.assertEqual(s.query_daily("bad", "2026-08-03"), [])
        self.assertEqual(s.query_daily("2026-08-03", "2026-08-01"), [])

    def test_redis_error_returns_empty(self):
        class BoomRedis(FakeRedis):
            def smembers(self, key):
                raise ConnectionError("down")

        s = _make_stats(BoomRedis())
        self.assertEqual(s.query_daily("2026-08-01", "2026-08-03"), [])
        self.assertEqual(s.list_provinces("2026-08-01", "2026-08-03"), [])


# ── llm_service 插桩（mock httpx 客户端）────────────────────────

class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


class _FakeHttpClient:
    """模拟共享 httpx.AsyncClient：成功时返回带 usage 的响应。"""

    is_closed = False

    def __init__(self, payload=None, exc: Exception = None):
        self._payload = payload or {
            "choices": [{"message": {"content": "这是测试话术"}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        }
        self._exc = exc

    async def post(self, url, headers=None, data=None, timeout=None):
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


class TestLlmServiceInstrumentation(unittest.IsolatedAsyncioTestCase):
    """generate() 成功/失败分支都会 record（含 usage token）。"""

    def _make_service(self, client, monkey_stats):
        import sys
        from services.llm_service import LLMService
        # services/__init__.py 把单例实例 llm_service 挂到包命名空间，会遮蔽同名子模块，
        # 故模块对象必须从 sys.modules 取（import services.llm_service as x 会拿到实例）
        svc_mod = sys.modules["services.llm_service"]

        svc = LLMService(
            "henan",
            config_override={
                "url": "http://fake-llm/v1/chat/completions",
                "model": "qwen-plus",
                "timeout": 5,
                "max_retries": 1,
            },
        )
        # mock 共享连接池 + 替换统计单例 + 跳过磁盘分省日志
        self._orig_get_client = LLMService._get_client
        self._orig_stats = svc_mod.llm_stats
        self._orig_log = svc._log_province_llm
        LLMService._get_client = classmethod(lambda cls: _async_return(client))
        svc_mod.llm_stats = monkey_stats
        svc._log_province_llm = lambda *a, **k: None
        self.addCleanup(self._restore, LLMService, svc_mod, svc)
        return svc

    def _restore(self, LLMService, svc_mod, svc):
        LLMService._get_client = self._orig_get_client
        svc_mod.llm_stats = self._orig_stats
        svc._log_province_llm = self._orig_log

    async def test_success_records_with_usage_tokens(self):
        stats = _make_stats(FakeRedis())
        svc = self._make_service(_FakeHttpClient(), stats)

        from utils.observability import begin_request_context, reset_request_context
        token = begin_request_context(province="henan", intent="语音共享")
        try:
            out = await svc.generate("写一句河南测试话术", province="henan")
        finally:
            reset_request_context(token)

        self.assertEqual(out, "这是测试话术")
        self.assertEqual(len(stats._buffer), 1)
        (day, prov), buf = next(iter(stats._buffer.items()))
        self.assertEqual(prov, "henan")
        self.assertEqual(buf["calls"], 1)
        self.assertEqual(buf["success"], 1)
        self.assertEqual(buf.get("fail", 0), 0)   # 稀疏计数：无失败时 fail 字段不出现
        self.assertEqual(buf["prompt_tokens"], 120)
        self.assertEqual(buf["completion_tokens"], 30)
        self.assertEqual(buf["calls@语音共享"], 1)

    async def test_final_failure_records_fail(self):
        stats = _make_stats(FakeRedis())
        svc = self._make_service(
            _FakeHttpClient(exc=ConnectionError("llm down")), stats,
        )
        out = await svc.generate("写一句河南测试话术", province="henan")
        self.assertEqual(out, "")
        buf = next(iter(stats._buffer.values()))
        self.assertEqual(buf["calls"], 1)
        self.assertEqual(buf.get("success", 0), 0)   # 稀疏计数：无成功时 success 字段不出现
        self.assertEqual(buf["fail"], 1)
        self.assertEqual(buf["prompt_tokens"], 0)

    async def test_test_province_not_recorded(self):
        stats = _make_stats(FakeRedis())
        svc = self._make_service(_FakeHttpClient(), stats)
        await svc.generate("写一句测试页话术", province="test")
        self.assertEqual(stats._buffer, {})


def _async_return(value):
    """包一层协程，供 mock classmethod _get_client 使用。"""
    async def _coro():
        return value
    return _coro()


if __name__ == "__main__":
    unittest.main()
