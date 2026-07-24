"""
请求级可观测性上下文

基于 Python contextvars.ContextVar 实现（asyncio 安全），
每个请求协程独立持有一份上下文，互不污染。

核心能力：
- trace_id 链路透传：任意层级调用 get_trace_id()，无需层层传参
- 各 Stage 耗时 / 缓存命中 / 降级打标：record_stage()
- 请求结束汇总：summarize_request_context() → 挂到响应 metadata
"""
import time
import uuid
from contextvars import ContextVar, Token
from typing import Any, Dict, List, Optional

_request_context: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "request_observability_context",
    default=None,
)


def _ensure_context() -> Dict[str, Any]:
    ctx = _request_context.get()
    if ctx is None:
        ctx = {
            "trace_id":    "",
            "route":       "unknown",
            "province":    "unknown",
            "intent":      "",
            "strategy":    "",
            "stage_events": [],
            "cache_hits":  {},
            "degrade_flags": [],
        }
        _request_context.set(ctx)
    return ctx


def begin_request_context(
    trace_id: Optional[str] = None,
    route: str = "recommend",      # 原为 "langgraph"，框架已移除，改为实际路由名
    province: str = "unknown",
    intent: str = "",
    strategy: str = "",
) -> Token:
    """请求开始时创建独立上下文，返回 Token 用于结束时还原。

    使用方式（main.py recommend 路由）：
        token = begin_request_context(trace_id=..., route="recommend", ...)
        try:
            ...
        finally:
            reset_request_context(token)
    """
    ctx = {
        "trace_id":    trace_id or uuid.uuid4().hex[:16],
        "route":       route,
        "province":    province,
        "intent":      intent,
        "strategy":    strategy,
        "stage_events": [],
        "cache_hits":  {},
        "degrade_flags": [],
    }
    return _request_context.set(ctx)


def reset_request_context(token: Token) -> None:
    """请求结束时还原上下文（必须调用，防止协程复用时上下文泄漏）"""
    _request_context.reset(token)


def get_request_context() -> Dict[str, Any]:
    """获取当前请求上下文快照"""
    return dict(_ensure_context())


def get_trace_id() -> str:
    """在任意层级取出当前请求的 trace_id"""
    return _ensure_context().get("trace_id", "")


def mask_phone(phone_number: str) -> str:
    """手机号脱敏：183****1234"""
    if not phone_number or len(phone_number) < 7:
        return phone_number
    return f"{phone_number[:3]}****{phone_number[-4:]}"


def add_degrade_flag(flag: str) -> None:
    """记录降级标记（LLM 失败、API 降级等）"""
    ctx = _ensure_context()
    degrade_flags = ctx.setdefault("degrade_flags", [])
    if flag not in degrade_flags:
        degrade_flags.append(flag)
        # dict 是可变对象，ContextVar 保存的是引用，原地修改已生效，无需重复 set


def set_cache_hit(stage: str, cache_hit: bool) -> None:
    """记录指定 stage 的缓存命中结果"""
    _ensure_context().setdefault("cache_hits", {})[stage] = cache_hit


def record_stage(
    stage: str,
    elapsed_ms: float,
    cache_hit: Optional[bool] = None,
    provider: Optional[str] = None,
    degrade_flag: bool = False,
    **extra: Any,
) -> Dict[str, Any]:
    """记录一个 Stage 的执行事件（耗时、缓存、降级、扩展字段）

    各 Step / 服务调用完成后调用，结果累积到 stage_events，
    最终通过 summarize_request_context() 挂到响应 metadata。
    """
    ctx = _ensure_context()
    event: Dict[str, Any] = {
        "trace_id":    ctx.get("trace_id", ""),
        "stage":       stage,
        "elapsed_ms":  round(float(elapsed_ms), 2),
        "cache_hit":   cache_hit,
        "provider":    provider or "",
        "degrade_flag": degrade_flag,
        "province":    ctx.get("province", "unknown"),
        "intent":      ctx.get("intent", ""),
        "strategy":    ctx.get("strategy", ""),
        "route":       ctx.get("route", "unknown"),
    }
    event.update({k: v for k, v in extra.items() if v is not None})

    # 写入 stage_events（dict 是引用，原地 append 无需重复 set ctx）
    ctx.setdefault("stage_events", []).append(event)

    if cache_hit is not None:
        ctx.setdefault("cache_hits", {})[stage] = cache_hit

    if degrade_flag:
        # 原地修改 degrade_flags，add_degrade_flag 内部已做去重，无需重复 set ctx
        add_degrade_flag(stage)

    return event


def elapsed_ms(start_time: float) -> float:
    """计算从 start_time（perf_counter）到现在的毫秒数"""
    return (time.perf_counter() - start_time) * 1000


def summarize_request_context() -> Dict[str, Any]:
    """汇总当前请求的所有可观测数据，供响应 metadata 使用。

    返回：
        trace_id / route / province / intent / strategy
        stage_events  — 各阶段耗时列表
        cache_hits    — 各阶段缓存命中情况
        degrade_flags — 发生降级的 stage 列表
        slowest_stage — 耗时最长的 stage
        degraded      — 是否有降级发生
    """
    ctx = _ensure_context()
    stage_events: List[Dict[str, Any]] = list(ctx.get("stage_events", []))
    slowest_stage = (
        max(stage_events, key=lambda e: e.get("elapsed_ms", 0))
        if stage_events else None
    )
    return {
        "trace_id":    ctx.get("trace_id", ""),
        "route":       ctx.get("route", "unknown"),
        "province":    ctx.get("province", "unknown"),
        "intent":      ctx.get("intent", ""),
        "strategy":    ctx.get("strategy", ""),
        "stage_events": stage_events,
        "cache_hits":  dict(ctx.get("cache_hits", {})),
        "degrade_flags": list(ctx.get("degrade_flags", [])),
        "slowest_stage": slowest_stage,
        "degraded":    bool(ctx.get("degrade_flags")),
    }
