"""
接口映射 LLM Prompt 常量（薄 re-export，兼容历史 import 路径）

提示词定义已统一迁移到项目级 prompt/ 目录，见 prompt/interface_mapping.py。
本模块仅做转发，保持 `from management.interface_mapper.scripts.prompts import ...`
的历史调用方零改动，不改变任何模型调用行为。
"""

from prompt.interface_mapping import (  # noqa: F401  re-export（向后兼容）
    AUTO_MAP_SYSTEM_PROMPT,
    PARSE_DOCX_SYSTEM_PROMPT,
    REFINE_MAPPING_SYSTEM_PROMPT,
)

__all__ = [
    "AUTO_MAP_SYSTEM_PROMPT",
    "PARSE_DOCX_SYSTEM_PROMPT",
    "REFINE_MAPPING_SYSTEM_PROMPT",
]
