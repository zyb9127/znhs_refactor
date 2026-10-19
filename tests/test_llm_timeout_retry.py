"""LLM 调用的超时分离与重试策略（services.llm_service.generate）。

背景（山东/辽宁生产 2026-09-20）：网关偶发 502（nginx 上游不可用），
排查时发现四处放大失败面的问题：

1. ``client.post(timeout=200)`` 传的是标量，httpx 会把 connect/read/write/pool
   四项全设成 200s —— 网关不可达要干等 200s 才判失败，连接池占满则静默排队 200s；
2. 重试没有总预算，最坏 3×200s+退避 ≈ 603s，而灵运侧 20s 就断开，
   后面的重试没人在等结果，只是占着 worker 给正在故障的网关加压；
3. ``except Exception`` 对 400/401/404 这类必然重复失败的错误也重试三次；
4. 退避是固定的 1s、2s，无抖动 —— 网关一抖，在飞的所有请求整齐重试，
   容易把瞬时 502 顶成持续故障。

本文件守这四条修复，并确保未传 budget_s 的离线链路（接口映射/自动填充）行为不变。
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

import httpx

from services.llm_service import LLMService, _LLMHttpStatusError


class _Resp:
    def __init__(self, status_code: int = 200, content: str = "这是测试话术") -> None:
        self.status_code = status_code
        self._payload = {"choices": [{"message": {"content": content}}]}
        self.text = json.dumps(self._payload, ensure_ascii=False) if status_code == 200 \
            else "<html><body><center><h1>502 Bad Gateway</h1></center></body></html>"

    def json(self) -> dict:
        return self._payload


class _RecordingClient:
    """记录每次 post 的 timeout；按预设脚本决定成功/抛错。"""

    is_closed = False

    def __init__(self, script) -> None:
        self.script = list(script)      # 每项：int 状态码 / Exception 实例
        self.calls: list = []

    async def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append(timeout)
        item = self.script.pop(0) if self.script else 200
        if isinstance(item, BaseException):
            raise item
        return _Resp(item)


def _make_service(client):
    import sys

    svc_mod = sys.modules["services.llm_service"]
    svc = LLMService("shandong", config_override={
        "url": "http://fake-llm/v1/chat/completions",
        "model": "qwen-plus",
        "timeout": 200,
        "max_retries": 3,
    })
    orig_get_client = LLMService._get_client
    orig_stats = svc_mod.llm_stats
    LLMService._get_client = classmethod(lambda cls: _async(client))
    svc_mod.llm_stats = mock.MagicMock()
    svc._log_province_llm = lambda *a, **k: None

    def _restore():
        LLMService._get_client = orig_get_client
        svc_mod.llm_stats = orig_stats

    return svc, _restore


async def _async(v):
    return v


class _Base(unittest.IsolatedAsyncioTestCase):
    def _svc(self, script):
        client = _RecordingClient(script)
        svc, restore = _make_service(client)
        self.addCleanup(restore)
        return svc, client


class TestSeparateTimeouts(_Base):
    """配置里的 timeout 只做读超时，连接/写/池必须是独立的秒级值。"""

    async def test_timeout_is_split_not_scalar(self) -> None:
        svc, client = self._svc([200])
        await svc.generate("写一句话术", province="shandong")

        t = client.calls[0]
        self.assertIsInstance(t, httpx.Timeout, "必须传 httpx.Timeout 而非标量")
        self.assertEqual(t.read, 200, "读超时应沿用配置值（大模型出字慢）")
        self.assertEqual(t.connect, LLMService._DEFAULT_CONNECT_TIMEOUT)
        self.assertEqual(t.write, LLMService._DEFAULT_WRITE_TIMEOUT)
        self.assertEqual(t.pool, LLMService._DEFAULT_POOL_TIMEOUT)
        # 核心回归：连接/池不再等于读超时
        self.assertLess(t.connect, t.read)
        self.assertLess(t.pool, t.read)

    async def test_connect_timeout_configurable(self) -> None:
        svc, client = self._svc([200])
        svc.llm_config["connect_timeout"] = 2.5
        svc.llm_config["pool_timeout"] = 1.5
        await svc.generate("写一句话术", province="shandong")
        self.assertEqual(client.calls[0].connect, 2.5)
        self.assertEqual(client.calls[0].pool, 1.5)

    async def test_read_timeout_capped_by_remaining_budget(self) -> None:
        """预算只剩几秒时，没必要还挂着 200s 的读超时等下去。"""
        svc, client = self._svc([200])
        await svc.generate("写一句话术", province="shandong", budget_s=6)
        self.assertLessEqual(client.calls[0].read, 6)
        self.assertGreater(client.calls[0].read, 0)


class TestRetryClassification(_Base):
    """只对瞬时故障重试；入参/鉴权类错误立即失败。"""

    async def test_502_is_retried_and_can_recover(self) -> None:
        svc, client = self._svc([502, 200])
        out = await svc.generate("写一句话术", province="shandong")
        self.assertEqual(out, "这是测试话术")
        self.assertEqual(len(client.calls), 2, "502 应重试且第二次成功")

    async def test_400_fails_immediately(self) -> None:
        """回归：入参错误重试三次结果一样，只会把失败耗时和网关压力乘以三。"""
        svc, client = self._svc([400, 200, 200])
        out = await svc.generate("写一句话术", province="shandong")
        self.assertEqual(out, "")
        self.assertEqual(len(client.calls), 1, "400 不该重试")

    async def test_auth_errors_fail_immediately(self) -> None:
        for code in (401, 403, 404, 422):
            with self.subTest(code=code):
                svc, client = self._svc([code, 200, 200])
                await svc.generate("写一句话术", province="shandong")
                self.assertEqual(len(client.calls), 1, f"{code} 不该重试")

    async def test_retryable_status_set(self) -> None:
        for code in (429, 500, 502, 503, 504):
            with self.subTest(code=code):
                self.assertTrue(LLMService._is_retryable(_LLMHttpStatusError(code, "x")))
        for code in (400, 401, 403, 404, 422):
            with self.subTest(code=code):
                self.assertFalse(LLMService._is_retryable(_LLMHttpStatusError(code, "x")))

    async def test_network_errors_are_retryable(self) -> None:
        """长连接被对端关掉后复用，属可重试。"""
        for exc in (httpx.ConnectError("refused"),
                    httpx.ReadTimeout("slow"),
                    httpx.RemoteProtocolError("closed")):
            with self.subTest(exc=type(exc).__name__):
                self.assertTrue(LLMService._is_retryable(exc))

    async def test_timeout_then_success(self) -> None:
        svc, client = self._svc([httpx.ReadTimeout("slow"), 200])
        out = await svc.generate("写一句话术", province="shandong")
        self.assertEqual(out, "这是测试话术")
        self.assertEqual(len(client.calls), 2)


class TestBudget(_Base):
    """总预算：只有「剩余时间还够再打一次」才重试。"""

    async def test_no_budget_keeps_legacy_retry_count(self) -> None:
        """不传 budget_s（接口映射/自动填充等离线链路）行为与改动前一致。"""
        svc, client = self._svc([502, 502, 502])
        with mock.patch.object(LLMService, "_wait_before_retry",
                               new=mock.AsyncMock()):
            out = await svc.generate("写一句话术", province="shandong")
        self.assertEqual(out, "")
        self.assertEqual(len(client.calls), 3, "无预算时应跑满 max_retries")

    async def test_slow_failure_skips_retry(self) -> None:
        """首次尝试就烧光预算 → 直接回退，不再无谓重试。"""
        svc, client = self._svc([502, 200, 200])

        real_post = client.post

        async def slow_post(*a, **k):
            # 模拟这次尝试耗时超过预算
            import time as _t
            _t0 = _t.perf_counter()
            while _t.perf_counter() - _t0 < 0.25:
                pass
            return await real_post(*a, **k)

        client.post = slow_post
        out = await svc.generate("写一句话术", province="shandong", budget_s=0.2)
        self.assertEqual(out, "")
        self.assertEqual(len(client.calls), 1, "预算耗尽后不应再发起尝试")

    async def test_fast_failure_still_retries(self) -> None:
        """秒级 502 → 预算充足，应当重试并恢复。"""
        svc, client = self._svc([502, 200])
        out = await svc.generate("写一句话术", province="shandong", budget_s=18)
        self.assertEqual(out, "这是测试话术")
        self.assertEqual(len(client.calls), 2)

    async def test_first_attempt_always_allowed(self) -> None:
        """预算配得过小也必须先打一次，不能一次都不调用。"""
        svc, client = self._svc([200])
        out = await svc.generate("写一句话术", province="shandong", budget_s=0.01)
        self.assertEqual(out, "这是测试话术")
        self.assertEqual(len(client.calls), 1)


class TestBackoffJitter(unittest.IsolatedAsyncioTestCase):
    """退避必须带抖动，否则多副本会整齐重试、把瞬时故障顶成持续故障。"""

    async def test_backoff_is_jittered(self) -> None:
        svc = LLMService("shandong", config_override={"url": "http://x", "timeout": 1})
        waits = []
        with mock.patch("asyncio.sleep", new=mock.AsyncMock(
                side_effect=lambda s: waits.append(s))):
            for _ in range(40):
                await svc._wait_before_retry(1)       # base = 2s
        self.assertTrue(waits, "应当有等待")
        self.assertTrue(all(0 <= w <= 2 for w in waits), f"抖动应落在 [0,2]: {waits}")
        self.assertGreater(len(set(waits)), 1, "固定值说明没有抖动")

    async def test_backoff_capped_by_remaining_budget(self) -> None:
        svc = LLMService("shandong", config_override={"url": "http://x", "timeout": 1})
        waits = []
        with mock.patch("asyncio.sleep", new=mock.AsyncMock(
                side_effect=lambda s: waits.append(s))):
            for _ in range(20):
                await svc._wait_before_retry(3, remaining_s=2.0)
        # 剩余 2s、最小尝试时间 1.5s → 最多只能睡 0.5s
        self.assertTrue(all(w <= 0.5 + 1e-9 for w in waits), f"未受预算约束: {waits}")

    async def test_no_sleep_when_budget_exhausted(self) -> None:
        svc = LLMService("shandong", config_override={"url": "http://x", "timeout": 1})
        sleep = mock.AsyncMock()
        with mock.patch("asyncio.sleep", new=sleep):
            await svc._wait_before_retry(2, remaining_s=0.5)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
