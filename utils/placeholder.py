"""
公共占位符映射表

统一维护主服务入参 → 占位符 的双向映射，供以下模块共同使用：
  - steps/data_step.py          (_build_params 中的 fixed_vars)
  - management/interface_mapper/scripts/tools.py  (match_params 通过 main_service_params.json)

数据来源：management/interface_mapper/config/main_service_params.json
  placeholders 字段中 source 不含 "extra_data." 前缀的条目 = 主服务固定入参
  source 含 "extra_data." 前缀的条目 = extra_data 字段（DataStep 通过 _flatten_extra_data 自动处理）

FIXED_PARAM_MAP 格式：
  { "{{PHONE}}": ("ctx.phone", "phone"), ... }
  key   = 占位符字符串
  value = (FlowContext 属性路径, 人类可读名称)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

# ── 从 main_service_params.json 动态加载，保证单一数据源 ──────────
_CFG_PATH = Path(__file__).parents[1] / "management" / "interface_mapper" / "config" / "main_service_params.json"

# FlowContext source 路径 → ctx 属性路径映射
_SOURCE_TO_CTX: Dict[str, str] = {
    "phone":    "ctx.phone",
    "intent":   "ctx.intent",
    "callId":   "ctx.trace_id",
    "province": "ctx.province",
    "topN":     "ctx.top_n",
}


def _load_fixed_param_map() -> Dict[str, Tuple[str, str]]:
    """从 main_service_params.json 加载主服务固定参数占位符映射。

    只加载 source 不含 'extra_data.' 的条目（extra_data 字段由 DataStep._flatten_extra_data 处理）。
    """
    try:
        with open(_CFG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        result: Dict[str, Tuple[str, str]] = {}
        for ph, info in cfg.get("placeholders", {}).items():
            source = info.get("source", "")
            if source.startswith("extra_data."):
                continue  # extra_data 字段由 _flatten_extra_data 自动处理
            ctx_path = _SOURCE_TO_CTX.get(source, "")
            readable = source  # 可读名称直接用 source 字段（phone/intent/callId 等）
            if ctx_path:
                result[ph] = (ctx_path, readable)
        return result
    except Exception:
        # 配置文件不存在时降级为内置默认值，保证运行时不崩溃
        return {
            "{{PHONE}}":        ("ctx.phone",    "phone"),
            "{{PHONE_NUMBER}}": ("ctx.phone",    "phone"),
            "{{INTENT}}":       ("ctx.intent",   "intent"),
            "{{CALL_ID}}":      ("ctx.trace_id", "callId"),
            "{{TASK_ID}}":      ("ctx.trace_id", "callId"),
            "{{PROVINCE}}":     ("ctx.province", "province"),
            "{{TOP_N}}":        ("ctx.top_n",    "topN"),
        }


# 模块级缓存（进程内单次加载）
FIXED_PARAM_MAP: Dict[str, Tuple[str, str]] = _load_fixed_param_map()

# 兼容旧格式：{{PHONE_NUMBER}} / {{TASK_ID}} 别名（main_service_params.json 未收录）
_ALIASES: Dict[str, Tuple[str, str]] = {
    "{{PHONE_NUMBER}}": ("ctx.phone",    "phone"),
    "{{TASK_ID}}":      ("ctx.trace_id", "callId"),
}
for _ph, _val in _ALIASES.items():
    FIXED_PARAM_MAP.setdefault(_ph, _val)

# 仅占位符 → 可读名称（用于前端展示）
PLACEHOLDER_TO_READABLE: Dict[str, str] = {
    ph: readable for ph, (_, readable) in FIXED_PARAM_MAP.items()
}

# 可读名称 → 占位符（反向查找，去重保留主占位符）
READABLE_TO_PLACEHOLDER: Dict[str, str] = {}
for _ph, (_, _readable) in FIXED_PARAM_MAP.items():
    if _readable not in READABLE_TO_PLACEHOLDER:
        READABLE_TO_PLACEHOLDER[_readable] = _ph


def dig_subfield(sample: Any, path: str) -> Tuple[str, Any]:
    """按点路径取嵌套子字段，返回 ``(叶子名, 值)``；任一级取不到返回 ``("", None)``。

    透传字段（passthrough_fields）支持 ``portrait_style.communication_style``
    这类子路径写法：运行时按**叶子名**注入上下文，话术模板即用 ``{communication_style}``
    引用。运行时（DataStep）、预览（preview_prompt）、配置期调色板（management）
    共用此函数，保证三处口径一致。
    """
    cur: Any = sample
    parts = [p for p in str(path or "").split(".") if p]
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return "", None
        cur = cur[p]
    return (parts[-1] if parts else ""), cur
