"""
MarketingPipeline — 三步线性管道

替代原 LangGraph orchestrator，无框架依赖，纯 Python async。

执行顺序（串行，保证数据依赖）：
  Step1: DataStep    — 并发调外部接口，映射 resource_context
  Step2: RecommendStep — 按策略筛选 TopN 推荐产品
  Step3: ScriptStep  — 并发 LLM 生成个性化话术

省份+意图级 Bundle 缓存：同一省份+意图的三个 Step 实例只创建一次，后续请求复用。

【修复记录】
- bundle key 由 province 改为 province:intent，防止同省不同意图共用同一 data_step
  导致 builder/配置相互污染
- execute() 不再自己调 begin_request_context / reset_request_context，
  避免与 main.py 层的 obs 上下文双层嵌套覆盖导致 stage_events 丢失
"""
from __future__ import annotations

import time
import uuid
from threading import Lock
from typing import Any, ClassVar, Dict, Optional

from loguru import logger

from core.context import FlowContext
from utils.observability import elapsed_ms, record_stage, summarize_request_context


# ══════════════════════════════════════════════════════════════
# 省份+意图级 Bundle（缓存 Step 实例）
# ══════════════════════════════════════════════════════════════

class StepBundle:
    """省份+意图级 Step 实例容器，避免重复初始化"""

    def __init__(self, province: str, intent: str = "default") -> None:
        # 延迟导入，避免循环依赖
        from steps.data_step import DataStep
        from steps.recommend_step import RecommendStep
        from steps.script_step import ScriptStep

        self.province = province
        self.intent   = intent
        self.data_step      = DataStep(province)
        self.recommend_step = RecommendStep(province)
        self.script_step    = ScriptStep(province)
        logger.info(f"📦 StepBundle 已创建: province={province} intent={intent}")


# ══════════════════════════════════════════════════════════════
# 主管道
# ══════════════════════════════════════════════════════════════

