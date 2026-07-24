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
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from loguru import logger

from prompt.script_generation import (
    SCRIPT_CONTEXT_HEADER,
    SCRIPT_GEN_RULES,
    SCRIPT_LEGACY_USER_TEMPLATE,
    SCRIPT_OUTPUT_SUFFIX,
    SCRIPT_SYSTEM_HEADER,
    SCRIPT_TEMPLATE_HEADER,
)

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


# 推荐套餐派生变量（单位已归一：元 / GB / 分钟），供模板直接引用 {pkg_fee} 等；
# 值由 PackageDiff 的多别名提取器计算（兼容 initFee 分单位、offerFlow MB 单位等）
_DERIVED_PKG_VAR_LABELS: Dict[str, str] = {
    "pkg_fee":   "推荐套餐月费(元)",
    "pkg_flow":  "推荐套餐流量(GB)",
    "pkg_voice": "推荐套餐语音(分钟)",
}


def _fmt_num(v: Any) -> str:
    """数值格式化：整数值去掉小数点（188.0 → 188），None → 空串。"""
    if v is None:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if abs(f - round(f)) < 1e-9 else str(round(f, 2))


def _try_compute_compound(expr: str, extra_info: Dict[str, Any]) -> Optional[str]:
    """尝试解析并计算复合占位符，如 flow+giftFlow-tf。

    先按原样精确匹配 extra_info key；找不到则按 + / - 拆分为多个独立变量，
    对每个变量在 extra_info 中查找值，全部找到后按顺序运算得出结果。
    任一变量缺失 → 返回 None（调用方应跳过整句）。
    """
    if not expr:
        return None
    # 1) 精确匹配：extra_info 里直接有这个 key
    if isinstance(extra_info, dict) and expr in extra_info:
        return _fmt_num(extra_info[expr])
    # 2) 不含运算符 → 非复合占位符，交给上层处理
    if "+" not in expr and "-" not in expr:
        return None
    # 3) 拆分: 按 + / - 分割，同时保留运算符顺序
    import re as _re
    parts = _re.split(r"([+\-])", expr)  # e.g. ['flow','+','giftFlow','-','tf']
    values = []
    for p in parts:
        p = p.strip()
        if p in ("+", "-"):
            values.append(p)
            continue
        if not isinstance(extra_info, dict) or p not in extra_info:
            return None  # 任一变量缺失 → 不计算
        val = extra_info[p]
        try:
            values.append(float(val))
        except (TypeError, ValueError):
            return None  # 值不可运算
    # 4) 顺序计算（纯从左到右，与模板书写顺序一致）
    result = values[0] if isinstance(values[0], (int, float)) else 0.0
    i = 1
    while i < len(values):
        op = values[i]
        num = values[i + 1]
        if op == "+":
            result = result + num
        else:
            result = result - num
        i += 2
    return _fmt_num(result)


