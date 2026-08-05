"""
prompt_builder — 话术 Prompt 组装（从 steps/script_step.py 迁移的 Prompt 构造逻辑）

迁移来源（oracle 对照：_refactor_backup/2026-07-03/steps__script_step.py）：
- VAR_LABELS                   ← ScriptStep._VAR_LABELS（原 L770-784）；
                                 优先从 schemas.get_standard_domains() 的 var_labels 构建，
                                 异常 fallback 到迁移过来的硬编码字典（两者内容一致）
- resource_context_prompt_vars ← ScriptStep._resource_context_prompt_vars（原 L800-812）
- append_prompt_extra_suffix   ← ScriptStep._append_prompt_extra_suffix（原 L830-841）
- build_prompt                 ← ScriptStep._build_prompt（原 L843-954，逐行等价迁移，
                                 self.field_aliases / self.max_length 改为显式参数）

新增：
- preview_prompt               用可配置示例数据构造最小 FlowContext 走同一 build_prompt，
                                供预览端点使用（消灭前端双写）

依赖说明：
    _fmt_package / _fmt_usage / _fmt_tags / _fmt_flat_domain /
    _fmt_recommended_product_full / _fmt_extra_for_prompt 等格式化工具
    未迁移（仍被 ScriptStep 的降级话术等逻辑使用），本模块经方法内延迟 import
    ScriptStep 类直接调用（均为 staticmethod/classmethod，无实例状态），
    避免 engine 与 steps 的模块级循环依赖。

等价性约束：build_prompt 输出必须与原实现逐字符相等（tests/test_prompt_builder.py
以备份原实现为 oracle 对照），禁止顺手优化字符串拼接。
"""
from __future__ import annotations

import copy
import difflib
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from loguru import logger

from prompt.script_generation import (
    SCRIPT_CONTEXT_HEADER,
    SCRIPT_GEN_RULES,
    SCRIPT_MISSING_FACTS_HEAD,
    SCRIPT_MISSING_FACTS_TAIL,
    SCRIPT_LEGACY_USER_TEMPLATE,
    SCRIPT_OUTPUT_SUFFIX,
    SCRIPT_PERSONA_RULE,
    SCRIPT_SYSTEM_HEADER,
    SCRIPT_TEMPLATE_HEADER,
)
from utils.field_naming import canon_key, dict_fuzzy_get
from utils.placeholder import dig_subfield

if TYPE_CHECKING:
    from core.context import FlowContext


# ── 变量标签单一真源 ──────────────────────────────────────────────

# 迁移自 ScriptStep._VAR_LABELS（原 L770-784）：schemas 读取失败时的兜底字典
_FALLBACK_VAR_LABELS: Dict[str, str] = {
    "cur_brief":  "当前套餐信息",
    "current_package": "当前套餐信息",
    "pkg_brief":  "推荐产品信息",
    "diff_str":   "差异",
    "usage_line": "历史用量",
    "usage":      "历史用量",
    "user_tags":  "用户标签",
    "tags":       "用户标签",
    "user_info":  "用户基础信息",
    "user_profile": "用户画像",
    "domain_ext": "扩展信息",
    "extra_info": "主服务补充信息(extra_info)",
    "extra_context": "模板匹配上下文(extra_context)",
}


def _load_var_labels() -> Dict[str, str]:
    """从 schemas.get_standard_domains() 的 var_labels 构建变量标签表。

    schemas/standard_domains.json 即以原 _VAR_LABELS 为准生成（单一真源）；
    任意异常 fallback 到迁移过来的硬编码字典，保证行为不变。
    """
    try:
        from schemas import get_standard_domains

        labels = get_standard_domains().get("var_labels") or {}
        labels = {str(k): str(v) for k, v in labels.items()}
        if labels:
            return labels
        logger.warning("[prompt_builder] standard_domains.var_labels 为空，使用内置兜底字典")
    except Exception as exc:
        logger.warning(f"[prompt_builder] 读取 standard_domains var_labels 失败，使用内置兜底字典: {exc}")
    return dict(_FALLBACK_VAR_LABELS)


# 变量键 → Prompt 中展示的中文标签（与《数据映射域》域名一致，并保留旧 linked_vars 别名）
VAR_LABELS: Dict[str, str] = _load_var_labels()

# ── 同义变量组（历史别名 ↔ 标准域名指向同一事实）───────────────────
# 用途：
# ① 上下文行锚点对齐 —— 模板占位符与 linked_vars 可能各用一套命名
#    （前端调色板插 {current_package}，旧模板写 {cur_brief}，后端自动并入 linked_vars
#    用标准域名），锚点必须跟模板实际用的名字一致，否则生成规则第 3 条同名匹配失败，
#    模型会按第 2 条跳过该槽位（数据明明存在却不填充）。
# ② 同组去重 —— linked_vars 同时含 cur_brief 与 current_package 时只注入一行。
_VAR_ALIAS_GROUPS: Dict[str, tuple] = {
    "current_package": ("current_package", "cur_brief", "cur_name"),
    "usage":           ("usage", "usage_line"),
    "tags":            ("tags", "user_tags"),
    "pkg_brief":       ("pkg_brief", "pkg_name"),
}
_ALIAS_CANON: Dict[str, str] = {
    alias: canon for canon, aliases in _VAR_ALIAS_GROUPS.items() for alias in aliases
}


def _var_alias_group(key: str) -> tuple:
    """返回 key 所属同义组的全部成员（无组则仅自身）。"""
    return _VAR_ALIAS_GROUPS.get(_ALIAS_CANON.get(key, key), (key,))


def _pkg_own_field(pkg: Dict[str, Any], key: str) -> str:
    """当前推荐产品自身字段的取值（供 {offerName} / {recommend_actual_price} 这类裸字段名解析）。

    两条硬约束：
    - 不得占用标准域/已知变量名：推荐条里常带与标准域同名的字段（北京推荐条自带 tags），
      若允许顶替，空的「用户标签」域会被产品的 tags 串填——正是防串填规则要杜绝的。
    - 空值（含空列表/空字典）视为无事实，不入 Prompt。
    """
    if not isinstance(pkg, dict) or not isinstance(key, str) or key.startswith("_"):
        return ""
    if key in _RESERVED_VAR_NAMES or key not in pkg:
        return ""
    v = pkg.get(key)
    if v is None or (isinstance(v, (list, dict, str)) and not v):
        return ""
    return _fmt_passthrough_value(v)


