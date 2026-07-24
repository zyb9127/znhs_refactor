"""
ApiClient — 异步 HTTP 客户端（替代原 external_api_service.py）

特性：
- 共享 httpx.AsyncClient 连接池（全局单例）
- 支持 GET / POST / 自定义 headers
- 超时配置（连接5s / 读取30s）
- 自动 JSON 序列化/反序列化
- 失败自动重试（最多 max_retries 次）
- 开发模式：mock_mode=True 时返回配置的 mock_response，不发真实请求
- 降级：真实请求因网络/超时全部失败后，若配置了非空 mock_response（通常来自接口文档「出参成功示例」），则返回该数据并标记降级

api_cfg 结构（来自 api_nodes.json）：
  {
    "url":       "http://...",
    "method":    "POST",           # 默认 POST
    "headers":   {"X-Channel-Id": "ngbusi"},
    "timeout":   30,
    "max_retries": 2,
    "mock_mode": false,
    "mock_response": {},
    "request_body_wrapper": "params"   # 可选；若设为 params，POST body 为 {"params": {<映射后的请求字段>}}
 
  }
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, Optional

import httpx
from loguru import logger

from utils.observability import record_stage


# GET 分支内部结果载体，避免将 http.client 的传输层头传给 httpx.Response
class _GetResult:
    __slots__ = ("status_code", "text")

    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text

# ── 读取应用环境（模块级缓存，避免重复 IO）──────────────────────
def _read_app_env() -> str:
    """从 config/config.json 读取 app.environment，失败返回 'production'。"""
    try:
        import json
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parents[1] / "config" / "config.json"
        with open(cfg_path, encoding="utf-8") as f:
            env = json.load(f).get("app", {}).get("environment", "production")
        # 与 utils.env_config 保持一致：清洗误带入的分隔符/引号（如全角 '；' 前缀）
        return env.strip().strip(";；'\"，, ").strip().lower()
    except Exception:
        return "production"

_APP_ENV: str = _read_app_env()

# ══════════════════════════════════════════════════════════════
# 全局连接池（应用级别单例）
# ══════════════════════════════════════════════════════════════

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_shared_client: Optional[httpx.AsyncClient] = None
_client_lock: Optional[asyncio.Lock] = None


def _get_client_lock() -> asyncio.Lock:
    """懒创建 asyncio.Lock（必须在事件循环启动后获取，避免跨 loop 问题）"""
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


async def get_shared_client() -> httpx.AsyncClient:
    """获取（或懒创建）全局共享 AsyncClient。

    双重检查 + asyncio.Lock 保证高并发下只创建一个实例，
    避免多协程同时判断 _shared_client is None 时创建多个连接池。
    """
    global _shared_client
    # 快速路径：已有有效 client，直接返回
    if _shared_client is not None and not _shared_client.is_closed:
        return _shared_client
    # 慢路径：加锁后再次检查（双重检查锁）
    async with _get_client_lock():
        if _shared_client is None or _shared_client.is_closed:
            _shared_client = httpx.AsyncClient(
                timeout=_DEFAULT_TIMEOUT,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
                follow_redirects=True,
            )
    return _shared_client


async def close_shared_client() -> None:
    global _shared_client
    if _shared_client and not _shared_client.is_closed:
        await _shared_client.aclose()
        _shared_client = None


# ══════════════════════════════════════════════════════════════
# ApiClient 类
# ══════════════════════════════════════════════════════════════

class ApiClient:
    """异步 HTTP 客户端（无状态，可直接实例化使用）"""

    async def call(
        self,
        api_cfg: Dict[str, Any],
        params: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """执行一次接口调用

        Args:
            api_cfg:  api_nodes.json 中单个接口的配置项
            params:   经过占位符替换后的请求参数（由 DataStep 生成）

        Returns:
            接口响应（JSON dict），失败返回 None
        """
        url      = api_cfg.get("url", "")
        method   = str(api_cfg.get("method", "POST")).upper()
        headers  = api_cfg.get("headers") or {}
        timeout  = float(api_cfg.get("timeout", 30))
        max_retry = int(api_cfg.get("max_retries", 2))

        # ── mock 模式判断 ──────────────────────────────────────
        # 优先级：
        #   1. config.json app.environment == "development" → 全局 mock（本地开发）
        #   2. 单接口 mock_mode=true（api_nodes.json 显式配置）
        #   3. 环境变量 API_MOCK_MODE=true
        use_mock = (
            _APP_ENV == "development"
            or api_cfg.get("mock_mode")
            or os.getenv("API_MOCK_MODE") == "true"
        )
        if use_mock:
            mock = api_cfg.get("mock_response") or {}
            mock_reason = (
                f"environment={_APP_ENV}" if _APP_ENV == "development"
                else ("api_nodes mock_mode=true" if api_cfg.get("mock_mode") else "env API_MOCK_MODE")
            )
            if not mock:
                logger.warning(
                    f"[ApiClient] ⚠️ mock 模式（{mock_reason}）但 mock_response 为空，"
                    f"接口将返回 {{}}，请在 api_nodes.json 中补充 mock_response。url={url or '(未配置)'}"
                )
            else:
                logger.debug(f"[ApiClient] 🔧 mock 模式（{mock_reason}）: url={url}")
            await asyncio.sleep(0.05)  # 模拟网络延迟
            return mock

        if not url:
            logger.error("[ApiClient] url 为空，跳过调用")
            return None
            
            
        # 部分省侧接口要求 body 为 { "params": { ... 业务字段 } }，用 request_body_wrapper 指定外层键名
        # GET/POST 均需包装（GET 接口同样需要将参数包在 wrapper key 下再拼到 URL 路径）
        body_for_http: Dict[str, Any] = params if isinstance(params, dict) else {}
        wrap_key = api_cfg.get("request_body_wrapper")
        if wrap_key and str(wrap_key).strip():
            body_for_http = {str(wrap_key).strip(): dict(body_for_http)}
            

        t0 = time.perf_counter()
        last_exc: Optional[Exception] = None

        for attempt in range(max_retry + 1):
            try:
                client = await get_shared_client()
                resp = await self._do_request(
                    client, method, url, headers, body_for_http, timeout
                )
                elapsed = (time.perf_counter() - t0) * 1000
                logger.info(
                    f"[ApiClient] ✅ {method} {url} "
                    f"status={resp.status_code} {elapsed:.0f}ms"
                )
                resp.raise_for_status()
                data = resp.json()
                record_stage(
                    stage="api_client.call",
                    elapsed_ms=elapsed,
                    cache_hit=False,
                    provider=url.split("/")[2] if "/" in url else url,
                    degrade_flag=False,
                    status_code=resp.status_code,
                )
                return data

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                logger.warning(
                    f"[ApiClient] ⚠️ 网络错误(attempt {attempt+1}/{max_retry+1}): "
                    f"{url} — {exc}"
                )
                if attempt < max_retry:
                    await asyncio.sleep(0.5 * (attempt + 1))

            except httpx.HTTPStatusError as exc:
                logger.error(
                    f"[ApiClient] ❌ HTTP错误: {url} status={exc.response.status_code}"
                )
                return None

            except Exception as exc:
                logger.error(f"[ApiClient] ❌ 调用异常: {url} — {exc}")
                return None

        mock_fallback = api_cfg.get("mock_response") or {}
        if mock_fallback:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.warning(
                f"[ApiClient] 网络不可用，使用接口文档 mock_response 降级: {url}"
            )
            record_stage(
                stage="api_client.call",
                elapsed_ms=elapsed,
                cache_hit=False,
                provider=url.split("/")[2] if "/" in url else url,
                degrade_flag=True,
                status_code=0,
                mock_fallback=True,
            )
            return mock_fallback

        logger.error(
            f"[ApiClient] ❌ 重试{max_retry}次失败: {url} — {last_exc}"
        )
        return None

    @staticmethod
    async def _do_request(
        client: httpx.AsyncClient,
        method: str,
        url: str,
        headers: Dict[str, str],
        params: Dict[str, Any],
        timeout: float,
    ) -> httpx.Response:
        merged_headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Accept":       "application/json",
            **headers,
        }
        if method == "GET":
            import json as _json
            import requests as _requests

            # GET 接口将参数 JSON 直接拼接到 URL 路径末尾
            # 格式：http://.../serviceName/{"params":{"key":"value"}}
            # 使用 requests 发送，行为与测试脚本完全一致：
            #   - 不对路径中的 { " } 做 percent-encode
            #   - 自动跟随 3xx 重定向（对应 curl --location）
            json_body = _json.dumps(params, ensure_ascii=False)
            url_with_body = url.rstrip("/") + "/" + json_body

            req_headers = {k: v for k, v in merged_headers.items()}

            def _sync_get() -> "_GetResult":
                r = _requests.get(url_with_body, headers=req_headers, timeout=timeout)
                return _GetResult(status_code=r.status_code, text=r.text)

            result = await asyncio.to_thread(_sync_get)
            # 构造一个最简 httpx.Response，content 已是合法 UTF-8 JSON 文本
            # 不携带任何传输层头，httpx 不会做二次解码
            mock_req = httpx.Request("GET", "http://placeholder", headers=req_headers)
            return httpx.Response(
                status_code=result.status_code,
                content=result.text.encode("utf-8"),
                headers={"content-type": "application/json; charset=utf-8"},
                request=mock_req,
            )

        return await client.post(
            url, json=params, headers=merged_headers, timeout=timeout
        )


# 全局单例
api_client = ApiClient()
