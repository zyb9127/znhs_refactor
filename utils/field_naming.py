"""
字段名规范化工具（配置写入与运行时共用）

背景：接口映射的 field_rename / _unit_conversions.new_field 目标字段名若含畸形括号
（双括号、全/半角混用，如「近6月平均流量((GB)）」），运行时数据键将与话术模板
子字段占位符无法同名对齐，导致槽位取不到值（北京「用户消费信息未生效」根因之一）。

三层防护中的公共实现（单一数据源）：
  1. 配置写入 choke point —— services/skill_publisher.publish_config 保存 api_nodes 前统一清洗
     （覆盖管理端保存、整份保存、interface_mapper LLM 智能映射、AutoConfigAgent 导入等全部写路径）
  2. 运行时产出 —— steps/data_step.DataStep._apply_unit_convert 应用重命名时就地规范化
     （即使 ES 中已存在畸形配置，产出的数据键仍是规范形态）
  3. 巡检 —— management/config_agent/linter 对存量配置告警（W108）
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

__all__ = [
    "canon_key",
    "clean_rename_field",
    "dict_fuzzy_get",
    "match_config_keys",
    "normalize_field_transform_renames",
    "normalize_api_nodes_renames",
    "autofill_usage_renames",
]


# 上游用量字段的「实际」前缀：`实际近6月平均流量（GB）` 与话术模板占位符
# `近6月平均流量(GB)` 语义相同（都指历史真实用量），但 canon_key 不抹前缀 → 取不到。
# 见 autofill_usage_renames。
_ACTUAL_USAGE_PREFIX = "实际"


# 全角括号/空格 → 半角（键名归一第一档）
_KEY_BRACKET_TRANS = str.maketrans({"（": "(", "）": ")", "【": "[", "】": "]", "　": " "})


def canon_key(s: Any) -> str:
    """键名归一：去掉全部括号与空白，仅留核心文字。

    「近6月平均流量((GB)）」「近6月平均流量(GB)」「近6月平均流量（GB）」→ 同一 canonical。
    容忍接口出参、field_transform 配置、话术模板子键三方在括号形态上的不一致
    （全/半角混用、双括号、多余空格）。"""
    return re.sub(r"[()（）\[\]【】\s]+", "", str(s))


def dict_fuzzy_get(d: Dict[str, Any], key: str) -> Any:
    """dict 取值三档匹配：精确 → 全/半角括号归一相等 → 去括号 canonical 相等。取不到返回 None。"""
    if not isinstance(d, dict):
        return None
    if key in d:
        return d[key]
    nk = str(key).translate(_KEY_BRACKET_TRANS)
    for k, v in d.items():
        if str(k).translate(_KEY_BRACKET_TRANS) == nk:
            return v
    ck = canon_key(key)
    if ck:
        for k, v in d.items():
            if canon_key(k) == ck:
                return v
    return None


def match_config_keys(source: Dict[str, Any], config_keys: Any) -> Dict[str, str]:
    """把配置里声明的键名（include_keys / exclude_keys / unit_convert 的字段名）
    对齐到数据源里实际存在的键名。

    返回 ``{数据源实际键: 命中的配置键}``。精确同名优先，其次按 :func:`canon_key` 归一后相等。
    上游接口把「近6月平均流量(MB）」写成「近6月平均流量（MB）」这类括号差异不再导致
    整条 filter 规则落空（生产表现为 usage/tags 映射域静默为空、话术缺历史用量）。"""
    if not isinstance(source, dict) or not config_keys:
        return {}
    wanted = {str(k) for k in config_keys}
    canon_index: Dict[str, str] = {}
    for k in wanted:
        ck = canon_key(k)
        if ck:
            canon_index.setdefault(ck, k)
    hits: Dict[str, str] = {}
    for sk in source:
        name = str(sk)
        if name in wanted:
            hits[sk] = name
            continue
        ck = canon_key(name)
        if ck and ck in canon_index:
            hits[sk] = canon_index[ck]
    return hits


def clean_rename_field(name: str) -> str:
    """重命名目标字段名括号规范化（与前端 SkillConfigEditor._cleanRenameField 同源）：
    连续左/右括号折叠为一个半角括号、全/半角混用统一为半角。

    「近6月平均流量((GB)）」→「近6月平均流量(GB)」；已规范的名字原样返回。"""
    s = str(name or "")
    s = re.sub(r"[(（]{2,}", "(", s)
    s = re.sub(r"[)）]{2,}", ")", s)
    s = re.sub(r"\(([^()（）]*)）", r"(\1)", s)
    s = re.sub(r"（([^()（）]*)\)", r"(\1)", s)
    return s


def normalize_field_transform_renames(ft: Any) -> List[str]:
    """就地规范化单个节点 field_transform 中全部重命名目标字段名。

    覆盖两处目标名（运行真源 + 展示镜像）：
    - 各规则的 field_rename 值
    - _unit_conversions[].new_field

    返回被修正的名字列表（"旧 → 新"），空列表表示无需修正。"""
    fixed: List[str] = []
    if not isinstance(ft, dict):
        return fixed
    for key, rule in ft.items():
        if key == "_unit_conversions" and isinstance(rule, list):
            for conv in rule:
                if isinstance(conv, dict) and conv.get("new_field"):
                    cleaned = clean_rename_field(conv["new_field"])
                    if cleaned != conv["new_field"]:
                        fixed.append(f"{conv['new_field']} → {cleaned}")
                        conv["new_field"] = cleaned
            continue
        if isinstance(rule, dict) and isinstance(rule.get("field_rename"), dict):
            renames = rule["field_rename"]
            for src in list(renames.keys()):
                cleaned = clean_rename_field(renames[src])
                if cleaned != renames[src]:
                    fixed.append(f"{renames[src]} → {cleaned}")
                    renames[src] = cleaned
    return fixed


def normalize_api_nodes_renames(api_nodes: Any) -> List[str]:
    """就地规范化整份 api_nodes 中全部节点的重命名目标字段名。

    返回被修正的条目列表（"节点名: 旧 → 新"）。供 publish_config 在保存前统一调用，
    保证任何写路径（管理端 / LLM 智能映射 / 批量导入）都无法把畸形键名写入存储。"""
    fixed: List[str] = []
    if not isinstance(api_nodes, dict):
        return fixed
    for node_name, cfg in api_nodes.items():
        if str(node_name).startswith("_") or not isinstance(cfg, dict):
            continue
        for item in normalize_field_transform_renames(cfg.get("field_transform")):
            fixed.append(f"{node_name}: {item}")
    return fixed


def _strip_actual_prefix(name: str) -> str:
    """去掉用量字段名的「实际」前缀并规范括号：`实际近6月平均流量（GB）`→`近6月平均流量(GB)`。
    括号统一半角（对齐话术模板占位符 `(GB)` 习惯）；非「实际」开头仅做括号规范。"""
    s = str(name or "")
    if s.startswith(_ACTUAL_USAGE_PREFIX):
        s = s[len(_ACTUAL_USAGE_PREFIX):]
    s = s.translate(_KEY_BRACKET_TRANS)   # 全角括号 → 半角，产出键与模板占位符同形
    return clean_rename_field(s)


def autofill_usage_renames(api_nodes: Any) -> List[str]:
    """为 usage.* 映射规则里带「实际」前缀的 include_keys 自动补 field_rename → 去前缀规范名。

    根治「上游把 `近6月平均流量` 改叫 `实际近6月平均流量（GB）`、配置只补了 include_keys
    却漏写 field_rename」这一类漂移（生产表现：usage 映射域产出仍是 `实际…（GB）` 原名，
    话术模板占位符 `近6月平均流量(GB)` 全部落空）。放在唯一写路径上，任何入口
    （管理端保存 / LLM 智能映射 / 批量导入 / republish）写进来的 usage 映射都会被拉齐，
    产出键名对齐到「去实际前缀 + 半角括号」的稳定规范名，模板不必再随上游前缀变动而改。

    行为边界（保守、可审计、幂等）：
    - 仅处理 field_transform 中 **target 以 "usage" 开头** 且含 include_keys 的规则；
    - 仅对 **以「实际」开头** 的 include_key 生成重命名（其它键交给运行时括号模糊匹配，无需 rename）；
    - **尊重既有 field_rename**：该 include_key 已有显式重命名则跳过，绝不覆盖人工/历史约定
      （因此已用 `主叫时长/月消费` 约定的存量技能不受影响）；
    - 去前缀后为空、或与原名相同则跳过。

    返回新增的重命名条目列表（"节点.域: 旧 → 新"）。就地修改 api_nodes。"""
    added: List[str] = []
    if not isinstance(api_nodes, dict):
        return added
    for node_name, cfg in api_nodes.items():
        if str(node_name).startswith("_") or not isinstance(cfg, dict):
            continue
        ft = cfg.get("field_transform")
        if not isinstance(ft, dict):
            continue
        for tgt, rule in ft.items():
            if str(tgt).startswith("_") or not isinstance(rule, dict):
                continue
            if not str(tgt).startswith("usage"):
                continue
            inc = rule.get("include_keys")
            if not isinstance(inc, list) or not inc:
                continue
            renames = rule.get("field_rename")
            if not isinstance(renames, dict):
                renames = {}
            changed = False
            for k in inc:
                ks = str(k)
                if not ks.startswith(_ACTUAL_USAGE_PREFIX):
                    continue
                if ks in renames:            # 已有显式重命名 → 尊重，不覆盖
                    continue
                target = _strip_actual_prefix(ks)
                if not target or target == ks:
                    continue
                renames[ks] = target
                added.append(f"{node_name}.{tgt}: {ks} → {target}")
                changed = True
            if changed:
                rule["field_rename"] = renames
    return added