def _pkg_fact_in(emitted: set, pkg: Dict[str, Any]) -> bool:
    """已注入【上下文数据】的内容里是否已含推荐产品事实。

    命中任一即算已含：产品类变量（pkg_brief / pkg_fee…）、以产品为根的子字段占位符
    （{pkg_brief[recommend_actual_price]}）、或产品自身字段名被直接注入。
    """
    for token in emitted:
        root = str(token).split("[", 1)[0]
        if root in _PKG_FACT_VAR_KEYS:
            return True
        if isinstance(pkg, dict) and root in pkg:
            return True
    return False


# 推荐套餐派生变量（单位已归一：元 / GB / 分钟），供模板直接引用 {pkg_fee} 等；
# 值由 PackageDiff 的多别名提取器计算（兼容 initFee 分单位、offerFlow MB 单位等）
_DERIVED_PKG_VAR_LABELS: Dict[str, str] = {
    "pkg_fee":   "推荐套餐月费(元)",
    "pkg_flow":  "推荐套餐流量(GB)",
    "pkg_voice": "推荐套餐语音(分钟)",
}

# 承载「推荐产品事实」的变量名：这些被注入即视为上下文已带产品信息
_PKG_FACT_VAR_KEYS = frozenset(
    {"pkg_brief", "pkg_name", "recommended_package"} | set(_DERIVED_PKG_VAR_LABELS)
)

# 推荐产品字段兜底注入时跳过的键：纯排序/内部标记，以及不该进话术的 ID 类字段
# （ID 一旦进上下文，模型可能把 2026070113291445801035520 这类串号念进话术）
_PKG_INJECT_SKIP_KEYS = frozenset({
    "rank", "offerId", "offer_id", "product_id", "package_id", "prod_id",
})

# 标准域 / 已知变量名：推荐产品字段不得占用这些名字（详见 _pkg_own_field）
_RESERVED_VAR_NAMES = (
    set(VAR_LABELS) | set(_ALIAS_CANON) | set(_DERIVED_PKG_VAR_LABELS)
)

# 可作为「推荐产品字段」暴露给配置页 / 运行时兜底注入的键的排除集
PKG_FIELD_EXCLUDED_KEYS = frozenset(_PKG_INJECT_SKIP_KEYS | _RESERVED_VAR_NAMES)


def _fmt_num(v: Any) -> str:
    """数值格式化：整数值去掉小数点（188.0 → 188），None → 空串。"""
    if v is None:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if abs(f - round(f)) < 1e-9 else str(round(f, 2))


def _script_step_cls():
    """延迟获取 ScriptStep 类（格式化工具的宿主，均为无实例状态的 static/classmethod）。"""
    from steps.script_step import ScriptStep

    return ScriptStep


# ── 个性化润色触发判定 ───────────────────────────────────────────

# 标签/画像类标准域变量（任一被注入上下文即触发润色规则）
_PERSONA_VAR_KEYS = frozenset({"tags", "user_tags", "user_profile"})
# 直传透传字段名中的性格/画像特征词（如 communication_style / portrait_style 展开的子字段）
_PERSONA_KEY_HINTS = ("style", "persona", "portrait", "性格", "画像", "风格", "偏好")


def _has_persona_context(emitted: set, passthrough_ctx: Dict[str, Any]) -> bool:
    """已注入的上下文中是否包含用户标签/画像/性格类信息（决定是否追加润色规则）。"""
    if emitted & _PERSONA_VAR_KEYS:
        return True
    # 子字段占位符（tags[xxx] / user_profile[xxx]）也算标签/画像类
    for e in emitted:
        root = str(e).split("[", 1)[0]
        if root in _PERSONA_VAR_KEYS:
            return True
    for k in (passthrough_ctx or {}):
        lk = str(k).lower()
        if any(h in lk for h in _PERSONA_KEY_HINTS):
            return True
    return False


# ── Prompt 构造（迁移自 ScriptStep，逐行等价） ─────────────────────

def resource_context_prompt_vars(
    ctx: "FlowContext", fa: Dict[str, Any]
) -> Dict[str, str]:
    """唯一数据源：FlowContext.resource_context（与《数据映射域》核心域对齐，不含整表推荐列表摘要）。"""
    step = _script_step_cls()
    rc = ctx.resource_context
    return {
        "current_package": step._fmt_package(rc["current_package"], fa),
        "usage": step._fmt_usage(rc["usage"]),
        "tags": step._fmt_tags(rc["tags"]),
        "user_info": step._fmt_flat_domain(rc["user_info"]),
        "user_profile": step._fmt_flat_domain(rc["user_profile"]),
        "domain_ext": step._fmt_flat_domain(rc["domain_ext"]),
    }


