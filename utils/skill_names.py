"""新建配置名称规则；前端与后端共享 schemas/skill_name_rules.json。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

RULES = json.loads((Path(__file__).resolve().parents[1] / "schemas/skill_name_rules.json").read_text(encoding="utf-8"))
_PATTERN = re.compile(RULES["pattern"])


def skill_name_error(value: Any) -> str:
    # 仅在新建/导入入口校验；历史配置的编辑、下线和删除不应被新规则阻断。
    if not isinstance(value, str):
        return "场景分类必须为文本"
    name = value.strip()
    if not name:
        return "场景分类不能为空"
    if not RULES["min_length"] <= len(name) <= RULES["max_length"]:
        return f"场景分类长度须为 {RULES['min_length']}～{RULES['max_length']} 个字符"
    if not _PATTERN.fullmatch(name):
        return RULES["format_error"]
    if name.lower() in RULES["reserved_names"]:
        return f"场景分类「{name}」为系统保留名称，请使用其他名称"
    return ""
