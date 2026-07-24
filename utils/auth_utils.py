"""
省份权限工具函数

鉴权流程由 services/lingyun_auth.py 的 LingyunAuthMiddleware 完成，
本模块只处理鉴权通过后的省份级写权限控制：

  - _get_user_province  : 从 request.state.lingyun_user 提取用户所属省份 code
  - _get_operator       : 获取操作人用户名（鉴权未启用时返回 fallback）
  - check_province_write: 检查用户是否有权对目标省份执行写操作

省份映射来源（优先级从高到低）：
  1. config/province_mapping.json（可选，运维维护）
  2. skill_registry 中已加载技能包的 meta.province_name 反查
  3. 兜底：dept_name 直接小写作为 province code
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Tuple, Dict

from fastapi import HTTPException, Request
from loguru import logger


def _load_province_mapping() -> Tuple[Dict[str, str], str]:
    """加载 config/province_mapping.json，返回 (mapping_dict, hq_dept_name)。"""
    path = Path(__file__).resolve().parents[1] / "config" / "province_mapping.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw.get("mapping", {}), raw.get("hq_dept_name", "本部")
    except Exception as e:
        logger.warning(f"加载 province_mapping.json 失败，使用空映射: {e}")
        return {}, "本部"


_PROVINCE_MAPPING, _HQ_DEPT_NAME = _load_province_mapping()

# 行政区后缀（按长度从长到短，避免「壮族自治区」被「自治区」提前截断）。
# 用于把灵运 deptName 的官方全称归一到 province_mapping.json 的短名，
# 使 31 个省级行政区在 deptName 带后缀时仍能正确识别。
_ADMIN_SUFFIXES = (
    "壮族自治区", "维吾尔自治区", "回族自治区",
    "特别行政区", "自治区", "省", "市",
)


def _normalize_dept_name(name: str) -> str:
    """去掉常见行政区后缀（省/市/自治区等），返回省份短名。

    例：北京市→北京、黑龙江省→黑龙江、内蒙古自治区→内蒙古、
        广西壮族自治区→广西、新疆维吾尔自治区→新疆、宁夏回族自治区→宁夏。
    """
    n = (name or "").strip()
    for suf in _ADMIN_SUFFIXES:
        if n.endswith(suf) and len(n) > len(suf):
            return n[: -len(suf)]
    return n


def get_user_province(request: Request) -> Optional[str]:
    """从鉴权信息中提取用户所属省份 code。

    返回值：
      - None  : 本部用户（无省份限制）或鉴权未启用（开发环境）
      - str   : 省份 code，如 "beijing"

    匹配顺序（覆盖全国 31 个省级行政区，不含港澳台）：
      1. deptName 精确命中 province_mapping.json；
      2. 去行政区后缀归一后再命中（兼容「北京市」「广西壮族自治区」等全称）；
      3. 从已加载技能包的 province_name 反查；
      4. 兜底：归一后的名字小写作为 code。
    """
    user = getattr(request.state, "lingyun_user", None)
    if user is None:
        return None  # 鉴权未启用，不限制

    dept_name = (user.user_info.dept_name or "").strip()
    if dept_name == _HQ_DEPT_NAME:
        return None  # 本部，无省份限制

    # 1. 精确查映射表
    if dept_name in _PROVINCE_MAPPING:
        return _PROVINCE_MAPPING[dept_name]

    # 2. 去行政区后缀归一后再查（兼容官方全称）
    normalized = _normalize_dept_name(dept_name)
    if normalized in _PROVINCE_MAPPING:
        return _PROVINCE_MAPPING[normalized]

    # 3. 从已加载技能包的 province_name 反查（同时兼容全称/短名）
    from utils.skill_runtime import skill_registry
    for s in skill_registry.list_all():
        meta_name = (s.get("meta") or {}).get("province_name", "")
        if meta_name and meta_name in (dept_name, normalized):
            return s["province"]

    # 4. 兜底：归一后的名字小写作为 code
    return normalized.lower()


def get_operator(request: Request, fallback: str = "admin") -> str:
    """从鉴权信息中获取操作人用户名，鉴权未启用时返回 fallback。"""
    user = getattr(request.state, "lingyun_user", None)
    if user is None:
        return fallback
    return user.user_info.username or fallback


def check_province_write(request: Request, target_province: str) -> None:
    """检查当前用户是否有权对目标省份执行写操作（增/删/改）。

    - 本部用户（user_province=None）或同省份用户：放行
    - 跨省份操作：抛 HTTP 403
    - 鉴权未启用（开发环境）：直接放行
    """
    user_province = get_user_province(request)
    if user_province is None:
        return  # 本部或鉴权未启用
    if user_province != target_province:
        raise HTTPException(
            status_code=403,
            detail=(
                f"无权操作其他省份数据"
                f"（您的省份：{user_province}，目标省份：{target_province}）"
            ),
        )