def _fmt_passthrough_value(v: Any) -> str:
    """直传透传字段值格式化：标量转字符串；dict 转「子键：值；…」的可读串（如 portrait_style
    大变量 {portrait_style} 呈现为 communication_style：直接爽快；business_conte：…，而非生 JSON
    blob，让大变量占位符在上下文里可读地体现）；list / 无标量子字段的 dict 回退紧凑 JSON。"""
    if v is None:
        return ""
    if isinstance(v, dict):
        parts: List[str] = []
        for _k, _val in v.items():
            if not isinstance(_k, str) or _k.startswith("_"):
                continue
            if _val in (None, "", [], {}):
                continue
            parts.append(f"{_k}：{_fmt_passthrough_value(_val)}")
        if parts:
            return "；".join(parts)
    if isinstance(v, (dict, list)):
        import json

        try:
            return json.dumps(v, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(v)
    return str(v)


# ── 子字段路径占位符（{域[子键]} / {域[子键1][子键2]}）────────────────
# 允许话术模板精确引用某个标准域字典下的具体字段（而非整块 JSON），实现字段级槽位对齐，
# 让「入参字段 → 模板槽位」的匹配更精准。语法用方括号包裹子键（子键可含中文 / 括号 / 点号，
# 方括号作边界比点号更稳），与前端调色板「展开域选子字段」插入的 token 完全一致。
#   例：{usage[data_usage][近6月平均流量(GB)]}  {current_package[offerName]}  {tags[融合状态]}
_SUBFIELD_TOKEN_RE = re.compile(r"\{(\w+)((?:\[[^\[\]]+\])+)\}")


# 键名归一 / 模糊取值的单一实现在 utils.field_naming（DataStep 的 filter 规则共用同一套
# 规则，保证「配置键名 ↔ 接口出参键名 ↔ 模板子键」三方在括号形态上的容忍度完全一致）
_dict_fuzzy_get = dict_fuzzy_get


def _subfield_get_child(obj: Any, key: str) -> Any:
    """按单个子键取值：dict 三档模糊匹配；缺失时下探一层嵌套子字典
    （容忍 usage 两级结构写成单级）；list 支持数字下标，或在成员 dict 中按键搜索。
    取不到返回 None。"""
    if isinstance(obj, dict):
        v = _dict_fuzzy_get(obj, key)
        if v is not None:
            return v
        for child in obj.values():
            if isinstance(child, dict):
                v = _dict_fuzzy_get(child, key)
                if v is not None:
                    return v
        return None
    if isinstance(obj, list):
        try:
            return obj[int(key)]
        except (ValueError, IndexError):
            pass
        for item in obj:
            if isinstance(item, dict):
                v = _dict_fuzzy_get(item, key)
                if v is not None:
                    return v
    return None


def _subfield_walk(root_obj: Any, keys: List[str]) -> Any:
    """按子键序列逐级下钻，任一级取不到即返回 None。"""
    cur = root_obj
    for k in keys:
        cur = _subfield_get_child(cur, k)
        if cur is None:
            return None
    return cur


def _subfield_missing_hint(subfield_roots: Dict[str, Any], token: str) -> str:
    """针对「映射产出里父级域有数据、但末级子键取不到」这种静默失配给出可读诊断。

    典型场景（北京生产）：field_transform 映射成功，usage.consumption 产出
    ``近6月平均月消费``，而话术模板占位符写的是 ``近6月平均消费``（少一个「月」字）。
    此时该槽位既非「域为空」也非「上游没返回」，普通缺失告警只会说「无数据」，运营无从下手。
    本函数下钻到「失配叶子的父级字典」，若父级确有键，则计算最接近的候选键返回提示；
    父级不存在/为空（=真·无数据）返回空串，交给常规缺失逻辑。

    注意：仅用于诊断/日志/测试页展示，绝不写入发给模型的 Prompt 正文
    （避免把相近但错误的候选值喂给模型导致串填）。
    """
    m = _SUBFIELD_TOKEN_RE.fullmatch("{" + str(token) + "}")
    if not m:
        return ""
    root = m.group(1)
    keys = re.findall(r"\[([^\[\]]+)\]", m.group(2))
    if root not in subfield_roots or not keys:
        return ""
    parent = _subfield_walk(subfield_roots.get(root), keys[:-1]) if len(keys) > 1 else subfield_roots.get(root)
    if not isinstance(parent, dict) or not parent:
        return ""   # 父级域本身为空 → 真·无数据，非失配，交常规缺失处理
    leaf = keys[-1]
    produced = [str(k) for k in parent.keys()]
    canon_leaf = canon_key(leaf)
    # 去括号归一后已相等 = 运行态 dict_fuzzy_get 本就能取到，不算失配（若仍进缺失列表
    # 另有他因），直接返回空串，避免给出误导性的「未命中」提示。
    if any(canon_key(k) == canon_leaf for k in produced):
        return ""
    # 候选优先级：一方为另一方子串 → difflib 相似度
    contained = [k for k in produced if canon_leaf and (canon_leaf in canon_key(k) or canon_key(k) in canon_leaf)]
    ranked = difflib.get_close_matches(leaf, produced, n=3, cutoff=0.4)
    best_list = contained or ranked
    best = best_list[0] if best_list else ""
    parent_path = ".".join([root] + keys[:-1])
    hint = (f"{parent_path} 已产出 {produced}，但模板子键『{leaf}』未命中")
    if best:
        hint += f"；最接近产出键：『{best}』——请核对是模板占位符写错、还是接口映射 field_rename 目标名不一致"
    return hint


def append_prompt_extra_suffix(
    tpl_raw: str, body: str, ei_txt: str, ec_txt: str
) -> str:
    """旧版 user_prompt_tpl 若未写 {extra_info}/{extra_context} 占位符，则追加主服务入参段。"""
    t = tpl_raw or ""
    out = body
    if ei_txt and "{extra_info}" not in t:
        out = f"{out}\n主服务补充信息(extra_info)：{ei_txt}"
    if ec_txt and "{extra_context}" not in t:
        out = f"{out}\n模板匹配上下文(extra_context)：{ec_txt}"
    return out


def _fill_prompt_parts(
    parts_out: Optional[Dict[str, str]],
    *,
    full: str,
    context_data: str = "",
    template: str = "",
    script_requirement: str = "",
    other: str = "",
    missing_facts: str = "",
    missing_slots: Optional[List[str]] = None,
    missing_slot_hints: Optional[Dict[str, str]] = None,
) -> None:
    """写入测试页可展示的提示词分段（就地更新 parts_out）。"""
    if parts_out is None:
        return
    parts_out.clear()
    parts_out.update({
        "full": full or "",
        "context_data": context_data or "",
        "template": template or "",
        "script_requirement": script_requirement or "",
        "other": other or "",
        "missing_facts": missing_facts or "",
        "missing_slots": list(missing_slots or []),
        # {token: 诊断提示}——仅「父域有数据但叶子子键失配」的槽位才有值（供测试页排障）
        "missing_slot_hints": dict(missing_slot_hints or {}),
    })


def _slot_label(token: str) -> str:
    """槽位占位符 → 可读标签：子字段取末级子键，标准域取中文标签。"""
    root = str(token).split("[", 1)[0]
    if "[" in token:
        keys = re.findall(r"\[([^\[\]]+)\]", token)
        if keys:
            return keys[-1]
    return _DERIVED_PKG_VAR_LABELS.get(root) or VAR_LABELS.get(root, root)


def build_prompt(
    user_prompt_tpl: str,
    template_text: str,
    ctx: "FlowContext",
    pkg: Dict[str, Any],
    diff: Any,
    linked_vars: Optional[List[str]] = None,
    script_requirement: str = "",
    extra_info_override: Optional[Dict[str, Any]] = None,
    field_aliases: Optional[Dict[str, Any]] = None,
    max_length: int = 100,
    slot_facts_out: Optional[Dict[str, str]] = None,
    parts_out: Optional[Dict[str, str]] = None,
) -> str:
    """组装 LLM Prompt（迁移自 ScriptStep._build_prompt，逐行等价）。

    - resource_context 域由 ``resource_context_prompt_vars`` 提供；``pkg_brief`` 为当前推荐条。
    - ``ctx.extra_info`` / ``ctx.extra_context`` 序列化为 JSON 字符串写入 ``fmt_vars``，并支持关联变量
      ``extra_info`` / ``extra_context``；未勾选时若对应字段非空也会自动追加一行（便于山东等主服务直传场景）。
    - 旧 ``user_prompt_tpl`` 可使用占位符 ``{extra_info}``、``{extra_context}``；若模板未包含占位符且字段非空，
      会在文末追加对应段落。
    - ``extra_info_override``：批量模式下条目级 extra_info（已合并全局），优先于 ctx.extra_info。
    - ``field_aliases`` / ``max_length``：原实现取自 ScriptStep 实例状态
      （self.field_aliases / self.max_length），迁移后改为显式参数，缺省值与实例默认一致。
    - ``slot_facts_out``：若传入 dict，则同步写入注入【上下文数据】的 ``占位符 → 事实值``，
      供 ScriptStep 在 LLM 输出后做确定性填槽（含子域 ``{域[子键]}``）。
    - ``parts_out``：若传入 dict，则写入分段结果（context_data / template /
      script_requirement / other / full），供测试页展示最终发给大模型的提示词。

    优先级：
    1. 若 linked_vars 非空或存在话术模板正文 → 新格式自动构造
    2. 否则 fallback 到旧 user_prompt_tpl（向后兼容）
    """
    step = _script_step_cls()
    fa = field_aliases or {}
    rp = resource_context_prompt_vars(ctx, fa)
    pkg_brief = step._fmt_recommended_product_full(pkg, fa)
    if not (pkg_brief or "").strip():
        pkg_brief = step._fmt_package(pkg, fa)
    diff_str     = diff.summary_str()
    template_ref = re.sub(r"\{[^{}]+\}", "", template_text or "").strip()

    # 批量模式：用条目级 extra_info（已合并全局）；单条模式：用 ctx.extra_info
    effective_extra_info = extra_info_override if extra_info_override is not None else ctx.extra_info
    ei_txt = step._fmt_extra_for_prompt(effective_extra_info)
    ec_txt = step._fmt_extra_for_prompt(ctx.extra_context)

    fmt_vars = dict(
        intent=ctx.intent,
        **rp,
        cur_brief=rp["current_package"],
        cur_name=rp["current_package"],
        pkg_brief=pkg_brief,
        pkg_name=pkg_brief,
        diff_str=diff_str,
        usage_line=rp["usage"],
        user_tags=rp["tags"],
        template=template_ref,
        template_ref=template_ref,
        template_content=template_text or "",
        table=diff.table_str(),
        max_length=max_length,
        reason_line=pkg.get("recommendation_reason", "更匹配您的使用需求"),
        features_line="",
        recommended_packages="",  # 已弃用，保留空串以免旧 user_prompt_tpl 含 {recommended_packages} 时 format 失败
        extra_info=ei_txt,
        extra_context=ec_txt,
        # 推荐套餐派生变量（单位归一：元/GB/分钟），供模板 {pkg_fee}/{pkg_flow}/{pkg_voice} 直接引用
        pkg_fee=_fmt_num(diff.tgt_fee),
        pkg_flow=_fmt_num(diff.tgt_data),
        pkg_voice=_fmt_num(diff.tgt_voice),
    )

    # ── 新格式：linked_vars 驱动的 context 工程 Prompt ───────────────
    # 结构：角色 → 【上下文数据】(映射域事实) → 【话术模板】(含槽位) → 【生成规则】 → 输出指令。
    # 事实前置 + 明确边界 + 防编造/防串填规则，引导模型仅依据映射结果填充槽位。
    if linked_vars or template_text:
        # 直传透传字段解析器：先查标准域/已知变量（fmt_vars），
        # 命中空值时回退到 extra_info 顶层同名字段（直传模式下入参字段直接作为 context）
        def _resolve_var(var_key: str) -> str:
            v = fmt_vars.get(var_key, "")
            if str(v).strip() != "":
                return str(v)
            if isinstance(effective_extra_info, dict) and var_key in effective_extra_info:
                return _fmt_passthrough_value(effective_extra_info.get(var_key))
            # 当前推荐产品自身字段（offerName / recommend_actual_price…）：多产品入参下
            # 产品字段只存在于数组元素里，不在 extra_info 顶层，按名兜底才能取到值。
            return _pkg_own_field(pkg, var_key)

        # 模板实际使用的占位符 token（锚点对齐依据）
        tpl_tokens: List[str] = re.findall(r"\{(\w+)\}", template_text or "")
        tpl_token_set = set(tpl_tokens)

        def _anchor_for(var_key: str) -> str:
            """选择上下文行锚点：优先用模板中实际出现的同组占位符名。

            linked_vars 与模板占位符可能各用一套命名（cur_brief ↔ current_package /
            usage_line ↔ usage 等历史别名）；锚点与模板同名才能被生成规则第 3 条精确匹配。
            """
            if var_key in tpl_token_set:
                return var_key
            for alias in _var_alias_group(var_key):
                if alias in tpl_token_set:
                    return alias
            return var_key

        # 1) 汇集事实上下文（映射域）；空值不入 Prompt，避免模型对空槽臆造或串填
        context_lines: List[str] = []
        emitted: set = set()

        def _record_slot_fact(token: str, val: str) -> None:
            """同步写入确定性填槽字典（与【上下文数据】行一一对应）。"""
            if slot_facts_out is None:
                return
            t = str(token or "").strip()
            v = str(val or "")
            if t and v.strip():
                slot_facts_out[t] = v

        for var_key in linked_vars or []:
            if var_key == "table":
                continue   # 差异表格不进 LLM，在前端另行展示
            if var_key == "recommended_packages":
                continue   # 已下线：不再向 Prompt 注入候选条数摘要（兼容旧模板 linked_vars）
            if var_key in emitted:
                continue   # 同义组已注入（如 cur_brief 与 current_package 同时勾选时只出一行）
            var_val = _resolve_var(var_key)
            if var_val.strip() == "":
                continue   # 空事实不展示（防止“标签：”空槽诱导编造）
            # 行首标注该事实对应的模板占位符 {anchor}，给模型精确的字符串锚点；
            # 锚点对齐模板实际用名，避免 linked_vars 与模板占位符别名错位导致槽位被跳过
            anchor = _anchor_for(var_key)
            label = (
                _DERIVED_PKG_VAR_LABELS.get(anchor) or VAR_LABELS.get(anchor)
                or _DERIVED_PKG_VAR_LABELS.get(var_key) or VAR_LABELS.get(var_key, var_key)
            )
            context_lines.append(f"{label} {{{anchor}}}：{var_val}")
            _record_slot_fact(anchor, var_val)
            emitted.update(_var_alias_group(var_key))
            emitted.add(var_key)
            emitted.add(anchor)
        # 直传透传通道：把接口配置（direct_mode=passthrough）选定的入参字段逐条注入，
        # 不依赖模板占位符风格（{xx} 或 **（xx）均可），确保【上下文数据】完整展示透传入参。
        passthrough_ctx = getattr(ctx, "passthrough_context", None) or {}
        if isinstance(passthrough_ctx, dict):
            # 被父级大变量整块体现的子字段：如 portrait_style 已渲染成
            # 「communication_style：…；business_conte：…」，其被提升为独立透传键的同名子字段
            # 就不必再单列一行（避免与父级重复）；仅当模板显式引用 {子字段} 时才单列以保证可填。
            _parent_covered: set = set()
            for _pv in passthrough_ctx.values():
                if isinstance(_pv, dict):
                    for _ck in _pv.keys():
                        if isinstance(_ck, str) and _ck in passthrough_ctx:
                            _parent_covered.add(_ck)
            for pk, pv in passthrough_ctx.items():
                if pk in emitted:
                    continue
                if (pk in _parent_covered
                        and not (template_text and ("{" + pk + "}") in template_text)):
                    continue
                val = _fmt_passthrough_value(pv)
                if val.strip() == "":
                    continue
                label = VAR_LABELS.get(pk, pk)
                context_lines.append(f"{label} {{{pk}}}：{val}")
                _record_slot_fact(pk, val)
                emitted.add(pk)
        # 模板引用但未注入的占位符自动补齐（两类，均只注入真实存在的非空事实，安全且不臆造）：
        # ① 已知变量（标准域/派生变量）被模板引用但漏勾 linked_vars → 补注入，
        #    避免拖入占位符却因未勾选导致模型按规则 2 跳过（数据实际存在）；
        # ② 直传场景 extra_info 顶层字段被模板引用 → 原有行为，按字段注入。
        _INJECTABLE_KNOWN = (
            set(VAR_LABELS) | set(_ALIAS_CANON) | set(_DERIVED_PKG_VAR_LABELS)
        ) - {"table", "recommended_packages", "extra_info", "extra_context"}
        if template_text:
            for token in tpl_tokens:
                if token in emitted:
                    continue
                if token in fmt_vars:
                    if token not in _INJECTABLE_KNOWN:
                        continue   # template/max_length/intent 等非事实变量不注入
                    val = _resolve_var(token)
                    if val.strip() == "":
                        continue
                    label = _DERIVED_PKG_VAR_LABELS.get(token) or VAR_LABELS.get(token, token)
                    context_lines.append(f"{label} {{{token}}}：{val}")
                    _record_slot_fact(token, val)
                    emitted.update(_var_alias_group(token))
                    emitted.add(token)
                    continue
                if isinstance(effective_extra_info, dict) and token in effective_extra_info:
                    val = _fmt_passthrough_value(effective_extra_info.get(token))
                else:
                    # 模板直接引用推荐产品字段名（{recommend_actual_price} 等）
                    val = _pkg_own_field(pkg, token)
                if val.strip() == "":
                    continue
                label = VAR_LABELS.get(token, token)
                context_lines.append(f"{label} {{{token}}}：{val}")
                _record_slot_fact(token, val)
                emitted.add(token)
        # 子字段路径占位符：{域[子键]…} → 从「原始域字典」按路径精确取值，字段级注入。
        # 原始域取自 ctx.resource_context（已过 field_transform/单位换算，键名=运行态映射后名），
        # 推荐条取自 pkg，直传取自 effective_extra_info；锚点用完整 token，与模板严格同名对齐。
        if template_text and "[" in template_text:
            _rc = ctx.resource_context if isinstance(
                getattr(ctx, "resource_context", None), dict) else {}
            _subfield_roots: Dict[str, Any] = {
                "current_package": _rc.get("current_package"),
                "cur_brief": _rc.get("current_package"),
                "cur_name": _rc.get("current_package"),
                "usage": _rc.get("usage"),
                "usage_line": _rc.get("usage"),
                "tags": _rc.get("tags"),
                "user_tags": _rc.get("tags"),
                "user_info": _rc.get("user_info"),
                "user_profile": _rc.get("user_profile"),
                "domain_ext": _rc.get("domain_ext"),
                "pkg_brief": pkg,
                "pkg_name": pkg,
                "recommended_package": pkg,
                "extra_info": effective_extra_info,
            }
            # 直传/透传子字段支持：把透传字段与 extra_info 的「顶层字典字段」也登记为子字段根，
            # 使 {字段名[子键]} 能像映射模式一样精确取值（参考映射模式填槽）。纯增量：
            # 仅对模板显式写出的子字段 token 生效，不改变既有整块/标量字段的注入行为。
            for _src in (passthrough_ctx, effective_extra_info):
                if isinstance(_src, dict):
                    for _fk, _fv in _src.items():
                        if (isinstance(_fk, str) and not _fk.startswith("_")
                                and isinstance(_fv, dict) and _fk not in _subfield_roots):
                            _subfield_roots[_fk] = _fv
            for _m in _SUBFIELD_TOKEN_RE.finditer(template_text):
                _token = _m.group(0)[1:-1]          # 去花括号：root[k1][k2]
                if _token in emitted:
                    continue
                _root = _m.group(1)
                if _root not in _subfield_roots:
                    continue
                _keys = re.findall(r"\[([^\[\]]+)\]", _m.group(2))
                _sval = _fmt_passthrough_value(_subfield_walk(_subfield_roots.get(_root), _keys))
                if _sval.strip() == "":
                    continue   # 取不到值的子字段不入 Prompt，避免模型对空槽臆造
                _leaf_label = _keys[-1] if _keys else _root
                context_lines.append(f"{_leaf_label} {{{_token}}}：{_sval}")
                _record_slot_fact(_token, _sval)
                emitted.add(_token)
        # 多产品兜底注入：一次请求要为多个产品各出一条话术，而以上通道都没往上下文里
        # 放进任何产品事实时，把当前推荐产品逐字段注入。此时产品信息是各条话术唯一的
        # 差异来源——运营漏勾产品类关联变量，N 条话术的上下文就完全相同，模型必然生成
        # N 条一样的话术（广东现网表现）。逐字段注入而非只给摘要，是为了兼容
        # `**（recommend_actual_price）` 这类非花括号占位符风格：模板写什么字段名，
        # 上下文里就有同名事实行。
        # 仅多产品时触发：单产品沿用原有「linked_vars 驱动」语义，提示词逐字节不变。
        if (len(getattr(ctx, "final_recommendations", None) or []) > 1
                and isinstance(pkg, dict) and pkg and not _pkg_fact_in(emitted, pkg)):
            _injected_pkg: List[str] = []
            # 摘要行仅在模板确实引用 {pkg_brief}/{pkg_name} 时注入：逐字段行已覆盖同样的
            # 事实，两者并存会让上下文长度翻倍且同一数值出现两次（易诱发串填）。
            if pkg_brief.strip() and (set(_var_alias_group("pkg_brief")) & tpl_token_set):
                _anchor = _anchor_for("pkg_brief")
                context_lines.append(f"{VAR_LABELS.get('pkg_brief', '推荐产品信息')} {{{_anchor}}}：{pkg_brief}")
                _record_slot_fact(_anchor, pkg_brief)
                emitted.update(_var_alias_group("pkg_brief"))
                emitted.add(_anchor)
            # 运营在「透传字段」里按 recommended_packages.<字段> 精确勾选过产品字段时，
            # 只注入白名单内的字段（避免十几个字段全进上下文）；未勾选则注入全部非空字段。
            _allow = getattr(ctx, "product_field_allow", None) or []
            for _pk in list(pkg.keys()):
                if _pk in emitted or _pk in PKG_FIELD_EXCLUDED_KEYS:
                    continue
                if _allow and _pk not in _allow:
                    continue
                _pval = _pkg_own_field(pkg, _pk)
                if not _pval.strip():
                    continue
                _plabel = _DERIVED_PKG_VAR_LABELS.get(_pk) or _pk
                context_lines.append(f"{_plabel} {{{_pk}}}：{_pval}")
                _record_slot_fact(_pk, _pval)
                emitted.add(_pk)
                _injected_pkg.append(_pk)
            if _injected_pkg:
                logger.warning(
                    f"[PromptBuilder] ⚠️ 话术模板未关联任何推荐产品变量，已自动注入产品字段 "
                    f"{_injected_pkg}（多产品时若不注入，各条话术上下文相同会生成同样内容）。"
                    "建议在「话术模板 → 关联变量」中勾选推荐产品相关变量"
                )

        # 主服务直传：未在关联变量中显式勾选时，仍注入非空的 extra_info / extra_context
        # 透传模式（passthrough_ctx 非空）下已逐字段展示，跳过整包 JSON 以免冗余。
        if "extra_info" not in emitted and ei_txt and not passthrough_ctx:
            context_lines.append(f"{VAR_LABELS['extra_info']} {{extra_info}}：{ei_txt}")
            _record_slot_fact("extra_info", ei_txt)
        if "extra_context" not in emitted and ec_txt:
            context_lines.append(f"{VAR_LABELS['extra_context']} {{extra_context}}：{ec_txt}")
            _record_slot_fact("extra_context", ec_txt)

        # ── 槽位缺数据：告警 + 写入 Prompt 的显式负向约束 ────────────
        # 模板引用的「事实类」占位符（标准域/派生变量/子字段路径）若最终没有对应的
        # 上下文数据行，模型不仅会跳过，还倾向于抓手边最像的数字顶上（北京生产把当前
        # 套餐的 128 元/30GB/200 分钟当成月均消费/流量/通话播报）。除了告警定位，
        # 还把缺口显式列进 Prompt——负向约束比让模型自行推断"哪些信息不存在"可靠得多。
        _missing_slots: List[str] = []
        for token in sorted(tpl_token_set):
            if token in emitted or token not in _INJECTABLE_KNOWN:
                continue
            _missing_slots.append(token)
        if template_text and "[" in template_text:
            for _m in _SUBFIELD_TOKEN_RE.finditer(template_text):
                _tok = _m.group(0)[1:-1]
                if _tok not in emitted and _tok not in _missing_slots:
                    _missing_slots.append(_tok)
        # 失配诊断：区分「域为空（真·无数据）」与「域有数据但叶子子键写错」两种缺失。
        # 后者是北京生产漏槽的隐蔽来源（映射成功却因键名差一字填不上），单独给出候选键提示。
        _missing_hints: Dict[str, str] = {}
        _roots_for_hint = locals().get("_subfield_roots")
        if isinstance(_roots_for_hint, dict):
            for _tok in _missing_slots:
                if "[" not in _tok:
                    continue
                _h = _subfield_missing_hint(_roots_for_hint, _tok)
                if _h:
                    _missing_hints[_tok] = _h
        if _missing_slots:
            logger.warning(
                f"[build_prompt] {getattr(ctx, 'province', '')}/{getattr(ctx, 'intent', '')} "
                f"话术模板引用的槽位无上下文数据（将被跳过，勿被其他字段串填）: "
                f"{sorted(_missing_slots)}；已注入={sorted(k for k in emitted if isinstance(k, str))}。"
                f"请检查接口映射（response_extract/field_transform）与接口响应数据"
            )
            for _tok, _h in _missing_hints.items():
                logger.warning(f"[build_prompt] 槽位失配 {{{_tok}}}：{_h}")
        missing_block = ""
        if _missing_slots and template_text:
            missing_block = (
                SCRIPT_MISSING_FACTS_HEAD
                + "、".join(f"{_slot_label(t)}{{{t}}}" for t in _missing_slots)
                + SCRIPT_MISSING_FACTS_TAIL
            )

        # 2) 分段拼装
        lines: List[str] = [SCRIPT_SYSTEM_HEADER]
        context_block = ""
        if context_lines:
            lines.append(SCRIPT_CONTEXT_HEADER)
            lines.extend(context_lines)
            context_block = SCRIPT_CONTEXT_HEADER + "\n" + "\n".join(context_lines)
        if missing_block:
            lines.append(missing_block)
        if template_text:
            lines.append(SCRIPT_TEMPLATE_HEADER)
            lines.append(template_text)
        other_parts: List[str] = [SCRIPT_SYSTEM_HEADER, SCRIPT_GEN_RULES]
        lines.append(SCRIPT_GEN_RULES)
        # 个性化润色规则：上下文含用户标签/画像/性格类信息时自动追加（编号顺延）
        rule_no = 5
        if _has_persona_context(emitted, passthrough_ctx):
            persona_line = f"{rule_no}. {SCRIPT_PERSONA_RULE}"
            lines.append(persona_line)
            other_parts.append(persona_line)
            rule_no += 1
        req_text = ""
        if script_requirement:
            req_line = f"{rule_no}. 话术要求：{script_requirement}"
            lines.append(req_line)
            req_text = script_requirement
        other_parts.append(SCRIPT_OUTPUT_SUFFIX)
        lines.append(SCRIPT_OUTPUT_SUFFIX)
        full = "\n".join(lines)
        _fill_prompt_parts(
            parts_out,
            full=full,
            context_data=context_block,
            template=template_text or "",
            script_requirement=req_text,
            other="\n".join(other_parts),
            missing_facts=missing_block,
            missing_slots=_missing_slots,
            missing_slot_hints=_missing_hints,
        )
        return full

    # ── 旧格式：user_prompt_tpl（向后兼容） ──────────────────────
    tpl = user_prompt_tpl or SCRIPT_LEGACY_USER_TEMPLATE
    try:
        out = tpl.format_map(fmt_vars)
        full = append_prompt_extra_suffix(tpl, out, ei_txt, ec_txt)
        _fill_prompt_parts(
            parts_out, full=full, other=full,
            script_requirement=script_requirement or "",
            template=template_text or "",
        )
        return full
    except (KeyError, ValueError):
        fb = (
            f"用户当前套餐：{rp['current_package']}\n推荐套餐：{pkg_brief}\n"
            f"套餐差异：{diff_str}\n近期用量：{rp['usage']}\n"
            f"用户标签：{rp['tags']}\n"
            f"用户基础信息：{rp['user_info']}\n用户画像：{rp['user_profile']}\n"
            f"扩展信息：{rp['domain_ext']}\n"
            f"意图：{ctx.intent}\n\n"
            f"请用中文写一句{max_length}字以内的营销推荐话术。\n话术："
        )
        full = append_prompt_extra_suffix(tpl, fb, ei_txt, ec_txt)
        _fill_prompt_parts(
            parts_out, full=full, other=full,
            script_requirement=script_requirement or "",
            template=template_text or "",
        )
        return full


# ── Prompt 预览（新增，供预览端点使用） ────────────────────────────

# 预览用内置示例数据（sample_ctx_data 可按顶层 key 覆盖）
_DEFAULT_SAMPLE_CTX: Dict[str, Any] = {
    "phone": "13800000000",
    "current_package": {
        "offerName": "畅享套餐59元档",
        "initFee": 59,
        "offerFlow": 20,
        "offerVoice": 200,
    },
    "recommended_package": {
        "offerId": "P1001",
        "offerName": "畅享套餐99元档",
        "initFee": 99,
        "offerFlow": 60,
        "offerVoice": 500,
        "recommendation_reason": "流量更充足，更匹配您的使用习惯",
    },
    "usage": {
        "data_usage": {"近6月平均流量(GB)": 35, "近6月平均流量饱和度": "175%"},
        "voice_usage": {"近6月平均主叫时长": 180},
        "consumption": {"近6月平均月消费": 66},
    },
    "tags": {"高频高额超套客户": "是", "是否老旧套餐": "是"},
    "user_info": {"星级": "五星", "网龄": "8年"},
    "user_profile": {"流量偏好": "视频类应用为主"},
    "domain_ext": {},
    "extra_info": {},
    "extra_context": {},
    "field_aliases": {},
    "max_length": 100,
}


def preview_prompt(
    template: Dict[str, Any],
    sample_ctx_data: Optional[Dict[str, Any]] = None,
    province: str = "",
    intent: str = "",
    passthrough_fields: Optional[List[str]] = None,
) -> str:
    """用示例数据预览单条模板最终发给 LLM 的 Prompt（与运行态 build_prompt 同一条路径）。

    Args:
        template: 单条话术模板（同 script_templates_v2 元素结构：
                  template_content / prompt_template / linked_vars / script_requirement ...）
        sample_ctx_data: 可选示例数据，按顶层 key 覆盖内置默认值；支持的 key 见
                  _DEFAULT_SAMPLE_CTX（current_package / recommended_package / usage /
                  tags / user_info / user_profile / domain_ext / extra_info /
                  extra_context / field_aliases / max_length / phone）
        province: 省份（缺省取模板 province 字段）
        intent:   意图（缺省取模板 intent 字段，再缺省用示例意图）

    Returns:
        组装好的 Prompt 文本
    """
    from core.context import FlowContext
    from plugins.package_diff import PackageDiff

    if passthrough_fields is not None:
        # 直传透传模式：上下文严格以「选择的透传入参」为准，不掺内置默认样例的标准域，
        # 否则默认的 current_package（畅享套餐59元档）/ usage / tags / user_profile 会伪装成
        # 「映射结果」混进上下文——与运行态（这些域根本没有入参、不会出现）不符。
        data = copy.deepcopy(sample_ctx_data) if isinstance(sample_ctx_data, dict) else {}
        # 遍历「实际透传入参」（extra_info 顶层），字段名恰与运行态标准域同名者，按入参原值直接放进
        # 对应域槽位——「直接放入」而非字段映射/重命名（与 DataStep 一致）。域清单取运行态权威定义
        # _NODE_TO_CTX_FIELD（单一事实源，不写死名字）；其余任意透传字段名走下方 passthrough_context
        # 通道，按其原字段名 {字段名} 呈现，故透传参数叫什么都自动适配。
        from steps.data_step import _NODE_TO_CTX_FIELD as _STD_DOMAINS_RT
        _ei0 = data.get("extra_info") if isinstance(data.get("extra_info"), dict) else {}
        for _k, _v in _ei0.items():
            if not isinstance(_k, str) or _k not in _STD_DOMAINS_RT:
                continue
            if _k == "recommended_packages":
                if (not data.get("recommended_package")
                        and isinstance(_v, list) and _v and isinstance(_v[0], dict)):
                    data["recommended_package"] = _v[0]
            elif not data.get(_k) and isinstance(_v, dict) and _v:
                data[_k] = _v
    else:
        data = copy.deepcopy(_DEFAULT_SAMPLE_CTX)
        if sample_ctx_data:
            for key, value in sample_ctx_data.items():
                data[key] = value

    template = template or {}
    eff_intent = intent or str(template.get("intent") or "") or "套餐推荐"
    eff_province = province or str(template.get("province") or "")

    ctx = FlowContext(
        phone=str(data.get("phone") or "13800000000"),
        intent=eff_intent,
        province=eff_province,
        current_package=data.get("current_package") or {},
        usage=data.get("usage") or {},
        tags=data.get("tags") or {},
        user_info=data.get("user_info") or {},
        user_profile=data.get("user_profile") or {},
        domain_ext=data.get("domain_ext") or {},
        extra_info=data.get("extra_info") or {},
        extra_context=data.get("extra_context") or {},
    )
    # 直传透传预览：仅当显式传入 passthrough_fields（表示该 skill 为透传模式）时，
    # 把示例 extra_info 的选定字段（空列表=全部顶层非标准域字段）填入 passthrough_context，
    # 使【上下文数据】逐字段展示透传入参，与运行态一致；未传则保持原预览行为。
    _ei = ctx.extra_info if isinstance(ctx.extra_info, dict) else {}
    if passthrough_fields is not None and _ei:
        _std = {"current_package", "usage", "tags", "user_info",
                "recommended_packages", "user_profile", "domain_ext"}
        _sel = passthrough_fields if passthrough_fields else list(_ei.keys())
        _pt: Dict[str, Any] = {}
        for k in _sel:
            if not isinstance(k, str) or not k or k.startswith("_"):
                continue
            # 子路径写法（portrait_style.communication_style）：按叶子名预览，与运行态一致
            if "." in k:
                _leaf, _val = dig_subfield(_ei, k)
                if _leaf and _leaf not in _std and _val not in (None, "", [], {}):
                    _pt.setdefault(_leaf, _val)
                continue
            if k in _ei and k not in _std and _ei[k] not in (None, "", [], {}):
                _pt[k] = _ei[k]
        # 与运行态 DataStep 一致：展开 portrait_style 一层，personality 子字段逐条预览；
        # 已按子路径精确勾选时不整块展开
        _nested = _ei.get("portrait_style")
        if isinstance(_nested, dict) and (
            not passthrough_fields or "portrait_style" in passthrough_fields
        ):
            # 保留父级 portrait_style（不再 pop）：与运行态 DataStep 一致，大变量占位符
            # {portrait_style} 需在预览上下文里可读地体现，其子字段另按叶子名逐条展示。
            for _ck, _cv in _nested.items():
                if (isinstance(_ck, str) and not _ck.startswith("_")
                        and _cv not in (None, "", [], {}) and _ck not in _std):
                    _pt.setdefault(_ck, _cv)
        ctx.passthrough_context = _pt
    pkg = data.get("recommended_package") or {}
    diff = PackageDiff(ctx.current_package, pkg)

    # 与 ScriptStep._prepare_one_script_sync 命中模板后的取值方式一致
    tpl_prompt = template.get("prompt_template") or template.get("template_content", "")
    tpl_content = template.get("template_content", "")
    tpl_linked_vars = template.get("linked_vars", []) or []
    tpl_script_req = template.get("script_requirement", "") or ""

    try:
        max_length = int(data.get("max_length") or 100)
    except (TypeError, ValueError):
        max_length = 100

    return build_prompt(
        user_prompt_tpl=tpl_prompt,
        template_text=tpl_content,
        ctx=ctx,
        pkg=pkg,
        diff=diff,
        linked_vars=tpl_linked_vars,
        script_requirement=tpl_script_req,
        field_aliases=data.get("field_aliases") or {},
        max_length=max_length,
    )


__all__ = [
    "VAR_LABELS",
    "resource_context_prompt_vars",
    "append_prompt_extra_suffix",
    "build_prompt",
    "preview_prompt",
]
