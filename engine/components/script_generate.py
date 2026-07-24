"""
script_generate 组件 —— 包装 steps.ScriptStep.run_batch 的薄适配器

与 core/pipeline.py 旧路径 Step3 一致：统一走 run_batch；
运行前若 ctx.batch_contexts 为空则注入兜底条目
[{"product_id": "", "stage": "", "scence": ""}]（等价于旧的单条无维度路径）。
兜底注入在 pipeline_runner 中也有同名特判，此处再做一次是防御性冗余
（组件被直接调用或自定义管道未经 runner 特判时仍保持行为一致）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Dict

from loguru import logger

from engine.component import StepComponent
from engine.registry import register_component

if TYPE_CHECKING:  # 仅类型标注用
    from core.context import FlowContext


@register_component
class ScriptGenerateComponent(StepComponent):
    """话术生成组件：对每个 batch_context 条目并发 LLM 生成话术，写入 ctx.marketing_scripts"""

    name: ClassVar[str] = "script_generate"

    # 组件本身无 params；话术配置来自技能包 biz_config（$ref 仅文档用途）
    config_schema: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "description": "无组件级参数。话术模板/策略读取技能包 skill_config.biz_config，结构见 biz_config.schema.json",
        "$ref": "biz_config.schema.json",
    }

    def __init__(self, province: str, intent: str = "default") -> None:
        super().__init__(province, intent)
        # 延迟导入，避免循环依赖（参照 core/pipeline.py StepBundle 的写法）
        from steps.script_step import ScriptStep

        self._step = ScriptStep(province)

    async def run(
        self,
        ctx: "FlowContext",
        skill_config: Dict[str, Any],
        params: Dict[str, Any],
    ) -> None:
        biz_config = (skill_config or {}).get("biz_config", {})
        # 兜底：与 core/pipeline.py L185-186 一致（防御性冗余，见模块 docstring）
        if not ctx.batch_contexts:
            ctx.batch_contexts = [{"product_id": "", "stage": "", "scence": ""}]
            logger.info(
                "[ScriptGenerateComponent] batch_contexts 为空，自动构造兜底条目  "
                f"final_recommendations={len(ctx.final_recommendations)} "
                f"(请求 top_n={ctx.top_n})"
            )
        await self._step.run_batch(ctx, biz_config=biz_config)
