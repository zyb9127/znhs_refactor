"""
交叉营销网关回调客户端

营销助手统一接口是**异步**语义：主接口只回「数据接收成功」，话术生成完成后由本模块
把结果写入交叉营销网关的 Redis 缓存（``POST /api/gateway/preload/cache``），下游再用
结果获取接口按 ``preload:{phoneNo}:{callId}:{identifier}`` 取。

配置来源：``config/config.json`` 的 ``cross_sell`` 段，每项可被环境变量覆盖（部署注入
不同网关地址时不必改配置文件）。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from utils.config_loader import config_loader
from utils.marketing_assistant import IDENTIFIER_SCRIPT

_SUCCESS_CODE = "0"


def _cfg(key: str, default: Any = None) -> Any:
    return config_loader.get(f"cross_sell.{key}", default)


def _env_str(name: str, fallback: str) -> str:
    val = str(os.environ.get(name, "") or "").strip()
    return val or fallback


def _env_int(name: str, fallback: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            logger.warning(f"[CrossSellCallback] 环境变量 {name}={raw!r} 非法整数，用配置值 {fallback}")
    return fallback


def is_enabled() -> bool:
    """营销助手统一接口总开关（默认开启，可用环境变量强制关闭）。"""
    raw = str(os.environ.get("ZNHS_CROSS_SELL_ENABLED", "") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return _cfg("enabled", True) is not False


def callback_url() -> str:
    """回调完整地址；基址缺失时返回空串（调用方据此跳过回调并告警）。"""
    base = _env_str("ZNHS_CROSS_SELL_CALLBACK_BASE", str(_cfg("callback_base_url", "") or ""))
    path = _env_str("ZNHS_CROSS_SELL_CALLBACK_PATH", str(_cfg("callback_path", "") or ""))
    base = base.strip().rstrip("/")
    path = path.strip()
    if not base or not path:
        return ""
    return f"{base}{path if path.startswith('/') else '/' + path}"


def default_intent() -> str:
    """省份下没有标记营销助手统一接口的技能包时的兜底意图（留空=不兜底）。"""
    return _env_str("ZNHS_CROSS_SELL_DEFAULT_INTENT", str(_cfg("default_intent", "") or ""))


def _stage_candidates(key: str, fallback: List[str]) -> List[str]:
    """环节名候选：配置可写单个字符串或字符串数组，逐个去空去重。

    各省话术模板的「应用环节」命名不统一（"切入" / "切入环节" / "个人市场"…），
    故按候选列表取第一个在该技能包模板里真实存在的（见
    routers.cross_sell.build_batch_contexts）。
    """
    raw = _cfg(key, None)
    if raw is None or raw == "" or raw == []:
        items = fallback
    elif isinstance(raw, list):
        items = [str(x) for x in raw]
    else:
        items = re.split(r"[,，]", str(raw))
    out: List[str] = []
    for it in items:
        s = str(it).strip()
        if s and s not in out:
            out.append(s)
    return out


def recommend_stage_candidates() -> List[str]:
    """营销推荐话术的环节名候选（回调 words）。

    产品若单独配了「推荐」环节模板，words 取该环节生成结果；否则回退到无环节
    （空 stage）的默认模板。见 routers.cross_sell.build_batch_contexts。
    """
    return _stage_candidates("recommend_stage", ["推荐", "推荐环节", "营销推荐"])


def pitch_stage_candidates() -> List[str]:
    """营销切入话术的环节名候选（回调 aiPitchMarketingDesc）。"""
    return _stage_candidates("pitch_stage", ["切入", "切入环节", "营销切入"])


def retention_stage_candidates() -> List[str]:
    """挽留指引话术的环节名候选（回调 aiRetentionMarketingDesc）。"""
    return _stage_candidates("retention_stage", ["挽留", "挽留环节", "挽留指引"])


def _expire_minutes() -> int:
    return _env_int("ZNHS_CROSS_SELL_EXPIRE_MINUTES", int(_cfg("callback_expire_minutes", 5) or 5))


def _timeout_seconds() -> int:
    return _env_int("ZNHS_CROSS_SELL_TIMEOUT", int(_cfg("callback_timeout_seconds", 5) or 5))


def _max_retries() -> int:
    raw = _cfg("callback_max_retries", 1)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 1
    return max(n, 0)


async def push_cache_detailed(
    *,
    touch_number: str,
    phone: str,
    value: Dict[str, Any],
    identifier: str = IDENTIFIER_SCRIPT,
    expire_minutes: Optional[int] = None,
    trace_id: str = "",
) -> Dict[str, Any]:
    """把话术结果写入交叉营销网关缓存，返回**结构化回调结果**（供测试页展示）。

    ``touch_number`` 即《preload_cache 接口文档》里的回调唯一标识 ``touchNumber``
    （呼叫 ID），与 ``servNumber`` / ``identifier`` 共同拼成网关 Redis key。

    返回字段：
    - ``attempted``  是否真正发起了 HTTP 回调（地址/参数齐全才会发起）
    - ``ok``         网关 ``rtnCode==0`` 视为成功
    - ``url`` / ``key`` / ``identifier`` / ``expire_minutes`` / ``result_count`` / ``bytes``
    - ``http_status`` / ``rtn_code`` / ``rtn_msg``  网关响应
    - ``cost_ms`` / ``attempts``  最后一次尝试的耗时与已尝试次数
    - ``error`` / ``skipped_reason``  失败原因 / 未发起原因

    失败只记录不抛：异步链路里回调失败不应影响本次请求（主接口早已 ack）。
    """
    expire_minutes = expire_minutes if expire_minutes else _expire_minutes()
    key = f"preload:{phone}:{touch_number}:{identifier}"
    scripts_n = len((value or {}).get("result") or []) if isinstance(value, dict) else 0
    result: Dict[str, Any] = {
        "attempted": False, "ok": False, "url": "", "key": key,
        "identifier": identifier, "expire_minutes": expire_minutes,
        "result_count": scripts_n, "bytes": -1, "http_status": None,
        "rtn_code": "", "rtn_msg": "", "cost_ms": 0.0, "attempts": 0,
        "error": "", "skipped_reason": "",
    }

    url = callback_url()
    result["url"] = url
    if not url:
        result["skipped_reason"] = ("回调地址未配置（cross_sell.callback_base_url / "
                                    "ZNHS_CROSS_SELL_CALLBACK_BASE）")
        logger.error(f"[CrossSellCallback] {result['skipped_reason']}，已跳过回调  trace_id={trace_id}")
        return result
    if not touch_number or not phone:
        result["skipped_reason"] = (f"touchNumber/servNumber 缺失，无法拼 Redis key"
                                    f"（touchNumber={touch_number!r} phone={bool(phone)}）")
        logger.error(f"[CrossSellCallback] {result['skipped_reason']}，已跳过回调  trace_id={trace_id}")
        return result

    # 请求体字段名对齐《交叉营销网关-preload_cache 接口文档》：
    #   touchNumber=回调唯一标识/呼叫ID、servNumber=手机号、identifier=hs/tj。
    # 三者共同拼成网关 Redis key：preload:{servNumber}:{touchNumber}:{identifier}。
    body = {
        "touchNumber": touch_number,
        "servNumber": phone,
        "identifier": identifier,
        "value": value,
        "expireMinutes": expire_minutes,
    }
    try:
        result["bytes"] = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    except Exception:
        result["bytes"] = -1
    timeout = _timeout_seconds()
    attempts = _max_retries() + 1

    # 回调发起前先记录目标与规模：即便后续超时/失败，也能定位写到哪个 key、多大报文
    logger.info(
        f"[CrossSellCallback] ▶ 开始回调  url={url} key={key} 回调项={scripts_n} "
        f"expireMinutes={expire_minutes} bytes={result['bytes']} timeout={timeout}s "
        f"maxRetries={attempts - 1} trace_id={trace_id}"
    )

    from services.api_client import get_shared_client

    result["attempted"] = True
    for attempt in range(1, attempts + 1):
        result["attempts"] = attempt
        t0 = time.perf_counter()
        try:
            client = await get_shared_client()
            resp = await client.post(
                url, json=body, timeout=timeout,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            cost = (time.perf_counter() - t0) * 1000
            result["cost_ms"] = round(cost, 1)
            result["http_status"] = resp.status_code
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            data = resp.json() if resp.text else {}
            result["rtn_code"] = str((data or {}).get("rtnCode", "")).strip()
            result["rtn_msg"] = str((data or {}).get("rtnMsg", "") or "")
            if result["rtn_code"] == _SUCCESS_CODE:
                result["ok"] = True
                logger.info(
                    f"[CrossSellCallback] ✅ 回调成功  key={key} 回调项={scripts_n} "
                    f"cost={cost:.0f}ms 第{attempt}/{attempts}次 trace_id={trace_id}"
                )
                return result
            result["error"] = f"网关返回失败 rtnCode={result['rtn_code']!r} rtnMsg={result['rtn_msg']!r}"
            logger.error(
                f"[CrossSellCallback] {result['error']} key={key} cost={cost:.0f}ms trace_id={trace_id}"
            )
            return result       # 业务失败重试无意义
        except Exception as exc:  # noqa: BLE001 - 网络类异常按次重试后放弃
            cost = (time.perf_counter() - t0) * 1000
            result["cost_ms"] = round(cost, 1)
            result["error"] = str(exc)
            if attempt < attempts:
                logger.warning(
                    f"[CrossSellCallback] 回调第 {attempt}/{attempts} 次失败，重试: {exc}  "
                    f"key={key} cost={cost:.0f}ms trace_id={trace_id}"
                )
                continue
            logger.error(
                f"[CrossSellCallback] ❌ 回调失败（已重试 {attempts - 1} 次）: {exc}  "
                f"url={url} key={key} cost={cost:.0f}ms trace_id={trace_id}"
            )
    return result


async def push_cache(
    *,
    touch_number: str,
    phone: str,
    value: Dict[str, Any],
    identifier: str = IDENTIFIER_SCRIPT,
    expire_minutes: Optional[int] = None,
    trace_id: str = "",
) -> bool:
    """把话术结果写入交叉营销网关缓存。成功返回 True（线上异步链路用，只关心成败）。"""
    res = await push_cache_detailed(
        touch_number=touch_number, phone=phone, value=value,
        identifier=identifier, expire_minutes=expire_minutes, trace_id=trace_id,
    )
    return bool(res.get("ok"))
