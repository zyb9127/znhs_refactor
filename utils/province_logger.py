"""
分省日志（按省份目录落盘，便于分省排查与统计）

目录结构：
  logs/provinces/<省份>/request_YYYY-MM-DD.jsonl    # 每次请求入参（1 请求 1 行）
  logs/provinces/<省份>/response_YYYY-MM-DD.jsonl   # 每次请求出参 + 关键统计（模型返回话术）
  logs/provinces/<省份>/llm_YYYY-MM-DD.jsonl        # 每次大模型调用的 prompt / 原始输出（细粒度）
  logs/provinces/<省份>/api_YYYY-MM-DD.jsonl        # 接口查询模式下调用下游接口的请求参数/响应结果

设计要点：
- 与主 loguru 日志相互独立：这里直接写 JSONL 文件，不干扰 utils/logger.py 的既有 sink。
- 每行一条 JSON，含 trace_id，可按 trace_id 串联「入参 → 大模型调用 → 出参」。
- 线程安全（进程内 Lock）；文件名内嵌日期实现按天切分；按 _RETENTION_DAYS 清理过期文件。
- 手机号脱敏；超长文本（prompt/output）按 _MAX_TEXT 截断，避免日志膨胀。
- 开关：环境变量 ZNHS_PROVINCE_LOG=0 可关闭（默认开）。任何异常都被吞掉，绝不影响主流程。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

_LOG_ROOT = Path(__file__).resolve().parents[1] / "logs" / "provinces"
_LOCK = threading.Lock()
_RETENTION_DAYS = 30
_MAX_TEXT = 20000            # 单字段文本上限（prompt/output），超出截断
_ENABLED = os.environ.get("ZNHS_PROVINCE_LOG", "1").strip() not in ("0", "false", "False")

# 已执行过当日清理的省份目录，避免每次写入都扫描
_cleaned: set = set()


def _safe(name: Any, default: str = "unknown") -> str:
    """把 province/intent 规整为安全的目录/文件名片段。"""
    s = str(name or "").strip()
    if not s:
        return default
    for ch in '/\\:*?"<>|':
        s = s.replace(ch, "_")
    return s[:80]


def _day() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def mask_phone(phone: Any) -> str:
    p = str(phone or "")
    return f"{p[:3]}****{p[-4:]}" if len(p) >= 7 else p


def _truncate(val: Any) -> Any:
    if isinstance(val, str) and len(val) > _MAX_TEXT:
        return val[:_MAX_TEXT] + f"...[truncated {len(val) - _MAX_TEXT} chars]"
    return val


def _truncate_obj(obj: Any) -> Any:
    """结构化对象（dict/list）体积守护：序列化超过 _MAX_TEXT 时降级为截断字符串，
    避免超大接口响应（如 other_info）撑爆日志；标量/短对象原样返回。"""
    if obj is None or isinstance(obj, (int, float, bool)):
        return obj
    if isinstance(obj, str):
        return _truncate(obj)
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return _truncate(str(obj))
    if len(s) > _MAX_TEXT:
        return s[:_MAX_TEXT] + f"...[truncated {len(s) - _MAX_TEXT} chars]"
    return obj


def _mask_phone_in_obj(obj: Any, phone: str) -> Any:
    """递归把对象中与真实手机号完全相等的值替换为脱敏形态（请求参数常内嵌手机号）。"""
    if not phone or len(str(phone)) < 7:
        return obj
    masked = mask_phone(phone)
    p = str(phone)

    def _walk(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: _walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [_walk(v) for v in x]
        if isinstance(x, str) and x == p:
            return masked
        return x

    try:
        return _walk(obj)
    except Exception:
        return obj


def _prov_dir(province: str) -> Path:
    d = _LOG_ROOT / _safe(province)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cleanup(prov_dir: Path) -> None:
    """删除超过保留天数的分省日志文件（每省每天只扫一次）。"""
    key = f"{prov_dir.name}:{_day()}"
    if key in _cleaned:
        return
    _cleaned.add(key)
    try:
        import time
        cutoff = time.time() - _RETENTION_DAYS * 86400
        for f in prov_dir.glob("*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
    except Exception:
        pass


def _write(province: str, filename: str, record: Dict[str, Any]) -> None:
    if not _ENABLED:
        return
    try:
        prov_dir = _prov_dir(province)
        _cleanup(prov_dir)
        line = json.dumps(record, ensure_ascii=False, default=str)
        path = prov_dir / filename
        with _LOCK:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception as e:  # 日志失败绝不影响主流程
        logger.warning(f"[province_logger] 写入失败 province={province} file={filename}: {e}")


# ── 对外 API ────────────────────────────────────────────────────

def log_request(
    province: str,
    intent: str,
    trace_id: str,
    phone: str,
    request_body: Dict[str, Any],
) -> None:
    """记录一次推荐请求的完整入参（脱敏手机号）。"""
    body = dict(request_body or {})
    if "phone" in body:
        body = {**body, "phone": mask_phone(body.get("phone"))}
    _write(province, f"request_{_day()}.jsonl", {
        "ts": _now(),
        "trace_id": trace_id,
        "province": province,
        "intent": intent,
        "phone": mask_phone(phone),
        "request": body,
    })


def log_response(
    province: str,
    intent: str,
    trace_id: str,
    phone: str,
    *,
    code: int,
    elapsed_ms: float,
    recommend_results: Optional[list] = None,
    other_info: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    """记录一次推荐请求的出参（模型返回话术）+ 关键统计。"""
    meta = metadata or {}
    results = recommend_results or []
    _write(province, f"response_{_day()}.jsonl", {
        "ts": _now(),
        "trace_id": trace_id,
        "province": province,
        "intent": intent,
        "phone": mask_phone(phone),
        "code": code,
        "elapsed_ms": round(float(elapsed_ms), 1),
        # 关键统计，便于聚合分析
        "script_count": len(results),
        "recommendation_count": meta.get("recommendation_count"),
        "degraded": meta.get("degraded"),
        "slowest_stage": (meta.get("slowest_stage") or {}).get("stage")
            if isinstance(meta.get("slowest_stage"), dict) else None,
        "degrade_flags": meta.get("degrade_flags"),
        "error": error,
        # 模型返回话术明细
        "recommend_results": results,
        "has_other_info": other_info not in (None, {}, []),
    })


def log_api_call(
    province: str,
    intent: str,
    trace_id: str,
    phone: str,
    *,
    api_name: str,
    url: str = "",
    method: str = "",
    request: Any = None,
    response: Any = None,
    elapsed_ms: float = 0.0,
    timeout_s: Optional[float] = None,
    error: Optional[str] = None,
) -> None:
    """记录接口查询模式下一次下游接口调用的请求参数与响应结果（分省）。

    - request/response 均做体积守护；request 内嵌的真实手机号做脱敏；
    - error 非空即视为失败（超时/网络异常/上游报错），success=False。
    """
    _write(province, f"api_{_day()}.jsonl", {
        "ts": _now(),
        "trace_id": trace_id,
        "province": province,
        "intent": intent,
        "phone": mask_phone(phone),
        "api_name": api_name,
        "url": url,
        "method": method,
        "timeout_s": timeout_s,
        "elapsed_ms": round(float(elapsed_ms), 1),
        "success": error is None,
        "error": error,
        "request": _truncate_obj(_mask_phone_in_obj(request, phone)),
        "response": _truncate_obj(response),
    })


def log_llm(
    province: str,
    intent: str,
    trace_id: str,
    *,
    stage: str,
    prompt: str,
    output: str,
    elapsed_ms: float,
    model: str = "",
    success: bool = True,
    error: Optional[str] = None,
) -> None:
    """记录一次大模型调用的 prompt / 原始输出（细粒度排查）。"""
    _write(province, f"llm_{_day()}.jsonl", {
        "ts": _now(),
        "trace_id": trace_id,
        "province": province,
        "intent": intent,
        "stage": stage,
        "model": model,
        "success": success,
        "elapsed_ms": round(float(elapsed_ms), 1),
        "prompt_len": len(prompt or ""),
        "output_len": len(output or ""),
        "prompt": _truncate(prompt),
        "output": _truncate(output),
        "error": error,
    })
