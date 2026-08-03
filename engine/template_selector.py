"""
template_selector — 话术模板选择（从 steps/script_step.py 迁移的模板匹配逻辑）

迁移来源（oracle 对照：_refactor_backup/2026-07-03/steps__script_step.py）：
- select_templates_expand  ← ScriptStep._select_templates_expand（原 L513-570，原样迁移）
- select_template_linear   ← ScriptStep._select_template（原 L572-765，原样迁移，保留为回退实现+测试基准）
- fuzzy_match_pid          ← _select_template 内嵌 _fuzzy_match_pid（原 L645-668，原样迁移为模块级函数）

新增：
- select_template          统一入口：index 非 None 走 TemplateIndex.select（唯一主路径），
                           否则回退 select_template_linear（旧线性扫描）
- build_selector_index     包装 engine.template_index.build_index；
                           env ZNHS_TEMPLATE_INDEX=="0" 或模板数 <30 时返回 None

注意：迁移函数逐行等价，禁止顺手优化——日志文案（含 [ScriptStep] 前缀）保持原样，
下游有等价性对照测试（tests/test_template_selector.py / tests/test_template_index.py）。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from engine.template_index import TemplateIndex, build_index


def fuzzy_match_pid(t_pid: str, req_pid: str) -> bool:
    """产品 ID 模糊匹配：支持模板 product_id 存储多个关键词（逗号/换行分隔）。

    适用于广东等省份"一条话术对应多个产品"的场景，如：
      模板 product_id = "xx套餐xx,xx升xx,xx体验xx"
      请求 product_id = "5G畅享套餐活动"  → 含"套餐"，命中第一项
      请求 product_id = "39元档升级活动"   → 含"升"，命中第二项
      请求 product_id = "129元体验优惠套餐" → 含"体验"，命中第三项

    匹配规则（大小写不敏感）：
      模板各关键词 与 请求 product_id 互相包含，任意一项命中即返回 True。
    空串不参与模糊匹配（空串代表「不限产品」的通用模板，走精确路径即可）。
    """
    if not t_pid or not req_pid:
        return False
    r_lower = req_pid.lower()
    # 模板 product_id 按逗号、换行拆分为多个关键词，逐一尝试
    keywords = [k.strip() for k in re.split(r"[,，\n]+", t_pid) if k.strip()]
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower in r_lower or r_lower in kw_lower:
            return True
    return False


def _build_search_list(
    templates_v2: List[Dict[str, Any]],
    intent: str,
    warn: bool = True,
) -> List[Dict[str, Any]]:
    """候选模板池：同 intent 且未删除；优先仅用 online。

    （原 select_template_linear 内联逻辑，抽出供宽松兜底复用，语义逐行保持一致。）
    优先 online 是为了避免配置里全是 offline 时误走旧 prompts（无 template_content），
    导致 LLM 收不到话术模板正文。intent 为空的模板视为「不限意图」，同样纳入候选。
    warn=False 用于二次调用（宽松兜底），避免同一请求重复打降级警告。
    """
    pool = [
        t for t in templates_v2
        if (not t.get("intent") or t.get("intent") == intent)
        and t.get("status") != "deleted"
    ]
    if not pool:
        return []
    online_only = [t for t in pool if t.get("status", "online") == "online"]
    if online_only:
        return online_only
    if warn:
        logger.warning(
            f"[ScriptStep] intent={intent!r} 无 status=online 的话术模板，"
            f"已降级使用 offline/其他状态模板共 {len(pool)} 条（仍带话术模板进 LLM）"
        )
    return pool


def select_templates_expand(
    templates_v2: List[Dict[str, Any]],
    intent: str,
    product_id: str,
    stage: str = "",
) -> List[Dict[str, Any]]:
    """枚举模式：返回指定 product_id + stage 下所有 scene 的话术模板列表。

    适用场景：batch_contexts 条目中 expand=true 且 scence 为空时，
    枚举该产品+环节下所有 scene，每个 scene 生成一条话术。

    匹配规则（按 product_id + stage 过滤，枚举 scene 维度）：
    - product_id 非空：优先匹配 product_id + stage 的模板；无则降级到通用产品（product_id 为空）+ stage
    - product_id 为空：只匹配通用产品（product_id 为空）+ stage 的模板
    - 返回所有命中模板（去重 scene），空 scene 的兜底模板不纳入枚举
    """
    pool = [
        t for t in templates_v2
        if (not t.get("intent") or t.get("intent") == intent)
        and t.get("status") not in ("deleted", "offline")
    ]
    if not pool:
        return []

    def _normalize(v: Any) -> str:
        return str(v or "").strip()

    pid = _normalize(product_id)
    stg = _normalize(stage)

    # 候选：product_id + stage 精确匹配，scene 非空（排除兜底模板）
    matched: List[Dict[str, Any]] = [
        t for t in pool
        if _normalize(t.get("product_id")) == pid
        and _normalize(t.get("stage")) == stg
        and _normalize(t.get("scene")) != ""
    ]

    # 降级：若无产品专属模板，用通用产品（product_id 为空）+ stage
    if not matched and pid:
        matched = [
            t for t in pool
            if _normalize(t.get("product_id")) == ""
            and _normalize(t.get("stage")) == stg
            and _normalize(t.get("scene")) != ""
        ]

    # 按 scene 去重（保留每个 scene 的第一个模板）
    seen_scenes: set = set()
    result: List[Dict[str, Any]] = []
    for t in matched:
        scn = _normalize(t.get("scene"))
        if scn not in seen_scenes:
            seen_scenes.add(scn)
            result.append(t)

    return result


def select_template_linear(
    templates_v2: List[Dict[str, Any]],
    intent:     str,
    product_id: str,
    stage:      str = "",
    scene:      str = "",
) -> Optional[Dict[str, Any]]:
    """从 script_templates_v2 列表中为指定产品选择话术模板（旧线性扫描，回退实现+测试基准）。

    基础维度：intent（技能包已由运行时按 province+intent 加载模板）
    可选维度：product_id（产品）、stage（环节）、scence（场景）

    匹配优先级（三阶段，取第一个命中的 online 模板）：

    【阶段1：产品精确匹配（product_id 完全一致）】
    1. 产品精确 + 环节 + 场景
    2. 产品精确 + 环节
    3. 产品精确 + 场景
    4. 仅产品精确

    【阶段2：产品模糊匹配（精确未命中且 product_id 非空时触发）】
    广东等省份话术模板的产品字段为描述性关键词（如"套餐"、"升"），
    当传入 product_id 无法精确匹配时，尝试子串模糊匹配：
    5. 产品模糊 + 环节 + 场景
    6. 产品模糊 + 环节
    7. 产品模糊 + 场景
    8. 仅产品模糊

    【阶段3：通用兜底（product_id 为空的模板，不限产品）】
    9.  通用 + 环节 + 场景
    10. 通用 + 环节
    11. 通用 + 场景
    12. 兜底全空（意图级通用）

    触发规则：
    - product_id 非空：走阶段1 → 阶段2 → 阶段3
    - product_id 为空：直接走阶段3

    返回 None 表示无可用 v2 模板，由调用方降级到旧 prompts 字段。
    """
    search_list = _build_search_list(templates_v2, intent)
    if not search_list:
        return None

    def _match(t: Dict[str, Any], pid: str, stg: str, scn: str) -> bool:
        """检查模板是否与给定维度完全吻合。
        候选维度非空 → 模板该维必须精确一致；
        候选维度为空 → 模板该维也必须为空（本轮只选「不限该维」的模板）。
        """
        t_pid = str(t.get("product_id", "") or "").strip()
        t_stg = str(t.get("stage",      "") or "").strip()
        t_scn = str(t.get("scene",      "") or "").strip()
        return t_pid == str(pid or "").strip() and \
               t_stg == str(stg or "").strip() and \
               t_scn == str(scn or "").strip()

    # ── 话术模板匹配优先级（三阶段，从高到低）────────────────────────
    #
    # 阶段1：产品精确匹配（product_id 完全一致）
    #   1. 产品精确 + 环节 + 场景
    #   2. 产品精确 + 环节
    #   3. 产品精确 + 场景
    #   4. 仅产品精确
    #
    # 阶段2：产品模糊匹配（product_id 关键词子串匹配，仅 product_id 非空时触发）
    #   5. 产品模糊 + 环节 + 场景
    #   6. 产品模糊 + 环节
    #   7. 产品模糊 + 场景
    #   8. 仅产品模糊
    #
    # 阶段3：通用兜底（product_id 为空的模板，不限产品）
    #   9.  通用 + 环节 + 场景   ← 如：广东+意图+—+个人市场+开口话术
    #   10. 通用 + 环节
    #   11. 通用 + 场景
    #   12. 兜底全空（意图级通用）
    #
    # 触发规则：
    #   - product_id 非空：走阶段1 → 阶段2 → 阶段3
    #   - product_id 为空：直接走阶段3（阶段1/2自动跳过）

    # ── 阶段1：产品精确匹配 ──────────────────────────────────────
    exact_candidates = [
        (product_id, stage, scene),   # 1. 产品精确+环节+场景
        (product_id, stage, ""),      # 2. 产品精确+环节
        (product_id, "", scene),      # 3. 产品精确+场景
        (product_id, "", ""),         # 4. 仅产品精确
    ]
    seen_exact: list = []
    for combo in exact_candidates:
        if combo not in seen_exact:
            seen_exact.append(combo)

    for pid, stg, scn in seen_exact:
        for t in search_list:
            if _match(t, pid, stg, scn):
                return t

    # ── 阶段2：产品模糊匹配（精确未命中，product_id 非空时触发）──────────
    req_pid = str(product_id or "").strip()
    if req_pid:
        stg_norm = str(stage or "").strip()
        scn_norm = str(scene or "").strip()
        fuzzy_stage_scene_combos = []
        if stg_norm and scn_norm:
            fuzzy_stage_scene_combos.append((stg_norm, scn_norm))   # 5. 模糊+环节+场景
        if stg_norm:
            fuzzy_stage_scene_combos.append((stg_norm, ""))         # 6. 模糊+环节
        if scn_norm:
            fuzzy_stage_scene_combos.append(("", scn_norm))         # 7. 模糊+场景
        fuzzy_stage_scene_combos.append(("", ""))                   # 8. 仅模糊产品

        seen_fuzzy: list = []
        for combo in fuzzy_stage_scene_combos:
            if combo not in seen_fuzzy:
                seen_fuzzy.append(combo)

        for f_stg, f_scn in seen_fuzzy:
            for t in search_list:
                t_pid = str(t.get("product_id", "") or "").strip()
                t_stg = str(t.get("stage",      "") or "").strip()
                t_scn = str(t.get("scene",      "") or "").strip()
                if (fuzzy_match_pid(t_pid, req_pid)
                        and t_stg == f_stg
                        and t_scn == f_scn):
                    logger.info(
                        f"[ScriptStep] 产品模糊匹配命中: "
                        f"req_pid={req_pid!r} → tpl_pid={t_pid!r} "
                        f"stage={f_stg!r} scene={f_scn!r}"
                    )
                    return t

    # ── 阶段3：通用兜底（product_id 为空的模板）──────────────────────
    # 触发条件：
    #   - product_id 非空但阶段1/2均未命中（如传入产品无对应话术）
    #   - product_id 为空（阶段1去重后自动跳过，直接到此）
    fallback_candidates = [
        ("", stage, scene),   # 9.  通用+环节+场景
        ("", stage, ""),      # 10. 通用+环节
        ("", "", scene),      # 11. 通用+场景
        ("", "", ""),         # 12. 兜底全空
    ]
    seen_fallback: list = []
    for combo in fallback_candidates:
        if combo not in seen_fallback:
            seen_fallback.append(combo)

    for pid, stg, scn in seen_fallback:
        for t in search_list:
            if _match(t, pid, stg, scn):
                return t

    return None


def select_template_loose(
    templates_v2: List[Dict[str, Any]],
    intent:     str,
    product_id: str,
    stage:      str = "",
    scene:      str = "",
) -> Optional[Dict[str, Any]]:
    """宽松兜底匹配：把请求中**为空的维度**视为「不限」。

    严格 12 档（select_template_linear / TemplateIndex.select）把「请求维度为空」解释为
    「模板该维也必须为空」。当调用方漏传 stage（或字段名写错，如把 "stage" 误写成
    "个人市场"），而模板又都配了具体 stage 时，12 档会一条都匹配不上，只能退化成不带
    话术模板的兜底 Prompt——话术质量不可控（生产表现为把整包 JSON 丢给大模型）。

    本函数只在严格档位返回 None 后由 select_template 调用，因此不改变任何已能命中的
    既有匹配；产品维度仍由紧到松：精确 → 模糊 → 通用（模板 product_id 为空）。
    """
    req_pid = str(product_id or "").strip()
    req_stg = str(stage or "").strip()
    req_scn = str(scene or "").strip()
    # 两个维度都给全时，严格档位已穷尽所有组合，宽松档不会带来新命中
    if req_stg and req_scn:
        return None

    search_list = _build_search_list(templates_v2, intent, warn=False)
    if not search_list:
        return None

    def _dims_ok(t: Dict[str, Any]) -> bool:
        t_stg = str(t.get("stage", "") or "").strip()
        t_scn = str(t.get("scene", "") or "").strip()
        return (not req_stg or t_stg == req_stg) and (not req_scn or t_scn == req_scn)

    for mode in ("exact", "fuzzy", "generic"):
        if mode in ("exact", "fuzzy") and not req_pid:
            continue
        for t in search_list:
            t_pid = str(t.get("product_id", "") or "").strip()
            if mode == "exact" and t_pid != req_pid:
                continue
            if mode == "fuzzy" and not fuzzy_match_pid(t_pid, req_pid):
                continue
            if mode == "generic" and t_pid != "":
                continue
            if not _dims_ok(t):
                continue
            logger.info(
                f"[ScriptStep] 宽松兜底命中({mode}): req_pid={req_pid!r} "
                f"stage={req_stg!r} scene={req_scn!r} → tpl_pid={t_pid!r} "
                f"tpl_stage={str(t.get('stage', '') or '').strip()!r} "
                f"tpl_scene={str(t.get('scene', '') or '').strip()!r}"
                "（请求未给定的维度按不限处理，请检查下游是否漏传 stage/scence）"
            )
            return t

    return None


def select_template(
    templates_v2: List[Dict[str, Any]],
    intent:     str,
    product_id: str,
    stage:      str = "",
    scene:      str = "",
    index: Optional[TemplateIndex] = None,
) -> Optional[Dict[str, Any]]:
    """模板选择统一入口。

    - index 非 None：走 TemplateIndex.select（哈希索引，唯一主路径，
      与线性扫描逐语义等价且支持 priority 字段）
    - index 为 None：回退 select_template_linear（旧线性扫描，行为与迁移前完全一致）
    """
    if index is not None:
        hit = index.select(product_id, stage=stage, scene=scene)
    else:
        hit = select_template_linear(
            templates_v2, intent, product_id, stage=stage, scene=scene
        )
    if hit:
        return hit
    # 严格 12 档未命中才走宽松兜底（漏传 stage/scence 时仍能命中模板）。
    # 放在包装层而非两个底层实现里：既让索引/线性两条路径行为一致，
    # 也保持它们之间「逐语义等价」的不变量（tests/test_template_index.py 有等价性对照）。
    return select_template_loose(
        templates_v2, intent, product_id, stage=stage, scene=scene
    )


def build_selector_index(
    templates_v2: List[Dict[str, Any]],
    intent: str,
) -> Optional[TemplateIndex]:
    """按开关与规模阈值构建模板索引（包装 engine.template_index.build_index）。

    返回 None 的情形（调用方回退旧线性扫描）：
    - 环境变量 ZNHS_TEMPLATE_INDEX == "0"（显式关闭）
    - 模板数 < 30（小规模线性扫描足够快，不值得建索引）
    - build_index 内部异常（其自身兜底返回 None 并 warning）
    """
    if os.environ.get("ZNHS_TEMPLATE_INDEX", "") == "0":
        return None
    if len(templates_v2 or []) < 30:
        return None
    return build_index(templates_v2, intent)


__all__ = [
    "select_template",
    "select_template_linear",
    "select_template_loose",
    "select_templates_expand",
    "fuzzy_match_pid",
    "build_selector_index",
]
