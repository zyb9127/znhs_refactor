"""
确定性配置 Lint（设计态治理工具，纯内存计算，不做任何网络/存储调用）

对齐规格书 §7，检查项编号固定（便于前端归类）：
- E101 模板缺 template_content / template_name
- E102 status 非法值（合法：online/offline/deleted，缺省视为 online）
- E103 template_id 重复
- E201 field_transform 的 from 槽位在 response_extract 中不存在（direct 零配置节点豁免）
- W101 模板 province 字段与包 province 不一致
- W102 模板 intent 字段与包 intent 不一致
- W103 同一 template_content 混用 {var} 与 {{var}} 两种占位符语法
- W104 template_content 的占位符不在已知变量（standard_domains ∪ linked_vars ∪ 生成变量）内
- W105 linked_vars 含未知变量名
- W106 response_extract 槽位既非 7 大标准域也没有任何 field_transform 引用（悬空槽位）
- W107 (intent, product_id, stage, scene) 维度完全相同的 online 模板 >1 条（引冲突检测）
- W108 field_rename / _unit_conversions.new_field 目标字段名含畸形括号（双括号/全半角混用，
       运行时数据键与模板子字段占位符无法精确同名对齐；存量配置巡检）
- I101 模板无 priority 字段（提示可用新字段控制优先级）

返回结构统一：{"errors": [...], "warnings": [...], "info": [...], "stats": {...}}，
每条 {"code", "level", "path", "message"}。

依赖说明：schemas 包（standard_domains 单一真源）由并行任务提供，import 失败时
W104/W105 使用内置最小 fallback 变量集合（照抄 steps/script_step.py L770-784 的
_VAR_LABELS 键集合）降级运行，不影响其余检查项。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger

from management.config_agent.conflict_detector import detect_conflicts

# ── 占位符提取（规格 §7 指定 regex）─────────────────────────────
# group(1) = {{var}} 双花括号命中；group(2) = {var} 单花括号命中
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}|\{(\w+)\}")

# 子字段路径占位符 {域[子键]} / {域[子键1][子键2]}：group(1)=根域名
# （方括号内子键可含中文/括号/点号，故不与单花括号 regex 重叠，需单独提取校验）
_SUBFIELD_PLACEHOLDER_RE = re.compile(r"\{(\w+)(?:\[[^\[\]]+\])+\}")

# 子字段占位符允许的根域（标准域 + 历史别名 + 推荐条派生名）——build_prompt._subfield_roots 同源
_SUBFIELD_ROOT_KEYS: Set[str] = {
    "current_package", "cur_brief", "cur_name",
    "usage", "usage_line", "tags", "user_tags",
    "user_info", "user_profile", "domain_ext",
    "pkg_brief", "pkg_name", "recommended_package", "extra_info",
}

# ── 合法 status 枚举（缺省/空串按 online 处理）──────────────────
_VALID_STATUS: Set[str] = {"online", "offline", "deleted"}

# ── 7 大标准域（与 steps/data_step.py 白名单一致）────────────────
_STANDARD_DOMAIN_KEYS: Set[str] = {
    "current_package", "usage", "tags", "user_info",
    "recommended_packages", "user_profile", "domain_ext",
}

# ── W104/W105 fallback 已知变量集合 ──────────────────────────────
# 照抄 steps/script_step.py L770-784 `_VAR_LABELS` 的键集合
_FALLBACK_VAR_LABEL_KEYS: Set[str] = {
    "cur_brief", "current_package", "pkg_brief", "diff_str",
    "usage_line", "usage", "user_tags", "tags", "user_info",
    "user_profile", "domain_ext", "extra_info", "extra_context",
}

# 生成变量（规格 §4 generated_vars）
_GENERATED_VARS: Set[str] = {
    "pkg_brief", "diff_str", "table", "cur_brief",
    "usage_line", "user_tags", "reason_line",
}

# 固定占位符名（{{PHONE}} 等，规格 §4 fixed_params，正文中出现不告警）
_FIXED_PARAM_NAMES: Set[str] = {"PHONE", "INTENT", "CALL_ID", "PROVINCE", "TOP_N"}

# _build_prompt fmt_vars 额外支持的合法占位符（避免对可正常渲染的变量误报）
_PROMPT_EXTRA_VARS: Set[str] = {
    "intent", "max_length", "template", "template_ref",
    "template_content", "reason_line", "recommended_packages",
    "cur_name", "pkg_name",
}

# 已知变量集合的模块级缓存（schemas 数据模块加载后不变，缓存安全）
_known_vars_cache: Optional[Set[str]] = None


def _collect_keys_recursive(node: Any, out: Set[str]) -> None:
    """从 standard_domains.json 的任意结构里防御式收集变量名。

    兼容并行任务可能采用的多种结构：
    - dict 值（如 var_labels: {key: label}）→ 收集键
    - list[dict]（如 domains: [{key, label, desc}]）→ 收集每项的 key
    - list[str]（如 generated_vars）→ 收集元素
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                out.add(str(key))
            else:
                _collect_keys_recursive(value, out)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, str):
                out.add(item)
            elif isinstance(item, dict):
                key = item.get("key")
                if isinstance(key, str) and key:
                    out.add(key)


