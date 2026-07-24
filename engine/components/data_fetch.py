"""
data_fetch 组件 —— 包装 steps.DataStep 的薄适配器

行为与 core/pipeline.py 旧路径 Step1 完全一致：
    await data_step.run(ctx, skill_config.get("api_nodes", {}))
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Dict

from engine.component import StepComponent
from engine.registry import register_component

if TYPE_CHECKING:  # 仅类型标注用
    from core.context import FlowContext


@register_component
class DataFetchComponent(StepComponent):
    """数据采集组件：并发调用技能包 api_nodes 中启用的外部接口，映射 resource_context 写入 ctx"""

    name: ClassVar[str] = "data_fetch"

    # 组件本身无 params；接口节点配置来自技能包 api_nodes（$ref 仅文档用途，供前端跳转 schema）
    config_schema: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "description": "无组件级参数。接口节点配置读取技能包 skill_config.api_nodes，结构见 api_nodes.schema.json",
        "$ref": "api_nodes.schema.json",
    }

    def __init__(self, province: str, intent: str = "default") -> None:
        super().__init__(province, intent)
        # 延迟导入，避免循环依赖（参照 core/pipeline.py StepBundle 的写法）
        from steps.data_step import DataStep

        self._step = DataStep(province)

    async def run(
        self,
        ctx: "FlowContext",
        skill_config: Dict[str, Any],
        params: Dict[str, Any],
    ) -> None:
        api_nodes = (skill_config or {}).get("api_nodes", {})
        await self._step.run(ctx, api_nodes)
