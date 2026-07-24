"""
实时服务路由

对外实时接口，无需灵运鉴权：
  POST /marketing/recommend  — 营销话术推荐主接口
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field, ValidationError
from typing import Any, Dict, List, Optional

from utils.observability import begin_request_context, reset_request_context, summarize_request_context
from utils.skill_runtime import skill_registry

router = APIRouter(tags=["实时服务"])

# 省份编码 → 技能包 province key 映射表
# 下游系统可传入数字编码，由此表转为内部标准 code
PROVINCE_CODE_MAP: dict = {
    "200": "guangdong",
    # 如需扩展其他省份编码，在此继续添加
}


class RecommendRequest(BaseModel):
    phone: str = Field(..., description="手机号")
    intent: str = Field(..., description="业务意图，需与技能包目录名一致")
    province: str = Field(..., description="省份代码，如 beijing")
    topN: int = Field(
        default=3, ge=1, le=10,
        description="推荐产品数量，有推荐接口时生效，默认3；不调接口/无推荐场景可不传",
    )
    callId: str = Field(default="", description="请求追踪ID")
    extra_data: Dict[str, Any] = Field(
        default_factory=dict,
        description="调用外部接口的补充入参，如 currentMainOffer 等，透传给 DataStep 构造接口请求",
    )
    extra_info: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "直传模式入参：调用方已持有的用户/产品信息，不调外部接口。"
            "① 技能包配置了直传节点（api_nodes 中 source_type=direct）时，"
            "extra_info 会按节点映射规则写入 resource_context 7 大标准域；"
            "② 未配置映射时整体作为 JSON 注入所有话术的 LLM Prompt。"
            "接口查询模式下可为空。"
        ),
    )
    batch_contexts: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "话术生成上下文列表，支持单条或多条，非空时进入批量模式。\n"
            "每项可含（均可为空）：\n"
            "  product_id：产品ID\n"
            "  stage：环节\n"
            "  scence：场景\n"
            "  expand：布尔值，默认 false。true 且 scence 为空时，"
            "枚举 product_id+stage 下所有 scene，每个 scene 各生成一条话术（枚举模式）。\n"
            "数据来源：\n"
            "  调接口有推荐产品：product_id 可为空，推荐结果自动展开；\n"
            "  调接口无推荐产品/不调接口：product_id 非空则直接用入参产品匹配话术模板。\n"
            "匹配维度（省份+意图为基础，从细到粗）：\n"
            "  产品+环节+场景、产品+环节、产品+场景、环节+场景、仅产品、仅环节、仅场景、意图兜底。"
        ),
    )


def _unwrap_params_body(raw: Any) -> Any:
    """兼容下游/网关把入参包在最外层 params 里的写法：
    {"params": {...业务字段...}} → 解包为 {...业务字段...}；
    普通顶层写法原样返回。仅当 params 为 dict 且顶层未直接出现业务字段时才解包，
    避免误伤本就把 params 当业务字段的情况。
    """
    if not isinstance(raw, dict):
        return raw
    inner = raw.get("params")
    if isinstance(inner, dict) and not ({"phone", "intent", "province"} & set(raw.keys())):
        return inner
    return raw


@router.post("/marketing/recommend")
async def recommend(request: Request):
    """营销话术推荐主接口（实时对外服务，无需灵运 satoken）。

    入参格式参见接口文档（callId / intent / phone / topN / extra_data）。
    兼容两种 body：顶层直接放业务字段，或最外层包一层 {"params": {...}}。
    出参：{code, message, data: {callId, phone, intent, province, recommend_results}}
    """
    # 读取并（按需）解包 params，再做 Pydantic 校验，兼容网关包裹写法
    try:
        raw_body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    body_data = _unwrap_params_body(raw_body)
    if not isinstance(body_data, dict):
        raise HTTPException(status_code=400, detail="请求体格式错误：需为 JSON 对象")
    try:
        req = RecommendRequest(**body_data)
    except ValidationError as ve:
        # 与 FastAPI 默认体校验一致，返回 422 与字段级错误
        raise HTTPException(status_code=422, detail=ve.errors())

    trace_id = req.callId or f"req-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
    obs_token = begin_request_context(
        trace_id=trace_id, route="recommend-main",
        province=req.province, intent=req.intent,
    )
    t0 = time.perf_counter()
    logger.info(
        f"[recommend] ▶ 请求开始  trace_id={trace_id} "
        f"province={req.province} intent={req.intent} phone={req.phone[:3]}****"
    )

    try:
        # 省份编码转换：支持下游传入数字编码（如 "200" → "guangdong"）
        province_key = PROVINCE_CODE_MAP.get(req.province, req.province)
        if province_key != req.province:
            logger.info(
                f"[recommend] 省份编码转换: {req.province!r} → {province_key!r}"
            )

        executor = skill_registry.get_executor(province_key, req.intent)
        if executor is None:
            logger.warning(f"[recommend] 未找到技能包: {province_key}:{req.intent}")
            raise HTTPException(
                status_code=404,
                detail=f"未找到技能包: province={province_key} intent={req.intent}",
            )

        # 将转换后的 province_key 写入请求数据，确保技能包内部使用标准 code
        req_data = req.model_dump()
        req_data["province"] = province_key
        result = await executor.execute(req_data)

        elapsed = (time.perf_counter() - t0) * 1000
        logger.info(
            f"[recommend] ✅ 完成  trace_id={trace_id}  "
            f"elapsed={elapsed:.0f}ms  scripts={len(result.get('recommend_results', []))}"
        )
        summarize_request_context()
        return JSONResponse({
            "code": 200,
            "message": "success",
            "data": {
                "callId": trace_id,
                "phone": req.phone,
                "intent": req.intent,
                "province": req.province,
                "recommend_results": result.get("recommend_results", []),
            },
            "other_info": result.get("other_info"),
        })

    except HTTPException:
        raise
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        logger.exception(f"[recommend] ❌ 异常  trace_id={trace_id}  elapsed={elapsed:.0f}ms")
        return JSONResponse({"code": 500, "message": str(exc), "data": None}, status_code=500)
    finally:
        reset_request_context(obs_token)
