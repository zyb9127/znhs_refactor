"""
交叉营销「营销助手统一接口」路由

  POST /marketing/preload  — 灵运前置下发报文 → 生成营销话术 → 回调网关缓存

与标准接口（``/marketing/recommend``）的差异只有两点，业务链路完全复用：
  1. **入参形状**：按《灵运平台交叉营销接口规范》的
     ``{"params": {"systemId","optType","inputs":{...}}}``，由
     :mod:`utils.marketing_assistant` 归一为标准请求体；
  2. **响应语义**：异步——本接口只回「数据接收成功」，话术生成完成后回调
     ``preload/cache`` 写 Redis（key ``preload:{phoneNo}:{callId}:hs``），
     下游用交叉营销结果获取接口取。

标记该模式的地方是**技能包接口节点**（直传透传节点上 ``request_variant=marketing_assistant``，
由运营在接口配置页选择「营销助手统一接口」），因此新增本模式不改动任何标准接口配置。

一次报文的处理编排（本模块职责）：
  ① 营销标志过滤：``marketingProductFlag`` / ``marketingActivityFlag`` 任一为否值的
     产品不生成话术；
  ② 场景路由：按 ``activityTypeName``（活动名称）分组，各组匹配**同名意图**的技能包；
     整批都没匹配上时回退到该省份唯一标记营销助手的技能包 / 全局兜底意图；
  ③ 环节展开：每组按技能包实际配了模板的环节生成切入话术（必出）与挽留话术（可选）；
  ④ 结果合并：各组话术按入参产品顺序归并成「一个产品一项」，**一次性**回调网关。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, List, Set, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger

from services.cross_sell_callback import (
    default_intent,
    is_enabled,
    pitch_stage_candidates,
    push_cache,
    recommend_stage_candidates,
    retention_stage_candidates,
)
from utils import province_logger
from utils.marketing_assistant import (
    ACTIVITY_NAME_FIELD,
    IDENTIFIER_SCRIPT,
    MarketingAssistantRequest,
    PRODUCT_LIST_FIELD,
    REQUEST_VARIANT_KEY,
    VARIANT_MARKETING_ASSISTANT,
    build_callback_value,
    group_products_by_activity,
    parse,
    product_label,
    resolve_product_list_field,
    split_marketable,
    to_recommend_body,
)
from utils.observability import (
    begin_request_context,
    reset_request_context,
    summarize_request_context,
)
from utils.province_code import resolve_province
from utils.skill_runtime import skill_registry

router = APIRouter(tags=["交叉营销"])

# 异步任务强引用集合：create_task 的返回值若不持有，任务可能被 GC 提前回收
_PENDING: Set[asyncio.Task] = set()

_ACK_OK = {"rtnMsg": "数据接收成功！", "rtnCode": "0"}


def _ack_fail(reason: str) -> Dict[str, str]:
    """网关约定的失败 ack（rtnCode 9999）。

    响应体严格只含 rtnMsg / rtnCode（对端按规范解析），失败原因写日志不外泄。
    """
    logger.warning(f"[preload] 拒收: {reason}")
    return {"rtnMsg": "数据接收失败！", "rtnCode": "9999"}


def resolve_marketing_assistant_intent(province: str) -> Tuple[str, str]:
    """找出该省份下标记为「营销助手统一接口」的技能包意图。

    判定依据：技能包 api_nodes 中存在启用的直传节点且
    ``request_variant == marketing_assistant``。这样运营在接口配置页一勾选即生效，
    无需在全局配置里再维护一张省份→意图表。

    Returns:
        (intent, 说明)。找不到时 intent 为空串，说明用于日志。
    """
    hits = []
    for item in skill_registry.list_all():
        if item.get("province") != province or item.get("enabled") is False:
            continue
        intent = str(item.get("intent") or "")
        pkg = skill_registry.get(province, intent)
        nodes = (pkg.config.get("api_nodes") or {}) if pkg is not None else {}
        for name, cfg in (nodes or {}).items():
            if str(name).startswith("_") or not isinstance(cfg, dict):
                continue
            if cfg.get("enabled") is False or cfg.get("source_type") != "direct":
                continue
            if cfg.get(REQUEST_VARIANT_KEY) == VARIANT_MARKETING_ASSISTANT:
                hits.append(intent)
                break

    if len(hits) == 1:
        return hits[0], "接口配置标记"
    if len(hits) > 1:
        chosen = sorted(hits)[0]
        logger.warning(
            f"[preload] 省份 {province} 有多个技能包标记为营销助手统一接口 {sorted(hits)}，"
            f"按字典序取 {chosen}；建议只保留一个"
        )
        return chosen, "接口配置标记（多个，取首个）"

    fallback = default_intent()
    if fallback and skill_registry.get(province, fallback) is not None:
        return fallback, "全局兜底意图 cross_sell.default_intent"
    return "", (
        f"省份 {province} 下没有任何技能包的直传节点标记 "
        f"{REQUEST_VARIANT_KEY}={VARIANT_MARKETING_ASSISTANT}"
        + (f"，兜底意图 {fallback!r} 也不存在" if fallback else "，且未配置兜底意图")
    )


def _has_marketing_assistant_node(pkg: Any) -> bool:
    """技能包是否有启用的「营销助手统一接口」直传节点。"""
    nodes = (pkg.config.get("api_nodes") or {}) if pkg is not None else {}
    for name, cfg in (nodes or {}).items():
        if str(name).startswith("_") or not isinstance(cfg, dict):
            continue
        if cfg.get("enabled") is False or cfg.get("source_type") != "direct":
            continue
        if cfg.get(REQUEST_VARIANT_KEY) == VARIANT_MARKETING_ASSISTANT:
            return True
    return False


def match_intent_by_activity(province: str, activity_name: str) -> str:
    """活动名称 → **同名意图**的技能包（要求该技能包有启用的营销助手直传节点）。

    运营按活动名称建技能包即可生效，不需要额外维护映射表。取不到返回空串。
    """
    name = str(activity_name or "").strip()
    if not name:
        return ""
    pkg = skill_registry.get(province, name)
    if pkg is None or getattr(pkg, "enabled", True) is False:
        return ""
    if _has_marketing_assistant_node(pkg):
        return name
    logger.warning(
        f"[preload] 省份 {province} 存在意图 {name!r} 与活动名称同名，但其接口节点未勾选"
        f"「营销助手统一接口」，本次不用它（勾选后即可按活动名称精确路由）"
    )
    return ""


def resolve_intent_for_activity(
    province: str,
    activity_name: str,
    *,
    allow_fallback: bool = True,
) -> Tuple[str, str]:
    """场景 skill 匹配：省份 + 活动名称（``activityTypeName``）→ 技能包意图。

    1. 意图名精确等于活动名称 → 用它（:func:`match_intent_by_activity`）；
    2. ``allow_fallback`` 时回退 :func:`resolve_marketing_assistant_intent`（该省份唯一
       标记营销助手的技能包 → 全局兜底意图 ``cross_sell.default_intent``），兼容意图名与
       活动名称不同名、或对端没下发活动名称的存量配置。

    回退的开关由调用方按整批报文判定：只要本批里**有任何活动名称匹配到了同名技能包**，
    说明该省份确实是按活动名称建的技能包，此时对没匹配上的活动就不再回退——否则会拿
    另一个活动的话术模板生成内容，比不出话术更糟。

    Returns:
        (intent, 说明)。找不到时 intent 为空串，说明用于日志。
    """
    name = str(activity_name or "").strip()
    hit = match_intent_by_activity(province, name)
    if hit:
        return hit, f"活动名称精确匹配意图名 {hit!r}"
    if not allow_fallback:
        return "", f"活动名称 {name or '-'} 无同名技能包（本批已有按活动名称匹配的技能包，不回退）"
    intent, note = resolve_marketing_assistant_intent(province)
    if intent:
        return intent, f"活动名称 {name or '-'} 无同名技能包，回退：{note}"
    return "", note


def resolve_stage_names(province: str, intent: str) -> Tuple[List[str], str, str, str]:
    """读取技能包模板里存在的环节集合，并按 config 候选名命中 推荐 / 切入 / 挽留。

    返回 ``(available_stages, recommend, pitch, retention)``：
    - ``available_stages``：该技能包 ``script_templates_v2`` 里实际出现的环节名（去空去重、排序，
      跳过 ``status=deleted``）；
    - ``recommend`` / ``pitch`` / ``retention``：各角色命中的环节名（候选里第一个真实存在的），
      未命中为空串。切入/挽留未命中即不生成对应话术（见 :func:`build_batch_contexts`）。

    供 :func:`build_batch_contexts` 与配置测试页诊断共用同一判定，保证「测试页看到的环节识别
    结果」与「线上真正会生成的环节」一致。
    """
    pkg = skill_registry.get(province, intent)
    biz = (pkg.config.get("biz_config") or {}) if pkg is not None else {}
    stageset = {
        str(t.get("stage") or "").strip()
        for t in (biz.get("script_templates_v2") or [])
        if isinstance(t, dict) and t.get("status") != "deleted"
    }

    def _pick(candidates: List[str]) -> str:
        return next((c for c in candidates if c and c in stageset), "")

    return (
        sorted(stageset),
        _pick(recommend_stage_candidates()),
        _pick(pitch_stage_candidates()),
        _pick(retention_stage_candidates()),
    )


def build_batch_contexts(province: str, intent: str) -> Tuple[List[Dict[str, Any]], str, str]:
    """生成 batch_contexts：推荐话术（必出）+ 切入 / 挽留话术（配了模板才多生成）。

    一个产品若配了「切入、推荐、挽留」三行环节模板，三条话术各自生成后归并成一条产品
    话术，分别对应回调的三个字段（三条话术相互独立）：

    - **营销推荐话术**（``words``）：**始终生成**。若技能包配了「推荐」环节模板，则以该
      环节（``stage=推荐``）匹配生成；否则回退到无环节（``stage=""``）的常规模板匹配。
      技能包只配普通话术模板时，只回 words；
    - **切入话术**（``aiPitchMarketingDesc``）：仅当技能包配了「切入」环节模板才多生成一条；
    - **挽留话术**（``aiRetentionMarketingDesc``）：仅当配了「挽留」环节模板才多生成一条。

    为什么要看模板存在性：模板匹配在环节维度会逐级降级（产品+环节 → 仅产品 → 通用），
    技能包没配某环节模板时传 ``stage=该环节`` 会命中推荐/通用模板，产出与 words 雷同的内容
    并填进 ai* 字段，等于让下游重复播报。故没配就不生成，对应字段留空。

    环节名候选来自 ``config.json → cross_sell.recommend_stage / pitch_stage / retention_stage``
    （可配单个字符串或候选列表，取第一个在模板里真实存在的），因为各省环节命名不统一
    （"切入" / "切入环节" / "个人市场"…）。

    Returns:
        (batch_contexts, 切入环节名, 挽留环节名)。三个环节都没配模板时返回 ``([], "", "")``——
        与既有行为完全一致（pipeline 自动构造一条无环节条目 → words，每个产品一条话术）。
    """
    _stages, recommend, pitch, retention = resolve_stage_names(province, intent)
    if not recommend and not pitch and not retention:
        # 三个环节都没配：走既有的「自动构造一条空条目」路径，byte 级等价，只出 words
        return [], "", ""
    # 推荐话术条目（words）：配了「推荐」环节走该环节模板，否则无环节常规匹配
    contexts: List[Dict[str, Any]] = [{"stage": recommend}]
    if pitch:
        contexts.append({"stage": pitch})
    if retention:
        contexts.append({"stage": retention})
    return contexts, pitch, retention


def inspect_direct_nodes(province: str, intent: str) -> List[Dict[str, Any]]:
    """逐个直传节点体检「产品列表能否进标准域」的三个必要条件（测试页排障用）。

    产品列表进不了标准域 ``recommended_packages`` 时，话术会用「空产品」匹配模板，
    填了产品 ID 的模板一律落空。三个条件（见 :mod:`steps.data_step` 直传透传分支）：
    映射方式=直接透传字段、接口规范=营销助手统一接口、透传字段包含 ``products``。
    """
    pkg = skill_registry.get(province, intent)
    nodes = (pkg.config.get("api_nodes") or {}) if pkg is not None else {}
    out: List[Dict[str, Any]] = []
    for name, cfg in nodes.items():
        if not isinstance(cfg, dict) or cfg.get("source_type") != "direct":
            continue
        if cfg.get("enabled") is False:
            continue
        fields = cfg.get("passthrough_fields") or []
        out.append({
            "name": name,
            "direct_mode": str(cfg.get("direct_mode") or ""),
            "direct_mode_ok": cfg.get("direct_mode") == "passthrough",
            "request_variant": str(cfg.get(REQUEST_VARIANT_KEY) or ""),
            "product_list_field": resolve_product_list_field(cfg),
            "variant_ok": bool(resolve_product_list_field(cfg)),
            # passthrough_fields 为空 = 全部顶层字段透传，products 自然包含在内
            "products_field_ok": (not fields) or any(
                str(f).split(".", 1)[0] == PRODUCT_LIST_FIELD for f in fields
            ),
        })
    return out


async def run_skill_generation_sync(
    req: MarketingAssistantRequest,
    province: str,
    intent: str,
    trace_id: str,
) -> Dict[str, Any]:
    """**同步**跑单个技能包的营销助手话术生成，返回回调 value 与排障信息（配置测试页用）。

    与线上 :func:`_generate_and_callback` 的区别：
    - **技能包范围固定**为传入的 province/intent（不按 ``activityTypeName`` 跨技能包路由），
      让运营在「测试智能话术配置」里测的就是当前这个技能包的模板；
    - **不回调网关**，直接把 :func:`build_callback_value` 的结果同步返回，便于页面即时展示；
    - 仍复用同一套「营销标志过滤 → 环节展开（推荐/切入/挽留）→ 逐产品并发生成 → 归并」链路，
      保证测试所见即线上所得。
    """
    marketable, skipped = split_marketable(req.products)
    executor = skill_registry.get_executor(province, intent)
    if executor is None:
        raise ValueError(f"技能包不存在或未加载: {province}:{intent}")
    available_stages, recommend_hit, _pitch_hit, _ret_hit = resolve_stage_names(province, intent)
    batch_contexts, pitch_stage, retention_stage = build_batch_contexts(province, intent)
    body = to_recommend_body(
        req, intent=intent, province=province,
        products=marketable, batch_contexts=batch_contexts,
    )
    result = await executor.execute(body)
    recommend_results = result.get("recommend_results") or []
    llm_prompts = result.get("llm_prompts") or []
    value = build_callback_value(
        req, recommend_results,
        pitch_stage=pitch_stage, retention_stage=retention_stage,
    )
    return {
        "value": value,
        "recommend_results": recommend_results,
        "llm_prompts": llm_prompts,
        "blocked_products": [product_label(p) for p in skipped],
        "batch_contexts": batch_contexts,
        "pitch_stage": pitch_stage,
        "retention_stage": retention_stage,
        # 产品/模板命中诊断：推荐产品数为 0 时话术会用「空产品」匹配模板，
        # 填了产品 ID 的模板一律不命中（只匹配产品 ID 为空的通用模板），
        # 且结果不回显 product_id，回调只能按位次兜底 —— 这里直接把这条链路暴露给测试页。
        "product_diagnostics": {
            "input_products": len(req.products),
            "marketable_products": len(marketable),
            # 直传节点体检：产品列表进不了标准域时，直接点名是哪个配置项没配
            "direct_nodes": inspect_direct_nodes(province, intent),
            "recommendation_count": int(
                (result.get("metadata") or {}).get("recommendation_count") or 0
            ),
            "scripts": [
                {
                    "stage": str(p.get("stage") or ""),
                    "product_id": str(p.get("product_id") or ""),
                    "template_matched": bool(str(p.get("template") or "").strip()),
                }
                for p in llm_prompts
                if isinstance(p, dict)
            ],
        },
        # 环节识别诊断：让测试页直观看到「切入/挽留 为空」是没配环节、环节名不匹配、还是模板缺失
        "stage_diagnostics": {
            "available_stages": available_stages,
            "recommend": {"hit": recommend_hit, "candidates": recommend_stage_candidates()},
            "pitch": {"hit": pitch_stage, "candidates": pitch_stage_candidates()},
            "retention": {"hit": retention_stage, "candidates": retention_stage_candidates()},
        },
    }


async def _run_group(
    req: MarketingAssistantRequest,
    province: str,
    activity_name: str,
    products: List[Dict[str, Any]],
    trace_id: str,
    allow_fallback: bool = True,
) -> Tuple[List[Dict[str, Any]], str, str]:
    """一个活动名称分组：路由技能包 → 生成话术。返回 (话术结果, 切入环节名, 挽留环节名)。"""
    intent, note = resolve_intent_for_activity(
        province, activity_name, allow_fallback=allow_fallback,
    )
    if not intent:
        logger.error(
            f"[preload] 活动 {activity_name or '-'!r}（{len(products)} 个产品）无法确定技能包，"
            f"已跳过  {note}  trace_id={trace_id}"
        )
        return [], "", ""
    executor = skill_registry.get_executor(province, intent)
    if executor is None:
        logger.error(f"[preload] 技能包不存在: {province}:{intent}  trace_id={trace_id}")
        return [], "", ""

    batch_contexts, pitch_stage, retention_stage = build_batch_contexts(province, intent)
    logger.info(
        f"[preload] 活动 {activity_name or '-'} → 技能包 {province}:{intent}（{note}）"
        f" 产品={len(products)} 环节={[c.get('stage') or '-' for c in batch_contexts] or ['-']}"
        f"  trace_id={trace_id}"
    )
    body = to_recommend_body(
        req, intent=intent, province=province,
        products=products, batch_contexts=batch_contexts,
    )
    province_logger.log_request(province, intent, trace_id, req.phone, body)
    t0 = time.perf_counter()
    result = await executor.execute(body)
    recommend_results = result.get("recommend_results") or []
    elapsed = (time.perf_counter() - t0) * 1000
    province_logger.log_response(
        province, intent, trace_id, req.phone,
        code=200, elapsed_ms=elapsed,
        recommend_results=recommend_results,
        other_info=result.get("other_info"),
        metadata=summarize_request_context(),
        llm_prompts=result.get("llm_prompts") or [],
    )
    return recommend_results, pitch_stage, retention_stage


async def _generate_and_callback(
    req: MarketingAssistantRequest,
    province: str,
    trace_id: str,
) -> None:
    """后台任务：按活动分组跑话术生成 → 合并结果一次性回调。异常只落日志，不外抛。"""
    t0 = time.perf_counter()
    obs_token = begin_request_context(
        trace_id=trace_id, route="cross-sell-preload",
        province=province, intent="",
    )
    try:
        # ① 营销标志过滤：两个 flag 任一为否值的产品不生成话术
        marketable, skipped = split_marketable(req.products)
        if skipped:
            logger.info(
                f"[preload] 营销标志过滤：跳过 {len(skipped)} 个产品 "
                f"{[product_label(p) for p in skipped]}（marketingProductFlag / "
                f"marketingActivityFlag 未同时为 1）  trace_id={trace_id}"
            )

        # ② 场景路由：按活动名称分组，各组各自匹配技能包
        groups = group_products_by_activity(marketable)
        if not groups:
            logger.warning(
                f"[preload] 无可营销产品，回调空结果  trace_id={trace_id}"
            )
        else:
            logger.info(
                f"[preload] 按 {ACTIVITY_NAME_FIELD} 分 {len(groups)} 组："
                f"{ {name or '-': len(items) for name, items in groups} }  trace_id={trace_id}"
            )

        # 本批只要有活动名称匹配到同名技能包，就按活动名称严格路由（不给其他活动回退）
        allow_fallback = not any(
            match_intent_by_activity(province, name) for name, _items in groups
        )

        # ③ 各组并发生成（总条数受产品数约束，分组不会放大 LLM 压力）
        outcomes = await asyncio.gather(*[
            _run_group(req, province, name, items, trace_id, allow_fallback)
            for name, items in groups
        ], return_exceptions=True)

        recommend_results: List[Dict[str, Any]] = []
        pitch_stage = ""
        retention_stage = ""
        for (name, items), outcome in zip(groups, outcomes):
            if isinstance(outcome, Exception):
                logger.error(
                    f"[preload] ❌ 活动 {name or '-'} 生成失败，其余分组继续: {outcome}"
                    f"  trace_id={trace_id}"
                )
                continue
            group_results, group_pitch, group_retention = outcome
            recommend_results.extend(group_results)
            pitch_stage = pitch_stage or group_pitch
            retention_stage = retention_stage or group_retention

        # ④ 合并回调：一个产品一项，推荐/切入/挽留话术各归其位
        elapsed = (time.perf_counter() - t0) * 1000
        value = build_callback_value(
            req, recommend_results,
            pitch_stage=pitch_stage, retention_stage=retention_stage,
        )
        result_items = value.get("result") or []
        # 话术角色分布：便于排查"下游某字段为空"是模板没配还是生成失败
        words_n = sum(1 for r in result_items if str(r.get("words") or "").strip())
        pitch_n = sum(1 for r in result_items if str(r.get("aiPitchMarketingDesc") or "").strip())
        ret_n = sum(1 for r in result_items if str(r.get("aiRetentionMarketingDesc") or "").strip())
        redis_key = f"preload:{req.phone}:{req.touch_id}:{IDENTIFIER_SCRIPT}"

        if not result_items:
            # 生成完成但无可回调话术：仍会向 Redis 写空 result，下游取不到话术，需醒目告警
            logger.warning(
                f"[preload] ⚠ 生成完成但无可回调话术（将向 Redis 写入空 result，下游取不到话术）"
                f"  trace_id={trace_id} key={redis_key} elapsed={elapsed:.0f}ms "
                f"products={len(req.products)} 可营销={len(marketable)} scripts={len(recommend_results)}"
            )
        else:
            logger.info(
                f"[preload] 话术生成完成  trace_id={trace_id} elapsed={elapsed:.0f}ms "
                f"products={len(req.products)} 可营销={len(marketable)} "
                f"scripts={len(recommend_results)} 回调项={len(result_items)}"
                f"（words={words_n} 切入={pitch_n} 挽留={ret_n}） key={redis_key}"
            )

        ok = await push_cache(
            touch_number=req.touch_id,
            phone=req.phone,
            value=value,
            identifier=IDENTIFIER_SCRIPT,
            trace_id=trace_id,
        )
        total = (time.perf_counter() - t0) * 1000
        logger.info(
            f"[preload] ■ 链路结束  trace_id={trace_id} 回调Redis={'成功' if ok else '失败'} "
            f"回调项={len(result_items)} 总耗时={total:.0f}ms key={redis_key}"
        )
    except Exception as exc:  # noqa: BLE001 - 后台任务必须自吞异常
        elapsed = (time.perf_counter() - t0) * 1000
        logger.exception(f"[preload] ❌ 后台生成异常  trace_id={trace_id} elapsed={elapsed:.0f}ms")
        try:
            province_logger.log_response(
                province, "", trace_id, req.phone,
                code=500, elapsed_ms=elapsed,
                metadata=summarize_request_context(), error=str(exc),
            )
        except Exception:
            pass
    finally:
        reset_request_context(obs_token)


def _has_any_route(province: str, products: List[Dict[str, Any]]) -> bool:
    """ack 前的可路由性预检：该省份至少有一个分组能落到技能包。

    只做注册表查询、不打业务日志（避免与后台任务重复告警）；预检通过后由后台任务
    逐组精确路由，个别活动名称无技能包时只跳过该组，不影响其他产品出话术。
    """
    if resolve_marketing_assistant_intent(province)[0]:
        return True
    for name, _items in group_products_by_activity(products):
        if not name:
            continue
        pkg = skill_registry.get(province, name)
        if (pkg is not None and getattr(pkg, "enabled", True) is not False
                and _has_marketing_assistant_node(pkg)):
            return True
    return False


def _spawn(coro) -> None:
    """派发后台任务并持有强引用，避免被 GC 回收。"""
    task = asyncio.create_task(coro)
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


async def handle_marketing_assistant_payload(raw: Any) -> JSONResponse:
    """营销助手报文统一处理入口（``/marketing/preload`` 与标准接口自动识别共用）。

    立即返回 ack，话术生成与回调在后台完成。返回 200 + rtnCode 表达成败，
    不用 HTTP 4xx/5xx：对端只关心「是否收到数据」。
    """
    req = parse(raw)
    if req is None:
        logger.warning("[preload] 入参不符合营销助手统一接口规范（缺 params.inputs）")
        return JSONResponse(_ack_fail("入参不符合营销助手统一接口规范"), status_code=200)

    trace_id = req.cache_call_id or f"xs-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
    province = resolve_province(req.province_code)
    logger.info(
        f"[preload] ▶ 收到营销助手报文  trace_id={trace_id} systemId={req.system_id!r} "
        f"optType={','.join(req.opt_types) or '-'} provinceCode={req.province_code!r}→{province!r} "
        f"phone={req.phone[:3]}**** products={len(req.products)}"
    )

    if not is_enabled():
        logger.warning(f"[preload] 营销助手统一接口已关闭（cross_sell.enabled=false）  trace_id={trace_id}")
        return JSONResponse(_ack_fail("营销助手统一接口未启用"), status_code=200)
    if not req.phone:
        return JSONResponse(_ack_fail("servNumber 为空"), status_code=200)
    if not province:
        return JSONResponse(_ack_fail("provinceCode 为空或无法识别"), status_code=200)
    if not req.touch_id:
        # touchNumber/callId/sequenceNo 都没有则拼不出 Redis key，结果无人可取，直接拒收
        return JSONResponse(_ack_fail("touchNumber、callId 与 sequenceNo 均为空，无法回写结果缓存"), status_code=200)

    if not req.wants_script:
        logger.info(
            f"[preload] optType={','.join(req.opt_types)} 不含 0（营销话术），本期不处理，"
            f"仅回收妥  trace_id={trace_id}"
        )
        return JSONResponse(_ACK_OK, status_code=200)

    if not _has_any_route(province, req.products):
        logger.error(
            f"[preload] 省份 {province} 没有任何可路由的营销助手技能包（活动名称"
            f"{[name or '-' for name, _ in group_products_by_activity(req.products)]} 均无同名意图，"
            f"也没有勾选营销助手的技能包/兜底意图），已丢弃  trace_id={trace_id}"
        )
        return JSONResponse(_ack_fail("该省份未配置营销助手统一接口技能包"), status_code=200)

    # 具体路由在后台按活动名称分组逐组决定（同一报文可能跨多个技能包）
    _spawn(_generate_and_callback(req, province, trace_id))
    return JSONResponse(_ACK_OK, status_code=200)


@router.post("/marketing/preload")
async def preload(request: Request):
    """营销助手统一接口（异步）：接收灵运前置报文，ack 后台生成话术并回调网关缓存。

    入参：《灵运平台交叉营销接口规范》一、交叉营销数据来源接口
          （``{"params":{"systemId","optType","inputs":{...}}}``）。
    出参：``{"rtnMsg":"数据接收成功！","rtnCode":"0"}``；失败 rtnCode=9999。
    结果：话术写入 ``preload:{phoneNo}:{callId}:hs``，由结果获取接口读取。
    """
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    return await handle_marketing_assistant_payload(raw)
