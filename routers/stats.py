"""
统计分析路由（一期：分省模型调用量查询）

  GET /api/stats/llm/daily?start=YYYY-MM-DD&end=YYYY-MM-DD&province=
      按「省份 × 日期」查询 LLM 调用量统计行（含意图细分）。
  GET /api/stats/llm/provinces?start=&end=
      日期范围内出现过的省份列表（供前端筛选下拉）。

鉴权：路由按 management_router 同款挂载在 /znhs-gray 与 /znhs 双前缀下，自动获得
灵运 satoken 鉴权；此处再用 get_user_province 做「分省用户强制只看本省」，
本部/开发模式（返回 None）不限制。

dev 模式（IS_DEV，未初始化 Redis）时 llm_stats 禁用，接口返回
``{enabled: false, rows: []}``，前端据此友好提示，不报错。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger

from services.llm_stats import llm_stats
from utils.auth_utils import get_user_province

router = APIRouter(tags=["统计分析"])

# 日期范围上限（对齐 Redis TTL 40 天）
_MAX_RANGE_DAYS = 40


def _parse_date(s: str, name: str) -> date:
    try:
        return date.fromisoformat(str(s or "").strip())
    except ValueError:
        raise HTTPException(400, f"参数 {name} 日期格式非法（应为 YYYY-MM-DD）: {s!r}")


def _resolve_range(start: str, end: str) -> tuple:
    d0, d1 = _parse_date(start, "start"), _parse_date(end, "end")
    if d0 > d1:
        raise HTTPException(400, "start 不能晚于 end")
    if (d1 - d0).days >= _MAX_RANGE_DAYS:
        raise HTTPException(400, f"日期范围不能超过 {_MAX_RANGE_DAYS} 天")
    return d0, d1


@router.get("/api/stats/llm/daily")
async def get_llm_stats_daily(
    request: Request,
    start: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end: str = Query(..., description="结束日期 YYYY-MM-DD"),
    province: Optional[str] = Query(None, description="省份 code（分省用户强制只看本省）"),
):
    """分省模型调用量按日查询。"""
    d0, d1 = _resolve_range(start, end)
    if not llm_stats.enabled:
        return {"code": 200, "data": {"enabled": False, "rows": []}}
    # 分省用户强制只看本省；本部/开发模式（None）不限制
    user_province = get_user_province(request)
    eff_province = user_province or (str(province or "").strip() or None)
    rows = llm_stats.query_daily(d0.isoformat(), d1.isoformat(), province=eff_province)
    return {"code": 200, "data": {"enabled": True, "rows": rows}}


@router.get("/api/stats/llm/provinces")
async def get_llm_stats_provinces(
    request: Request,
    start: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end: str = Query(..., description="结束日期 YYYY-MM-DD"),
):
    """日期范围内出现过的省份列表（前端筛选下拉）。"""
    d0, d1 = _resolve_range(start, end)
    if not llm_stats.enabled:
        return {"code": 200, "data": {"enabled": False, "provinces": []}}
    # 分省用户下拉只给本省（看不了别省数据，给了也误导）
    user_province = get_user_province(request)
    if user_province:
        provinces = [user_province]
    else:
        provinces = llm_stats.list_provinces(d0.isoformat(), d1.isoformat())
    return {"code": 200, "data": {"enabled": True, "provinces": provinces}}
