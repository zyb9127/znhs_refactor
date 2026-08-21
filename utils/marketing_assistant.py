"""
营销助手统一接口（灵运前置 / 交叉营销）报文适配器

背景
----
交叉营销场景下，灵运前置按《灵运平台交叉营销接口规范》的固定形状下发报文
（``{"params": {"systemId", "optType", "inputs": {...}}}``），与本服务对外的
「标准接口」（``{phone, intent, province, extra_info, batch_contexts}``）形状不同。

本模块是**唯一的形状适配点**，运行期（routers.cross_sell / routers.realtime）与
配置期（routers.management 调色板、Prompt 预览、测试报文生成）共用同一套判定与
归一逻辑，保证「运营在配置页看到的透传字段」与「运行时真正进入话术上下文的字段」
完全一致。

设计约束
--------
1. **纯增量**：只在识别到营销助手报文时生效，标准接口报文原样返回，已上线技能零影响；
2. **零改名**：``inputs`` 下的业务对象/字段一律**按原名、原层级**进入 extra_info
   （``userinfo`` / ``products`` / ``userinfo_json`` / ``userinfo.userExtra`` …
   全部保持原样），话术模板与调色板占位符也一律用原名，不改写成 7 大标准域，
   也不派生别名字段——「配置页看到的名字 == 报文里的名字 == 运行时取值的名字」；
3. **产品列表只映射值、不改键名**：多产品能力（逐产品并发生成、模板按产品字段匹配、
   productId 回显）需要标准域 ``recommended_packages``，故由 DataStep 按节点配置
   （:func:`resolve_product_list_field`）把 ``products`` 的**值**喂给标准域，
   键名对外始终是 ``products``，调色板不会出现第二个产品列表占位符；
4. **网关元数据不进话术上下文**：sequenceNo / staffId / touchNumber 等只在
   :class:`MarketingAssistantRequest` 上携带（供回调用），不写进 extra_info，
   避免污染提示词。

产品维度的三条业务规则（字段名同样取报文原名，判定逻辑集中在本模块）：
- ``activityTypeName``（活动名称）→ 场景 skill：按活动名称分组，各组路由到同名意图的
  技能包（:func:`group_products_by_activity`，路由见 routers.cross_sell）；
- ``marketingProductFlag`` / ``marketingActivityFlag`` → 是否生成话术
  （:func:`is_marketable`，**两个都为 1 才生成**，任一非 1（含 0/空/缺失）即跳过该产品）；
- ``business_type`` / ``productId`` / ``productName`` → 话术模板匹配维度
  （在 steps.script_step 的模板匹配候选链里按「精确优先」依次尝试）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# api_nodes 直传节点上的接口规范标记（前端「接口规范」单选写入）
REQUEST_VARIANT_KEY = "request_variant"
VARIANT_STANDARD = "standard"
VARIANT_MARKETING_ASSISTANT = "marketing_assistant"

# 报文里的产品数组键名（原名，不改写；DataStep 只把它的值映射进标准域）
PRODUCT_LIST_FIELD = "products"
# 直传节点上可显式指定产品列表字段名的配置键（留给后续别的接口规范复用）
PRODUCT_LIST_FIELD_KEY = "product_list_field"

# 场景（技能包意图）匹配字段：省份 + 活动名称 → 技能包
ACTIVITY_NAME_FIELD = "activityTypeName"
# 是否生成话术的营销标志字段：两个都必须为真值(1)才生成，任一非 1（含 0/空/缺失）即跳过
MARKETING_FLAG_FIELDS = ("marketingProductFlag", "marketingActivityFlag")
# 营销标志的真值集合（对端用 "1"，兼容 true/yes 等写法）
_FLAG_TRUTHY = frozenset({"1", "true", "yes", "y", "t", "是", "on"})

# 处理类型：0-营销话术、1-营销推荐、2-比价（本期只处理 0）
OPT_TYPE_SCRIPT = "0"

# 回调标识：话术 hs、推荐 tj（《交叉营销网关-preload_cache 接口文档》）
IDENTIFIER_SCRIPT = "hs"
IDENTIFIER_RECOMMEND = "tj"

# inputs 下的网关元数据键（不进 extra_info）
_META_KEYS = frozenset({
    "sequenceNo", "servNumber", "provinceCode", "callId",
    "staffId", "staffNo", "touchNumber",
})
# 标准接口特征键：出现即判定为标准报文，绝不按营销助手解析
_STANDARD_MARKERS = frozenset({"phone", "intent", "province", "extra_info"})


@dataclass
class MarketingAssistantRequest:
    """解析后的营销助手报文（业务对象按原名进 extra_info，网关元数据单独携带）。"""

    system_id: str = ""
    opt_types: List[str] = field(default_factory=list)
    sequence_no: str = ""
    phone: str = ""
    province_code: str = ""
    call_id: str = ""
    staff_id: str = ""
    staff_no: str = ""
    touch_number: str = ""
    inputs: Dict[str, Any] = field(default_factory=dict)
    products: List[Dict[str, Any]] = field(default_factory=list)
    extra_info: Dict[str, Any] = field(default_factory=dict)

    @property
    def wants_script(self) -> bool:
        """optType 是否要求营销话术（0）。未传 optType 时按需要话术处理。"""
        return not self.opt_types or OPT_TYPE_SCRIPT in self.opt_types

    @property
    def cache_call_id(self) -> str:
        """会话 callId：优先 callId，缺失时退到 sequenceNo（供内部 pipeline recommend 请求用）。"""
        return self.call_id or self.sequence_no

    @property
    def touch_id(self) -> str:
        """回调唯一标识（写缓存 key 用）——对齐《preload_cache 接口文档》的 ``touchNumber``。

        接口文档把回调请求体的唯一标识字段定名为 ``touchNumber``（"呼叫 ID，作为 key 的一部分"），
        网关据此拼 Redis key ``preload:{servNumber}:{touchNumber}:{identifier}``，下游也按同一
        touchNumber 回查。优先取 ``inputs.touchNumber``，缺失时退到 callId / sequenceNo，
        保证 key 永不为空。
        """
        return self.touch_number or self.call_id or self.sequence_no


def _envelope(raw: Any) -> Optional[Dict[str, Any]]:
    """取出承载 ``inputs`` 的那一层（兼容有/无最外层 params 包裹）。

    非营销助手报文返回 None。判定要求同时满足：
    - 该层没有标准接口特征键（phone/intent/province/extra_info）；
    - ``inputs`` 是非空 dict；
    - 该层带 systemId/optType，或 inputs 里带 servNumber/sequenceNo。
    """
    if not isinstance(raw, dict) or not raw:
        return None
    candidates: List[Dict[str, Any]] = []
    inner = raw.get("params")
    if isinstance(inner, dict) and inner:
        candidates.append(inner)
    candidates.append(raw)
    for cur in candidates:
        if _STANDARD_MARKERS & set(cur.keys()):
            continue
        inputs = cur.get("inputs")
        if not isinstance(inputs, dict) or not inputs:
            continue
        if ({"systemId", "optType"} & set(cur.keys())) or (
            {"servNumber", "sequenceNo"} & set(inputs.keys())
        ):
            return cur
    return None


def is_marketing_assistant_payload(raw: Any) -> bool:
    """是否为营销助手统一接口报文（供路由分流与配置期样例识别）。"""
    return _envelope(raw) is not None


def _parse_opt_types(raw: Any) -> List[str]:
    """``"0,1,2"`` → ``["0","1","2"]``；空值返回空列表。"""
    text = str(raw or "").strip()
    if not text:
        return []
    return [p.strip() for p in text.replace("，", ",").split(",") if p.strip()]


def build_extra_info(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """``inputs`` → extra_info：业务对象**按原名、原层级**全量透传。

    唯一的处理是剥掉传输层：
    - 去掉网关元数据键（``_META_KEYS``：sequenceNo / servNumber / provinceCode /
      callId / staffId / staffNo / touchNumber），它们只用于回调寻址，进了话术
      上下文只会污染提示词；
    - 去掉空值与 ``_`` 前缀内部键。

    其余（``userinfo``（含 ``userExtra`` 原嵌套）、``products``、``userinfo_json``、
    各活动列表，以及后续报文新增的任何对象）一律原名原样保留：话术模板与配置页
    调色板看到的就是灵运报文里的名字，运营按 ``userinfo.currPrice`` /
    ``products.productName`` 这样的原始路径勾选即可。
    """
    ei: Dict[str, Any] = {}
    if not isinstance(inputs, dict):
        return ei
    for key, val in inputs.items():
        if not isinstance(key, str) or key.startswith("_") or key in _META_KEYS:
            continue
        if val in (None, "", [], {}):
            continue
        ei[key] = val
    return ei


def is_marketable(product: Any) -> bool:
    """该产品是否要生成话术（``marketingProductFlag`` / ``marketingActivityFlag``）。

    判定（严格）：两个标志**都为真值**（1/true/yes…）才生成；**任一非真值**——包括
    ``0``/false、**空字符串、字段缺失**、其它非 1 值——都不生成。营销产品标志与营销
    活动标志都表示"可营销"，缺一不可，且必须由对端显式置 1。
    """
    if not isinstance(product, dict):
        return False
    for key in MARKETING_FLAG_FIELDS:
        val = str(product.get(key, "")).strip().lower()
        if val not in _FLAG_TRUTHY:
            return False
    return True


def split_marketable(
    products: Any,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """产品列表 → (要生成话术的, 被营销标志挡掉的)。非 dict 项一律丢弃。"""
    keep: List[Dict[str, Any]] = []
    skip: List[Dict[str, Any]] = []
    for p in products or []:
        if not isinstance(p, dict) or not p:
            continue
        (keep if is_marketable(p) else skip).append(p)
    return keep, skip


def product_label(product: Dict[str, Any]) -> str:
    """产品在日志里的可读标识（productId/产品名），仅用于排障。"""
    if not isinstance(product, dict):
        return "?"
    pid = str(product.get("productId") or product.get("offerId") or "").strip()
    name = str(product.get("productName") or "").strip()
    return f"{pid or '-'}({name})" if name else (pid or "-")


def group_products_by_activity(
    products: Any,
) -> List[tuple[str, List[Dict[str, Any]]]]:
    """按 ``activityTypeName``（活动名称）分组，保持首次出现顺序。

    活动名称就是「场景 skill」的匹配键：同一批报文里的产品可能属于不同活动，各组分别
    路由到同名意图的技能包生成话术（见 routers.cross_sell）。活动名称缺失的产品归到
    空串一组，由调用方按兜底规则处理。
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for p in products or []:
        if not isinstance(p, dict) or not p:
            continue
        name = str(p.get(ACTIVITY_NAME_FIELD) or "").strip()
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(p)
    return [(name, groups[name]) for name in order]