def _script_step_cls():
    """延迟获取 ScriptStep 类（格式化工具的宿主，均为无实例状态的 static/classmethod）。"""
    from steps.script_step import ScriptStep

    return ScriptStep


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
    """直传透传字段值格式化：标量转字符串，dict/list 转紧凑 JSON，None/空 → 空串。"""
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        import json

        try:
            return json.dumps(v, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(v)
    return str(v)


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
    template_ref = re.sub(r"\[[^\[\]]+\]", "", template_ref).strip()

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
            return ""

        # 模板实际使用的占位符 token（锚点对齐依据），兼容 {变量} / [变量] / {a+b-c} 格式
        tpl_tokens: List[str] = (
            re.findall(r"\{([\w+\-]+)\}", template_text or "")
            + re.findall(r"\[([\w+\-]+)\]", template_text or "")
        )
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
            emitted.update(_var_alias_group(var_key))
            emitted.add(var_key)
            emitted.add(anchor)
        # 直传透传通道：仅注入模板中实际引用的字段，模板未使用的字段不展示，
        # 避免 LLM 看到模板不需要的裸数据后随意借用（如把 flowStandard=5 当成"5GB流量"）。
        passthrough_ctx = getattr(ctx, "passthrough_context", None) or {}
        if isinstance(passthrough_ctx, dict):
            for pk, pv in passthrough_ctx.items():
                if pk in emitted:
                    continue
                # 只注入模板占位符中实际引用的字段，其余跳过
                if pk not in tpl_token_set:
                    continue
                val = _fmt_passthrough_value(pv)
                if val.strip() == "":
                    continue
                label = VAR_LABELS.get(pk, pk)
                context_lines.append(f"{label} {{{pk}}}：{val}")
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
                    emitted.update(_var_alias_group(token))
                    emitted.add(token)
                    continue
                if not isinstance(effective_extra_info, dict) or token not in effective_extra_info:
                    # 复合占位符（如 flow+giftFlow-tf）：尝试从 extra_info 拆分计算
                    if "+" in token or "-" in token:
                        result = _try_compute_compound(token, effective_extra_info or {})
                        if result is not None:
                            label = VAR_LABELS.get(token, token)
                            context_lines.append(f"{label} {{{token}}}：{result}")
                            emitted.add(token)
                    continue
                val = _fmt_passthrough_value(effective_extra_info.get(token))
                if val.strip() == "":
                    continue
                label = VAR_LABELS.get(token, token)
                context_lines.append(f"{label} {{{token}}}：{val}")
                emitted.add(token)
        # 主服务直传：未在关联变量中显式勾选时，仍注入非空的 extra_info / extra_context
        # 透传模式（passthrough_ctx 非空）下已逐字段展示，跳过整包 JSON 以免冗余。
        if "extra_info" not in emitted and ei_txt and not passthrough_ctx:
            context_lines.append(f"{VAR_LABELS['extra_info']} {{extra_info}}：{ei_txt}")
        if "extra_context" not in emitted and ec_txt:
            context_lines.append(f"{VAR_LABELS['extra_context']} {{extra_context}}：{ec_txt}")

        # 清理模板正文中未被解析的占位符（简单或复合）：直接移除所在整句话，
        # 避免 LLM 看到无值可填的占位符后张冠李戴（如把 internetAge=20 填到 totalFlow 里）。
        if template_text:
            import re as _re2
            for token in tpl_tokens:
                if token in emitted:
                    continue
                # 未解析的占位符 → 移除包含它的当前分句（以逗号/句号等为界）
                _escaped = _re2.escape(token)
                template_text = _re2.sub(
                    rf"[^。！？\n，,]*[\{{\[]\s*{_escaped}\s*[\}}\]][^。！？\n，,]*[。！？\n，,，]?",
                    "",
                    template_text,
                )
            # 清理残留的句首/句尾标点与空白
            template_text = _re2.sub(r'^[，,、。！？\s]+', '', template_text.strip())
            template_text = _re2.sub(r'[，,、\s]+$', '', template_text)
            # 模板所有句子均因占位符数据缺失被清除 → 返回空，
            # 由调用方跳过 LLM 直接使用降级话术
            if not template_text:
                return ""
            # 回扫：清理后模板里还剩下哪些占位符，上下文里也同步裁剪，
            # 避免 internetAge 等被注入但对应句子因同句其他占位符缺失一并移除后，
            # 变成孤立的"僵尸数据"游离在上下文中被 LLM 误用
            remaining_tokens = set(
                re.findall(r"\{([\w+\-]+)\}", template_text)
                + re.findall(r"\[([\w+\-]+)\]", template_text)
            )
            context_lines = [
                line for line in context_lines
                if any(tok in remaining_tokens for tok in re.findall(r"\{([\w+\-]+)\}", line) + re.findall(r"\[([\w+\-]+)\]", line))
            ]

        # 2) 分段拼装
        lines: List[str] = [SCRIPT_SYSTEM_HEADER]
        if context_lines:
            lines.append(SCRIPT_CONTEXT_HEADER)
            lines.extend(context_lines)
        if template_text:
            lines.append(SCRIPT_TEMPLATE_HEADER)
            lines.append(template_text)
        lines.append(SCRIPT_GEN_RULES)
        if script_requirement:
            lines.append(f"5. 话术要求：{script_requirement}")
        lines.append(SCRIPT_OUTPUT_SUFFIX)
        return "\n".join(lines)

    # ── 旧格式：user_prompt_tpl（向后兼容） ──────────────────────
    tpl = user_prompt_tpl or SCRIPT_LEGACY_USER_TEMPLATE
    try:
        out = tpl.format_map(fmt_vars)
        return append_prompt_extra_suffix(tpl, out, ei_txt, ec_txt)
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
        return append_prompt_extra_suffix(tpl, fb, ei_txt, ec_txt)


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
        _pt = {
            k: _ei[k] for k in _sel
            if isinstance(k, str) and not k.startswith("_")
            and k in _ei and k not in _std
            and _ei[k] not in (None, "", [], {})
        }
        # 与运行态 DataStep 一致：展开 portrait_style 一层，personality 子字段逐条预览
        _nested = _ei.get("portrait_style")
        if isinstance(_nested, dict):
            _pt.pop("portrait_style", None)
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
