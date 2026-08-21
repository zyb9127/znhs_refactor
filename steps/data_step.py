"""
DataStep — Step1: 数据采集

职责：
1. 读取 api_nodes.json 中的接口配置
2. 并发调用所有启用的外部接口
3. 将接口响应通过两步映射写入统一 resource_context
4. 填充 FlowContext（current_package / usage / tags / recommended_packages）

两步映射机制：
  第1步 response_extract：路径取值 → 命名中间槽位（如 raw_tags、current_package）
  第2步 field_transform ：对中间槽位做 passthrough / filter_include / filter_exclude → 写入 resource_context

  标准 resource_context 槽位（current_package / recommended_packages / usage / tags）
  如果在 response_extract 中被直接命名，会自动透传到 resource_context（无需在 field_transform 中显式声明）。

节点数据来源（source_type）：
  "api"（默认）  接口查询模式 — 调外部 HTTP 接口，响应作为映射数据源
  "direct"       直传模式 — 不调接口，主服务入参 extra_info 作为映射数据源，
                 复用同一套 response_extract + field_transform 写入 7 大标准域
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import copy
import time
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from core.context import FlowContext
from plugins.unit_converter import UnitConverterRegistry
from services.api_client import api_client
from services.cache_service import cache_service
from utils.field_naming import match_config_keys
from utils.marketing_assistant import (
    MARKETING_FLAG_FIELDS,
    PRODUCT_LIST_FIELD,
    REQUEST_VARIANT_KEY,
    looks_like_marketing_products,
    product_label,
    resolve_product_list_field,
    select_marketable_products,
    split_marketable,
)
from utils.observability import record_stage
from utils.placeholder import FIXED_PARAM_MAP, dig_subfield


# 标准 resource_context 节点白名单（6大核心域 + 1扩展域）
_NODE_TO_CTX_FIELD: Dict[str, str] = {
    "current_package":      "current_package",
    "usage":                "usage",
    "tags":                 "tags",
    "user_info":            "user_info",
    "recommended_packages": "recommended_packages",
    "user_profile":         "user_profile",
    "domain_ext":           "domain_ext",
}

# ── field_transform 规则处理器注册表 ──────────────────────────────
# 新增 rule 类型只需在此注册，无需修改 _apply_field_rule 主体
_FIELD_RULE_HANDLERS: Dict[str, Callable] = {}


def _register_rule(*names: str):
    """注册 field_transform 规则处理器的装饰器，支持多个别名"""
    def decorator(fn: Callable) -> Callable:
        for name in names:
            _FIELD_RULE_HANDLERS[name] = fn
        return fn
    return decorator


def _is_exclude_rule(rule_type: Any) -> bool:
    """该 field_transform 规则是否为排除型（决定"产出为空"该如何解读）。"""
    return str(rule_type).strip().lower() in ("filter_exclude", "exclude")


def _filter_empty(d: Dict[str, Any]) -> Dict[str, Any]:
    """过滤空值（空字符串 / None / null 字面量）"""
    return {k: v for k, v in d.items() if str(v).strip() not in ("", "None", "null")}


@_register_rule("passthrough")
def _rule_passthrough(source: Any, rule: Dict[str, Any]) -> Any:
    return source


@_register_rule("filter_include", "include")
def _rule_filter_include(source: Any, rule: Dict[str, Any]) -> Any:
    if not isinstance(source, dict):
        return {}
    hits = match_config_keys(source, rule.get("include_keys", []))
    return DataStep._apply_unit_convert(
        _filter_empty({k: v for k, v in source.items() if k in hits}), rule
    )


@_register_rule("filter_exclude", "exclude")
def _rule_filter_exclude(source: Any, rule: Dict[str, Any]) -> Any:
    if not isinstance(source, dict):
        return {}
    hits = match_config_keys(source, rule.get("exclude_keys", []))
    return DataStep._apply_unit_convert(
        _filter_empty({k: v for k, v in source.items() if k not in hits}), rule
    )


@_register_rule("constant")
def _rule_constant(source: Any, rule: Dict[str, Any]) -> Any:
    return rule.get("value")


class DataStep:
    """数据采集步骤（api_nodes.json 配置驱动，并发调接口，两步 JSON 映射）"""

    # 单接口调用超时（秒），可通过 api_node 配置中的 timeout_seconds 覆盖
    DEFAULT_TIMEOUT = 10

    def __init__(self, province: str = "default") -> None:
        self.province     = province
        self.enable_cache = True
        self.cache_ttl    = 60    # 秒

    # ── 主入口 ────────────────────────────────────────────────────

    async def run(self, ctx: FlowContext, api_nodes: Dict[str, Any]) -> None:
        """执行数据采集，结果直接写入 ctx

        加载当前省份+意图技能包下所有启用的接口节点（enabled=True）。
        """
        if not api_nodes:
            logger.warning("[DataStep] api_nodes 为空，跳过数据采集")
            return

        enabled_nodes = {
            name: cfg for name, cfg in api_nodes.items()
            if isinstance(cfg, dict) and cfg.get("enabled", True)
                and not name.startswith("_")
        }

        if not enabled_nodes:
            logger.warning("[DataStep] 无启用的接口节点，跳过数据采集")
            self._apply_domain_fallbacks(ctx, api_nodes)
            return

        logger.info(f"[DataStep] 加载接口节点: {list(enabled_nodes.keys())}")

        cache_key = self._cache_key(ctx, enabled_nodes)

        if self.enable_cache:
            # get_or_load 实现单飞：同 key 并发请求只发起一次真实调用
            cached_data, from_cache = await cache_service.get_or_load(
                key=cache_key,
                loader=lambda: self._fetch_all(ctx, enabled_nodes),
                ttl_seconds=self.cache_ttl,
            )
            if from_cache:
                logger.info("[DataStep] ✅ 命中缓存")
        else:
            cached_data = await self._fetch_all(ctx, enabled_nodes)

        if cached_data:
            self._write_to_ctx(ctx, cached_data.get("resources", {}))
            # 缓存命中时也恢复 raw_responses，防止 other_info 等依赖字段丢失
            ctx.raw_responses.update(cached_data.get("raw_responses", {}))
            # 直传透传字段（运行时通道）
            if cached_data.get("passthrough"):
                ctx.passthrough_context.update(cached_data["passthrough"])
            # 产品字段白名单（运行时通道）：运营精确勾选了 recommended_packages.<字段> 时生效
            for _pf in (cached_data.get("product_field_allow") or []):
                if _pf not in ctx.product_field_allow:
                    ctx.product_field_allow.append(_pf)
            # 接口调用轨迹（测试页展示入参/出参；含缓存命中回放）
            traces = cached_data.get("api_call_traces") or []
            if traces:
                ctx.api_call_traces.extend(traces)

        # 空域兜底：接口映射后仍为空的标准域，按配置从主服务入参 extra_data 回填
        self._apply_domain_fallbacks(ctx, api_nodes)

    def _apply_domain_fallbacks(self, ctx: FlowContext, api_nodes: Dict[str, Any]) -> None:
        """标准域空值兜底：按 api_nodes["_domain_fallbacks"] 从入参 extra_data 回填。

        配置示例（api_nodes.json 顶层，`_` 前缀键不会被当作接口节点执行）：
            "_domain_fallbacks": { "current_package": "currentMainOffer" }
        值为 extra_data 内的点路径（可带 "extra_data." 前缀，等价）。

        仅当接口映射后该标准域仍为空时生效——解决"上游接口偶发不返回当前套餐等
        数据 → 话术槽位无事实 → 占位符残留/串填"的填槽不稳定问题；接口有数据时
        完全不影响既有行为。
        """
        cfg = api_nodes.get("_domain_fallbacks")
        if not isinstance(cfg, dict) or not cfg:
            return
        for domain, path in cfg.items():
            if domain not in _NODE_TO_CTX_FIELD:
                logger.warning(f"[DataStep] _domain_fallbacks 含未知标准域 {domain!r}，忽略")
                continue
            if getattr(ctx, domain, None):
                continue   # 接口已取到数据，不覆盖
            p = str(path or "").strip()
            if p.startswith("extra_data."):
                p = p[len("extra_data."):]
            if not p:
                continue
            val = self._get_path(ctx.extra_data or {}, p)
            if val in (None, "", [], {}):
                logger.warning(
                    f"[DataStep] 标准域[{domain}]为空且入参兜底路径 extra_data.{p} 亦无数据，"
                    "话术对应槽位将缺失"
                )
                continue
            setattr(ctx, domain, val)
            logger.info(
                f"[DataStep] ✅ 标准域[{domain}]接口未取到数据，已用入参 extra_data.{p} 回填"
            )

    # ── 并发取数（可被 get_or_load 缓存）────────────────────────

    async def _fetch_all(
        self,
        ctx: FlowContext,
        enabled_nodes: Dict[str, Any],
    ) -> Dict[str, Any]:
        """并发调用所有启用接口，返回 resources / raw_responses / api_call_traces。"""
        logger.info(f"[DataStep] 并发调用接口: {list(enabled_nodes.keys())}")

        # extra_data 展开只做一次，传给各 _call_one，避免 N 次重复递归
        flattened_extra = self._flatten_extra_data(ctx.extra_data or {})

        tasks = [
            self._call_one(api_name, api_cfg, ctx, flattened_extra)
            for api_name, api_cfg in enabled_nodes.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: Dict[str, Any] = {
            "current_package":      {},
            "usage":                {},
            "tags":                 {},
            "user_info":            {},
            "recommended_packages": [],
            "user_profile":         {},
            "domain_ext":           {},
        }
        raw_responses: Dict[str, Any] = {}
        passthrough: Dict[str, Any] = {}
        product_field_allow: List[str] = []
        api_call_traces: List[Dict[str, Any]] = []

        for (api_name, node_cfg), result in zip(enabled_nodes.items(), results):
            if isinstance(result, Exception):
                ctx.add_error(f"接口[{api_name}]失败: {result}")
                logger.error(f"[DataStep] ❌ {api_name}: {result}")
                api_call_traces.append({
                    "api_name": api_name,
                    "source_type": node_cfg.get("source_type", "api"),
                    "url": str(node_cfg.get("url") or ""),
                    "method": str(node_cfg.get("method") or "POST"),
                    "mock_mode": bool(node_cfg.get("mock_mode")),
                    "request": None,
                    "response": None,
                    "elapsed_ms": None,
                    "error": f"{type(result).__name__}: {result}",
                })
                continue
            if result.get("trace"):
                api_call_traces.append(result["trace"])
            if result.get("failed"):
                ctx.add_error(f"接口[{api_name}]失败: {result.get('trace', {}).get('error') or 'unknown'}")
                logger.error(f"[DataStep] ❌ {api_name}: {result.get('trace', {}).get('error')}")
                # 失败节点仍保留 raw（通常为空），便于前端按节点对齐展示
                raw_responses[api_name] = result.get("raw", {})
                continue
            raw_responses[api_name] = result.get("raw", {})
            node_res = result.get("resources", {}) or {}
            # 节点级映射结果摘要（生产排障：定位哪个节点产出/丢失了哪些域）
            _rec = node_res.get("recommended_packages")
            logger.info(
                f"[DataStep] 节点[{api_name}] 映射摘要: "
                f"source_type={node_cfg.get('source_type', 'api')} "
                f"raw非空={bool(result.get('raw'))} "
                f"产出域={list(node_res.keys())} "
                f"推荐产品数={len(_rec) if isinstance(_rec, list) else 0}"
            )
            merged = self._deep_merge(merged, node_res)
            if result.get("passthrough"):
                passthrough.update(result["passthrough"])
            for _pf in (result.get("product_field_allow") or []):
                if _pf not in product_field_allow:
                    product_field_allow.append(_pf)

        # candidate_products 兼容性
        if not merged.get("recommended_packages") and merged.get("candidate_products"):
            merged["recommended_packages"] = merged.pop("candidate_products")

        # 兜底自愈：存在 api 节点、原始响应里有推荐列表，但映射后 recommended_packages 为空
        # ——通常是 ES 配置的 response_extract 缺失/改名了 recommended_packages 映射（如北京生产问题）。
        # 仅在「当前为空」时触发，绝不覆盖已成功映射的结果；direct 直传模式响应里不含
        # bean.recommend_results，不会误触发，其他省份行为不变。仍打 WARNING 提示修正配置。
        if not merged.get("recommended_packages"):
            merged["recommended_packages"] = self._salvage_recommendations(raw_responses)

        return {
            "resources": merged,
            "raw_responses": raw_responses,
            "passthrough": passthrough,
            "product_field_allow": product_field_allow,
            "api_call_traces": api_call_traces,
        }

    @staticmethod
    def _salvage_recommendations(raw_responses: Dict[str, Any]) -> list:
        """从原始响应中按常见路径兜底提取推荐产品列表（映射缺失时的自愈）。

        探测路径（按优先级）：营销助手 products（仅当带灵运独有字段，见
        :func:`looks_like_marketing_products`）→ bean.recommend_results / recommend_results /
        bean.recommendResults / recommendResults / bean.recommendList / recommendList。
        命中即返回该列表并打 WARNING，提示运营修正该节点的 response_extract
        （recommended_packages → bean.recommend_results）。未命中返回 []（保持原行为）。
        """
        probe_keys = ("recommend_results", "recommendResults", "recommendList")
        for api_name, raw in raw_responses.items():
            if not isinstance(raw, dict):
                continue
            # 营销助手直传节点没走「直接透传」子模式、且映射里漏了 recommended_packages 时，
            # 按报文独有字段认出 products 并套营销标志规则（判据见 looks_like_marketing_products）
            if looks_like_marketing_products(raw.get(PRODUCT_LIST_FIELD)):
                keep, skip = split_marketable(raw.get(PRODUCT_LIST_FIELD))
                if keep:
                    logger.warning(
                        f"[DataStep] ⚠️ 兜底自愈：节点[{api_name}] 报文 "
                        f"{PRODUCT_LIST_FIELD!r} 是营销助手产品列表（{len(keep)} 条"
                        + (f"，营销标志挡掉 {len(skip)} 条" if skip else "")
                        + f"），但映射后 recommended_packages 为空，已自动采用。"
                        f"请把该直传节点改为「直接透传字段」+「营销助手统一接口」"
                    )
                    return keep
            bean = raw.get("bean") if isinstance(raw.get("bean"), dict) else {}
            for pk in probe_keys:
                hits = bean.get(pk) if isinstance(bean.get(pk), list) else raw.get(pk)
                if isinstance(hits, list) and hits:
                    logger.warning(
                        f"[DataStep] ⚠️ 兜底自愈：节点[{api_name}] 原始响应含推荐列表 "
                        f"'{pk}'（{len(hits)} 条），但配置未映射 recommended_packages，"
                        f"已自动采用该列表。请尽快修正该节点 response_extract："
                        f"recommended_packages → bean.{pk}"
                    )
                    return hits
        return []

    # ── 单接口调用 ────────────────────────────────────────────────

    def _log_api_call(
        self,
        api_name: str,
        api_cfg: Dict[str, Any],
        ctx: FlowContext,
        params: Any,
        *,
        response: Any,
        elapsed_ms: float,
        timeout_s: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        """把接口查询模式下的一次下游调用（请求参数/响应结果）写入分省接口日志。
        任何异常都被吞掉，绝不影响主流程。"""
        try:
            from utils.province_logger import log_api_call
            log_api_call(
                self.province, getattr(ctx, "intent", ""),
                getattr(ctx, "trace_id", ""), getattr(ctx, "phone", ""),
                api_name=api_name,
                url=str(api_cfg.get("url") or ""),
                method=str(api_cfg.get("method") or "POST"),
                request=params,
                response=response,
                elapsed_ms=elapsed_ms,
                timeout_s=timeout_s,
                error=error,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[DataStep] 分省接口日志写入跳过: {e}")

    def _make_trace(
        self,
        api_name: str,
        api_cfg: Dict[str, Any],
        *,
        request: Any = None,
        response: Any = None,
        elapsed_ms: Optional[float] = None,
        error: Optional[str] = None,
        mapping: Optional[List[Dict[str, Any]]] = None,
        resources: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """构造单节点调用轨迹（测试页展示入参/出参 + 映射诊断）。"""
        wrap = api_cfg.get("request_body_wrapper")
        req_out = request
        # 若配置了 body 外层包装键，同步展示实际上送形态，便于对照抓包
        if (
            wrap and str(wrap).strip()
            and isinstance(request, dict)
            and str(wrap).strip() not in request
        ):
            req_out = {str(wrap).strip(): request}
        return {
            "api_name": api_name,
            "source_type": api_cfg.get("source_type", "api") or "api",
            "url": str(api_cfg.get("url") or ""),
            "method": str(api_cfg.get("method") or "POST"),
            "mock_mode": bool(api_cfg.get("mock_mode")),
            "request_body_wrapper": str(wrap).strip() if wrap else "",
            "request": req_out,
            "response": response,
            "elapsed_ms": round(elapsed_ms, 1) if elapsed_ms is not None else None,
            "error": error,
            # 映射诊断：每条 field_transform 规则的数据源/命中情况，供测试页直接定位
            # 「接口有数据但映射域为空」这类静默失败（配置键名与出参键名不一致等）
            "mapping": list(mapping or []),
            "mapped_domains": sorted(resources.keys()) if isinstance(resources, dict) else [],
        }

    async def _call_one(
        self,
        api_name: str,
        api_cfg: Dict[str, Any],
        ctx: FlowContext,
        flattened_extra: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """调一个接口，返回 {"raw", "resources", "trace", ...}。

        flattened_extra：由 _fetch_all 预先展开的 extra_data 占位符，避免重复递归。
        超时时长优先读取 api_cfg["timeout_seconds"]，默认 DEFAULT_TIMEOUT 秒。

        source_type="direct" 的节点不发 HTTP 请求，直接以 ctx.extra_info 为数据源
        执行同样的两步映射（response_extract + field_transform）。

        接口查询失败时不抛异常，返回 failed=True + trace（含入参与错误），
        由 _fetch_all 记入 ctx.errors，保证测试页仍能看到这次调用的入参。
        """
        t0 = time.perf_counter()

        # ── 直传模式节点：extra_info 作为"响应"，不调外部接口 ──
        if api_cfg.get("source_type") == "direct":
            raw = ctx.extra_info or {}
            if not raw:
                logger.warning(f"[DataStep] 直传节点 {api_name}: extra_info 为空，跳过映射")
                return {
                    "raw": {}, "resources": {},
                    "trace": self._make_trace(
                        api_name, api_cfg, request={"extra_info": {}},
                        response={}, elapsed_ms=(time.perf_counter() - t0) * 1000,
                        error="extra_info 为空",
                    ),
                }
            # 直接透传子模式：不做 7 域强制映射，仅按同名标准域透传；
            # 自定义入参字段（如 recommend_actual_price）收集到 passthrough 通道，
            # 由 build_prompt 逐字段注入【上下文数据】（带 {字段名} 锚点）。
            if api_cfg.get("direct_mode") == "passthrough":
                resources = {
                    k: v for k, v in raw.items()
                    if k in _NODE_TO_CTX_FIELD and v not in (None, "", [], {})
                }
                # 产品列表按调用方原字段名直传（营销助手统一接口叫 products）：
                # 只把**值**喂进标准域 recommended_packages（多产品逐条生成、模板按产品
                # 字段匹配都依赖它），键名对外仍是原名 —— 调色板/话术模板里引用的还是
                # {products}，不会多出一个同义占位符。
                _pl_field = resolve_product_list_field(api_cfg)
                # 兜底自愈：节点漏配「营销助手统一接口」时 _pl_field 为空，产品列表进不了
                # 标准域 → 话术会用「空产品」匹配模板（填了产品 ID 的模板一律落空），且结果
                # 不回显 product_id。报文里 products 带灵运独有字段时按 products 兜底并告警，
                # 判据见 looks_like_marketing_products（标准接口省份不会误触发）。
                _salvaged_ma = False
                if not _pl_field and looks_like_marketing_products(raw.get(PRODUCT_LIST_FIELD)):
                    _pl_field = PRODUCT_LIST_FIELD
                    _salvaged_ma = True
                    logger.warning(
                        f"[DataStep] ⚠️ 兜底自愈：直传节点 {api_name} 未配"
                        f"「营销助手统一接口」（{REQUEST_VARIANT_KEY}="
                        f"{api_cfg.get(REQUEST_VARIANT_KEY)!r}），但报文 "
                        f"{PRODUCT_LIST_FIELD!r} 带营销助手独有字段，已按该字段喂入标准域"
                        f"并套用营销标志规则。请尽快把该节点的「接口规范」改为营销助手统一接口："
                        f"配置页的样例解包、调色板产品字段、活动名称路由仍依赖该标记"
                    )
                if _pl_field and not resources.get("recommended_packages"):
                    # 自愈路径已确认是营销助手报文，直接套营销标志规则，
                    # 与「节点配置正确」时的行为保持一致（不因漏配而放过 flag≠1 的产品）
                    _items, _skipped = (
                        split_marketable(raw.get(_pl_field))
                        if _salvaged_ma
                        else select_marketable_products(raw.get(_pl_field), api_cfg)
                    )
                    if _skipped:
                        logger.info(
                            f"[DataStep] 直传节点 {api_name}: 营销标志过滤掉 {len(_skipped)} 个产品 "
                            f"{[product_label(p) for p in _skipped]}"
                            f"（{'/'.join(MARKETING_FLAG_FIELDS)} 未同时为 1，不生成话术）"
                        )
                    if _items:
                        resources["recommended_packages"] = _items
                        logger.info(
                            f"[DataStep] 直传节点 {api_name}: 产品列表字段 {_pl_field!r} "
                            f"({len(_items)} 条) 已喂入标准域 recommended_packages（键名不变）"
                        )
                # 选定要暴露的透传字段：配置了 passthrough_fields 用之，否则全部顶层字段；
                # 排除标准域（走 resources 通道）与下划线开头的内部键、空值。
                # 支持「子路径」写法（如 portrait_style.communication_style）：只暴露该子字段，
                # 按叶子名注入，供话术模板用 {communication_style} 直接引用。
                sel = api_cfg.get("passthrough_fields") or list(raw.keys())
                explicit_sel = bool(api_cfg.get("passthrough_fields"))
                passthrough: Dict[str, Any] = {}
                # 列表域下的子路径（recommended_packages.<字段>）是「逐条产品各取自己那份」的
                # 字段，无法收敛成单个透传值，故作为产品字段白名单交给 build_prompt 逐条注入。
                product_allow: List[str] = []
                for k in sel:
                    if not isinstance(k, str) or not k or k.startswith("_"):
                        continue
                    if "." in k:
                        root, _, _rest = k.partition(".")
                        if isinstance(raw.get(root), list):
                            leaf = k.rsplit(".", 1)[-1]
                            if leaf and leaf not in product_allow:
                                product_allow.append(leaf)
                            continue
                        leaf, val = dig_subfield(raw, k)
                        if (leaf and leaf not in _NODE_TO_CTX_FIELD
                                and val not in (None, "", [], {})):
                            passthrough.setdefault(leaf, val)
                        continue
                    if (k in raw and k not in _NODE_TO_CTX_FIELD
                            and raw[k] not in (None, "", [], {})):
                        passthrough[k] = raw[k]
                # 嵌套画像对象（portrait_style）展开一层：把 communication_style /
                # business_conte 等标量子字段提升为独立透传字段，由 build_prompt 逐条注入
                # 【上下文数据】（带 {字段名} 锚点），供“话术要求”按名个性化引用。
                # 保留父级 portrait_style（不再 pop）：调色板把它作为「直传大变量」占位符
                # {portrait_style} 暴露，运营勾选/拖入后必须在上下文里可读地体现（见
                # build_prompt._fmt_passthrough_value 的 dict 可读化），否则占位符落空。
                # 运营已按子路径精确勾选时不再整块展开，尊重其选择。
                nested_style = raw.get("portrait_style")
                if isinstance(nested_style, dict) and (
                    not explicit_sel or "portrait_style" in sel
                ):
                    for ck, cv in nested_style.items():
                        if (isinstance(ck, str) and not ck.startswith("_")
                                and cv not in (None, "", [], {})
                                and ck not in _NODE_TO_CTX_FIELD):
                            passthrough.setdefault(ck, cv)
                logger.info(
                    f"[DataStep] 直传节点 {api_name} 透传模式：同名域={list(resources.keys())}，"
                    f"透传字段={list(passthrough.keys())}"
                    + (f"，产品字段白名单={product_allow}" if product_allow else "")
                )
                return {
                    "raw": raw, "resources": resources, "passthrough": passthrough,
                    "product_field_allow": product_allow,
                    "trace": self._make_trace(
                        api_name, api_cfg, request={"extra_info": raw},
                        response=raw, elapsed_ms=(time.perf_counter() - t0) * 1000,
                    ),
                }
            if not api_cfg.get("response_extract") and not api_cfg.get("field_transform"):
                # 零配置兜底：extra_info 顶层 key 与 7 大标准域同名时直接写入
                resources = {
                    k: v for k, v in raw.items()
                    if k in _NODE_TO_CTX_FIELD and v not in (None, "", [], {})
                }
                logger.info(f"[DataStep] 直传节点 {api_name} 无映射规则，按同名域透传: {list(resources.keys())}")
                return {
                    "raw": raw, "resources": resources,
                    "trace": self._make_trace(
                        api_name, api_cfg, request={"extra_info": raw},
                        response=raw, elapsed_ms=(time.perf_counter() - t0) * 1000,
                    ),
                }
            mapping_diag: List[Dict[str, Any]] = []
            extracted = self._extract_fields(raw, api_cfg)
            resources = self._transform_fields(extracted, api_cfg, raw, mapping_diag)
            record_stage(
                stage=f"data_step.{api_name}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                cache_hit=False, provider=f"direct:{api_name}", degrade_flag=False,
            )
            logger.info(f"[DataStep] 直传映射完成: {api_name} → {list(resources.keys())}")
            return {
                "raw": raw, "resources": resources,
                "trace": self._make_trace(
                    api_name, api_cfg, request={"extra_info": raw},
                    response=raw, elapsed_ms=(time.perf_counter() - t0) * 1000,
                    mapping=mapping_diag, resources=resources,
                ),
            }

        params = self._build_params(api_cfg, ctx, flattened_extra)
        timeout = float(api_cfg.get("timeout_seconds") or self.DEFAULT_TIMEOUT)
        _api_t0 = time.perf_counter()
        try:
            raw = await asyncio.wait_for(api_client.call(api_cfg, params), timeout=timeout)
        except asyncio.TimeoutError:
            elapsed = (time.perf_counter() - _api_t0) * 1000
            err = f"timeout>{timeout}s"
            self._log_api_call(
                api_name, api_cfg, ctx, params,
                response=None, elapsed_ms=elapsed, timeout_s=timeout, error=err,
            )
            return {
                "raw": {}, "resources": {}, "failed": True,
                "trace": self._make_trace(
                    api_name, api_cfg, request=params, response=None,
                    elapsed_ms=elapsed, error=err,
                ),
            }
        except Exception as e:  # 其余网络/上游异常：落分省日志 + 返回失败轨迹
            elapsed = (time.perf_counter() - _api_t0) * 1000
            err = f"{type(e).__name__}: {e}"
            self._log_api_call(
                api_name, api_cfg, ctx, params,
                response=None, elapsed_ms=elapsed, timeout_s=timeout, error=err,
            )
            return {
                "raw": {}, "resources": {}, "failed": True,
                "trace": self._make_trace(
                    api_name, api_cfg, request=params, response=None,
                    elapsed_ms=elapsed, error=err,
                ),
            }
        raw = raw or {}
        elapsed = (time.perf_counter() - _api_t0) * 1000
        # 分省接口调用日志：请求参数 + 响应结果（便于按省排查接口查询链路）
        self._log_api_call(
            api_name, api_cfg, ctx, params,
            response=raw, elapsed_ms=elapsed, timeout_s=timeout, error=None,
        )
        trace = self._make_trace(
            api_name, api_cfg, request=params, response=raw, elapsed_ms=elapsed,
        )

        # ── 新式两步映射（response_extract + field_transform）──
        # 入口守卫：只要配了 response_extract 或 field_transform 任一即触发映射。
        #   · 带 response_extract 的存量配置：行为与旧版完全一致（第①步取数命名 + 第②步转换）。
        #   · 仅配 field_transform（无 response_extract）：extracted 为空，第②步直接用 from 的
        #     JSON 路径从原始响应取值（见 _transform_fields 的路径回退），实现"免中间集直连映射"。
        if api_cfg.get("response_extract") or api_cfg.get("field_transform"):
            mapping_diag: List[Dict[str, Any]] = []
            extracted = self._extract_fields(raw, api_cfg)
            resources = self._transform_fields(extracted, api_cfg, raw, mapping_diag)
            record_stage(
                stage=f"data_step.{api_name}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                cache_hit=False, provider=api_name, degrade_flag=False,
            )
            logger.debug(f"[DataStep] 两步映射完成: {api_name}")
            trace["mapping"] = mapping_diag
            trace["mapped_domains"] = sorted(resources.keys())
            return {"raw": raw, "resources": resources, "trace": trace}

        # 零配置兜底（与直传模式的零配置行为对齐）：未配置任何映射时，
        # 从响应顶层及常见业务载体（bean/data/result）按同名标准域直接透传，
        # 保证"未做智能映射"的接口也能让话术模板占位符拿到数据。
        resources = self._zero_config_passthrough(raw)
        if resources:
            logger.info(
                f"[DataStep] {api_name} 无映射规则，按同名标准域零配置透传: {list(resources.keys())}"
            )
        else:
            logger.warning(f"[DataStep] {api_name} 未配置 response_extract / field_transform，且无同名标准域字段可透传")
        return {"raw": raw, "resources": resources, "trace": trace}

    @staticmethod
    def _zero_config_passthrough(raw: Any) -> Dict[str, Any]:
        """接口查询模式零配置兜底：按同名标准域从原始响应透传。

        探测层级：响应顶层 → bean / data / result 子对象（常见业务载体）。
        另外识别常见推荐列表命名（recommend_results 等）归入 recommended_packages。
        """
        if not isinstance(raw, dict):
            return {}
        resources: Dict[str, Any] = {}
        _rec_alias = ("recommend_results", "recommendResults", "recommendList")
        layers = [raw] + [
            raw[k] for k in ("bean", "data", "result") if isinstance(raw.get(k), dict)
        ]
        for layer in layers:
            for field in _NODE_TO_CTX_FIELD:
                if field not in resources and layer.get(field) not in (None, "", [], {}):
                    resources[field] = layer[field]
            if "recommended_packages" not in resources:
                for ra in _rec_alias:
                    v = layer.get(ra)
                    if isinstance(v, list) and v:
                        resources["recommended_packages"] = v
                        break
        return resources

    # ══════════════════════════════════════════════════════════════
    # 新式两步映射
    # ══════════════════════════════════════════════════════════════

    def _extract_fields(
        self,
        raw: Any,
        api_cfg: Dict[str, Any],
    ) -> Dict[str, Any]:
        """第1步：按 response_extract 从原始响应提取命名中间槽位

        示例配置：
            "response_extract": {
                "current_package":      "bean.mainoffer",
                "recommended_packages": "bean.recommend_results",
                "raw_tags":             "bean.tags"
            }
        """
        rules = api_cfg.get("response_extract") or {}
        extracted: Dict[str, Any] = {}
        for slot_name, path in rules.items():
            if slot_name.startswith("_"):
                continue
            extracted[slot_name] = self._get_path(raw, str(path))
        return extracted

    #: 探测中间槽位时依次尝试的响应载体层
    _PROBE_LAYERS = ("", "bean.", "data.", "result.", "object.")

    @classmethod
    def _probe_source(cls, raw: Any, from_field: str) -> tuple:
        """response_extract 丢失中间槽位时，按名在原始响应中探测数据源（运行态自愈）。

        北京「套餐推荐」两次生产事故都是同一形态：field_transform 仍写着
        ``from: raw_tags``，但 ES 里的 response_extract 被某次保存冲掉了 raw_tags，
        于是 usage / tags 静默产出为空、话术缺历史用量与用户标签。保存守护只能防新账，
        存量坏配置仍需运行态兜底——按 ``raw_xxx`` → ``xxx`` 去前缀，在响应顶层与
        bean/data/result/object 常见业务载体下按名找同名字段。

        Returns:
            (探测到的数据源, 命中的 JSON 路径)；未命中返回 (None, "")。
        """
        if not isinstance(raw, dict) or not from_field:
            return None, ""
        names = [str(from_field)]
        if names[0].startswith("raw_") and len(names[0]) > 4:
            names.append(names[0][4:])
        for name in names:
            for layer in cls._PROBE_LAYERS:
                path = f"{layer}{name}"
                val = cls._get_path(raw, path)
                if val not in (None, "", [], {}):
                    return val, path
        return None, ""

    def _transform_fields(
        self,
        extracted: Dict[str, Any],
        api_cfg: Dict[str, Any],
        raw: Any = None,
        diagnostics_out: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """第2步：按 field_transform 将中间槽位加工写入 resource_context

        逻辑：
        1. response_extract 中直接命名为标准 slot（current_package 等）的字段自动透传
        2. field_transform 中的规则显式覆盖（优先级高于自动透传）

        `from` 取值来源（向后兼容，优先级从高到低）：
        1. response_extract 已命名的中间槽位（历史行为，现网配置均走此路）
        2. 【新增】直接按 JSON 路径从原始响应 raw 取值（如 "bean.tags"），
           仅当该名字不是已命名中间槽位时才生效，因此不影响存量配置的读取。

        示例配置：
            "field_transform": {
                "tags": {
                    "from": "raw_tags",          # 引用 response_extract 命名的中间集
                    "type": "filter_exclude",
                    "exclude_keys": ["近3月平均流量(MB）", ...]
                },
                "usage.data_usage": {
                    "from": "bean.tags",          # 新增：也可直接写响应 JSON 路径
                    "type": "filter_include",
                    "include_keys": ["近3月平均流量(MB）", ...]
                }
            }
        """
        transform_rules = api_cfg.get("field_transform") or {}
        resources: Dict[str, Any] = {}

        # 步骤1：标准 slot 在 response_extract 中直接命名时，自动透传
        for slot, val in extracted.items():
            if slot in _NODE_TO_CTX_FIELD and not slot.startswith("_"):
                resources[slot] = val

        # 步骤2：field_transform 规则显式覆盖（支持嵌套路径，如 usage.data_usage）
        for target_path, rule in transform_rules.items():
            if target_path.startswith("_"):
                continue
            explicit_from = rule.get("from")
            from_field    = explicit_from if explicit_from is not None else target_path
            source_desc   = ""
            # 优先取已命名中间槽位（历史行为）；仅当"显式"配置的 from 不是已命名中间集时，
            # 才按 JSON 路径回退到原始响应。省略 from 的规则保持与旧版完全一致，零回归。
            if from_field in extracted:
                source = extracted.get(from_field)
                source_desc = f"中间槽位 {from_field}"
            elif explicit_from is not None and raw is not None:
                source = self._get_path(raw, from_field)
                if source is not None:
                    source_desc = f"响应路径 {from_field}"
                    logger.debug(
                        f"[DataStep] field_transform '{target_path}' 的 from='{from_field}' "
                        f"未命中中间集，按 JSON 路径直接从响应取值"
                    )
            else:
                source = None
            # 数据源为空时：先尝试按名自愈探测（存量坏配置丢了中间槽位的兜底），
            # 仍取不到才告警。此前静默跳过，导致 usage/tags 缺失长期不可见。
            if source in (None, "", [], {}):
                healed, healed_path = self._probe_source(raw, from_field)
                if healed is not None:
                    source = healed
                    source_desc = f"自愈探测 {healed_path}"
                    logger.warning(
                        f"[DataStep] ⚠️ 自愈：field_transform '{target_path}' 的 from='{from_field}' "
                        f"在 response_extract 中缺失，已按名从响应 '{healed_path}' 取到数据。"
                        f"请尽快在接口配置中补回 response_extract：{from_field} → {healed_path}"
                    )
                else:
                    source_desc = "缺失"
                    logger.warning(
                        f"[DataStep] field_transform '{target_path}' 数据源为空: "
                        f"from='{from_field}'（中间槽位存在={from_field in extracted}，"
                        f"extracted 槽位={list(extracted.keys())}）→ 该映射域将缺失，"
                        f"请检查 response_extract 是否包含该槽位、接口响应对应路径是否有数据"
                    )
            rule_type  = rule.get("type", "passthrough")
            value      = self._apply_field_rule(source, rule_type, rule)
            # 数据源明明有值，规则却产出空：include 键名与上游实际返回的键名对不上
            # （上游改名/加前缀），或 exclude 把字段全排掉了。这是最难排查的一类静默失败，
            # 单独告警并把配置键名与接口实际键名都打出来，供直接比对。
            if isinstance(source, dict) and source and not value:
                reason = (
                    "全部字段都被 exclude_keys 排除"
                    if _is_exclude_rule(rule_type)
                    else "include_keys 与接口实际键名无一匹配"
                )
                logger.warning(
                    f"[DataStep] field_transform '{target_path}' 数据源有 {len(source)} 个字段，"
                    f"但 {rule_type} 规则产出为空（{reason}）→ 该映射域将缺失。"
                    f"配置键名={list(rule.get('include_keys') or rule.get('exclude_keys') or [])[:10]}；"
                    f"接口实际键名={list(source.keys())[:10]}。请按接口实际出参核对键名"
                )
            # 空容器（{} / []）不写入：空数据源经 filter 规则会产出空壳子字典，
            # 若写入会让映射域看似"有值"（truthy 外壳），污染下游空域检测与预览
            if value not in (None, {}, []):
                self._set_path(resources, target_path, value)
            if diagnostics_out is not None:
                diagnostics_out.append(self._make_mapping_diag(
                    target_path, from_field, source_desc, rule, rule_type, source, value
                ))

        return resources

    @staticmethod
    def _make_mapping_diag(
        target_path: str,
        from_field: str,
        source_desc: str,
        rule: Dict[str, Any],
        rule_type: str,
        source: Any,
        value: Any,
    ) -> Dict[str, Any]:
        """单条 field_transform 规则的映射诊断（测试页「映射诊断」区展示）。

        status 语义：ok=有产出；empty_source=数据源取不到；
        no_key_matched=数据源有字段但 include_keys 一个都没对上（配置与接口出参不一致）；
        all_excluded=exclude 规则把数据源字段全排掉了。
        """
        src_keys = list(source.keys())[:30] if isinstance(source, dict) else []
        out_keys = list(value.keys())[:30] if isinstance(value, dict) else []
        if source in (None, "", [], {}):
            status = "empty_source"
        elif not value:
            status = "all_excluded" if _is_exclude_rule(rule_type) else "no_key_matched"
        else:
            status = "ok"
        return {
            "target": target_path,
            "from": from_field,
            "source": source_desc,
            "rule": rule_type,
            "status": status,
            "config_keys": [
                str(k) for k in
                (rule.get("include_keys") or rule.get("exclude_keys") or [])[:30]
            ],
            "source_keys": [str(k) for k in src_keys],
            "output_keys": [str(k) for k in out_keys],
        }

    @staticmethod
    def _apply_field_rule(
        source: Any,
        rule_type: str,
        rule: Dict[str, Any],
    ) -> Any:
        """执行单条 field_transform 规则，通过 _FIELD_RULE_HANDLERS 注册表分发。

        内置类型：passthrough / filter_include / include / filter_exclude / exclude / constant
        扩展新类型：在模块级用 @_register_rule("新类型") 注册即可，无需修改此方法。
        """
        t = str(rule_type).strip().lower()
        handler = _FIELD_RULE_HANDLERS.get(t)
        if handler is not None:
            return handler(source, rule)
        logger.warning(f"[DataStep] 未知 field_transform 类型: '{rule_type}'，透传原值")
        return source

    @staticmethod
    def _apply_unit_convert(data: Any, rule_or_convert: Any = None) -> Any:
        """对字典字段应用单位转换 + 字段名称重命名。

        支持两种调用方式（向后兼容）：
        1. 旧版：只传 unit_convert dict
        2. 新版：传完整 rule dict（含 "unit_convert" 和 "field_rename"）

        执行顺序：先值转换（unit_convert），再字段重命名（field_rename）。
        示例：近3月平均流量(MB) 1024 → 近3月平均流量(GB) 1.0
        """
        if not isinstance(data, dict):
            return data

        # 兼容旧版调用（只传 unit_convert dict）
        if isinstance(rule_or_convert, dict) and (
            "unit_convert" in rule_or_convert or "field_rename" in rule_or_convert
        ):
            unit_convert = rule_or_convert.get("unit_convert") or {}
            field_rename = rule_or_convert.get("field_rename") or {}
        else:
            unit_convert = rule_or_convert if isinstance(rule_or_convert, dict) else {}
            field_rename = {}

        result = dict(data)

        # 配置里声明的字段名与接口出参键名可能只差括号形态（半角 vs 全角、双括号），
        # 精确匹配会让换算与重命名双双落空——数据仍是 MB 却挂着 (GB) 的模板占位符。
        # 统一按 canonical 归一定位实际键（同 filter 规则）。
        def _actual_key(name: str) -> Optional[str]:
            if name in result:
                return name
            hits = match_config_keys(result, [name])
            return next(iter(hits), None)

        # 1. 先进行值转换（使用原始字段名作为 key）
        for field, converter in unit_convert.items():
            key = _actual_key(field)
            if key is not None:
                result[key] = UnitConverterRegistry.apply(converter, result[key])
                logger.debug(f"[DataStep] Applied unit convert: {key} with {converter}")

        # 2. 然后执行字段重命名（目标名就地规范化：即使存量配置含畸形括号
        #    「近6月平均流量((GB)）」，产出的数据键也统一为「近6月平均流量(GB)」，
        #    保证与话术模板子字段占位符/调色板 token 同名对齐）
        from utils.field_naming import clean_rename_field

        for old_name, new_name in field_rename.items():
            key = _actual_key(old_name)
            if key is not None:
                value = result.pop(key)
                cleaned = clean_rename_field(new_name)
                if cleaned != new_name:
                    logger.warning(
                        f"[DataStep] 重命名目标名含畸形括号，已规范化: '{new_name}' → '{cleaned}'"
                    )
                result[cleaned] = value
                logger.info(f"[DataStep] Renamed field '{key}' → '{cleaned}' after unit conversion")

        return result

    # ── 请求参数构造 ──────────────────────────────────────────────

    def _build_params(
        self,
        api_cfg: Dict[str, Any],
        ctx: FlowContext,
        flattened_extra: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """替换 request_template 中的占位符

        占位符来源（优先级从低到高）：
        1. 主服务固定参数：{{PHONE}} {{INTENT}} {{CALL_ID}} {{PROVINCE}} {{TOP_N}}
        2. extra_data 路径展开：{{extra_data.ioId}} {{extra_data.currentMainOffer.curOfferName}} 等
        3. ctx.extra_vars：调用方显式传入的自定义占位符（最高优先级，可覆盖上述）

        flattened_extra：_fetch_all 预展开的 extra_data，不传则在此展开（兜底兼容）。
        """
        template = copy.deepcopy(api_cfg.get("request_template") or {})
        if not template:
            return {"phone": ctx.phone, "intent": ctx.intent}

        # 1. 主服务固定参数（从 utils/placeholder.py 统一维护的映射表动态构建）
        _ctx_attr_map = {
            "ctx.phone":    ctx.phone,
            "ctx.intent":   ctx.intent,
            "ctx.trace_id": ctx.trace_id,
            "ctx.province": ctx.province,
            "ctx.top_n":    str(ctx.top_n),
        }
        fixed_vars: Dict[str, str] = {
            ph: _ctx_attr_map.get(attr_path, "")
            for ph, (attr_path, _) in FIXED_PARAM_MAP.items()
        }

        # 2. extra_data 占位符（优先使用预展开结果，避免重复递归）
        extra_vars = flattened_extra if flattened_extra is not None else self._flatten_extra_data(ctx.extra_data or {})

        # 3. 合并（ctx.extra_vars 优先级最高）
        replacements = {**fixed_vars, **extra_vars, **ctx.extra_vars}

        tpl_str = json.dumps(template, ensure_ascii=False)
        for k, v in replacements.items():
            tpl_str = tpl_str.replace(k, str(v))
        try:
            return json.loads(tpl_str)
        except Exception as e:
            logger.warning(f"[DataStep] request_template JSON 解析失败，降级为基础参数: {e}")
            return {"phone": ctx.phone, "intent": ctx.intent}

    @staticmethod
    def _flatten_extra_data(
        data: Any,
        prefix: str = "extra_data",
        result: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """将 extra_data 嵌套结构展开为 {{extra_data.xxx.yyy}} 占位符映射。

        示例：
            {"ioId": "abc", "currentMainOffer": {"curOfferName": "128元套餐"}}
        展开为：
            {"{{extra_data.ioId}}": "abc",
             "{{extra_data.currentMainOffer.curOfferName}}": "128元套餐"}
        """
        if result is None:
            result = {}
        if isinstance(data, dict):
            for k, v in data.items():
                DataStep._flatten_extra_data(v, f"{prefix}.{k}", result)
        elif isinstance(data, list):
            # 列表不展开为占位符（保留原始值，通过 JSON 序列化处理）
            result[f"{{{{{prefix}}}}}"] = json.dumps(data, ensure_ascii=False)
        else:
            result[f"{{{{{prefix}}}}}"] = "" if data is None else str(data)
        return result

    # ── 工具方法 ──────────────────────────────────────────────────

    @staticmethod
    def _get_path(data: Any, path: str) -> Any:
        """按 . 分隔路径取值，空路径/$result 返回 data 本身"""
        if not path or path == "$result":
            return data
        v = data
        for key in str(path).split("."):
            if not isinstance(v, dict):
                return None
            v = v.get(key)
        return v

    @staticmethod
    def _set_path(target: Dict[str, Any], path: str, value: Any) -> None:
        """按 . 分隔路径写值（自动创建中间层）"""
        keys = str(path).split(".")
        cur = target
        for key in keys[:-1]:
            if key not in cur or not isinstance(cur[key], dict):
                cur[key] = {}
            cur = cur[key]
        cur[keys[-1]] = value

    @staticmethod
    def _deep_merge(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
        """深度合并两个 resource_context 片段"""
        merged = dict(base)
        for k, v in (incoming or {}).items():
            if k == "candidate_products":
                k = "recommended_packages"
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = DataStep._deep_merge(merged[k], v)
            elif isinstance(v, list) and v:
                merged[k] = v
            elif v not in (None, "", [], {}):
                merged[k] = v
        return merged

    @staticmethod
    def _write_to_ctx(ctx: FlowContext, data: Dict[str, Any]) -> None:
        """将合并后的资源数据写入 FlowContext 对应字段。

        由 _NODE_TO_CTX_FIELD 白名单统一驱动，新增域字段只需更新白名单即可。
        """
        for field in _NODE_TO_CTX_FIELD:
            val = data.get(field)
            if val:
                setattr(ctx, field, val)
        # 兼容旧版 extra_resources（不在白名单内）
        if data.get("extra_resources"):
            ctx.extra_resources = data["extra_resources"]

    def _cache_key(
        self,
        ctx: FlowContext,
        enabled_nodes: Dict[str, Any],
    ) -> str:
        """生成缓存 key

        纳入**整份接口节点配置**的哈希（而非仅 URL）：保存 api_nodes 后配置立即生效。
        只按 URL 做 key 时，改 response_extract / field_transform / passthrough_fields
        这类只影响映射结果、不改 URL 的配置，缓存不会失效——TTL 窗口内测试页仍返回
        改配置前的映射结果，表现为「保存了但没生效」。节点配置在两次保存之间是稳定的，
        纳入哈希不降低命中率。
        extra_data 同样参与请求参数构造（占位符展开），必须纳入 key，
        否则不同用户的 extra_data 不同但 extra_vars 相同时会命中同一缓存。
        extra_info 是直传（source_type=direct）节点的映射数据源，同样必须纳入 key。
        """
        payload = {
            "province":   self.province,
            "phone":      ctx.phone,
            "intent":     ctx.intent,
            "extra_data": ctx.extra_data,
            "extra_info": ctx.extra_info,
            "extra_vars": ctx.extra_vars,
            "api_nodes":  enabled_nodes,
        }
        h = hashlib.md5(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()[:16]
        return f"data_step:{self.province}:{ctx.phone}:{h}"