def select_marketable_products(
    products: Any,
    api_cfg: Any,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按节点配置决定是否套用营销标志规则 → (要生成话术的, 被挡掉的)。

    只有营销助手统一接口节点（``request_variant=marketing_assistant``）才按
    :func:`is_marketable` 过滤；其他节点（含标准接口直传）原样返回，被挡列表恒为空 ——
    营销标志是灵运接口规范特有的语义，不能外溢到已上线省份。
    """
    items = [p for p in (products or []) if isinstance(p, dict) and p]
    cfg = api_cfg if isinstance(api_cfg, dict) else {}
    if cfg.get(REQUEST_VARIANT_KEY) != VARIANT_MARKETING_ASSISTANT:
        return items, []
    return split_marketable(items)


def resolve_product_list_field(api_cfg: Any) -> str:
    """取该直传节点「产品列表字段名」（报文原名），无则返回空串。

    多产品链路（逐产品各出一条话术）依赖标准域 ``recommended_packages``，而营销助手
    报文里这份列表叫 ``products``。本函数是 DataStep / 配置期取该字段名的**唯一出口**：
    只把值喂进标准域，键名对外仍是原名，故调色板不会多出一个产品列表占位符。

    优先级：节点显式配置 ``product_list_field`` > 营销助手统一接口默认 ``products``。
    """
    if not isinstance(api_cfg, dict):
        return ""
    explicit = api_cfg.get(PRODUCT_LIST_FIELD_KEY)
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if api_cfg.get(REQUEST_VARIANT_KEY) == VARIANT_MARKETING_ASSISTANT:
        return PRODUCT_LIST_FIELD
    return ""


def looks_like_marketing_products(value: Any) -> bool:
    """这份列表是否**明确**是灵运营销助手的 ``products``（按报文独有字段判定）。

    判据是灵运接口规范独有、其他省份产品对象不会出现的字段：营销标志
    （``marketingProductFlag`` / ``marketingActivityFlag``）或活动分类名称
    （``activityTypeName``）。三者任一出现即认定 —— 用于**配置遗漏时的兜底自愈**
    （节点忘勾「营销助手统一接口」导致产品列表进不了标准域），判据收得很紧，
    标准接口省份的直传产品列表不会误判。
    """
    for p in (value or []) if isinstance(value, list) else []:
        if not isinstance(p, dict):
            continue
        if any(f in p for f in MARKETING_FLAG_FIELDS) or ACTIVITY_NAME_FIELD in p:
            return True
    return False


def parse(raw: Any) -> Optional[MarketingAssistantRequest]:
    """解析营销助手报文；不是该形状返回 None。"""
    envelope = _envelope(raw)
    if envelope is None:
        return None
    inputs = envelope.get("inputs") or {}
    extra_info = build_extra_info(inputs)
    return MarketingAssistantRequest(
        system_id=str(envelope.get("systemId") or "").strip(),
        opt_types=_parse_opt_types(envelope.get("optType")),
        sequence_no=str(inputs.get("sequenceNo") or "").strip(),
        phone=str(inputs.get("servNumber") or "").strip(),
        province_code=str(inputs.get("provinceCode") or "").strip(),
        call_id=str(inputs.get("callId") or "").strip(),
        staff_id=str(inputs.get("staffId") or "").strip(),
        staff_no=str(inputs.get("staffNo") or "").strip(),
        touch_number=str(inputs.get("touchNumber") or "").strip(),
        inputs=inputs,
        products=[
            p for p in (extra_info.get(PRODUCT_LIST_FIELD) or [])
            if isinstance(p, dict) and p
        ],
        extra_info=extra_info,
    )


def normalize_sample(raw: Any) -> Optional[Dict[str, Any]]:
    """配置期样例解包：营销助手报文 → extra_info 本体；非该形状返回 None。

    运营在接口配置页粘贴的是灵运原始报文（带 params / systemId / optType 外壳），
    调色板 / Prompt 预览 / 测试报文都要看到剥壳后的 ``inputs`` 本体——字段名与
    运行时、与报文原文三者完全一致。
    """
    req = parse(raw)
    return req.extra_info if req is not None else None


def to_recommend_body(
    req: MarketingAssistantRequest,
    *,
    intent: str,
    province: str,
    products: Optional[List[Dict[str, Any]]] = None,
    batch_contexts: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """营销助手报文 → 标准推荐请求体（交给既有 pipeline 执行）。

    ``extra_info`` 就是原名原样的 ``inputs`` 本体；产品列表仍叫 ``products``，
    由 DataStep 按节点配置喂进标准域 ``recommended_packages``（见
    :func:`resolve_product_list_field`）。

    Args:
        products: 只对这批产品生成话术（按活动名称分组后各组只传自己那几个产品）；
            None = 全部。extra_info 浅拷贝后替换产品数组，不改动 req 本体。
        batch_contexts: 话术环节维度（切入/挽留各一条，见 routers.cross_sell）；
            None/空 = pipeline 自动构造一条空条目，每个产品出一条话术。
    """
    extra_info = req.extra_info
    items = req.products if products is None else [p for p in products if isinstance(p, dict) and p]
    if products is not None:
        extra_info = dict(extra_info)
        extra_info[PRODUCT_LIST_FIELD] = items
    return {
        "phone": req.phone,
        "intent": intent,
        "province": province,
        "callId": req.cache_call_id,
        "topN": max(len(items), 1),
        "extra_data": {},
        "extra_info": extra_info,
        "batch_contexts": list(batch_contexts or []),
    }


def build_callback_value(
    req: MarketingAssistantRequest,
    recommend_results: List[Dict[str, Any]],
    *,
    pitch_stage: str = "",
    retention_stage: str = "",
) -> Dict[str, Any]:
    """话术结果 → 回调 ``value``（对齐《交叉营销结果获取接口》的 data 结构）。

    **一个产品一项**：同一产品可能生成多条话术（营销推荐 / 切入环节 / 挽留环节，由
    ``batch_contexts.stage`` 区分），在此按产品归并到同一项的不同字段。三条话术相互独立：

    - ``words``：**营销推荐话术**（商品话术，下游取这个）——配了「推荐」环节则取该环节话术，
      否则取无环节的常规话术，**始终有值**；
    - ``aiPitchMarketingDesc``：灵运营销切入话术——**仅当**技能包配了切入环节模板才有值，
      否则留空（不拿 words 顶替，同一条话术填多个字段会让下游重复播报）；
    - ``aiRetentionMarketingDesc``：灵运挽留指引话术——**仅当**配了挽留环节模板才有值。

    其余字段：
    - ``activityId`` / ``activityType``：取自入参产品（``activityId`` /
      ``activityTypeCode`` 活动分类编码），本服务不生成；
    - ``productId``：入参产品 ID（与话术结果回显的 product_id 同源）；
    - ``rank``：结果内的密集排序（1..N），按入参产品顺序；
    - ``aiRecommendReason`` / ``aiRecommendScore``：本服务不产出，留空不编造。

    产品与话术的对齐以 productId 为准；话术没回显 product_id 时（模板匹配字段配错、产品列表
    未喂入标准域 ``recommended_packages`` 等），三个角色**各自**按位次与入参产品对齐兜底，
    避免整批结果为空、或只剩 words 而切入/挽留被静默丢弃。
    """
    pitch_s = str(pitch_stage or "").strip()
    ret_s = str(retention_stage or "").strip()

    def _role(stage: str) -> str:
        """环节名 → 话术角色。非切入/挽留环节（含空环节、推荐环节）一律算营销推荐话术 words。"""
        if ret_s and stage == ret_s:
            return "retention"
        if pitch_s and stage == pitch_s:
            return "pitch"
        return "words"

    # 产品 ID → {"words": 推荐话术, "pitch": 切入话术, "retention": 挽留话术}
    by_pid: Dict[str, Dict[str, Dict[str, Any]]] = {}
    # 话术没回显 product_id 时的兜底：按位次与入参产品对齐（角色 → 位次 → 话术）。
    # 三个角色各自计数：同一角色的第 k 条话术对应第 k 个产品（环节内按产品顺序生成）。
    unattributed: Dict[str, Dict[int, Dict[str, Any]]] = {
        "words": {}, "pitch": {}, "retention": {},
    }
    role_count: Dict[str, int] = {"words": 0, "pitch": 0, "retention": 0}
    for item in recommend_results or []:
        if not isinstance(item, dict):
            continue
        role = _role(str(item.get("stage") or "").strip())
        pid = str(item.get("product_id") or item.get("offerId") or "").strip()
        if pid:
            by_pid.setdefault(pid, {}).setdefault(role, item)
        else:
            unattributed[role][role_count[role]] = item
        role_count[role] += 1

    result: List[Dict[str, Any]] = []
    for idx, src in enumerate(req.products):
        pid = str(src.get("productId") or src.get("offerId") or "").strip()
        slot = by_pid.get(pid) if pid else None
        if slot is None:
            # product_id 全都没回显时（模板匹配字段配错、推荐产品未喂入标准域等），
            # 三个角色一起按位次兜底 —— 只兜 words 会让已生成的切入/挽留被静默丢弃。
            slot = {
                role: items[idx]
                for role, items in unattributed.items()
                if items.get(idx)
            }
        if not slot:
            continue        # 该产品未生成话术（被营销标志挡掉 / 无技能包可路由）
        words_item = slot.get("words") or {}
        pitch = slot.get("pitch") or {}
        retention = slot.get("retention") or {}
        result.append({
            "activityId": str(src.get("activityId") or ""),
            "productId": pid or str(words_item.get("product_id") or ""),
            "words": str(words_item.get("marketing_text") or ""),
            "rank": str(len(result) + 1),
            "activityType": str(src.get("activityTypeCode") or ""),
            "aiPitchMarketingDesc": str(pitch.get("marketing_text") or ""),
            "aiRetentionMarketingDesc": str(retention.get("marketing_text") or ""),
            "aiRecommendReason": "",
            "aiRecommendScore": "",
        })

    return {
        "sequenceNo": req.sequence_no,
        "servNumber": req.phone,
        "callId": req.cache_call_id,
        "optType": ",".join(req.opt_types),
        "touchNumber": req.touch_number,
        "result": result,
        "recommendResult": [],
    }
