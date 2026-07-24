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
    "clean_rename_field",
    "normalize_field_transform_renames",
    "normalize_api_nodes_renames",
]


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