def _get_known_vars() -> Set[str]:
    """已知变量全集：schemas.get_standard_domains()（可用时）∪ 内置 fallback。

    schemas 包由并行任务编写，import/调用失败时静默降级为 fallback 集合。
    """
    global _known_vars_cache
    if _known_vars_cache is not None:
        return _known_vars_cache

    known: Set[str] = set()
    known |= _FALLBACK_VAR_LABEL_KEYS
    known |= _GENERATED_VARS
    known |= _FIXED_PARAM_NAMES
    known |= _STANDARD_DOMAIN_KEYS
    known |= _PROMPT_EXTRA_VARS

    try:
        from schemas import get_standard_domains  # 延迟 import，并行任务提供
        domains = get_standard_domains()
        if isinstance(domains, dict):
            _collect_keys_recursive(domains, known)
    except Exception as exc:  # noqa: BLE001 - 降级路径，任何异常都走 fallback
        logger.debug(f"[ConfigLinter] schemas 不可用，W104/W105 使用内置 fallback 变量集合: {exc}")

    _known_vars_cache = known
    return known


def _item(code: str, level: str, path: str, message: str) -> Dict[str, str]:
    """构造单条检查结果。"""
    return {"code": code, "level": level, "path": path, "message": message}


def _empty_report() -> Dict[str, Any]:
    """空报告骨架。"""
    return {"errors": [], "warnings": [], "info": [], "stats": {}}


def _extract_placeholders(content: str) -> Tuple[List[str], List[str]]:
    """提取正文占位符，返回 (双花括号名列表, 单花括号名列表)。"""
    doubles: List[str] = []
    singles: List[str] = []
    for match in _PLACEHOLDER_RE.finditer(content or ""):
        if match.group(1) is not None:
            doubles.append(match.group(1))
        elif match.group(2) is not None:
            singles.append(match.group(2))
    return doubles, singles


