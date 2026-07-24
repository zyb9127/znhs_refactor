"""
北京 · 套餐升档 — 省份编排入口

职责：
1. 构建 FlowContext（传入 extra_data，DataStep 自动解析占位符）
2. 调用三步管道
3. 透传原始 bean 数据到 other_info
"""
from __future__ import annotations

from typing import Any, Dict

from core.context import FlowContext
from core.pipeline import MarketingPipeline


async def run_scenario_flow(
    context: Any,               # SkillExecutionContext
    request_data: Dict[str, Any],
) -> Dict[str, Any]:
    """北京套餐升档主流程"""
    ctx = FlowContext(
        phone=request_data.get("phone", ""),
        intent=request_data.get("intent", "套餐升档"),
        province="beijing",
        top_n=int(request_data.get("topN", 3)),
        trace_id=getattr(context, "trace_id", ""),
        extra_data=request_data.get("extra_data") or {},
        extra_info=request_data.get("extra_info") or {},
        extra_context=request_data.get("extra_context") or {},
    )

    skill_config = getattr(context, "package", None)
    skill_config = skill_config.config if skill_config else {}

    result = await MarketingPipeline().execute(ctx, skill_config=skill_config)

    # 透传原始接口响应到 other_info
    first_raw = next(iter(ctx.raw_responses.values()), {})
    result["other_info"] = (
        first_raw.get("bean", first_raw) if isinstance(first_raw, dict) else first_raw
    )
    return result
