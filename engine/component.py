"""
组件化执行引擎 —— 组件基类

StepComponent 是运行态功能组件的抽象基类：
- 组件无请求级状态，实例按 (name, province, intent) 在 registry 中缓存复用
  （镜像 core/pipeline.py MarketingPipeline.StepBundle 的缓存策略）；
- 组件执行结果统一写入 FlowContext（与现有 Step 约定一致）；
- 组件内部异常向上抛出，由 pipeline_runner 统一捕获处理（容错继续下一步）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, Dict

if TYPE_CHECKING:  # 仅类型标注用，避免运行时不必要的导入
    from core.context import FlowContext


class StepComponent(ABC):
    """运行态功能组件基类。

    组件无请求级状态，实例按 (province, intent) 缓存复用。

    子类约定：
    - 必须设置类属性 ``name``（注册名，如 "data_fetch"），并用
      ``@register_component`` 装饰以登记到组件注册表；
    - ``config_schema`` 为组件 params 的 JSON Schema（供前端渲染表单，
      仅文档/表单用途，运行时不强制校验）；
    - 实现 ``run``，把结果写入 ctx。
    """

    # 注册名，如 "data_fetch"，子类必须覆盖
    name: ClassVar[str] = ""

    # 组件 params 的 JSON Schema（供前端渲染表单）
    config_schema: ClassVar[Dict[str, Any]] = {}

    def __init__(self, province: str, intent: str = "default") -> None:
        """构造组件实例。

        Args:
            province: 省份代码（与技能包目录一致）
            intent:   业务意图，默认 "default"
        """
        self.province = province
        self.intent = intent

    @abstractmethod
    async def run(
        self,
        ctx: "FlowContext",
        skill_config: Dict[str, Any],
        params: Dict[str, Any],
    ) -> None:
        """执行组件。

        结果写入 ctx（与现有 Step 约定一致），异常向上抛由 runner 统一处理。

        Args:
            ctx:          请求级流程上下文
            skill_config: 技能包完整配置 {"api_nodes": {...}, "biz_config": {...}}
            params:       管道定义中该步骤的 params（组件级参数）
        """
        raise NotImplementedError