def _lint_one_template(
    template: Dict[str, Any], province: str, intent: str, path: str,
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """单条模板的全部单体检查项（E101/E102/W101-W105/I101）。

    返回 (errors, warnings, info)。跨模板检查（E103/W107）由 lint_biz_config 负责。
    """
    errors: List[Dict] = []
    warnings: List[Dict] = []
    info: List[Dict] = []

    # E101 缺必填字段
    if not str(template.get("template_name") or "").strip():
        errors.append(_item(
            "E101", "error", f"{path}.template_name",
            "模板缺少 template_name（必填）",
        ))
    if not str(template.get("template_content") or "").strip():
        errors.append(_item(
            "E101", "error", f"{path}.template_content",
            "模板缺少 template_content（必填）",
        ))

    # E102 status 非法值（缺省/空串按 online 处理，不报错）
    status_raw = template.get("status")
    if status_raw not in (None, ""):
        status = str(status_raw).strip()
        if status not in _VALID_STATUS:
            errors.append(_item(
                "E102", "error", f"{path}.status",
                f"status 非法值 {status!r}，合法值: online/offline/deleted",
            ))

    # W101 模板 province 与包 province 不一致（两者均非空才比较）
    t_province = str(template.get("province") or "").strip()
    if t_province and str(province or "").strip() and t_province != str(province).strip():
        warnings.append(_item(
            "W101", "warning", f"{path}.province",
            f"模板 province={t_province!r} 与包 province={province!r} 不一致",
        ))

    # W102 模板 intent 与包 intent 不一致（两者均非空才比较）
    t_intent = str(template.get("intent") or "").strip()
    if t_intent and str(intent or "").strip() and t_intent != str(intent).strip():
        warnings.append(_item(
            "W102", "warning", f"{path}.intent",
            f"模板 intent={t_intent!r} 与包 intent={intent!r} 不一致",
        ))

    # 占位符相关检查
    content = str(template.get("template_content") or "")
    doubles, singles = _extract_placeholders(content)

    # W103 混用两种占位符语法
    if doubles and singles:
        warnings.append(_item(
            "W103", "warning", f"{path}.template_content",
            f"同一正文混用 {{var}} 与 {{{{var}}}} 两种占位符语法"
            f"（双花括号: {sorted(set(doubles))}，单花括号: {sorted(set(singles))}）",
        ))

    # 已知变量 = standard_domains 已知变量 ∪ 本模板 linked_vars ∪ 生成变量
    known = _get_known_vars()
    linked_vars_raw = template.get("linked_vars")
    linked_vars = [
        str(v) for v in linked_vars_raw
        if isinstance(v, (str, int))
    ] if isinstance(linked_vars_raw, list) else []
    allowed = known | set(linked_vars)

    # W104 正文占位符未知
    unknown_placeholders = []
    for name in doubles + singles:
        if name not in allowed and name not in unknown_placeholders:
            unknown_placeholders.append(name)
    for name in unknown_placeholders:
        warnings.append(_item(
            "W104", "warning", f"{path}.template_content",
            f"占位符 {{{name}}} 不在已知变量集合（标准域/linked_vars/生成变量）内，运行时可能无法渲染",
        ))

    # W104（子字段占位符）：{域[子键]} 的根域须是已知标准域/别名，否则运行时取不到值
    unknown_sub_roots: List[str] = []
    for m in _SUBFIELD_PLACEHOLDER_RE.finditer(content):
        root = m.group(1)
        if (root not in _SUBFIELD_ROOT_KEYS and root not in allowed
                and root not in unknown_sub_roots):
            unknown_sub_roots.append(root)
    for root in unknown_sub_roots:
        warnings.append(_item(
            "W104", "warning", f"{path}.template_content",
            f"子字段占位符根域 {{{root}[...]}} 不是已知标准域（可用根域：当前套餐/历史用量/"
            f"用户标签/用户信息/画像/推荐产品等），运行时可能无法取到子字段值",
        ))

    # W105 linked_vars 含未知变量名
    for var in linked_vars:
        if var not in known:
            warnings.append(_item(
                "W105", "warning", f"{path}.linked_vars",
                f"linked_vars 含未知变量名 {var!r}",
            ))

    # I101 无 priority 字段
    if "priority" not in template:
        info.append(_item(
            "I101", "info", f"{path}.priority",
            "模板未设置 priority 字段，可用该字段控制同维度模板的优先级（越大越优先）",
        ))

    return errors, warnings, info


def lint_template(template: Dict[str, Any], province: str, intent: str) -> Dict[str, Any]:
    """单条模板 lint（供 agent proposal 校验/模板保存接口使用）。

    参数:
        template: 单条模板 dict
        province: 包省份（用于 W101 比对）
        intent:   包意图（用于 W102 比对）

    返回:
        {"errors": [...], "warnings": [...], "info": [...], "stats": {}}
    """
    report = _empty_report()
    if not isinstance(template, dict):
        report["errors"].append(_item(
            "E101", "error", "", "模板必须是 JSON 对象",
        ))
        return report

    errors, warnings, info = _lint_one_template(template, province, intent, path="template")
    report["errors"] = errors
    report["warnings"] = warnings
    report["info"] = info
    return report


def lint_biz_config(biz_config: Dict[str, Any], province: str, intent: str) -> Dict[str, Any]:
    """biz_config 全量 lint（模板单体检查 + 跨模板检查 + 统计）。

    参数:
        biz_config: biz_config.json 内容
        province:   包省份
        intent:     包意图

    返回:
        {"errors": [...], "warnings": [...], "info": [...],
         "stats": {"template_total", "online_count", "scene_count",
                   "product_count", "max_conflict_group"}}
    """
    report = _empty_report()
    if not isinstance(biz_config, dict):
        report["errors"].append(_item(
            "E101", "error", "", "biz_config 必须是 JSON 对象",
        ))
        return report

    templates = biz_config.get("script_templates_v2")
    if not isinstance(templates, list):
        templates = []

    # ── 单模板检查 ────────────────────────────────────────────
    for idx, tpl in enumerate(templates):
        path = f"script_templates_v2[{idx}]"
        if not isinstance(tpl, dict):
            report["errors"].append(_item(
                "E101", "error", path, "模板必须是 JSON 对象",
            ))
            continue
        errors, warnings, info = _lint_one_template(tpl, province, intent, path)
        report["errors"].extend(errors)
        report["warnings"].extend(warnings)
        report["info"].extend(info)

    # ── E103 template_id 重复（跨模板）───────────────────────
    seen_ids: Dict[str, int] = {}
    for idx, tpl in enumerate(templates):
        if not isinstance(tpl, dict):
            continue
        tid = str(tpl.get("template_id") or "").strip()
        if not tid:
            continue
        if tid in seen_ids:
            report["errors"].append(_item(
                "E103", "error", f"script_templates_v2[{idx}].template_id",
                f"template_id {tid!r} 与 script_templates_v2[{seen_ids[tid]}] 重复",
            ))
        else:
            seen_ids[tid] = idx

    # ── W107 同维度 online 模板冲突（引冲突检测）─────────────
    conflict_report = detect_conflicts(templates, intent)
    max_conflict_group = 0
    for conflict in conflict_report.get("exact_conflicts", []):
        ids = conflict.get("template_ids", [])
        max_conflict_group = max(max_conflict_group, len(ids))
        dims = conflict.get("dims", {})
        report["warnings"].append(_item(
            "W107", "warning", "script_templates_v2",
            f"维度 (product_id={dims.get('product_id')!r}, stage={dims.get('stage')!r}, "
            f"scene={dims.get('scene')!r}) 下存在 {len(ids)} 条 online 模板 {ids}，"
            f"运行时仅命中 {conflict.get('winner')!r}（判胜依据: {conflict.get('winner_reason')}）",
        ))

    # ── stats ─────────────────────────────────────────────────
    dict_templates = [t for t in templates if isinstance(t, dict)]
    online_count = sum(
        1 for t in dict_templates
        if str(t.get("status") or "online").strip() == "online"
    )
    scenes = {
        str(t.get("scene") or "").strip()
        for t in dict_templates if str(t.get("scene") or "").strip()
    }
    products = {
        str(t.get("product_id") or "").strip()
        for t in dict_templates if str(t.get("product_id") or "").strip()
    }
    report["stats"] = {
        "template_total": len(dict_templates),
        "online_count": online_count,
        "scene_count": len(scenes),
        "product_count": len(products),
        "max_conflict_group": max_conflict_group,
    }
    return report


def lint_api_nodes(api_nodes: Dict[str, Any], province: str, intent: str) -> Dict[str, Any]:
    """api_nodes 全量 lint（E201 悬空 from 槽位 / W106 悬空 response_extract 槽位）。

    参数:
        api_nodes: api_nodes.json 内容（key 为节点名，"_" 开头忽略）
        province:  包省份（当前检查项未用，保留签名一致性）
        intent:    包意图（同上）

    返回:
        {"errors": [...], "warnings": [...], "info": [...], "stats": {"node_count", "enabled_count"}}
    """
    report = _empty_report()
    if not isinstance(api_nodes, dict):
        report["errors"].append(_item(
            "E201", "error", "", "api_nodes 必须是 JSON 对象",
        ))
        return report

    node_count = 0
    enabled_count = 0
    for node_name, node in api_nodes.items():
        # 与 DataStep 一致：下划线开头的 key 视为注释/元数据，忽略
        if str(node_name).startswith("_") or not isinstance(node, dict):
            continue
        node_count += 1
        if node.get("enabled", True):
            enabled_count += 1

        response_extract = node.get("response_extract")
        if not isinstance(response_extract, dict):
            response_extract = {}
        field_transform = node.get("field_transform")
        if not isinstance(field_transform, dict):
            field_transform = {}

        is_direct = str(node.get("source_type") or "").strip() == "direct"
        # direct 零配置节点（无 response_extract）：extra_info 顶层同名域直接透传，豁免 E201
        skip_e201 = is_direct and not response_extract

        # E201 field_transform 的 from 槽位不存在于 response_extract
        referenced_slots: Set[str] = set()
        for target, rule in field_transform.items():
            # 下划线开头为内部 meta 键（如 _unit_conversions，值为 list 而非转换规则），
            # 不是真实转换规则，跳过 E201/引用统计，避免误报 from 槽位不存在。
            if str(target).startswith("_"):
                continue
            # from 缺省等于目标路径（与 data_step.py L327 语义一致）
            if isinstance(rule, dict):
                effective_from = str(rule.get("from") or target)
            else:
                effective_from = str(target)
            referenced_slots.add(effective_from)
            if skip_e201:
                continue
            if effective_from not in response_extract:
                report["errors"].append(_item(
                    "E201", "error",
                    f"{node_name}.field_transform.{target}",
                    f"from 槽位 {effective_from!r} 在 response_extract 中不存在",
                ))

        # W106 response_extract 槽位悬空（既非 7 大标准域也无 field_transform 引用）
        for slot in response_extract:
            slot_name = str(slot)
            if slot_name.startswith("_"):
                continue  # 与 DataStep 一致，下划线槽位忽略
            if slot_name in _STANDARD_DOMAIN_KEYS:
                continue  # 标准域槽位自动透传
            if slot_name in referenced_slots:
                continue
            report["warnings"].append(_item(
                "W106", "warning",
                f"{node_name}.response_extract.{slot_name}",
                f"槽位 {slot_name!r} 既非 7 大标准域也没有任何 field_transform 引用（悬空槽位）",
            ))

        # W108 重命名目标字段名含畸形括号（双括号 / 全半角混用）——
        # 运行时数据键将与话术模板子字段占位符无法精确同名对齐（存量配置巡检；
        # 新保存已被 publish_config / DataStep 双层规范化，此处提示尽早清洗配置）
        try:
            from utils.field_naming import clean_rename_field  # 延迟 import
        except Exception:  # noqa: BLE001 - util 不可用时跳过该检查
            clean_rename_field = None
        if clean_rename_field is not None:
            for target, rule in field_transform.items():
                if str(target) == "_unit_conversions" and isinstance(rule, list):
                    for idx, conv in enumerate(rule):
                        nf = conv.get("new_field") if isinstance(conv, dict) else None
                        if nf and clean_rename_field(nf) != nf:
                            report["warnings"].append(_item(
                                "W108", "warning",
                                f"{node_name}.field_transform._unit_conversions[{idx}]",
                                f"重命名目标字段名 {nf!r} 含畸形括号，建议改为 "
                                f"{clean_rename_field(nf)!r}（否则模板子字段占位符需依赖模糊匹配）",
                            ))
                    continue
                if isinstance(rule, dict) and isinstance(rule.get("field_rename"), dict):
                    for src, dst in rule["field_rename"].items():
                        if dst and clean_rename_field(dst) != dst:
                            report["warnings"].append(_item(
                                "W108", "warning",
                                f"{node_name}.field_transform.{target}.field_rename",
                                f"重命名目标字段名 {dst!r} 含畸形括号，建议改为 "
                                f"{clean_rename_field(dst)!r}（否则模板子字段占位符需依赖模糊匹配）",
                            ))

    report["stats"] = {"node_count": node_count, "enabled_count": enabled_count}
    return report


def lint_package(province: str, intent: str) -> Dict[str, Any]:
    """从 skill_registry 读取指定技能包配置并跑全量 lint（biz_config + api_nodes）。

    延迟 import registry，避免模块加载期的服务依赖。

    返回:
        合并报告，路径分别带 "biz_config." / "api_nodes." 前缀以便定位。
    """
    report = _empty_report()
    try:
        from utils.skill_runtime import skill_registry  # 延迟 import
        pkg = skill_registry.get(province, intent)
    except Exception as exc:  # noqa: BLE001 - registry 不可用属于环境问题，转错误项
        report["errors"].append(_item(
            "E000", "error", "",
            f"读取技能包失败: {exc}",
        ))
        return report

    if pkg is None:
        report["errors"].append(_item(
            "E000", "error", "",
            f"技能包不存在: {province}:{intent}",
        ))
        return report

    config = pkg.config or {}
    biz_report = lint_biz_config(config.get("biz_config") or {}, province, intent)
    api_report = lint_api_nodes(config.get("api_nodes") or {}, province, intent)

    def _prefixed(items: List[Dict], prefix: str) -> List[Dict]:
        out = []
        for item in items:
            copied = dict(item)
            copied["path"] = f"{prefix}.{copied['path']}" if copied.get("path") else prefix
            out.append(copied)
        return out

    for level in ("errors", "warnings", "info"):
        report[level].extend(_prefixed(biz_report[level], "biz_config"))
        report[level].extend(_prefixed(api_report[level], "api_nodes"))

    report["stats"] = {**biz_report.get("stats", {}), **api_report.get("stats", {})}
    return report
