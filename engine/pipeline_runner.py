"""
组件化执行引擎 —— JSON 定义的管道执行器

管道定义放在 biz_config["pipeline"]（可选 key，复用现有 ES/Redis 存储通道），结构：

    {
      "steps": [
        {"component": "data_fetch", "params": {}},
        {"component": "recommend", "params": {},
         "when": {"ctx_field": "recommended_packages", "op": "nonempty"}},
        {"component": "script_generate", "params": {}}
      ]
    }

执行语义（与旧 MarketingPipeline.execute 三步等价）：
- 逐步串行执行，先查 when 条件（op 仅支持 nonempty/empty/always），不满足则跳过；
- 组件实例经 registry.get_component(name, ctx.province, ctx.intent) 缓存复用；
- 每步 try/except：异常 ctx.add_error(f"{name}异常: {exc}") 后继续下一步
  （与旧 pipeline 容错一致，不中断整体流程）；
- 每步 record_stage(stage=f"component.{name}", ...)，纳入请求级 stage_events；
- script_generate 组件运行前，若 ctx.batch_contexts 为空则兜底注入
  [{"product_id": "", "stage": "", "scence": ""}]（与 core/pipeline.py L185-186 一致）。
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Dict, List

from loguru import logger

from engine.registry import COMPONENT_CLASSES, get_component
from utils.observability import elapsed_ms, record_stage

if TYPE_CHECKING:  # 仅类型标注用
    from core.context import FlowContext


# when 条件支持的操作符
_WHEN_OPS = ("nonempty", "empty", "always")

# 与旧三步管道等价的默认管道定义（供文档/测试对照）
DEFAULT_PIPELINE: Dict[str, Any] = {
    "steps": [
        {"component": "data_fetch", "params": {}},
        {
            "component": "recommend",
            "params": {},
            "when": {"ctx_field": "recommended_packages", "op": "nonempty"},
        },
        {"component": "script_generate", "params": {}},
    ]
}


def _check_when(ctx: "FlowContext", when: Any) -> bool:
    """判断 when 条件是否满足。

    when 结构：{"ctx_field": "<FlowContext 字段名>", "op": "nonempty"|"empty"|"always"}
    缺省（None/空 dict）视为 always。未知 op 不跳过（validate_pipeline_def 已提前拦截）。
    """
    if not when:
        return True
    op = str(when.get("op", "always")).strip().lower()
    if op == "always":
        return True
    field = str(when.get("ctx_field", "") or "")
    value = getattr(ctx, field, None)
    if op == "nonempty":
        return bool(value)
    if op == "empty":
        return not bool(value)
    logger.warning(f"[PipelineRunner] 未知 when.op={op}，条件视为满足不跳过")
    return True


def validate_pipeline_def(pipeline_def: Any) -> List[str]:
    """校验管道定义结构，返回错误列表（空列表表示合法）。

    检查项：整体结构 / steps 列表 / 组件名已注册 / params 类型 / when 结构与 op 取值。
    """
    errors: List[str] = []
    if not isinstance(pipeline_def, dict):
        errors.append("pipeline 定义必须是 object")
        return errors

    steps = pipeline_def.get("steps")
    if not isinstance(steps, list):
        errors.append("pipeline.steps 必须是数组")
        return errors
    if not steps:
        errors.append("pipeline.steps 不能为空")
        return errors

    for idx, step in enumerate(steps):
        path = f"steps[{idx}]"
        if not isinstance(step, dict):
            errors.append(f"{path} 必须是 object")
            continue

        name = step.get("component")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{path}.component 必须是非空字符串")
        elif name not in COMPONENT_CLASSES:
            errors.append(f"{path}.component 未知组件名: {name}")

        params = step.get("params")
        if params is not None and not isinstance(params, dict):
            errors.append(f"{path}.params 必须是 object")

        when = step.get("when")
        if when is not None:
            if not isinstance(when, dict):
                errors.append(f"{path}.when 必须是 object")
            else:
                op = str(when.get("op", "always")).strip().lower()
                if op not in _WHEN_OPS:
                    errors.append(
                        f"{path}.when.op 非法值: {op}（仅支持 {'/'.join(_WHEN_OPS)}）"
                    )
                if op != "always" and not str(when.get("ctx_field", "") or "").strip():
                    errors.append(f"{path}.when.ctx_field 必须是非空字符串")
    return errors


async def run_pipeline(
    ctx: "FlowContext",
    skill_config: Dict[str, Any],
    pipeline_def: Dict[str, Any],
) -> None:
    """按 JSON 管道定义逐步执行组件（结果写入 ctx，不返回值）。

    Args:
        ctx:          FlowContext 请求上下文
        skill_config: 技能包完整配置 {"api_nodes": {...}, "biz_config": {...}}
        pipeline_def: biz_config["pipeline"] 的管道定义（调用方应先过 validate_pipeline_def）
    """
    skill_config = skill_config or {}
    steps = (pipeline_def or {}).get("steps") or []

    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            ctx.add_error(f"pipeline.steps[{idx}]异常: 步骤定义不是 object")
            continue

        name = str(step.get("component", "") or "")
        params = step.get("params") or {}
        when = step.get("when")

        # when 条件不满足 → 跳过该步骤
        if not _check_when(ctx, when):
            logger.debug(
                f"[PipelineRunner] 跳过组件 component={name} idx={idx} "
                f"when={when} trace_id={ctx.trace_id}"
            )
            record_stage(
                stage=f"component.{name}",
                elapsed_ms=0.0,
                provider="component",
                degrade_flag=False,
                skipped=True,
            )
            continue

        # script_generate 运行前兜底：batch_contexts 为空注入空条目
        # （与 core/pipeline.py L185-186 的旧路径行为一致）
        if name == "script_generate" and not ctx.batch_contexts:
            ctx.batch_contexts = [{"product_id": "", "stage": "", "scence": ""}]
            logger.info(
                f"[PipelineRunner] batch_contexts 为空，自动构造兜底条目  "
                f"final_recommendations={len(ctx.final_recommendations)} "
                f"(请求 top_n={ctx.top_n})"
            )

        wall_start = time.perf_counter()
        failed = False
        try:
            component = get_component(name, ctx.province, ctx.intent)
            logger.info(
                f"[PipelineRunner] 执行组件 component={name} idx={idx} "
                f"trace_id={ctx.trace_id}"
            )
            await component.run(ctx, skill_config, params)
        except Exception as exc:  # 容错：单步异常不中断整体（与旧 pipeline 一致）
            failed = True
            logger.error(f"[PipelineRunner] 组件执行异常 component={name}: {exc}")
            ctx.add_error(f"{name}异常: {exc}")
        finally:
            # 参照 core/pipeline.py L204-213 的 record_stage 调用方式
            record_stage(
                stage=f"component.{name}",
                elapsed_ms=elapsed_ms(wall_start),
                provider="component",
                degrade_flag=failed,
                errors=len(ctx.errors),
            )
