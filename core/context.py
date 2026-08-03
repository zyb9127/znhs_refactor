"""
FlowContext — 贯穿三步管道的数据容器

替代原 LangGraph TypedDict MarketingState，使用纯 Python dataclass，
零框架依赖，类型安全，IDE 友好。

数据流：
  入参 → Step1(data_step) 填充 resource_context
       → Step2(recommend_step) 填充 final_recommendations
       → Step3(script_step) 填充 marketing_scripts
       → to_result() 构建对外响应
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FlowContext:
    """三步管道执行上下文"""

    # ─── 入参 ───────────────────────────────────────────────────
    phone: str                              # 用户手机号
    intent: str                             # 业务意图（与技能包目录名一致）
    province: str                           # 省份代码
    top_n: int = 3                          # 推荐数量
    trace_id: str = ""                      # 请求追踪ID
    extra_vars: Dict[str, str] = field(default_factory=dict)
    # 省份自定义占位符：{"{{IOID}}": "xxx", "{{CURRENT_OFFER_NAME}}": "xxx", ...}

    extra_data: Dict[str, Any] = field(default_factory=dict)
    """调用接口的补充入参（如 currentMainOffer），透传给 DataStep 用于构造接口请求"""

    extra_info: Dict[str, Any] = field(default_factory=dict)
    """非接口调用的已知信息（用户/产品），可直接用于话术生成，无需再调接口"""

    extra_context: Dict[str, Any] = field(default_factory=dict)
    """【已废弃，仅保留内部兼容】话术模板匹配上下文（含 stage/scence）。
    对外接口已去掉该字段，请统一使用 batch_contexts 替代。
    """

    passthrough_context: Dict[str, Any] = field(default_factory=dict)
    """直传透传字段（运行时通道，不进 ES）：source_type=direct 且 direct_mode=passthrough 的节点，
    把选定的 extra_info 入参字段（非 7 标准域的自定义字段）按原字段名收集于此，
    由 build_prompt 逐字段注入【上下文数据】（带 {字段名} 锚点），供话术模板槽位填充。
    """

    batch_contexts: List[Dict[str, Any]] = field(default_factory=list)
    """话术生成上下文列表，支持单条或多条，非空时进入批量模式（统一入口）。
    每项可含（均可为空）：product_id（产品ID）、stage（环节）、scence（场景）、expand（布尔，枚举模式）。

    匹配规则：
    - 有推荐产品（接口返回 recommended_packages）时：
        product_id 非空 → 从推荐结果中找对应产品；找不到则以ID构造占位产品；
        product_id 为空 → 对所有推荐结果展开。
    - 无推荐产品（不调接口/接口无返回）时：
        product_id 非空 → 直接以该ID匹配话术模板；
        product_id 为空 → 以 stage/scence 匹配通用话术模板（空产品对象兜底）。
    - expand=true 且 scence 为空：枚举 product_id+stage 下所有 scene，每个 scene 各生成一条话术。
    """

    # ─── Step1 输出：统一 resource_context ───────────────────────
    current_package: Dict[str, Any] = field(default_factory=dict)
    """用户当前套餐信息
    标准字段: offerId/offerName/initFee/offerFlow/offerVoice/...
    """

    usage: Dict[str, Any] = field(default_factory=dict)
    """用户历史用量
    结构:
      data_usage:  {近6月平均流量(GB), 近6月平均流量饱和度, is_frequently_exceed, ...}
      voice_usage: {近6月平均主叫时长, 近6月平均语音饱和度, ...}
      consumption: {近6月平均月消费, 近3月平均月消费, ...}
    """

    tags: Dict[str, Any] = field(default_factory=dict)
    """用户标签
    示例: {融合状态, 高频高额超套客户, 是否老旧套餐, 双卡槽状态, ...}
    """

    recommended_packages: List[Dict[str, Any]] = field(default_factory=list)
    """上游推荐产品列表（来自外部接口，Step1 填充）
    标准字段: offerId/offerName/initFee/offerFlow/offerVoice/rank/...
    """

    user_info: Dict[str, Any] = field(default_factory=dict)
    """用户基础信息
    示例: 用户等级、网龄、终端类型、开通时间、星级等
    """

    user_profile: Dict[str, Any] = field(default_factory=dict)
    """用户画像
    示例: 流量偏好/使用场景、语音偏好等
    """

    domain_ext: Dict[str, Any] = field(default_factory=dict)
    """扩展域：上述信息对应不上的情况
    示例: 家庭业务、当前参加的合约/活动信息、订购信息等
    """

    extra_resources: Dict[str, Any] = field(default_factory=dict)
    """兼容旧版扩展资源节点（已被 domain_ext 替代，保留向后兼容）"""

    raw_responses: Dict[str, Any] = field(default_factory=dict)
    """各接口原始响应（key=api_nodes中的api_name）
    北京场景：透传 bean 到 other_info 时使用
    """

    api_call_traces: List[Dict[str, Any]] = field(default_factory=list)
    """接口查询模式下各节点的调用轨迹（入参/出参/耗时/错误），供测试页排障。
    每项含：api_name / source_type / url / method / mock_mode / request / response /
    elapsed_ms / error。直传节点也会记录（request 为 extra_info 摘要）。
    """

    # ─── Step2 输出：推荐筛选结果 ─────────────────────────────────
    final_recommendations: List[Dict[str, Any]] = field(default_factory=list)
    """经策略筛选后的推荐产品列表（TopN，含rank字段）"""

    # ─── Step3 输出：话术 ────────────────────────────────────────
    marketing_scripts: List[Dict[str, Any]] = field(default_factory=list)
    """每个推荐产品对应的话术列表
    每项结构: {product_id, rank, marketing_text, package_name, ...}
    """

    # ─── 元数据 ──────────────────────────────────────────────────
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    _start_time: float = field(default_factory=time.perf_counter, repr=False)

    # ─── 便捷属性 ────────────────────────────────────────────────

    @property
    def resource_context(self) -> Dict[str, Any]:
        """返回统一 resource_context 结构（供话术模板/LLM使用）
        固定 6 大核心域 + 1 扩展域
        """
        return {
            "current_package":      self.current_package,
            "usage":                self.usage,
            "tags":                 self.tags,
            "user_info":            self.user_info,
            "recommended_packages": self.recommended_packages,
            "user_profile":         self.user_profile,
            "domain_ext":           self.domain_ext,
        }

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._start_time) * 1000

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def _build_recommend_results(self) -> List[Dict[str, Any]]:
        """构建 recommend_results 列表。

        - 批量模式（batch_contexts 非空）：每条话术回显对应的 stage/scence
        - 单维度模式：保持原有字段，不含 stage/scence
        """
        is_batch = bool(self.batch_contexts)
        results: List[Dict[str, Any]] = []
        for idx, item in enumerate(self.marketing_scripts):
            # 字段顺序对齐对外规范样例：批量模式 stage/scence 在前
            entry: Dict[str, Any] = {}
            if is_batch:
                entry["stage"]  = item.get("stage", "")
                entry["scence"] = item.get("scence", "")
            entry["product_id"] = item.get("product_id", "")
            entry["offerId"] = item.get("offerId", "")
            entry["rank"] = item.get("rank", idx + 1)
            entry["marketing_text"] = item.get("marketing_text", "")
            entry["diff_table"] = item.get("diff_table")
            results.append(entry)
        return results

    def _build_llm_prompts(self) -> List[Dict[str, Any]]:
        """从 marketing_scripts 提取发给大模型的最终提示词分段（测试页排障用）。"""
        out: List[Dict[str, Any]] = []
        for item in self.marketing_scripts:
            p = item.get("_llm_prompt")
            if not isinstance(p, dict):
                continue
            entry = dict(p)
            # 批量模式可能在 finalize 之后覆写 stage/scence，以话术结果为准
            if item.get("stage") is not None:
                entry["stage"] = item.get("stage") or ""
            if item.get("scence") is not None:
                entry["scence"] = item.get("scence") or ""
            out.append(entry)
        return out

    def to_result(self) -> Dict[str, Any]:
        """构建标准对外响应结构"""
        return {
            "success":               not bool(self.errors) or len(self.marketing_scripts) > 0,
            "phone":                 self.phone,
            "intent":                self.intent,
            "province":              self.province,
            "trace_id":              self.trace_id,
            # 三步核心输出
            "resource_context":      self.resource_context,
            "final_recommendations": self.final_recommendations,
            "marketing_scripts":     self.marketing_scripts,
            # 对外接口格式（recommend_results）
            # 批量模式下每条结果回显 stage/scence；单维度模式不含这两个字段
            "recommend_results": self._build_recommend_results(),
            "errors":   self.errors,
            "api_calls": list(self.api_call_traces),
            # 每条话术对应的最终 LLM 提示词分段（上下文数据/模板/话术要求/其他）
            "llm_prompts": self._build_llm_prompts(),
            "metadata": {
                **self.metadata,
                "elapsed_ms":            round(self.elapsed_ms, 1),
                "recommendation_count":  len(self.final_recommendations),
                "script_count":          len(self.marketing_scripts),
            },
        }
