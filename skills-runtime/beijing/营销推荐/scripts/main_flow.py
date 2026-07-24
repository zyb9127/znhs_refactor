"""
北京 · 套餐推荐 — 省份编排入口

职责：
1. 构建 FlowContext（传入 extra_data，DataStep 自动解析占位符）
2. 调用三步管道
3. 北京特定：将原始 bean 数据透传到 other_info
"""
from __future__ import annotations

from typing import Any, Dict

from core.context import FlowContext
from core.pipeline import MarketingPipeline


def _primary_api_raw(raw_responses: Dict[str, Any]) -> Any:
    """与 api_nodes 中节点名无关：优先 user_package_api，否则取唯一/首个启用接口的原始响应。"""
    if not raw_responses:
        return {}
    preferred = raw_responses.get("user_package_api")
    if preferred is not None:
        return preferred
    return next(iter(raw_responses.values()), {})


async def run_scenario_flow(
    context: Any,               # SkillExecutionContext
    request_data: Dict[str, Any],
) -> Dict[str, Any]:
    """北京套餐推荐主流程"""
    ctx = FlowContext(
        phone=request_data.get("phone", ""),
        intent=request_data.get("intent", "套餐推荐"),
        province="beijing",
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

    # 北京特定：透传原始 bean（键名随 api_nodes 节点名变化，不能写死 user_package_api）
    raw_bean = _primary_api_raw(ctx.raw_responses)
    result["other_info"] = raw_bean.get("bean", raw_bean) if isinstance(raw_bean, dict) else raw_bean

    return result
