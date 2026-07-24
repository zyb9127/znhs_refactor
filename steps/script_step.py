"""
ScriptStep — Step3: 话术生成

职责：
1. 从 biz_config.script_templates / prompts 读取话术模板和 LLM Prompt
2. 对 ctx.final_recommendations 中每个产品，并发调 LLM 生成个性化话术
   （模板/Prompt 构建在 asyncio.to_thread 中执行，避免阻塞事件循环导致 LLM 无法并行发出）
3. 结果写入 ctx.marketing_scripts

关键设计：
- 用户侧描述数据来自 FlowContext.resource_context（current_package、usage、tags 等）；推荐单条用 pkg_brief。
- 主服务直传的 ``extra_info`` / ``extra_context`` 不经单独解析服务，格式化为 JSON 文本注入 Prompt（可作为关联变量勾选，或未勾选且非空时自动追加）。
- 话术模板与 Prompt 来自 biz_config；并发 LLM；PackageDiff 注入差异；失败则降级话术。

【重构说明（合并冗余）】
- 模板选择逻辑已迁移至 engine/template_selector.py（_select_template /
  _select_templates_expand 保留为薄委托，兼容既有外部引用与测试）；
- Prompt 组装逻辑已迁移至 engine/prompt_builder.py（_build_prompt /
  _VAR_LABELS / _resource_context_prompt_vars / _append_prompt_extra_suffix 同理）；
- run()/run_batch() 的重复配置解析合并为 _load_biz()；模板数达标时构建一次哈希索引
  （build_selector_index，构建后只读、线程安全）；可选 LLM 并发信号量
  （strategy.llm_max_concurrency 或环境变量 ZNHS_LLM_MAX_CONCURRENCY，未设置行为不变）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from core.context import FlowContext
from services.llm_service import llm_service
from utils.observability import record_stage
from plugins.package_diff import PackageDiff
from engine.prompt_builder import (
    VAR_LABELS,
    append_prompt_extra_suffix,
    build_prompt,
    resource_context_prompt_vars,
)
from engine.template_selector import (
    build_selector_index,
    select_template,
    select_template_linear,
    select_templates_expand,
)


# 并发度：同时向 LLM 发起的请求数
_DEFAULT_CONCURRENCY = 3

# 内置默认字段别名（biz_config 未配置时的兜底）
_DEFAULT_FIELD_ALIASES: Dict[str, List[str]] = {
    "pkg_name":  ["offerName",  "package_name",   "productName",  "name"],
    "pkg_fee":   ["initFee",    "monthly_fee",     "price",        "fee"],
    "pkg_flow":  ["offerFlow",  "data_quota",      "dataGB",       "flow"],
    "pkg_voice": ["offerVoice", "voice_quota",     "voiceMinutes", "voice"],
    "product_id": ["offerId",    "product_id",      "package_id",   "offer_id"],
}

class ScriptStep:
    """话术生成步骤（并发 LLM，biz_config 配置驱动，省份无关）"""

    def __init__(self, province: str = "default") -> None:
        # province 仅兼容 Pipeline 构造参数；话术逻辑只依赖 FlowContext + biz_config
        self.max_length    = 150          # 话术最大字符数默认值（营销话术一般 150 字内；可由 biz_config.strategy.max_script_length 覆盖）
        self.concurrency   = _DEFAULT_CONCURRENCY
        self.field_aliases: Dict[str, List[str]] = {}   # 由 _load_biz() 从 biz_config 注入
        # 以下由 _load_biz() 每次请求解析注入
        self._templates_v2: List[Dict[str, Any]] = []
        self._fallback_prompt_tpl: str = ""
        self._llm_max_concurrency: int = 0   # 0 = 不限流（历史默认行为）

    # ── 配置解析（run / run_batch 共用）──────────────────────────

    def _load_biz(self, biz_config: Dict[str, Any]) -> None:
        """解析 biz_config 公共配置段（原 run()/run_batch() 开头的重复解析段合并至此）。

        写入实例属性：max_length / concurrency / field_aliases /
        _templates_v2 / _fallback_prompt_tpl / _llm_max_concurrency。
        """
        strategy = biz_config.get("strategy", {})
        prompts  = biz_config.get("prompts", {})

        self.max_length  = strategy.get("max_script_length", self.max_length)
        self.concurrency = strategy.get("max_parallel_scripts", self.concurrency)
        self.field_aliases = biz_config.get("field_aliases", {})

        # script_templates_v2：新格式（列表），支持 product_id 精确匹配 + 兜底
        self._templates_v2 = biz_config.get("script_templates_v2", [])

        # 旧格式兜底 Prompt（当 v2 无可用模板时使用）
        old_prompt_cfg = (
            prompts.get("package_recommendation", {})
            or prompts.get(list(prompts.keys())[0], {}) if prompts else {}
        )
        self._fallback_prompt_tpl = old_prompt_cfg.get("user_prompt_template", "")

        # LLM 并发上限：strategy.llm_max_concurrency 优先，其次环境变量
        # ZNHS_LLM_MAX_CONCURRENCY；两者都未设置（或非法/<=0）→ 0 表示不限流（行为不变）
        raw = strategy.get("llm_max_concurrency")
        if raw is None:
            raw = os.environ.get("ZNHS_LLM_MAX_CONCURRENCY")
        try:
            n = int(raw) if raw is not None and str(raw).strip() != "" else 0
        except (TypeError, ValueError):
            n = 0
        self._llm_max_concurrency = n if n > 0 else 0

    def _make_llm_semaphore(self) -> Optional[asyncio.Semaphore]:
        """按 _load_biz 解析出的并发上限创建信号量；未配置返回 None（不限流，行为不变）。"""
        if self._llm_max_concurrency > 0:
            logger.info(
                f"[ScriptStep] LLM 并发限流已启用 max_concurrency={self._llm_max_concurrency}"
            )
            return asyncio.Semaphore(self._llm_max_concurrency)
        return None

    async def _generate_llm(
        self,
        prompt: str,
        ctx: FlowContext,
        stage: str,
        sem: Optional[asyncio.Semaphore],
    ) -> str:
        """调用 LLM 生成话术；sem 非 None 时信号量只包住 generate 调用段（不包模板准备）。"""
        if sem is None:
            return await llm_service.generate(
                prompt,
                temperature=0.3,
                max_tokens=300,
                stage=stage,
                provider="script_step",
                province=ctx.province,
            )
        async with sem:
            return await llm_service.generate(
                prompt,
                temperature=0.3,
                max_tokens=300,
                stage=stage,
                provider="script_step",
                province=ctx.province,
            )

    # ── 主入口 ────────────────────────────────────────────────────

    async def run(self, ctx: FlowContext, biz_config: Dict[str, Any]) -> None:
        """并发生成每个推荐产品的话术，写入 ctx.marketing_scripts。

        模板查找优先级（script_templates_v2 驱动，三级降级）：
        1. intent 匹配 + product_id 精确匹配 + status=online
        2. intent 匹配 + product_id=''（兜底模板） + status=online
        3. biz_config.prompts.package_recommendation（旧字段，最终保底）
        """
        t0 = time.perf_counter()

        packages = ctx.final_recommendations
        if not packages:
            logger.warning("[ScriptStep] final_recommendations 为空，跳过话术生成")
            ctx.marketing_scripts = []
            return

        # ── 从 biz_config 读取配置（与 run_batch 共用 _load_biz）──
        self._load_biz(biz_config)
        templates_v2 = self._templates_v2
        fallback_prompt_tpl = self._fallback_prompt_tpl

        logger.info(
            f"[ScriptStep] 并发生成 {len(packages)} 条话术 "
            f"intent={ctx.intent} templates_v2={len(templates_v2)} "
            f"max_length={self.max_length}"
        )

        # 生产排查：上游接口未返回当前套餐时，cur_brief/diff_str 事实缺失，
        # Prompt 会按“空事实不展示”跳过对应行（不再出现「未知套餐」「月费—」）
        if not ctx.current_package:
            logger.warning(
                f"[ScriptStep] ⚠️ current_package 为空（phone={ctx.phone} intent={ctx.intent}），"
                "话术将不包含当前套餐与差异信息，请检查上游接口返回及 response_extract 映射"
            )

        # 模板索引：规模达标且未关闭时构建一次（构建后只读、线程安全，作参数传入准备函数）
        tpl_index = build_selector_index(templates_v2, ctx.intent)
        # LLM 并发限流信号量（未配置时为 None，行为与历史一致）
        sem = self._make_llm_semaphore()

        # 从 extra_context 中提取模板匹配维度
        ec = ctx.extra_context or {}
        match_stage = ec.get("stage", "")
        match_scene = ec.get("scence", "")   # 注意：接口定义拼写为 scence

        async def _gen_one(pkg: Dict[str, Any], rank: int) -> Dict[str, Any]:
            # 模板匹配与 _build_prompt 为同步逻辑；在 to_thread 中执行避免阻塞事件循环，
            # 保证所有协程能真正并发发起 LLM 请求。
            prep = await asyncio.to_thread(
                self._prepare_one_script_sync,
                pkg,
                rank,
                ctx,
                biz_config,
                templates_v2,
                match_stage,
                match_scene,
                fallback_prompt_tpl,
                tpl_index=tpl_index,
            )
            llm_success = False
            raw = ""
            if not prep.get("_skip_llm"):
                try:
                    raw = await self._generate_llm(
                        prep["prompt"], ctx, "script_step.llm", sem
                    )
                    llm_success = True
                except Exception as exc:
                    logger.error(
                        f"[ScriptStep] ❌ 产品[{prep.get('package_name', '')}]话术生成失败: {exc}"
                    )
            text = self._post_process(raw)
            if not text:
                llm_success = False
                text = self._fallback_text(prep["pkg"], prep["diff"])
            return self._finalize_script_one(prep, text, llm_success)

               

        tasks = [_gen_one(pkg, i + 1) for i, pkg in enumerate(packages)]
        results = await asyncio.gather(*tasks)
        ctx.marketing_scripts = list(results)

        # 准确统计降级情况：任意一条话术走了 fallback → 标记降级
        degraded = any(not item.get("_llm_success", True) for item in results)
        # 清理内部标记字段，不对外暴露
        for item in ctx.marketing_scripts:
            item.pop("_llm_success", None)

        logger.info(f"[ScriptStep] ✅ 话术生成完成，共 {len(results)} 条，降级={degraded}")
        record_stage(
            stage="script_step",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            cache_hit=False,
            provider="script_step.llm",
            degrade_flag=degraded,
            script_count=len(results),
        )

    # ── 批量话术生成入口 ──────────────────────────────────────────

    async def run_batch(self, ctx: FlowContext, biz_config: Dict[str, Any]) -> None:
        """批量模式：对每个 batch_context 条目生成话术，写入 ctx.marketing_scripts。

        batch_contexts 每项结构：
            { "product_id": str, "stage": str, "scence": str }

        - product_id 非空 → 从 final_recommendations 中找对应产品；找不到则用产品占位
        - product_id 为空 → 对 final_recommendations 中所有产品展开（笛卡尔）

        结果结构与单维度一致，额外携带 stage/scence 字段供 to_result() 回显。
        """
        t0 = time.perf_counter()

        batch_contexts = ctx.batch_contexts
        if not batch_contexts:
            logger.warning("[ScriptStep.batch] batch_contexts 为空，跳过批量生成")
            ctx.marketing_scripts = []
            return

        # ── 从 biz_config 读取配置（与 run() 共用 _load_biz）──────
        self._load_biz(biz_config)
        templates_v2 = self._templates_v2
        fallback_prompt_tpl = self._fallback_prompt_tpl

        # 模板索引：构建一次后只读（线程安全），作参数传入准备函数
        tpl_index = build_selector_index(templates_v2, ctx.intent)
        # LLM 并发限流信号量。批量模式下多产品话术改为并发生成，为避免未配置并发上限时
        # 向 LLM 网关瞬时打出过多请求（原串行等价于并发=1），此处兜底用 max_parallel_scripts
        # （默认 3）作为并发上限；已显式配置 llm_max_concurrency 时以其为准，不改变既有行为。
        sem = self._make_llm_semaphore()
        if sem is None:
            _default_cap = self.concurrency if isinstance(self.concurrency, int) and self.concurrency > 0 else 3
            sem = asyncio.Semaphore(_default_cap)

        # 产品查找辅助：product_id → 产品 dict（来自 final_recommendations）
        fa = self.field_aliases
        pid_keys = fa.get("product_id", _DEFAULT_FIELD_ALIASES["product_id"])
        rec_by_pid: Dict[str, Dict[str, Any]] = {}
        for pkg in ctx.final_recommendations:
            pid = str(self._get_field(pkg, pid_keys) or "").strip()
            if pid:
                rec_by_pid[pid] = pkg

        logger.info(
            f"[ScriptStep.batch] 开始批量生成  batch_size={len(batch_contexts)} "
            f"rec_products={len(rec_by_pid)} templates_v2={len(templates_v2)}"
        )

        async def _gen_batch_one(
            bc: Dict[str, Any], global_rank: int
        ) -> List[Dict[str, Any]]:
            """对单个 batch_context 条目生成话术列表。

            expand=true 模式：scence 为空时枚举 product_id+stage 下所有 scene，每个 scene 生成一条话术。
            普通模式：按 product_id/stage/scence 匹配单个最精确模板，生成一条话术。
            """
            bc_pid    = str(bc.get("product_id") or "").strip()
            bc_stage  = str(bc.get("stage") or "").strip()
            bc_scene  = str(bc.get("scence") or "").strip()
            bc_expand = bool(bc.get("expand", False))

            # ── expand 模式：枚举该 product_id+stage 下所有 scene ──────
            if bc_expand and not bc_scene:
                # 确定要展开的产品列表
                # - bc_pid 非空：用指定产品（先从推荐结果找，找不到则占位）
                # - bc_pid 为空 + 有推荐产品：对所有推荐产品各自展开 scene
                # - bc_pid 为空 + 无推荐产品：用通用产品（product_id=""）匹配模板
                if bc_pid:
                    expand_pkgs = [rec_by_pid.get(bc_pid) or {"product_id": bc_pid}]
                elif ctx.final_recommendations:
                    expand_pkgs = list(ctx.final_recommendations)
                else:
                    expand_pkgs = [{}]

                fa = self.field_aliases
                pid_keys = fa.get("product_id", _DEFAULT_FIELD_ALIASES["product_id"])

                # 单条 (产品 × scene) 话术生成协程（供并发调度；行为与原串行逐条一致）
                async def _gen_one_expand(
                    pkg_exp: Dict[str, Any], pkg_rank: int, tpl_scene: str
                ) -> Dict[str, Any]:
                    prep = await asyncio.to_thread(
                        self._prepare_one_script_sync,
                        pkg_exp,
                        pkg_rank,   # 使用产品自身 rank，不自增
                        ctx,
                        biz_config,
                        templates_v2,
                        bc_stage,
                        tpl_scene,
                        fallback_prompt_tpl,
                        tpl_index=tpl_index,
                    )
                    llm_success = False
                    raw = ""
                    if not prep.get("_skip_llm"):
                        try:
                            raw = await self._generate_llm(
                                prep["prompt"], ctx, "script_step.batch.expand.llm", sem
                            )
                            llm_success = True
                        except Exception as exc:
                            logger.error(
                                f"[ScriptStep.batch.expand] ❌ 产品[{prep.get('package_name', '')}]"
                                f" stage={bc_stage!r} scene={tpl_scene!r} 话术生成失败: {exc}"
                            )
                    text = self._post_process(raw)
                    if not text:
                        llm_success = False
                        text = self._fallback_text(prep["pkg"], prep["diff"])

                    script = self._finalize_script_one(prep, text, llm_success)
                    script["stage"]  = bc_stage
                    script["scence"] = tpl_scene
                    script.pop("_llm_success", None)
                    return script

                # 按原顺序枚举 (产品 × scene) 任务后并发生成（受 sem 限流，gather 保序）
                expand_tasks = []
                for pkg_exp in expand_pkgs:
                    # 取该产品的实际 product_id（推荐产品用 offerId 等字段）
                    actual_pid = str(self._get_field(pkg_exp, pid_keys) or bc_pid or "").strip()
                    # rank 来自推荐产品自身的 rank 字段，同一产品多个 scene 共用同一 rank
                    pkg_rank = int(pkg_exp.get("rank") or global_rank)
                    expand_templates = self._select_templates_expand(
                        templates_v2, ctx.intent, actual_pid, stage=bc_stage
                    )
                    if not expand_templates:
                        logger.warning(
                            f"[ScriptStep.batch.expand] product_id={actual_pid!r} "
                            f"stage={bc_stage!r} 未找到模板，跳过该产品"
                        )
                        continue

                    for tpl in expand_templates:
                        tpl_scene = str(tpl.get("scene") or "").strip()
                        expand_tasks.append(_gen_one_expand(pkg_exp, pkg_rank, tpl_scene))

                item_results: List[Dict[str, Any]] = (
                    list(await asyncio.gather(*expand_tasks)) if expand_tasks else []
                )

                if not item_results:
                    logger.warning(
                        f"[ScriptStep.batch.expand] stage={bc_stage!r} 所有产品均未找到模板，跳过"
                    )
                    return []

                logger.info(
                    f"[ScriptStep.batch.expand] stage={bc_stage!r} "
                    f"共枚举 {len(item_results)} 条话术（{len(expand_pkgs)} 个产品）"
                )
                return item_results

            # ── 普通模式 ────────────────────────────────────────────────

            # 确定要生成话术的产品列表
            if bc_pid:
                # product_id 指定：从推荐结果中找；找不到则构造最小占位产品（无推荐场景）
                pkg = rec_by_pid.get(bc_pid) or {"product_id": bc_pid}
                target_pkgs = [pkg]
            else:
                if ctx.final_recommendations:
                    # 有推荐产品：对所有推荐结果展开（笛卡尔）
                    target_pkgs = list(ctx.final_recommendations)
                else:
                    # 无推荐产品且 product_id 为空：用空产品对象走一次模板匹配，
                    # 以 stage/scence 为唯一维度匹配「不限产品」的通用话术模板
                    target_pkgs = [{}]

            # 单个产品话术生成协程（供并发调度；行为与原串行逐条一致）
            async def _gen_one_pkg(i: int, pkg: Dict[str, Any]) -> Dict[str, Any]:
                prep = await asyncio.to_thread(
                    self._prepare_one_script_sync,
                    pkg,
                    global_rank + i,
                    ctx,
                    biz_config,
                    templates_v2,
                    bc_stage,
                    bc_scene,
                    fallback_prompt_tpl,
                    tpl_index=tpl_index,
                )
                llm_success = False
                raw = ""
                if not prep.get("_skip_llm"):
                    try:
                        raw = await self._generate_llm(
                            prep["prompt"], ctx, "script_step.batch.llm", sem
                        )
                        llm_success = True
                    except Exception as exc:
                        logger.error(
                            f"[ScriptStep.batch] ❌ 产品[{prep.get('package_name', '')}]"
                            f" stage={bc_stage!r} scene={bc_scene!r} 话术生成失败: {exc}"
                        )
                text = self._post_process(raw)
                if not text:
                    llm_success = False
                    text = self._fallback_text(prep["pkg"], prep["diff"])

                script = self._finalize_script_one(prep, text, llm_success)
                # 批量模式：注入 stage/scence，供 to_result() 回显
                script["stage"]  = bc_stage
                script["scence"] = bc_scene
                script.pop("_llm_success", None)
                return script

            # 多产品并发生成（受 sem 限流；gather 保序，rank=global_rank+i 与输出结构均与串行一致）
            item_results = list(await asyncio.gather(*[
                _gen_one_pkg(i, pkg) for i, pkg in enumerate(target_pkgs)
            ]))
            return item_results

        # 并发执行所有 batch_context 条目
        rank_counter = 1
        tasks = []
        for bc in batch_contexts:
            tasks.append(_gen_batch_one(bc, rank_counter))
            # 粗略计算 rank 偏移（product_id 为空时展开数量不定，先按 topN 估计）
            bc_pid = str(bc.get("product_id") or "").strip()
            rank_counter += 1 if bc_pid else max(len(ctx.final_recommendations), 1)

        nested_results = await asyncio.gather(*tasks)

        all_scripts: List[Dict[str, Any]] = []
        for sub in nested_results:
            all_scripts.extend(sub)

        ctx.marketing_scripts = all_scripts
        degraded = any(not item.get("_llm_success", True) for item in all_scripts)
        for item in ctx.marketing_scripts:
            item.pop("_llm_success", None)

        logger.info(
            f"[ScriptStep.batch] ✅ 批量话术生成完成，共 {len(all_scripts)} 条，降级={degraded}"
        )
        record_stage(
            stage="script_step_batch",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            cache_hit=False,
            provider="script_step.llm",
            degrade_flag=degraded,
            script_count=len(all_scripts),
        )

    # ── 单条话术：准备（线程）+ LLM（async）+ 组装 ─────────────────

    def _prepare_one_script_sync(
        self,
        pkg: Dict[str, Any],
        rank: int,
        ctx: FlowContext,
        biz_config: Dict[str, Any],
        templates_v2: List[Dict[str, Any]],
        match_stage: str,
        match_scene: str,
        fallback_prompt_tpl: str,
        extra_info_override: Optional[Dict[str, Any]] = None,
        tpl_index: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """在线程池执行：模板选择 + PackageDiff + 组装 Prompt。

        extra_info_override：批量模式下由 run_batch 传入的条目级 extra_info（已合并全局），
        为 None 时 _build_prompt 直接使用 ctx.extra_info（单条/标准模式）。
        tpl_index：run/run_batch 构建一次的模板哈希索引（engine.template_index.TemplateIndex，
        构建后只读、线程安全）；为 None 时 select_template 回退旧线性扫描，行为不变。
        """
        product_id_hint = self._get_field(
            pkg,
            self.field_aliases.get("product_id", _DEFAULT_FIELD_ALIASES["product_id"]),
        )
        logger.info(
            f"[ScriptStep] 模板匹配 product_id={product_id_hint!r} "
            f"stage={match_stage!r} scene={match_scene!r} "
            f"templates_v2_count={len(templates_v2)}"
        )
        matched = select_template(
            templates_v2,
            ctx.intent,
            str(product_id_hint),
            stage=match_stage,
            scene=match_scene,
            index=tpl_index,
        )
        logger.info(
            f"[ScriptStep] 模板匹配结果: "
            f"{'命中 ' + str(matched.get('template_name', '')) if matched else '未命中，走旧格式'}"
        )
        if matched:
            tpl_prompt = matched.get("prompt_template") or matched.get("template_content", "")
            tpl_content = matched.get("template_content", "")
        else:
            tpl_prompt = fallback_prompt_tpl
            tpl_content = ""
            if not tpl_content:
                legacy = (biz_config.get("script_templates") or {}).get(ctx.intent) or {}
                if isinstance(legacy, dict) and legacy.get("template_content"):
                    tpl_content = str(legacy.get("template_content", "")).strip()
        tpl_linked_vars = matched.get("linked_vars", []) if matched else []
        tpl_script_req = matched.get("script_requirement", "") if matched else ""
        if not matched and tpl_content and not tpl_linked_vars:
            tpl_linked_vars = ["cur_brief", "pkg_brief", "diff_str", "usage_line"]

        fa = self.field_aliases
        pid_keys = fa.get("product_id", _DEFAULT_FIELD_ALIASES["product_id"])
        product_id = self._get_field(pkg, pid_keys)
        package_name = self._get_field(pkg, fa.get("pkg_name", _DEFAULT_FIELD_ALIASES["pkg_name"]))

        diff = PackageDiff(ctx.current_package, pkg)
        
        prompt = self._build_prompt(
            user_prompt_tpl=tpl_prompt,
            template_text=tpl_content,
            ctx=ctx,
            pkg=pkg,
            diff=diff,
            linked_vars=tpl_linked_vars or [],
            script_requirement=tpl_script_req,
        )
        return {
            "pkg":             pkg,
            "rank":            rank,
            "diff":            diff,
            "linked_vars":     tpl_linked_vars,
            "user_prompt_tpl": tpl_prompt,
            "product_id":      product_id,
            "package_name":    package_name,
            "prompt":          prompt,
            "_skip_llm":       not bool(prompt),  # prompt 为空时跳过 LLM，直接走降级话术
        }
        
    def _finalize_script_one(
        self,
        prep: Dict[str, Any],
        text: str,
        llm_success: bool,
    ) -> Dict[str, Any]:
        pkg = prep["pkg"]
        rank = prep["rank"]
        diff = prep["diff"]
        linked_vars = prep.get("linked_vars") or []
        user_prompt_tpl = prep.get("user_prompt_tpl") or ""
        product_id = prep["product_id"]
        package_name = prep["package_name"]
        fa = self.field_aliases
        fee = self._get_field(pkg, fa.get("pkg_fee", _DEFAULT_FIELD_ALIASES["pkg_fee"]))
        flow = self._get_field(pkg, fa.get("pkg_flow", _DEFAULT_FIELD_ALIASES["pkg_flow"]))
        include_table = "table" in linked_vars or "{table}" in user_prompt_tpl
        result: dict = {
            "product_id":     product_id,
            "rank":           rank,
            "marketing_text": text,
            "package_name":   package_name,
            "monthly_fee":    fee or 0,
            "data_quota":     flow or "",
            "_llm_success":   llm_success,
        }
        if include_table:
            result["diff_table"] = diff.to_table()
        return result


    # ── 模板选择（实现已迁移至 engine/template_selector.py，此处为薄委托）──

    @staticmethod
    def _select_templates_expand(
        templates_v2: List[Dict[str, Any]],
        intent: str,
        product_id: str,
        stage: str = "",
    ) -> List[Dict[str, Any]]:
        """薄委托：实现已迁移至 engine.template_selector.select_templates_expand
        （保留方法名与签名，兼容既有外部引用/测试）。"""
        return select_templates_expand(templates_v2, intent, product_id, stage=stage)

    @staticmethod
    def _select_template(
        templates_v2: List[Dict[str, Any]],
        intent:     str,
        product_id: str,
        stage:      str = "",
        scene:      str = "",
    ) -> Optional[Dict[str, Any]]:
        """薄委托：实现已迁移至 engine.template_selector.select_template_linear
        （12档三阶段匹配语义不变；保留方法名与签名，兼容既有外部引用/测试）。"""
        return select_template_linear(
            templates_v2, intent, product_id, stage=stage, scene=scene
        )

    # ── Prompt 构造（实现已迁移至 engine/prompt_builder.py，此处为薄委托）──

    # 变量键 → Prompt 中展示的中文标签（单一真源：engine.prompt_builder.VAR_LABELS，
    # 其优先从 schemas.get_standard_domains() 构建，异常回退迁移过去的硬编码字典）
    _VAR_LABELS: Dict[str, str] = VAR_LABELS

    @staticmethod
    def _fmt_extra_for_prompt(obj: Any) -> str:
        """将 extra_info / extra_context 序列化为 Prompt 可读文本（JSON）。"""
        if obj is None:
            return ""
        if isinstance(obj, dict) and not obj:
            return ""
        if isinstance(obj, list) and not obj:
            return ""
        try:
            return json.dumps(obj, ensure_ascii=False)
        except TypeError:
            return str(obj)

    def _resource_context_prompt_vars(
        self, ctx: FlowContext, fa: Dict[str, Any]
    ) -> Dict[str, str]:
        """薄委托：实现已迁移至 engine.prompt_builder.resource_context_prompt_vars。"""
        return resource_context_prompt_vars(ctx, fa)

    @staticmethod
    def _fmt_flat_domain(d: Dict[str, Any]) -> str:
        """user_info / user_profile / domain_ext：格式化为 k:v（一层嵌套 dict 展开）。"""
        if not isinstance(d, dict) or not d:
            return ""
        parts: List[str] = []
        _empty = {"", "None", "null", "0"}
        for k, v in d.items():
            if isinstance(v, dict):
                for sk, sv in v.items():
                    if str(sv).strip() not in _empty:
                        parts.append(f"{sk}:{sv}")
            elif str(v).strip() not in _empty:
                parts.append(f"{k}:{v}")
        return "，".join(parts)

    @staticmethod
    def _append_prompt_extra_suffix(
        tpl_raw: str, body: str, ei_txt: str, ec_txt: str
    ) -> str:
        """薄委托：实现已迁移至 engine.prompt_builder.append_prompt_extra_suffix。"""
        return append_prompt_extra_suffix(tpl_raw, body, ei_txt, ec_txt)

    def _build_prompt(
        self,
        user_prompt_tpl: str,
        template_text: str,
        ctx: FlowContext,
        pkg: Dict[str, Any],
        diff: Any,
        linked_vars: Optional[List[str]] = None,
        script_requirement: str = "",
        extra_info_override: Optional[Dict[str, Any]] = None,
    ) -> str:
        """薄委托：实现已迁移至 engine.prompt_builder.build_prompt（逐行等价，
        实例状态 self.field_aliases / self.max_length 以显式参数传入）。"""
        return build_prompt(
            user_prompt_tpl=user_prompt_tpl,
            template_text=template_text,
            ctx=ctx,
            pkg=pkg,
            diff=diff,
            linked_vars=linked_vars,
            script_requirement=script_requirement,
            extra_info_override=extra_info_override,
            field_aliases=self.field_aliases,
            max_length=self.max_length,
        )

    # ── 话术后处理 ────────────────────────────────────────────────

    @staticmethod
    def _post_process(text: str) -> str:
        """对 LLM 输出做最终整形（Markdown/前缀清洗已在 llm_service._clean_llm_output 完成）

        仅保留：多段落合并 / 去首尾引号 / 无中文兜底
        """
        text = (text or "").strip()
        if not text:
            return ""
        if "\n" in text:
            parts = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
            text = " ".join(parts)
        text = text.strip().strip('"""\'')
        if text and not re.search(r"[\u4e00-\u9fff]", text):
            return ""
        return text

    def _fallback_text(self, pkg: Dict[str, Any], diff: Any) -> str:
        """LLM 降级兜底话术（字段名由 field_aliases / 默认别名解析）"""
        fa = self.field_aliases
        name = (
            self._get_field(pkg, fa.get("pkg_name", _DEFAULT_FIELD_ALIASES["pkg_name"]))
            or "推荐套餐"
        )
        fee = self._get_field(pkg, fa.get("pkg_fee", _DEFAULT_FIELD_ALIASES["pkg_fee"]))
        flow = self._get_field(pkg, fa.get("pkg_flow", _DEFAULT_FIELD_ALIASES["pkg_flow"]))
        parts = [f"推荐您升级{name}"]
        if fee:
            parts.append(f"仅需{fee}元/月")
        if flow:
            parts.append(f"流量{flow}GB" if str(flow).isdigit() else f"流量{flow}")
        return "，".join(parts) + "，回复1立即办理。"

    # ── 格式化工具（省份无关）────────────────────────────────────

    @staticmethod
    def _get_field(
        d: Dict[str, Any],
        keys: List[str],
        default: Any = "",
    ) -> Any:
        """按优先级列表依次取字段，返回第一个非 None 值"""
        for k in keys:
            v = d.get(k)
            if v is not None:
                return v
        return default

    @classmethod
    def _fmt_package(
        cls,
        pkg: Dict[str, Any],
        field_aliases: Optional[Dict[str, Any]] = None,
    ) -> str:
        """套餐信息格式化为简洁单行摘要。

        字段名通过 biz_config.field_aliases 配置；未配置时使用 _DEFAULT_FIELD_ALIASES 兜底。
        空包/缺套餐名时不再输出「未知套餐」占位（生产接口偶发不返回当前套餐，
        该占位会被 LLM 原样写进话术）；无任何可用信息时返回空串，
        由 Prompt 组装层按“空事实不展示”规则跳过该行。
        """
        if not isinstance(pkg, dict) or not pkg:
            return ""

        fa = field_aliases or {}

        def _get(key: str) -> Any:
            return cls._get_field(pkg, fa.get(key, _DEFAULT_FIELD_ALIASES[key]))

        name  = _get("pkg_name")
        fee   = _get("pkg_fee")
        flow  = _get("pkg_flow")
        voice = _get("pkg_voice")

        parts = [str(name)] if name else []
        if fee:
            parts.append(f"{fee}元/月")
        if flow:
            parts.append(f"流量{flow}GB" if str(flow).isdigit() else f"流量{flow}")
        if voice:
            parts.append(f"语音{voice}分钟" if str(voice).isdigit() else f"语音{voice}")

        # 收集五组核心维度（含 product_id）的所有原始字段名，追加时跳过
        _core_alias_keys = ("pkg_name", "pkg_fee", "pkg_flow", "pkg_voice", "product_id")
        used_keys: set = set()
        for alias_key in _core_alias_keys:
            for raw_key in fa.get(alias_key, _DEFAULT_FIELD_ALIASES.get(alias_key, [])):
                used_keys.add(raw_key)

        # 追加其余非空字段，长字符串截断至 130 字
        _empty_vals = {"", "None", "null"}
        for k, v in pkg.items():
            if k in used_keys:
                continue
            if v is None:
                continue
            if isinstance(v, (dict, list)):
                s = json.dumps(v, ensure_ascii=False)
            else:
                s = str(v).strip()
            if not s or s in _empty_vals:
                continue
            if len(s) > 130:
                s = s[:130] + "..."
            parts.append(f"{k}:{s}")

        return "，".join(parts)

    #: 写入 Prompt 时跳过的推荐产品字段（仅排序用）
    _SKIP_RECOMMENDED_PKG_KEYS = frozenset({"rank"})

    @classmethod
    def _fmt_recommended_product_full(
        cls,
        pkg: Dict[str, Any],
        field_aliases: Optional[Dict[str, Any]] = None,
    ) -> str:
        """单条推荐产品：将全部非空字段拼入 Prompt（保留接口原始字段名，含 offerDesc/offerId 等）。

        与 _fmt_package 区分：后者只用 pkg_name 等别名拼一行，缺 offerName 时会省略套餐名；
        上游若仅返回 offerId、描述类字段，全量输出才能保证 LLM 收到目标套餐信息。
        """
        if not isinstance(pkg, dict):
            return ""
        _empty = {"", "None", "null"}
        parts: List[str] = []
        for k in sorted(pkg.keys()):
            if k in cls._SKIP_RECOMMENDED_PKG_KEYS:
                continue
            v = pkg.get(k)
            if v is None:
                continue
            if isinstance(v, dict):
                if not v:
                    continue
                s = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, list):
                if not v:
                    continue
                s = json.dumps(v, ensure_ascii=False)
            else:
                s = str(v).strip()
            if not s or s in _empty:
                continue
            parts.append(f"{k}:{s}")
        return "，".join(parts)

    @staticmethod
    def _fmt_usage(usage: Dict[str, Any]) -> str:
        """省份无关的通用用量摘要。

        【设计说明】
        不再硬编码任何省份字段名（如"近6月平均流量(MB）"）。
        哪些字段进入 usage.data_usage / usage.voice_usage 由 api_nodes.json
        的 field_transform 配置决定；此处只负责将所有非空字段格式化输出。
        这样新省份接入只需修改 api_nodes.json，无需改代码。
        """
        if not isinstance(usage, dict):
            return ""
        parts: List[str] = []
        _empty = {"", "None", "null", "0"}
        for section_val in usage.values():
            if isinstance(section_val, dict):
                for k, v in section_val.items():
                    if str(v).strip() not in _empty:
                        parts.append(f"{k}:{v}")
            elif section_val and str(section_val).strip() not in _empty:
                parts.append(str(section_val))
        return "，".join(parts) if parts else ""

    @staticmethod
    def _fmt_tags(tags: Dict[str, Any]) -> str:
        """用户标签格式化（只保留值为真的标签名，省份无关）"""
        if not isinstance(tags, dict):
            return ""
        return "、".join(
            k for k, v in tags.items()
            if str(v).strip() not in ("", "否", "0", "False", "false", "No", "null", "None")
        )
