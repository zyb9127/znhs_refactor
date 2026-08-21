"""
省份编码归一

下游系统传入的省份标识有三种形态，统一转成系统内 province code（技能包目录名）：
  1. 已是系统 code（``beijing``）      → 原样；
  2. 中文省名（``广东``）              → config/province_mapping.json ``mapping``；
  3. 数字省码（``371`` / ``200``）     → config/province_mapping.json ``numeric_mapping``。

映射表放配置文件而非写死在代码里：交叉营销触点各省码由对端约定，出现偏差时运维
改 JSON 即可，不必改代码发版。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from loguru import logger

_MAPPING_PATH = Path(__file__).resolve().parents[1] / "config" / "province_mapping.json"

_cache: Dict[str, str] = {}


def _load() -> Dict[str, str]:
    """合并中文省名与数字省码两张表（键统一 strip，值为系统 province code）。"""
    merged: Dict[str, str] = {}
    try:
        with open(_MAPPING_PATH, encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception as exc:  # noqa: BLE001 - 配置缺失不应阻断服务启动
        logger.warning(f"[ProvinceCode] 读取 {_MAPPING_PATH} 失败，省份编码映射为空: {exc}")
        return merged
    for section in ("mapping", "numeric_mapping"):
        table = data.get(section)
        if not isinstance(table, dict):
            continue
        for raw, code in table.items():
            key = str(raw).strip()
            if not key or str(key).startswith("_") or not isinstance(code, str):
                continue
            merged[key] = code.strip()
    return merged


def province_code_map() -> Dict[str, str]:
    """全量映射表（首次调用时加载并缓存）。"""
    global _cache
    if not _cache:
        _cache = _load()
    return _cache


def resolve_province(raw: str) -> str:
    """省份标识 → 系统 province code；映射不到时原样返回（交由上层报「技能包不存在」）。"""
    key = str(raw or "").strip()
    if not key:
        return key
    table = province_code_map()
    if key in table:
        return table[key]
    # 数字码兼容前导零写法（"020" ↔ "20"）
    if key.isdigit():
        for variant in (key.lstrip("0"), key.zfill(3)):
            if variant and variant in table:
                return table[variant]
    return key


def reload() -> None:
    """清空缓存（配置热更新后调用）。"""
    global _cache
    _cache = {}