class MarketingPipeline:
    """营销推荐三步管道（无 LangGraph 依赖）

    用法（技能包 main_flow.py 中）：
        pipeline = MarketingPipeline()
        result = await pipeline.execute(ctx, skill_config=context.package.config)

    用法（直接调用，无技能包时）：
        ctx = FlowContext(phone=..., intent=..., province=...)
        result = await MarketingPipeline().execute(ctx)
    """

    # 省份+意图级 Bundle 缓存（类级别，跨请求复用）
    # key = "province:intent"
    _bundles: ClassVar[Dict[str, StepBundle]] = {}
    _lock:    ClassVar[Lock] = Lock()

    # ── Bundle 管理 ───────────────────────────────────────────────

    @classmethod
    def get_bundle(cls, province: str, intent: str = "default") -> StepBundle:
        """获取（或懒创建）省份+意图 Bundle"""
        key = f"{province}:{intent}"
        bundle = cls._bundles.get(key)
        if bundle is None:
            with cls._lock:
                if key not in cls._bundles:
                    cls._bundles[key] = StepBundle(province, intent)
                bundle = cls._bundles[key]
        return bundle

    @classmethod
    def invalidate_bundle(
        cls,
        province: Optional[str] = None,
        intent: Optional[str] = None,
    ) -> None:
        """使 Bundle 缓存失效（配置热更新时调用）

        - invalidate_bundle(province, intent) → 只清除指定 bundle
        - invalidate_bundle(province)         → 清除该省份所有 intent 的 bundle
        - invalidate_bundle()                 → 清除全部 bundle
        """
        with cls._lock:
            if province and intent:
                key = f"{province}:{intent}"
                cls._bundles.pop(key, None)
                logger.info(f"♻️ Bundle 缓存已失效: {key}")
            elif province:
                prefix = f"{province}:"
                stale = [k for k in cls._bundles if k.startswith(prefix)]
                for k in stale:
                    cls._bundles.pop(k, None)
                logger.info(f"♻️ Bundle 缓存已失效（省份全量）: province={province} 共{len(stale)}个")
            else:
                cls._bundles.clear()
                logger.info("♻️ 全部 Bundle 缓存已失效")

    # ── 主执行入口 ────────────────────────────────────────────────

    async def execute(
        self,
        ctx: FlowContext,
        skill_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """执行三步管道

        Args:
            ctx:          FlowContext，包含 phone/intent/province/extra_vars 等入参
            skill_config: 技能包配置，结构为
                          {"api_nodes": {...}, "biz_config": {...}}
                          若为 None 则从全局 config 中读取默认配置

        Returns:
            ctx.to_result() 标准响应 dict

        注意：可观测性上下文（begin_request_context）由调用方（main.py 或 SkillExecutor）
        统一管理，Pipeline 本身不再创建/重置 obs 上下文，避免双层覆盖导致 stage_events 丢失。
        """
        skill_config = skill_config or {}
        api_nodes   = skill_config.get("api_nodes", {})
        biz_config  = skill_config.get("biz_config", {})

        # 生成 trace_id（若调用方未设置）
        if not ctx.trace_id:
            ctx.trace_id = f"pipe-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"

        strategy = (
            biz_config.get("strategy", {}).get("default_strategy", "direct")
        )
        # ctx.top_n 已由技能入口 / SkillExecutor 按请求 topN 写入，不得再用 biz_config.strategy.top_n
        # 覆盖，否则 POST body 中的 topN 会失效（如北京 biz 默认 top_n=2）。


        wall_start = time.perf_counter()
        # log_summary 已删除（原为 LangGraph 残留的空壳函数），改用 logger.debug 记录管道开始
        logger.debug(
            f"[Pipeline] pipeline_start trace_id={ctx.trace_id} "
            f"phone={ctx.phone[:3]}****{ctx.phone[-4:] if len(ctx.phone) >= 7 else ''}"
        )

        # ── 组件化管道分支：biz_config.pipeline 存在且合法 → engine.pipeline_runner ──
        # 现网配置均无 pipeline 字段，恒走下方旧三步路径（行为不变）。
        pipeline_def = biz_config.get("pipeline")
        use_engine_pipeline = False
        if pipeline_def:
            try:
                # 延迟 import：仅配置了 pipeline 时才加载 engine（导入即注册内置组件）
                from engine.pipeline_runner import run_pipeline, validate_pipeline_def

                validate_errors = validate_pipeline_def(pipeline_def)
                if validate_errors:
                    logger.warning(
                        f"[Pipeline] biz_config.pipeline 定义非法，回退旧三步路径: "
                        f"{validate_errors} trace_id={ctx.trace_id}"
                    )
                else:
                    use_engine_pipeline = True
            except Exception as exc:
                logger.warning(
                    f"[Pipeline] engine 管道执行器不可用，回退旧三步路径: {exc}"
                )

        # Bundle 按 province + intent 隔离，防止多意图 builder 互相污染
        # （组件化管道路径不创建 Bundle：组件实例由 engine.registry 自行缓存）
        bundle = None
        if not use_engine_pipeline:
            bundle = self.get_bundle(ctx.province, ctx.intent)

        try:
            if use_engine_pipeline:
                # ── 组件化管道：逐步执行 biz_config.pipeline.steps ──
                logger.info(
                    f"[Pipeline] ▶ 组件化管道执行（biz_config.pipeline）  "
                    f"steps={len((pipeline_def or {}).get('steps') or [])} "
                    f"trace_id={ctx.trace_id}"
                )
                await run_pipeline(ctx, skill_config, pipeline_def)
            else:
                # ── Step1: 数据采集 ─────────────────────────────────
                logger.info(f"[Pipeline] ▶ Step1 数据采集  trace_id={ctx.trace_id}")
                await bundle.data_step.run(ctx, api_nodes)
                logger.info(f"[Pipeline] ▶ Step1 数据采集结果为: {ctx}")
                # ── Step2: 推荐筛选 ─────────────────────────────────
                # 若无推荐候选（接口未返回 / 不调接口场景），跳过推荐筛选，
                # 直接由 Step3 从 batch_contexts 入参中获取产品/环节/场景匹配话术
                if ctx.recommended_packages:
                    logger.info(f"[Pipeline] ▶ Step2 推荐筛选  strategy={strategy}")
                    await bundle.recommend_step.run(ctx, strategy=strategy)
                    logger.info(f"[Pipeline] ▶ Step2 推荐筛选结果为: {ctx.final_recommendations}")
                else:
                    logger.info(
                        "[Pipeline] ▶ Step2 跳过（recommended_packages 为空）"
                        "，Step3 直接从 batch_contexts 入参获取产品/环节/场景"
                    )
                    ctx.final_recommendations = []

                # ── Step3: 话术生成 ─────────────────────────────────
                # 统一走 run_batch：
                #   batch_contexts 非空 → 直接使用（每项独立指定 product_id/stage/scence/expand）；
                #   batch_contexts 为空 → 自动构造一条空条目，run_batch 对 final_recommendations 展开，
                #     等价于原来"不带任何匹配维度的单条路径"。
                if not ctx.batch_contexts:
                    ctx.batch_contexts = [{"product_id": "", "stage": "", "scence": ""}]
                    logger.info(
                        f"[Pipeline] ▶ Step3 话术生成（batch_contexts 为空，自动构造兜底条目）  "
                        f"final_recommendations={len(ctx.final_recommendations)} "
                        f"(请求 top_n={ctx.top_n})"
                    )
                else:
                    logger.info(
                        f"[Pipeline] ▶ Step3 话术生成（批量模式）  "
                        f"batch_size={len(ctx.batch_contexts)} "
                        f"final_recommendations={len(ctx.final_recommendations)}"
                    )
                await bundle.script_step.run_batch(ctx, biz_config=biz_config)

        except Exception as exc:
            logger.error(f"[Pipeline] ❌ 执行异常: {exc}")
            ctx.add_error(f"pipeline异常: {exc}")

        finally:
            # 用 record_stage 替代已删除的 log_summary，将管道整体耗时纳入 stage_events
            record_stage(
                stage="pipeline",
                elapsed_ms=elapsed_ms(wall_start),
                provider="pipeline",
                degrade_flag=bool(ctx.errors),
                script_count=len(ctx.marketing_scripts),
                errors=len(ctx.errors),
            )

        return ctx.to_result()
