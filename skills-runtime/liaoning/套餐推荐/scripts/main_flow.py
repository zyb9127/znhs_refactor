"""
山东 · 套餐推荐 — 省份编排入口

职责：
1. 构建 FlowContext（传入 extra_data，DataStep 自动解析占位符）
2. 调用三步管道
"""
from __future__ import annotations

from typing import Any, Dict

from core.context import FlowContext
from core.pipeline import MarketingPipeline


async def run_scenario_flow(
    context: Any,               # SkillExecutionContext
    request_data: Dict[str, Any],
) -> Dict[str, Any]:
    """山东套餐推荐主流程"""
    pkg = getattr(context, "package", None)
    province_code = pkg.province if pkg else "shandong"

    ctx = FlowContext(
        phone=request_data.get("phone", ""),
        intent=request_data.get("intent", "套餐推荐"),
        province=province_code,
        top_n=int(request_data.get("topN", 3)),
        trace_id=getattr(context, "trace_id", ""),
        extra_data=request_data.get("extra_data") or {},
        extra_info=request_data.get("extra_info") or {},
        extra_context=request_data.get("extra_context") or {},
        batch_contexts=request_data.get("batch_contexts") or [],
    )

    skill_config = getattr(context, "package", None)
    skill_config = skill_config.config if skill_config else {}

    result = await MarketingPipeline().execute(ctx, skill_config=skill_config)

    # 透传用户套餐接口原始 bean（与省份无关，字段名由 api_nodes 配置）
    raw_bean = ctx.raw_responses.get("user_package_api", {})
    result["other_info"] = raw_bean.get("bean", raw_bean) if isinstance(raw_bean, dict) else raw_bean

    return result
