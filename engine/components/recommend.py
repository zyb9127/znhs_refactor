"""
recommend 组件 —— 包装 steps.RecommendStep 的薄适配器

策略取值顺序（与 core/pipeline.py 旧路径 Step2 一致）：
    params.strategy > biz_config.strategy.default_strategy > "direct"

when 跳过语义由 pipeline_runner 处理（DEFAULT_PIPELINE 中本组件配
when: recommended_packages nonempty，与旧路径"无推荐候选跳过 Step2"等价）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Dict

from engine.component import StepComponent
from engine.registry import register_component

if TYPE_CHECKING:  # 仅类型标注用
    from core.context import FlowContext


@register_component
class RecommendComponent(StepComponent):
    """推荐筛选组件：按策略从 recommended_packages 筛选 TopN，写入 ctx.final_recommendations"""

    name: ClassVar[str] = "recommend"

    config_schema: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {
            "strategy": {
                "type": "string",
                "description": "推荐策略；缺省读 biz_config.strategy.default_strategy，最终兜底 direct",
            }
        },
    }

    def __init__(self, province: str, intent: str = "default") -> None:
        super().__init__(province, intent)
        # 延迟导入，避免循环依赖（参照 core/pipeline.py StepBundle 的写法）
        from steps.recommend_step import RecommendStep

        self._step = RecommendStep(province)

    async def run(
        self,
        ctx: "FlowContext",
        skill_config: Dict[str, Any],
        params: Dict[str, Any],
    ) -> None:
        params = params or {}
        biz_config = (skill_config or {}).get("biz_config") or {}
        strategy = (
            params.get("strategy")
            or (biz_config.get("strategy") or {}).get("default_strategy")
            or "direct"
        )
        await self._step.run(ctx, strategy=strategy)
