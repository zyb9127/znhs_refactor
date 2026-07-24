"""
engine —— 组件化执行引擎（运行态）

对外导出：
- StepComponent                       组件基类
- register_component / get_component / list_components / invalidate_components  组件注册与实例缓存
- run_pipeline / validate_pipeline_def / DEFAULT_PIPELINE  JSON 管道执行器
- build_index                         话术模板哈希索引构建（engine.template_index）
- select_template / select_template_linear / select_templates_expand /
  fuzzy_match_pid / build_selector_index   模板选择（engine.template_selector，自 ScriptStep 迁移）
- build_prompt / preview_prompt / VAR_LABELS /
  resource_context_prompt_vars / append_prompt_extra_suffix  Prompt 组装（engine.prompt_builder，自 ScriptStep 迁移）

模块加载末尾会尝试导入 engine.components 触发内置组件注册；
template_index / components 由并行任务编写，导入失败仅告警不崩溃。
"""
from loguru import logger

from engine.component import StepComponent
from engine.registry import (
    get_component,
    get_component_class,
    invalidate_components,
    list_components,
    register_component,
)
from engine.pipeline_runner import (
    DEFAULT_PIPELINE,
    run_pipeline,
    validate_pipeline_def,
)

# build_index 由 engine/template_index.py 提供（并行编写），导入失败时提供
# 等价降级实现：返回 None，调用方回退旧线性扫描（与规格 §5 契约一致）
try:
    from engine.template_index import build_index
except ImportError as _exc:
    logger.warning(f"engine.template_index 导入失败，模板索引降级为不可用: {_exc}")

    def build_index(templates_v2, intent):  # type: ignore[misc]
        """降级实现：template_index 模块缺失时恒返回 None（调用方回退旧线性扫描）"""
        return None

# 模板选择（自 steps/script_step.py 迁移，见 engine/template_selector.py）
from engine.template_selector import (
    build_selector_index,
    fuzzy_match_pid,
    select_template,
    select_template_linear,
    select_templates_expand,
)

# Prompt 组装（自 steps/script_step.py 迁移，见 engine/prompt_builder.py）
from engine.prompt_builder import (
    VAR_LABELS,
    append_prompt_extra_suffix,
    build_prompt,
    preview_prompt,
    resource_context_prompt_vars,
)

__all__ = [
    "StepComponent",
    "register_component",
    "get_component",
    "get_component_class",
    "list_components",
    "invalidate_components",
    "run_pipeline",
    "validate_pipeline_def",
    "DEFAULT_PIPELINE",
    "build_index",
    # engine.template_selector
    "select_template",
    "select_template_linear",
    "select_templates_expand",
    "fuzzy_match_pid",
    "build_selector_index",
    # engine.prompt_builder
    "build_prompt",
    "preview_prompt",
    "VAR_LABELS",
    "resource_context_prompt_vars",
    "append_prompt_extra_suffix",
]

# 导入内置组件包，触发 @register_component 注册（该包由并行任务编写）
try:
    import engine.components  # noqa: F401
except ImportError as _exc:
    logger.warning(f"engine.components 导入失败，内置组件未注册: {_exc}")
