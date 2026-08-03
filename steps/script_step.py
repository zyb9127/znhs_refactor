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
  （build_selector_index，构建后只读、线程安全）；LLM 并发上限来自
  config/concurrency.json（默认 8），可被 biz_config.strategy.llm_max_concurrency
  或环境变量 ZNHS_LLM_MAX_CONCURRENCY 覆盖。
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
from utils.config_loader import config_loader
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
    fuzzy_match_pid,
    select_template,
    select_template_linear,
    select_templates_expand,
)


# 并发度兜底（config/concurrency.json 与 config_loader 均不可用时）
_DEFAULT_CONCURRENCY = 8

# 内置默认字段别名（biz_config 未配置时的兜底）
_DEFAULT_FIELD_ALIASES: Dict[str, List[str]] = {
    "pkg_name":  ["offerName",  "package_name",   "productName",  "name"],
    "pkg_fee":   ["initFee",    "monthly_fee",     "price",        "fee"],
    "pkg_flow":  ["offerFlow",  "data_quota",      "dataGB",       "flow"],
    "pkg_voice": ["offerVoice", "voice_quota",     "voiceMinutes", "voice"],
    "product_id": ["offerId",    "product_id",      "package_id",   "offer_id"],
}

# LLM 未填充的残留占位符：{var} 或 {域[子键]}（根名为 ASCII 变量名，子键允许中文）
_RESIDUAL_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_]\w*(?:\[[^\[\]{}]+\])*\}")


def _apply_slot_facts(text: str, slot_facts: Optional[Dict[str, str]]) -> str:
    """确定性填槽：用【上下文数据】中已注入的事实值替换 LLM 输出里同名占位符。

    优先于残留清理——映射域/子域有值时保证填入准确（不依赖模型是否听话）；
    长 token 优先（``current_package[curOfferDesc]`` 先于 ``current_package``）。
    """
    if not text or not slot_facts or "{" not in text:
        return text
    out = text
    filled: List[str] = []
    for token, val in sorted(slot_facts.items(), key=lambda kv: -len(kv[0])):
        ph = "{" + token + "}"
        if ph in out and str(val).strip():
            out = out.replace(ph, str(val))
            filled.append(token)
    if filled:
        logger.info(f"[ScriptStep] ✅ 确定性填槽: {filled}")
    return out


def _strip_residual_placeholders(text: str) -> str:
    """清除话术中 LLM 未填充的残留占位符（生产曾把 {current_package[curOfferDesc]} 原样播给用户）。

    策略：优先删除包含占位符的整个子句（避免"您当前套餐为，"这类悬空表述）；
    若删完不剩有效中文（极端：每个子句都有占位符），退化为仅删占位符本身。
    无残留时原样返回，零开销。
    """
    if not text or "{" not in text:
        return text
    tokens = _RESIDUAL_PLACEHOLDER_RE.findall(text)
    if not tokens:
        return text
    # 按子句切分（保留分隔符），丢弃含占位符的子句及其后的分隔符
    parts = re.split(r"([，。；！？,;!?])", text)
    kept: List[str] = []
    for i in range(0, len(parts), 2):
        seg = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        if _RESIDUAL_PLACEHOLDER_RE.search(seg):
            continue
        kept.append(seg + sep)
    cleaned = "".join(kept).strip()
    if not re.search(r"[\u4e00-\u9fff]", cleaned):
        cleaned = re.sub(r"\s{2,}", " ", _RESIDUAL_PLACEHOLDER_RE.sub("", text)).strip()
    logger.warning(
        f"[ScriptStep] ⚠️ 话术残留未填充占位符已清理: {tokens}"
        "（通常为对应映射域运行态为空，请检查接口响应与 response_extract/field_transform 映射）"
    )
    return cleaned

