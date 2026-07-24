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
from typing import Any, Callable, Dict, Optional

from loguru import logger

from core.context import FlowContext
from plugins.unit_converter import UnitConverterRegistry
from services.api_client import api_client
from services.cache_service import cache_service
from utils.observability import record_stage
from utils.placeholder import FIXED_PARAM_MAP


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


def _filter_empty(d: Dict[str, Any]) -> Dict[str, Any]:
    """过滤空值（空字符串 / None / null 字面量）"""
    return {k: v for k, v in d.items() if str(v).strip() not in ("", "None", "null")}


@_register_rule("passthrough")
def _rule_passthrough(source: Any, rule: Dict[str, Any]) -> Any:
    return source


@_register_rule("filter_include", "include")
def _rule_filter_include(source: Any, rule: Dict[str, Any]) -> Any:
    include_keys = set(rule.get("include_keys", []))
    if not isinstance(source, dict):
        return {}
    return DataStep._apply_unit_convert(
        _filter_empty({k: v for k, v in source.items() if k in include_keys}), rule
    )


@_register_rule("filter_exclude", "exclude")
def _rule_filter_exclude(source: Any, rule: Dict[str, Any]) -> Any:
    exclude_keys = set(rule.get("exclude_keys", []))
    if not isinstance(source, dict):
        return {}
    return DataStep._apply_unit_convert(
        _filter_empty({k: v for k, v in source.items() if k not in exclude_keys}), rule
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

    # ── 并发取数（可被 get_or_load 缓存）────────────────────────

    async def _fetch_all(
        self,
        ctx: FlowContext,
        enabled_nodes: Dict[str, Any],
    ) -> Dict[str, Any]:
        """并发调用所有启用接口，返回 {"resources": ..., "raw_responses": ...}"""
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

        for (api_name, node_cfg), result in zip(enabled_nodes.items(), results):
            if isinstance(result, Exception):
                ctx.add_error(f"接口[{api_name}]失败: {result}")
                logger.error(f"[DataStep] ❌ {api_name}: {result}")
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

        # candidate_products 兼容性
        if not merged.get("recommended_packages") and merged.get("candidate_products"):
            merged["recommended_packages"] = merged.pop("candidate_products")

        # 兜底自愈：存在 api 节点、原始响应里有推荐列表，但映射后 recommended_packages 为空
        # ——通常是 ES 配置的 response_extract 缺失/改名了 recommended_packages 映射（如北京生产问题）。
        # 仅在「当前为空」时触发，绝不覆盖已成功映射的结果；direct 直传模式响应里不含
        # bean.recommend_results，不会误触发，其他省份行为不变。仍打 WARNING 提示修正配置。
        if not merged.get("recommended_packages"):
            merged["recommended_packages"] = self._salvage_recommendations(raw_responses)

        return {"resources": merged, "raw_responses": raw_responses, "passthrough": passthrough}

    @staticmethod
    def _salvage_recommendations(raw_responses: Dict[str, Any]) -> list:
        """从原始响应中按常见路径兜底提取推荐产品列表（映射缺失时的自愈）。

        探测路径（按优先级）：bean.recommend_results / recommend_results / bean.recommendResults /
        recommendResults / bean.recommendList / recommendList。命中即返回该列表并打 WARNING，
        提示运营修正该节点的 response_extract（recommended_packages → bean.recommend_results）。
        未命中返回 []（保持原行为）。
        """
        probe_keys = ("recommend_results", "recommendResults", "recommendList")
        for api_name, raw in raw_responses.items():
            if not isinstance(raw, dict):
                continue
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

    async def _call_one(
        self,
        api_name: str,
        api_cfg: Dict[str, Any],
        ctx: FlowContext,
        flattened_extra: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """调一个接口，返回 {"raw": ..., "resources": ...}

        flattened_extra：由 _fetch_all 预先展开的 extra_data 占位符，避免重复递归。
        超时时长优先读取 api_cfg["timeout_seconds"]，默认 DEFAULT_TIMEOUT 秒。

        source_type="direct" 的节点不发 HTTP 请求，直接以 ctx.extra_info 为数据源
        执行同样的两步映射（response_extract + field_transform）。
        """
        t0 = time.perf_counter()

        # ── 直传模式节点：extra_info 作为"响应"，不调外部接口 ──
        if api_cfg.get("source_type") == "direct":
            raw = ctx.extra_info or {}
            if not raw:
                logger.warning(f"[DataStep] 直传节点 {api_name}: extra_info 为空，跳过映射")
                return {"raw": {}, "resources": {}}
            # 直接透传子模式：不做 7 域强制映射，仅按同名标准域透传；
            # 自定义入参字段（如 recommend_actual_price）收集到 passthrough 通道，
            # 由 build_prompt 逐字段注入【上下文数据】（带 {字段名} 锚点）。
            if api_cfg.get("direct_mode") == "passthrough":
                resources = {
                    k: v for k, v in raw.items()
                    if k in _NODE_TO_CTX_FIELD and v not in (None, "", [], {})
                }
                # 选定要暴露的透传字段：配置了 passthrough_fields 用之，否则全部顶层字段；
                # 排除标准域（走 resources 通道）与下划线开头的内部键、空值。
                sel = api_cfg.get("passthrough_fields") or list(raw.keys())
                passthrough = {
                    k: raw[k] for k in sel
                    if isinstance(k, str) and not k.startswith("_")
                    and k in raw and k not in _NODE_TO_CTX_FIELD
                    and raw[k] not in (None, "", [], {})
                }
                # 嵌套画像对象（portrait_style）展开一层：把 communication_style /
                # business_conte 等标量子字段提升为独立透传字段，由 build_prompt 逐条注入
                # 【上下文数据】（带 {字段名} 锚点），供“话术要求”按名个性化引用；
                # 同时移除父级 JSON blob，避免同一信息重复出现。
                nested_style = raw.get("portrait_style")
                if isinstance(nested_style, dict):
                    passthrough.pop("portrait_style", None)
                    for ck, cv in nested_style.items():
                        if (isinstance(ck, str) and not ck.startswith("_")
                                and cv not in (None, "", [], {})
                                and ck not in _NODE_TO_CTX_FIELD):
                            passthrough.setdefault(ck, cv)
                logger.info(
                    f"[DataStep] 直传节点 {api_name} 透传模式：同名域={list(resources.keys())}，"
                    f"透传字段={list(passthrough.keys())}"
                )
                return {"raw": raw, "resources": resources, "passthrough": passthrough}
            if not api_cfg.get("response_extract") and not api_cfg.get("field_transform"):
                # 零配置兜底：extra_info 顶层 key 与 7 大标准域同名时直接写入
                resources = {
                    k: v for k, v in raw.items()
                    if k in _NODE_TO_CTX_FIELD and v not in (None, "", [], {})
                }
                logger.info(f"[DataStep] 直传节点 {api_name} 无映射规则，按同名域透传: {list(resources.keys())}")
                return {"raw": raw, "resources": resources}
            extracted = self._extract_fields(raw, api_cfg)
            resources = self._transform_fields(extracted, api_cfg, raw)
            record_stage(
                stage=f"data_step.{api_name}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                cache_hit=False, provider=f"direct:{api_name}", degrade_flag=False,
            )
            logger.info(f"[DataStep] 直传映射完成: {api_name} → {list(resources.keys())}")
            return {"raw": raw, "resources": resources}

        params = self._build_params(api_cfg, ctx, flattened_extra)
        timeout = float(api_cfg.get("timeout_seconds") or self.DEFAULT_TIMEOUT)
        _api_t0 = time.perf_counter()
        try:
            raw = await asyncio.wait_for(api_client.call(api_cfg, params), timeout=timeout)
        except asyncio.TimeoutError:
            self._log_api_call(
                api_name, api_cfg, ctx, params,
                response=None, elapsed_ms=(time.perf_counter() - _api_t0) * 1000,
                timeout_s=timeout, error=f"timeout>{timeout}s",
            )
            raise TimeoutError(f"接口 {api_name} 超时（>{timeout}s）")
        except Exception as e:  # 其余网络/上游异常：先落分省接口日志再原样抛出
            self._log_api_call(
                api_name, api_cfg, ctx, params,
                response=None, elapsed_ms=(time.perf_counter() - _api_t0) * 1000,
                timeout_s=timeout, error=f"{type(e).__name__}: {e}",
            )
            raise
        raw = raw or {}
        # 分省接口调用日志：请求参数 + 响应结果（便于按省排查接口查询链路）
        self._log_api_call(
            api_name, api_cfg, ctx, params,
            response=raw, elapsed_ms=(time.perf_counter() - _api_t0) * 1000,
            timeout_s=timeout, error=None,
        )

        # ── 新式两步映射（response_extract + field_transform）──
        # 入口守卫：只要配了 response_extract 或 field_transform 任一即触发映射。
        #   · 带 response_extract 的存量配置：行为与旧版完全一致（第①步取数命名 + 第②步转换）。
        #   · 仅配 field_transform（无 response_extract）：extracted 为空，第②步直接用 from 的
        #     JSON 路径从原始响应取值（见 _transform_fields 的路径回退），实现"免中间集直连映射"。
        if api_cfg.get("response_extract") or api_cfg.get("field_transform"):
            extracted = self._extract_fields(raw, api_cfg)
            resources = self._transform_fields(extracted, api_cfg, raw)
            record_stage(
                stage=f"data_step.{api_name}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                cache_hit=False, provider=api_name, degrade_flag=False,
            )
            logger.debug(f"[DataStep] 两步映射完成: {api_name}")
            return {"raw": raw, "resources": resources}

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
        return {"raw": raw, "resources": resources}

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

    def _transform_fields(
        self,
        extracted: Dict[str, Any],
        api_cfg: Dict[str, Any],
        raw: Any = None,
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
            # 优先取已命名中间槽位（历史行为）；仅当"显式"配置的 from 不是已命名中间集时，
            # 才按 JSON 路径回退到原始响应。省略 from 的规则保持与旧版完全一致，零回归。
            if from_field in extracted:
                source = extracted.get(from_field)
            elif explicit_from is not None and raw is not None:
                source = self._get_path(raw, from_field)
                if source is not None:
                    logger.debug(
                        f"[DataStep] field_transform '{target_path}' 的 from='{from_field}' "
                        f"未命中中间集，按 JSON 路径直接从响应取值"
                    )
            else:
                source = None
            # 数据源为空时响亮告警（此前静默跳过，导致 usage/tags 缺失长期不可见）：
            # 常见原因是 response_extract 丢失了被引用的中间槽位（如 raw_tags），
            # 或接口响应中对应路径无数据。目标域将为空 → 话术槽位取不到值。
            if source in (None, "", [], {}):
                logger.warning(
                    f"[DataStep] field_transform '{target_path}' 数据源为空: "
                    f"from='{from_field}'（中间槽位存在={from_field in extracted}，"
                    f"extracted 槽位={list(extracted.keys())}）→ 该映射域将缺失，"
                    f"请检查 response_extract 是否包含该槽位、接口响应对应路径是否有数据"
                )
            rule_type  = rule.get("type", "passthrough")
            value      = self._apply_field_rule(source, rule_type, rule)
            # 空容器（{} / []）不写入：空数据源经 filter 规则会产出空壳子字典，
            # 若写入会让映射域看似"有值"（truthy 外壳），污染下游空域检测与预览
            if value not in (None, {}, []):
                self._set_path(resources, target_path, value)

        return resources

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

        # 1. 先进行值转换（使用原始字段名作为 key）
        for field, converter in unit_convert.items():
            if field in result:
                result[field] = UnitConverterRegistry.apply(converter, result[field])
                logger.debug(f"[DataStep] Applied unit convert: {field} with {converter}")

        # 2. 然后执行字段重命名（目标名就地规范化：即使存量配置含畸形括号
        #    「近6月平均流量((GB)）」，产出的数据键也统一为「近6月平均流量(GB)」，
        #    保证与话术模板子字段占位符/调色板 token 同名对齐）
        from utils.field_naming import clean_rename_field

        for old_name, new_name in field_rename.items():
            if old_name in result:
                value = result.pop(old_name)
                cleaned = clean_rename_field(new_name)
                if cleaned != new_name:
                    logger.warning(
                        f"[DataStep] 重命名目标名含畸形括号，已规范化: '{new_name}' → '{cleaned}'"
                    )
                result[cleaned] = value
                logger.info(f"[DataStep] Renamed field '{old_name}' → '{cleaned}' after unit conversion")

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

        加入各接口 URL 的哈希，确保 api_nodes 配置更新后缓存自动失效。
        extra_data 同样参与请求参数构造（占位符展开），必须纳入 key，
        否则不同用户的 extra_data 不同但 extra_vars 相同时会命中同一缓存。
        extra_info 是直传（source_type=direct）节点的映射数据源，同样必须纳入 key。
        """
        api_urls = sorted(
            cfg.get("url", "mock" if cfg.get("mock_mode") else "")
            if cfg.get("source_type") != "direct" else f"direct:{name}"
            for name, cfg in enabled_nodes.items()
            if isinstance(cfg, dict)
        )
        payload = {
            "province":   self.province,
            "phone":      ctx.phone,
            "intent":     ctx.intent,
            "extra_data": ctx.extra_data,
            "extra_info": ctx.extra_info,
            "extra_vars": ctx.extra_vars,
            "api_urls":   api_urls,
        }
        h = hashlib.md5(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()[:16]
        return f"data_step:{self.province}:{ctx.phone}:{h}"
