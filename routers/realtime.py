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

from routers.cross_sell import handle_marketing_assistant_payload
from utils.marketing_assistant import is_marketing_assistant_payload
from utils.observability import begin_request_context, reset_request_context, summarize_request_context
from utils.province_code import province_code_map, resolve_province
from utils.skill_runtime import skill_registry
from utils import province_logger

router = APIRouter(tags=["实时服务"])

# 省份编码 → 技能包 province key 映射表（中文省名 + 数字省码，真源在
# config/province_mapping.json，运维可直接改配置扩省，不必改代码）。
# 保留本模块级名称是为了兼容既有引用；取值请统一走 utils.province_code.resolve_province。
PROVINCE_CODE_MAP: dict = province_code_map()


class RecommendRequest(BaseModel):
    phone: str = Field(..., description="手机号")
    intent: str = Field(..., description="业务意图，需与技能包目录名一致")
    province: str = Field(..., description="省份代码，如 beijing")
    topN: int = Field(
        default=3, ge=1,
        description=(
            "推荐产品数量，仅在服务端调推荐接口取候选时生效，默认3、无上限；"
            "直传 extra_info.recommended_packages 时不截断（传几个产品出几条话术）"
        ),
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


# 允许「顶层直传」的业务标准域/兼容块白名单。
# 背景：对外规范要求这些字段放在 extra_info 内，但部分下游（如广东现网）习惯把它们与
# phone/province 平级写在顶层；RecommendRequest 未开启 extra="allow"，Pydantic 会静默丢弃，
# 表现为「传了推荐产品却只生成一条兜底话术」。这里按白名单折叠进 extra_info，
# 两种写法都能跑。只收白名单键（不是全部未知字段），避免把网关元数据等噪声灌进话术上下文。
_TOP_LEVEL_EXTRA_INFO_KEYS = (
    # 7 大标准域
    "current_package", "recommended_packages", "usage", "tags",
    "user_info", "user_profile", "domain_ext",
    # 广东旧版单产品兼容块（final_recommendations 为单产品对象，diff 为其差异）
    "final_recommendations", "diff",
)


def _fold_top_level_extra_info(body: Dict[str, Any]) -> List[str]:
    """把顶层直传的标准域键就地折叠进 body["extra_info"]，返回被折叠的键名列表。

    规则（保守、幂等、不覆盖）：
    - 仅处理 _TOP_LEVEL_EXTRA_INFO_KEYS 白名单内的键；
    - extra_info 内已有同名键时**以 extra_info 为准**，顶层同名值忽略（不覆盖既有约定写法）；
    - 空值（None/""/[]/{}）不折叠，避免用空对象顶掉后续兜底逻辑。
    """
    folded: List[str] = []
    ei = body.get("extra_info")
    if not isinstance(ei, dict):
        ei = {}
    for key in _TOP_LEVEL_EXTRA_INFO_KEYS:
        if key not in body:
            continue
        val = body.get(key)
        if val in (None, "", [], {}):
            continue
        if key in ei:          # extra_info 优先，顶层重复写法忽略
            continue
        ei[key] = val
        folded.append(key)
    if folded:
        body["extra_info"] = ei
    return folded


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


def _wants_debug(request: Request) -> bool:
    """运营测试页排障开关：?debug=1|true|yes|on 时附带内部字段。"""
    return str(request.query_params.get("debug", "")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _build_success_payload(
    *,
    call_id: str,
    phone: str,
    intent: str,
    province: str,
    recommend_results: list,
    other_info: Any,
    debug: bool = False,
    resource_context: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    api_calls: Optional[list] = None,
    llm_prompts: Optional[list] = None,
) -> Dict[str, Any]:
    """组装对外成功响应：默认仅规范字段；debug 时附加排障字段。"""
    payload: Dict[str, Any] = {
        "code": 200,
        "message": "success",
        "data": {
            "callId": call_id,
            "phone": phone,
            "intent": intent,
            "province": province,
            "recommend_results": recommend_results,
        },
        "other_info": other_info,
    }
    if debug:
        payload["resource_context"] = resource_context or {}
        payload["metadata"] = metadata or {}
        payload["api_calls"] = api_calls or []
        payload["llm_prompts"] = llm_prompts or []
    return payload


@router.post("/marketing/recommend")
async def recommend(request: Request):
    """营销话术推荐主接口（实时对外服务，无需灵运 satoken）。

    入参格式参见接口文档（callId / intent / phone / topN / extra_data）。
    兼容两种 body：顶层直接放业务字段，或最外层包一层 {"params": {...}}。
    出参：{code, message, data: {callId, phone, intent, province, recommend_results}, other_info}。
    查询参数 debug=1 时额外返回 resource_context / metadata / api_calls / llm_prompts（运营测试页用）。
    """
    # 读取并（按需）解包 params，再做 Pydantic 校验，兼容网关包裹写法
    try:
        raw_body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    # 营销助手统一接口报文（params.inputs 形状）打到本地址时自动分流到交叉营销异步链路，
    # 避免下游配错地址就完全不通。标准接口报文没有 params.inputs，判定互斥、行为不变。
    if is_marketing_assistant_payload(raw_body):
        logger.info("[recommend] 识别为营销助手统一接口报文，转交交叉营销异步链路处理")
        return await handle_marketing_assistant_payload(raw_body)
    body_data = _unwrap_params_body(raw_body)
    if not isinstance(body_data, dict):
        raise HTTPException(status_code=400, detail="请求体格式错误：需为 JSON 对象")
    # 顶层直传的标准域（recommended_packages / current_package 等）折叠进 extra_info，
    # 兼容把业务字段与 phone/province 平级写的下游，避免被 Pydantic 静默丢弃
    _folded = _fold_top_level_extra_info(body_data)
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
    # 运营测试页 / TestConsole：分省日志额外双写到 logs/provinces/test/
    _extra_log_token = None
    if province_logger.is_test_trace(trace_id):
        _extra_log_token = province_logger.set_extra_log_province("test")
        logger.info(f"[recommend] 测试请求，分省日志双写 province=test  trace_id={trace_id}")
    t0 = time.perf_counter()
    logger.info(
        f"[recommend] ▶ 请求开始  trace_id={trace_id} "
        f"province={req.province} intent={req.intent} phone={req.phone[:3]}****"
    )
    if _folded:
        logger.info(
            f"[recommend] 顶层直传字段已折叠进 extra_info: {_folded}  trace_id={trace_id}"
            "（建议下游按对外规范放进 extra_info）"
        )

    try:
        # 省份编码转换：支持下游传入数字编码（如 "200" / "371"）或中文省名
        province_key = resolve_province(req.province)
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
        # 分省日志：记录完整入参（按省份目录落盘，便于分省排查）
        province_logger.log_request(
            province_key, req.intent, trace_id, req.phone, req_data,
        )
        result = await executor.execute(req_data)

        elapsed = (time.perf_counter() - t0) * 1000
        logger.info(
            f"[recommend] ✅ 完成  trace_id={trace_id}  "
            f"elapsed={elapsed:.0f}ms  scripts={len(result.get('recommend_results', []))}"
        )
        obs_summary = summarize_request_context()
        recommend_results = result.get("recommend_results", [])
        # 分省日志：记录出参（模型返回话术）+ 关键统计
        province_logger.log_response(
            province_key, req.intent, trace_id, req.phone,
            code=200, elapsed_ms=elapsed,
            recommend_results=recommend_results,
            other_info=result.get("other_info"),
            metadata=obs_summary,
            llm_prompts=result.get("llm_prompts") or [],
        )
        return JSONResponse(_build_success_payload(
            call_id=trace_id,
            phone=req.phone,
            intent=req.intent,
            province=req.province,
            recommend_results=recommend_results,
            other_info=result.get("other_info"),
            debug=_wants_debug(request),
            resource_context=result.get("resource_context") or {},
            metadata={
                **(result.get("metadata") or {}),
                **(obs_summary or {}),
            },
            api_calls=result.get("api_calls") or [],
            llm_prompts=result.get("llm_prompts") or [],
        ))

    except HTTPException:
        raise
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        logger.exception(f"[recommend] ❌ 异常  trace_id={trace_id}  elapsed={elapsed:.0f}ms")
        # 分省日志：记录异常出参，便于分省排查失败请求
        try:
            province_logger.log_response(
                resolve_province(req.province), req.intent,
                trace_id, req.phone, code=500, elapsed_ms=elapsed,
                metadata=summarize_request_context(), error=str(exc),
            )
        except Exception:
            pass
        return JSONResponse({"code": 500, "message": str(exc), "data": None}, status_code=500)
    finally:
        if _extra_log_token is not None:
            province_logger.reset_extra_log_province(_extra_log_token)
        reset_request_context(obs_token)