class ScriptStep:
    """话术生成步骤（并发 LLM，biz_config 配置驱动，省份无关）"""

    def __init__(self, province: str = "default") -> None:
        # province 仅兼容 Pipeline 构造参数；话术逻辑只依赖 FlowContext + biz_config
        self.max_length    = 150          # 话术最大字符数默认值（营销话术一般 150 字内；可由 biz_config.strategy.max_script_length 覆盖）
        self.concurrency   = _DEFAULT_CONCURRENCY
        self.field_aliases: Dict[str, List[str]] = {}   # 由 _load_biz() 从 biz_config 注入
        self._match_cfg: Dict[str, Any] = {}             # biz_config.template_match（模板匹配取值配置）
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
        # 全局默认来自 config/concurrency.json（默认 8）；biz_config.max_parallel_scripts 可覆盖
        try:
            global_cap = int(config_loader.get_script_llm_max_concurrency())
        except Exception:
            global_cap = _DEFAULT_CONCURRENCY
        if global_cap <= 0:
            global_cap = _DEFAULT_CONCURRENCY
        self.concurrency = strategy.get("max_parallel_scripts", global_cap)
        self.field_aliases = biz_config.get("field_aliases", {})

        # template_match：模板匹配维度取值配置（接口查询模式：推荐结果字段 → 模板匹配键）
        # 形如 {"product_id_from": "curOfferId" 或 ["curOfferId","productInfo.offerId"],
        #       "stage_from": "...", "scene_from": "..."}；支持点路径与多候选。
        # 未配置时行为不变（走 field_aliases.product_id / 默认别名）。
        mc = biz_config.get("template_match")
        self._match_cfg = mc if isinstance(mc, dict) else {}

        # script_templates_v2：新格式（列表），支持 product_id 精确匹配 + 兜底
        self._templates_v2 = biz_config.get("script_templates_v2", [])

        # 旧格式兜底 Prompt（当 v2 无可用模板时使用）
        old_prompt_cfg = (
            prompts.get("package_recommendation", {})
            or prompts.get(list(prompts.keys())[0], {}) if prompts else {}
        )
        self._fallback_prompt_tpl = old_prompt_cfg.get("user_prompt_template", "")

        # LLM 并发上限优先级：
        #   1) biz_config.strategy.llm_max_concurrency（按省/意图覆盖）
        #   2) 环境变量 ZNHS_LLM_MAX_CONCURRENCY
        #   3) config/concurrency.json → llm_max_concurrency（全局默认 8）
        raw = strategy.get("llm_max_concurrency")
        if raw is None:
            raw = os.environ.get("ZNHS_LLM_MAX_CONCURRENCY")
        if raw is None:
            raw = global_cap
        try:
            n = int(raw) if raw is not None and str(raw).strip() != "" else 0
        except (TypeError, ValueError):
            n = 0
        self._llm_max_concurrency = n if n > 0 else global_cap

    @staticmethod
    def _estimate_batch_tasks(
        batch_contexts: List[Dict[str, Any]],
        ctx: FlowContext,
    ) -> int:
        """估算本次批量要生成的话术条数，用于自适应并发上限。

        与 run_batch 的展开规则保持一致：条目指定了 product_id → 1 条；
        product_id 为空 → 对 final_recommendations 逐产品各 1 条。
        expand 模式条数取决于命中的模板集合，此处无法预知，按产品数估算（偏小无副作用：
        并发上限只是限流阈值，估小仅表示不额外放宽）。
        """
        rec_n = len(ctx.final_recommendations or []) or 1
        total = 0
        for bc in batch_contexts or []:
            pid = str((bc or {}).get("product_id", "") or "").strip()
            total += 1 if pid else rec_n
        return max(total, 1)

    def _make_llm_semaphore(self) -> Optional[asyncio.Semaphore]:
        """按 _load_biz 解析出的并发上限创建信号量（来自 concurrency.json / 覆盖项）。"""
        n = self._llm_max_concurrency if self._llm_max_concurrency > 0 else _DEFAULT_CONCURRENCY
        logger.info(f"[ScriptStep] LLM 并发限流已启用 max_concurrency={n}")
        return asyncio.Semaphore(n)

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
            llm_success = True
            raw = ""
            try:
                raw = await self._generate_llm(
                    prep["prompt"], ctx, "script_step.llm", sem
                )
            except Exception as exc:
                logger.error(
                    f"[ScriptStep] ❌ 产品[{prep.get('package_name', '')}]话术生成失败: {exc}"
                )
                llm_success = False
                raw = ""
            text = self._post_process(raw, prep.get("slot_facts"))
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
        # LLM 并发限流：默认读 config/concurrency.json（8），可被 biz_config / 环境变量覆盖
        sem = self._make_llm_semaphore()
        if sem is None:
            _cap = self.concurrency if isinstance(self.concurrency, int) and self.concurrency > 0 else _DEFAULT_CONCURRENCY
            logger.info(f"[ScriptStep.batch] 使用兜底并发上限 max_concurrency={_cap}")
            sem = asyncio.Semaphore(_cap)

        # 产品查找辅助：product_id → 产品 dict（来自 final_recommendations）
        # 取值走 _match_product_id：template_match.product_id_from 配置优先（支持点路径），
        # 让接口查询结果的任意字段可关联到话术模板的 product_id
        rec_by_pid: Dict[str, Dict[str, Any]] = {}
        for pkg in ctx.final_recommendations:
            pid = self._match_product_id(pkg)
            if pid:
                rec_by_pid[pid] = pkg

        logger.info(
            f"[ScriptStep.batch] 开始批量生成  batch_size={len(batch_contexts)} "
            f"rec_products={len(rec_by_pid)} templates_v2={len(templates_v2)}"
        )

        def _resolve_pkg_for_bc(bc_pid: str) -> Dict[str, Any]:
            """按 batch_contexts.product_id 从推荐列表取产品，与单产品模板匹配规则对齐。

            查找顺序（与话术模板匹配同一套精确→模糊语义）：
              1. offerId 精确命中（rec_by_pid）
              2. 产品候选标识精确命中（offerId / offerName / recommend_package_name）
              3. 产品名关键词模糊命中（广东式：bc_pid="流量" ↔ offerName 含"流量"）
            都找不到时回退占位 ``{"product_id": bc_pid}``，兼容无推荐列表、
            仅靠 batch_contexts.product_id 匹配模板的旧单产品写法。

            命中真实产品后会浅拷贝并写入 ``_batch_product_id_hint=bc_pid``，
            让模板匹配优先用入参 product_id（与单产品「按 batch 指定值匹配」一致），
            避免产品名同时含多个关键词时命中错误模板（如「升169套餐」误命中「套餐」）。
            """
            found = self._find_pkg_for_batch_pid(
                bc_pid, rec_by_pid, ctx.final_recommendations
            )
            # 占位产品（仅 product_id 一键）已等价于单产品写法，无需再挂 hint
            if not found or (
                set(found.keys()) == {"product_id"} and found.get("product_id") == bc_pid
            ):
                return found
            out = dict(found)
            out["_batch_product_id_hint"] = bc_pid
            return out

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
                # 有推荐产品时始终展开 TopN；bc_pid 仅作模板业务类型 hint，不过滤产品。
                # 无推荐产品时：bc_pid 非空走占位/定位；都空则通用模板。
                if ctx.final_recommendations:
                    expand_pkgs = [
                        self._attach_batch_pid_hint(pkg, bc_pid)
                        for pkg in ctx.final_recommendations
                    ]
                elif bc_pid:
                    expand_pkgs = [_resolve_pkg_for_bc(bc_pid)]
                else:
                    expand_pkgs = [{}]

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
                    llm_success = True
                    raw = ""
                    try:
                        raw = await self._generate_llm(
                            prep["prompt"], ctx, "script_step.batch.expand.llm", sem
                        )
                    except Exception as exc:
                        logger.error(
                            f"[ScriptStep.batch.expand] ❌ 产品[{prep.get('package_name', '')}]"
                            f" stage={bc_stage!r} scene={tpl_scene!r} 话术生成失败: {exc}"
                        )
                        llm_success = False
                    text = self._post_process(raw, prep.get("slot_facts"))
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
                    # 取该产品的实际 product_id（template_match 配置优先，回退 offerId 等默认别名）
                    actual_pid = self._match_product_id(pkg_exp) or bc_pid
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

            # 确定要生成话术的产品列表。
            # 关键语义（广东多产品）：
            #   - 有 recommended_packages（final_recommendations 非空）→ 始终对 TopN
            #     产品各生成一条；batch_contexts.product_id **只作业务类型**优先匹配
            #     话术模板（挂到 _batch_product_id_hint），**不用于过滤产品**；
            #     响应 product_id 仍回显 offerId。
            #   - 无推荐产品（旧单产品）→ product_id 非空时按名称/ID 定位或占位生成 1 条。
            if ctx.final_recommendations:
                target_pkgs = [
                    self._attach_batch_pid_hint(pkg, bc_pid)
                    for pkg in ctx.final_recommendations
                ]
            elif bc_pid:
                target_pkgs = [_resolve_pkg_for_bc(bc_pid)]
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
                llm_success = True
                raw = ""
                try:
                    raw = await self._generate_llm(
                        prep["prompt"], ctx, "script_step.batch.llm", sem
                    )
                except Exception as exc:
                    logger.error(
                        f"[ScriptStep.batch] ❌ 产品[{prep.get('package_name', '')}]"
                        f" stage={bc_stage!r} scene={bc_scene!r} 话术生成失败: {exc}"
                    )
                    llm_success = False
                    raw = ""
                text = self._post_process(raw, prep.get("slot_facts"))
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
            # rank 偏移：有推荐产品时按展开数计；无推荐时每条 batch 占 1
            if ctx.final_recommendations:
                rank_counter += len(ctx.final_recommendations)
            else:
                rank_counter += 1

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
        # 产品标识候选：offerId 优先，未命中任何模板时回退产品名（见 _match_product_id_candidates）
        pid_candidates = self._match_product_id_candidates(pkg)
        product_id_hint = pid_candidates[0] if pid_candidates else ""
        # 匹配维度兜底：入参未带 stage/scene 时，可按 template_match 配置从推荐结果字段取
        # （接口查询模式下让查询结果自带的环节/场景字段参与模板匹配）
        if not str(match_stage or "").strip():
            match_stage = str(self._resolve_match_dim(pkg, "stage_from", None) or "").strip()
        if not str(match_scene or "").strip():
            match_scene = str(self._resolve_match_dim(pkg, "scene_from", None) or "").strip()
        logger.info(
            f"[ScriptStep] 模板匹配 product_id={product_id_hint!r} "
            f"stage={match_stage!r} scene={match_scene!r} "
            f"templates_v2_count={len(templates_v2)}"
        )
        matched = None
        for _cand in (pid_candidates or [""]):
            matched = select_template(
                templates_v2,
                ctx.intent,
                str(_cand),
                stage=match_stage,
                scene=match_scene,
                index=tpl_index,
            )
            if matched:
                if _cand != product_id_hint:
                    logger.info(
                        f"[ScriptStep] 产品 ID {product_id_hint!r} 未命中任何模板，"
                        f"改用产品名候选 {_cand!r} 命中（回显 product_id 仍为 {product_id_hint!r}）"
                    )
                break
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
        # 出参 product_id 与模板匹配用同一取值逻辑（template_match 优先），保证回显一致
        product_id = self._resolve_match_dim(pkg, "product_id_from", pid_keys)
        # 出参 offerId：只取产品真实 offerId（不走 product_id 别名链，避免占位产品误填）
        offer_id = str(pkg.get("offerId") or pkg.get("offer_id") or "").strip()
        package_name = self._get_field(pkg, fa.get("pkg_name", _DEFAULT_FIELD_ALIASES["pkg_name"]))

        diff = PackageDiff(ctx.current_package, pkg)

        # 收集【上下文数据】注入的占位符→事实值，供 LLM 后确定性填槽（含子域）
        slot_facts: Dict[str, str] = {}
        # 分段提示词（上下文数据 / 话术模板 / 话术要求 / 其他），供测试页展示
        prompt_parts: Dict[str, str] = {}
        prompt = self._build_prompt(
            user_prompt_tpl=tpl_prompt,
            template_text=tpl_content,
            ctx=ctx,
            pkg=pkg,
            diff=diff,
            linked_vars=tpl_linked_vars or [],
            script_requirement=tpl_script_req,
            slot_facts_out=slot_facts,
            parts_out=prompt_parts,
        )
        return {
            "pkg":             pkg,
            "rank":            rank,
            "diff":            diff,
            "linked_vars":     tpl_linked_vars,
            "user_prompt_tpl": tpl_prompt,
            "product_id":      product_id,
            "offerId":         offer_id,
            "package_name":    package_name,
            "prompt":          prompt,
            "prompt_parts":    prompt_parts,
            "slot_facts":      slot_facts,
            "match_stage":     match_stage,
            "match_scene":     match_scene,
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
        offer_id = prep.get("offerId") or ""
        package_name = prep["package_name"]
        fa = self.field_aliases
        fee = self._get_field(pkg, fa.get("pkg_fee", _DEFAULT_FIELD_ALIASES["pkg_fee"]))
        flow = self._get_field(pkg, fa.get("pkg_flow", _DEFAULT_FIELD_ALIASES["pkg_flow"]))
        include_table = "table" in linked_vars or "{table}" in user_prompt_tpl
        # 挂到结果上的提示词分段（测试页读取；生产调用方可忽略）
        parts = prep.get("prompt_parts") or {}
        llm_prompt = {
            "product_id": product_id,
            "offerId": offer_id,
            "rank": rank,
            "package_name": package_name or "",
            "stage": prep.get("match_stage") or "",
            "scence": prep.get("match_scene") or "",
            "context_data": parts.get("context_data") or "",
            "template": parts.get("template") or "",
            "script_requirement": parts.get("script_requirement") or "",
            "other": parts.get("other") or "",
            "full": parts.get("full") or prep.get("prompt") or "",
            # 模板引用但本次无事实的槽位（测试页高亮，直接指向缺数据的映射域）
            "missing_facts": parts.get("missing_facts") or "",
            "missing_slots": list(parts.get("missing_slots") or []),
            # {token: 提示}——父域有数据但叶子子键失配（映射键名≠模板子键）的排障线索
            "missing_slot_hints": dict(parts.get("missing_slot_hints") or {}),
        }
        result: dict = {
            "product_id":     product_id,
            "offerId":        offer_id,
            "rank":           rank,
            "marketing_text": text,
            "package_name":   package_name,
            "monthly_fee":    fee or 0,
            "data_quota":     flow or "",
            "_llm_success":   llm_success,
            "_llm_prompt":    llm_prompt,
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
        slot_facts_out: Optional[Dict[str, str]] = None,
        parts_out: Optional[Dict[str, str]] = None,
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
            slot_facts_out=slot_facts_out,
            parts_out=parts_out,
        )

    # ── 话术后处理 ────────────────────────────────────────────────

    @staticmethod
    def _post_process(
        text: str,
        slot_facts: Optional[Dict[str, str]] = None,
    ) -> str:
        """对 LLM 输出做最终整形（Markdown/前缀清洗已在 llm_service._clean_llm_output 完成）

        顺序：多段落合并 → 去首尾引号 → 确定性填槽（有映射事实则强制替换，含子域）
        → 残留占位符清理（无事实的槽）→ 无中文兜底。
        """
        text = (text or "").strip()
        if not text:
            return ""
        if "\n" in text:
            parts = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
            text = " ".join(parts)
        text = text.strip().strip('"""\'')
        # 1) 有映射事实的占位符（含 {域[子键]}）确定性填入，不依赖 LLM
        text = _apply_slot_facts(text, slot_facts)
        # 2) 仍残留的占位符（映射域运行态为空）整句清理，避免花括号露给客户
        text = _strip_residual_placeholders(text)
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

    @staticmethod
    def _get_path_value(d: Any, path: str) -> Any:
        """按点路径从嵌套 dict/list 取值（如 "productInfo.offerId"、"list.0.id"）；取不到返回 None。"""
        cur = d
        for part in str(path).split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            elif isinstance(cur, list):
                try:
                    cur = cur[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
            if cur is None:
                return None
        return cur

    def _resolve_match_dim(
        self,
        pkg: Dict[str, Any],
        cfg_key: str,
        fallback_keys: Optional[List[str]],
    ) -> Any:
        """模板匹配维度取值：template_match 配置优先，回退 field_aliases/默认别名。

        配置值支持：单字段名 / 点路径 / 逗号分隔多候选 / 字符串数组，
        按顺序取第一个非空值。fallback_keys 为 None 表示无配置时返回空串（stage/scene 用）。
        """
        spec = (self._match_cfg or {}).get(cfg_key)
        if spec:
            paths = spec if isinstance(spec, list) else re.split(r"[,，]", str(spec))
            for p in paths:
                p = str(p).strip()
                if not p:
                    continue
                v = self._get_path_value(pkg, p)
                if v not in (None, "", [], {}):
                    return v
        if fallback_keys is None:
            return ""
        return self._get_field(pkg, fallback_keys)

    def _match_product_id(self, pkg: Dict[str, Any]) -> str:
        """取推荐结果条目中用于匹配话术模板 product_id 的值（统一入口）。"""
        pid_keys = self.field_aliases.get("product_id", _DEFAULT_FIELD_ALIASES["product_id"])
        return str(self._resolve_match_dim(pkg, "product_id_from", pid_keys) or "").strip()

    @staticmethod
    def _attach_batch_pid_hint(pkg: Dict[str, Any], bc_pid: str) -> Dict[str, Any]:
        """把 batch_contexts.product_id 挂到产品上，仅作模板业务类型匹配 hint。

        不影响响应回显的 offerId；bc_pid 为空时原样返回（避免无谓拷贝）。
        """
        key = str(bc_pid or "").strip()
        if not key or not isinstance(pkg, dict):
            return pkg
        out = dict(pkg)
        out["_batch_product_id_hint"] = key
        return out

    def _find_pkg_for_batch_pid(
        self,
        bc_pid: str,
        rec_by_pid: Dict[str, Dict[str, Any]],
        recommendations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """按 batch_contexts.product_id 定位推荐产品（与单产品模板匹配语义对齐）。

        查找顺序：
          1. offerId 精确（rec_by_pid）
          2. 产品候选标识精确（offerId / offerName / recommend_package_name）
          3. 关键词模糊（bc_pid="流量" ↔ offerName 含"流量"；与 fuzzy_match_pid 一致）
        都未命中 → 占位 ``{"product_id": bc_pid}``，兼容无推荐列表的旧单产品写法。
        """
        key = str(bc_pid or "").strip()
        if not key:
            return {}
        hit = rec_by_pid.get(key)
        if hit:
            return hit

        pkgs = list(recommendations or [])
        # 2. 精确：bc_pid 等于任一候选标识
        for pkg in pkgs:
            for cand in self._match_product_id_candidates(pkg):
                if cand == key:
                    logger.info(
                        f"[ScriptStep.batch] batch product_id={key!r} "
                        f"精确命中推荐产品 offerId={self._match_product_id(pkg)!r}"
                    )
                    return pkg
        # 3. 模糊：bc_pid 与产品名/ID 互相包含（广东关键词场景）
        for pkg in pkgs:
            for cand in self._match_product_id_candidates(pkg):
                if fuzzy_match_pid(key, cand) or fuzzy_match_pid(cand, key):
                    logger.info(
                        f"[ScriptStep.batch] batch product_id={key!r} "
                        f"模糊命中推荐产品 cand={cand!r} "
                        f"offerId={self._match_product_id(pkg)!r}"
                    )
                    return pkg

        logger.info(
            f"[ScriptStep.batch] batch product_id={key!r} 未命中推荐列表，"
            "使用占位产品（按入参 product_id 匹配模板）"
        )
        return {"product_id": key}

    def _match_product_id_candidates(self, pkg: Dict[str, Any]) -> List[str]:
        """模板匹配用的产品标识**候选序列**（按序尝试，第一个命中模板的胜出）。

        背景：产品 ID 与话术模板的 product_id 未必同形。广东按「产品名关键词」配模板
        （product_id="流量"/"套餐"/"升"），而下游推荐结果给的是纯数字 offerId
        （2026060810324218501011930）——用 offerId 匹配全部落空，只能退化成把整包 JSON
        丢给大模型，话术质量不可控。此时产品名 offerName（【广州】【纯裸升】升169套餐-2606）
        恰好能模糊命中关键词模板。

        候选顺序：显式配置 product_id_from → offerId 类别名 → 产品名类别名 →
        recommend_package_name。

        安全边界（保证不改变既有省份行为）：
        - 只有**前序候选一个模板都没命中**时才会尝试后续候选；配了通用兜底模板
          （product_id 为空）的省份第一候选必命中，行为完全不变；
        - 回显给调用方的 product_id 仍走 _match_product_id（offerId），不受本候选序列影响；
        - 可用 biz_config.template_match.disable_name_fallback=true 关闭名称回退。
        """
        cands: List[str] = []

        def _add(v: Any) -> None:
            s = str(v or "").strip()
            if s and s not in cands:
                cands.append(s)

        # batch_contexts.product_id 指定值优先（与单产品「按入参 product_id 匹配」对齐）
        _add(pkg.get("_batch_product_id_hint"))
        _add(self._match_product_id(pkg))
        if not (self._match_cfg or {}).get("disable_name_fallback"):
            name_keys = self.field_aliases.get("pkg_name", _DEFAULT_FIELD_ALIASES["pkg_name"])
            _add(self._get_field(pkg, name_keys))
            _add(pkg.get("recommend_package_name"))
        return cands

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

    #: 标记型标签的假值（整条丢弃）与真值（只报标签名，不报值）
    _TAG_FALSE_VALUES = frozenset(
        {"", "否", "0", "False", "false", "No", "no", "null", "None"})
    _TAG_TRUE_VALUES = frozenset({"1", "是", "True", "true", "Yes", "yes", "Y", "y"})

    @classmethod
    def _fmt_tags(cls, tags: Dict[str, Any]) -> str:
        """用户标签格式化（省份无关）。

        标记型标签（值为 0/1、是/否）只输出标签名——「高频高额超套客户」本身即事实，
        带上 "=1" 反而会被模型念进话术。其余标签输出 ``键:值``：各省 tags 里常混有
        带数值的业务字段（北京把「实际近6月平均消费（元）」这类用量数据也放在 tags 中），
        只报键名会把唯一的真实数字丢掉，模型转而拿套餐月费顶替，正是月均消费填错的来源。
        """
        if not isinstance(tags, dict):
            return ""
        parts: List[str] = []
        for k, v in tags.items():
            if isinstance(v, (dict, list)):
                if not v:
                    continue
                parts.append(f"{k}:{json.dumps(v, ensure_ascii=False)}")
                continue
            s = str(v).strip()
            if s in cls._TAG_FALSE_VALUES:
                continue
            parts.append(str(k) if s in cls._TAG_TRUE_VALUES else f"{k}:{s}")
        return "、".join(parts)
