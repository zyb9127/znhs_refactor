"""
test_prompt_builder — engine.prompt_builder 与原 ScriptStep._build_prompt 的逐字符等价测试

oracle 策略（REFACTOR-SPEC-V2 R3d，选"复制原函数体进测试文件"方案）：
    _OraclePromptStep 继承 ScriptStep，并用逐行复制自
    _refactor_backup/2026-07-03/steps__script_step.py 的原实现体覆盖以下成员：
      - _VAR_LABELS                   （原 L770-784）
      - _resource_context_prompt_vars （原 L800-812）
      - _append_prompt_extra_suffix   （原 L830-841）
      - _build_prompt                 （原 L843-954）
    这样即使后续阶段（R3c）把 ScriptStep 上述方法改成对 engine.prompt_builder 的
    薄委托，oracle 仍保有原实现，测试不退化为自我对照。
    未迁移的格式化工具（_fmt_package/_fmt_usage/_fmt_tags/_fmt_flat_domain/
    _fmt_recommended_product_full/_fmt_extra_for_prompt）经继承取得（原逻辑不变）。

覆盖三组固定 fixture：
    1. 新格式 linked_vars 路径（含 table/recommended_packages 跳过、未知变量、
       extra 自动追加、script_requirement、extra_info_override）
    2. 旧格式 user_prompt_tpl format_map 路径（自定义模板/缺省模板/占位符抑制后缀）
    3. format 异常兜底路径（KeyError 与 ValueError）

运行：cd ROOT && python -m unittest tests.test_prompt_builder -v
约束：不调真实网络/ES/Redis/LLM（纯内存字符串组装）。
"""
from __future__ import annotations

import json
import re
import unittest
import unittest.mock
from typing import Any, Dict, List, Optional

from engine.prompt_builder import (
    VAR_LABELS,
    append_prompt_extra_suffix,
    build_prompt,
    preview_prompt,
    resource_context_prompt_vars,
)

# 依赖 ScriptStep 的格式化工具与 FlowContext/PackageDiff；
# script_step 导入失败（如环境缺配置）时跳过全部对照测试并注明。
_BASELINE_IMPORT_ERROR = ""
try:
    from core.context import FlowContext
    from plugins.package_diff import PackageDiff
    from steps.script_step import ScriptStep
    _BASELINE_AVAILABLE = True
except Exception as exc:  # noqa: BLE001 - 记录任意导入失败原因
    FlowContext = None  # type: ignore[assignment]
    PackageDiff = None  # type: ignore[assignment]
    ScriptStep = object  # type: ignore[assignment]
    _BASELINE_AVAILABLE = False
    _BASELINE_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


def setUpModule() -> None:
    if not _BASELINE_AVAILABLE:
        print(
            "[test_prompt_builder] steps.script_step 导入失败，"
            f"跳过 Prompt 等价对照测试: {_BASELINE_IMPORT_ERROR}"
        )


class _OraclePromptStep(ScriptStep):  # type: ignore[misc]
    """原实现 oracle：以下成员逐行复制自 _refactor_backup/2026-07-03/steps__script_step.py。

    来源行号：_VAR_LABELS L770-784 / _resource_context_prompt_vars L800-812 /
    _append_prompt_extra_suffix L830-841 / _build_prompt L843-954。
    """

    # ── 原 L770-784 ────────────────────────────────────────────
    _VAR_LABELS: Dict[str, str] = {
        "cur_brief":  "当前套餐信息",
        "current_package": "当前套餐信息",
        "pkg_brief":  "推荐产品信息",
        "diff_str":   "差异",
        "usage_line": "历史用量",
        "usage":      "历史用量",
        "user_tags":  "用户标签",
        "tags":       "用户标签",
        "user_info":  "用户基础信息",
        "user_profile": "用户画像",
        "domain_ext": "扩展信息",
        "extra_info": "主服务补充信息(extra_info)",
        "extra_context": "模板匹配上下文(extra_context)",
    }

    # ── 原 L800-812 ────────────────────────────────────────────
    def _resource_context_prompt_vars(
        self, ctx: "FlowContext", fa: Dict[str, Any]
    ) -> Dict[str, str]:
        """唯一数据源：FlowContext.resource_context（与《数据映射域》核心域对齐，不含整表推荐列表摘要）。"""
        rc = ctx.resource_context
        return {
            "current_package": self._fmt_package(rc["current_package"], fa),
            "usage": self._fmt_usage(rc["usage"]),
            "tags": self._fmt_tags(rc["tags"]),
            "user_info": self._fmt_flat_domain(rc["user_info"]),
            "user_profile": self._fmt_flat_domain(rc["user_profile"]),
            "domain_ext": self._fmt_flat_domain(rc["domain_ext"]),
        }

    # ── 原 L830-841 ────────────────────────────────────────────
    @staticmethod
    def _append_prompt_extra_suffix(
        tpl_raw: str, body: str, ei_txt: str, ec_txt: str
    ) -> str:
        """旧版 user_prompt_tpl 若未写 {extra_info}/{extra_context} 占位符，则追加主服务入参段。"""
        t = tpl_raw or ""
        out = body
        if ei_txt and "{extra_info}" not in t:
            out = f"{out}\n主服务补充信息(extra_info)：{ei_txt}"
        if ec_txt and "{extra_context}" not in t:
            out = f"{out}\n模板匹配上下文(extra_context)：{ec_txt}"
        return out

    # ── 原 L843-954 ────────────────────────────────────────────
    def _build_prompt(
        self,
        user_prompt_tpl: str,
        template_text: str,
        ctx: "FlowContext",
        pkg: Dict[str, Any],
        diff: Any,
        linked_vars: Optional[List[str]] = None,
        script_requirement: str = "",
        extra_info_override: Optional[Dict[str, Any]] = None,
    ) -> str:
        fa = self.field_aliases
        rp = self._resource_context_prompt_vars(ctx, fa)
        pkg_brief = self._fmt_recommended_product_full(pkg, fa)
        if not (pkg_brief or "").strip():
            pkg_brief = self._fmt_package(pkg, fa)
        diff_str     = diff.summary_str()
        template_ref = re.sub(r"\{[^{}]+\}", "", template_text or "").strip()

        # 批量模式：用条目级 extra_info（已合并全局）；单条模式：用 ctx.extra_info
        effective_extra_info = extra_info_override if extra_info_override is not None else ctx.extra_info
        ei_txt = self._fmt_extra_for_prompt(effective_extra_info)
        ec_txt = self._fmt_extra_for_prompt(ctx.extra_context)

        def _fmt_num(v):
            if v is None:
                return ""
            try:
                f = float(v)
            except (TypeError, ValueError):
                return str(v)
            return str(int(f)) if abs(f - round(f)) < 1e-9 else str(round(f, 2))

        fmt_vars = dict(
            intent=ctx.intent,
            **rp,
            cur_brief=rp["current_package"],
            cur_name=rp["current_package"],
            pkg_brief=pkg_brief,
            pkg_name=pkg_brief,
            diff_str=diff_str,
            usage_line=rp["usage"],
            user_tags=rp["tags"],
            template=template_ref,
            template_ref=template_ref,
            template_content=template_text or "",
            table=diff.table_str(),
            max_length=self.max_length,
            reason_line=pkg.get("recommendation_reason", "更匹配您的使用需求"),
            features_line="",
            recommended_packages="",  # 已弃用，保留空串以免旧 user_prompt_tpl 含 {recommended_packages} 时 format 失败
            pkg_fee=_fmt_num(diff.tgt_fee),
            pkg_flow=_fmt_num(diff.tgt_data),
            pkg_voice=_fmt_num(diff.tgt_voice),
            extra_info=ei_txt,
            extra_context=ec_txt,
        )

        # ── 新格式：linked_vars 驱动的 context 工程 Prompt ───────────────
        # oracle：逐行镜像 engine.prompt_builder.build_prompt 新格式分支（context 工程重构版），
        # 字符串与 prompt/script_generation.py 的 SCRIPT_* 常量保持一致。
        if linked_vars or template_text:
            def _fmt_pt(v):
                if v is None:
                    return ""
                if isinstance(v, (dict, list)):
                    import json
                    try:
                        return json.dumps(v, ensure_ascii=False)
                    except (TypeError, ValueError):
                        return str(v)
                return str(v)

            def _resolve_var(var_key):
                v = fmt_vars.get(var_key, "")
                if str(v).strip() != "":
                    return str(v)
                if isinstance(effective_extra_info, dict) and var_key in effective_extra_info:
                    return _fmt_pt(effective_extra_info.get(var_key))
                return ""

            # ── 同义变量组 / 派生变量标签（镜像 engine.prompt_builder） ──
            _GROUPS = {
                "current_package": ("current_package", "cur_brief", "cur_name"),
                "usage":           ("usage", "usage_line"),
                "tags":            ("tags", "user_tags"),
                "pkg_brief":       ("pkg_brief", "pkg_name"),
            }
            _CANON = {a: c for c, aliases in _GROUPS.items() for a in aliases}

            def _grp(key):
                return _GROUPS.get(_CANON.get(key, key), (key,))

            _DERIVED_LABELS = {
                "pkg_fee":   "推荐套餐月费(元)",
                "pkg_flow":  "推荐套餐流量(GB)",
                "pkg_voice": "推荐套餐语音(分钟)",
            }

            tpl_tokens = re.findall(r"\{(\w+)\}", template_text or "")
            tpl_token_set = set(tpl_tokens)

            def _anchor_for(var_key):
                if var_key in tpl_token_set:
                    return var_key
                for alias in _grp(var_key):
                    if alias in tpl_token_set:
                        return alias
                return var_key

            context_lines: List[str] = []
            emitted: set = set()
            for var_key in linked_vars or []:
                if var_key == "table":
                    continue   # 差异表格不进 LLM，在前端另行展示
                if var_key == "recommended_packages":
                    continue   # 已下线：不再向 Prompt 注入候选条数摘要（兼容旧模板 linked_vars）
                if var_key in emitted:
                    continue   # 同义组已注入
                var_val = _resolve_var(var_key)
                if var_val.strip() == "":
                    continue   # 空事实不展示
                anchor = _anchor_for(var_key)
                label = (
                    _DERIVED_LABELS.get(anchor) or self._VAR_LABELS.get(anchor)
                    or _DERIVED_LABELS.get(var_key) or self._VAR_LABELS.get(var_key, var_key)
                )
                context_lines.append(f"{label} {{{anchor}}}：{var_val}")
                emitted.update(_grp(var_key))
                emitted.add(var_key)
                emitted.add(anchor)
            passthrough_ctx = getattr(ctx, "passthrough_context", None) or {}
            if isinstance(passthrough_ctx, dict):
                for pk, pv in passthrough_ctx.items():
                    if pk in emitted:
                        continue
                    val = _fmt_pt(pv)
                    if val.strip() == "":
                        continue
                    label = self._VAR_LABELS.get(pk, pk)
                    context_lines.append(f"{label} {{{pk}}}：{val}")
                    emitted.add(pk)
            _INJECTABLE_KNOWN = (
                set(self._VAR_LABELS) | set(_CANON) | set(_DERIVED_LABELS)
            ) - {"table", "recommended_packages", "extra_info", "extra_context"}
            if template_text:
                for token in tpl_tokens:
                    if token in emitted:
                        continue
                    if token in fmt_vars:
                        if token not in _INJECTABLE_KNOWN:
                            continue
                        val = _resolve_var(token)
                        if val.strip() == "":
                            continue
                        label = _DERIVED_LABELS.get(token) or self._VAR_LABELS.get(token, token)
                        context_lines.append(f"{label} {{{token}}}：{val}")
                        emitted.update(_grp(token))
                        emitted.add(token)
                        continue
                    if not isinstance(effective_extra_info, dict) or token not in effective_extra_info:
                        continue
                    val = _fmt_pt(effective_extra_info.get(token))
                    if val.strip() == "":
                        continue
                    label = self._VAR_LABELS.get(token, token)
                    context_lines.append(f"{label} {{{token}}}：{val}")
                    emitted.add(token)
            if "extra_info" not in emitted and ei_txt and not passthrough_ctx:
                context_lines.append(f"{self._VAR_LABELS['extra_info']} {{extra_info}}：{ei_txt}")
            if "extra_context" not in emitted and ec_txt:
                context_lines.append(f"{self._VAR_LABELS['extra_context']} {{extra_context}}：{ec_txt}")

            # 缺失事实负向约束（镜像 engine.prompt_builder；oracle 不含子字段占位符分支，
            # 故只统计模板里的普通 {token}，与该分支用例的覆盖范围一致）
            _missing = [
                t for t in sorted(tpl_token_set)
                if t not in emitted and t in _INJECTABLE_KNOWN
            ]

            def _slot_label(token):
                return _DERIVED_LABELS.get(token) or self._VAR_LABELS.get(token, token)

            missing_block = ""
            if _missing and template_text:
                missing_block = (
                    "【缺失事实】本次未取到以下槽位的数据（映射结果为空）："
                    + "、".join(f"{_slot_label(t)}{{{t}}}" for t in _missing)
                    + "。上述槽位没有任何可用事实：严禁编造，严禁用其他行的值代替"
                    "——尤其不得用当前套餐或推荐套餐的包含量（套餐流量、语音额度、月费）"
                    "冒充用户的历史使用量（月均流量、主叫时长、月均消费）；"
                    "请在话术中整体略过相关表述，也不得保留占位符原文。"
                )

            lines = [
                "你是套餐营销推荐坐席，负责将【上下文数据】填充进【话术模板】，"
                "生成自然、口语化的个性化套餐营销推荐话术。"
            ]
            if context_lines:
                lines.append(
                    "【上下文数据】（映射模式最终事实：接口出参已按映射规则写入标准域并完成字段重命名与单位换算；"
                    "直传字段为主服务入参原值。下列每行「{占位符}：值」即为该槽位唯一正确取值，"
                    "请按同名占位符原样填入话术，勿反推接口原始字段名、勿改写数值。"
                    "未列出的占位符表示映射结果为空——是你唯一可依据的事实来源，请勿使用未在此列出的信息）"
                )
                lines.extend(context_lines)
            if missing_block:
                lines.append(missing_block)
            if template_text:
                lines.append("【话术模板】")
                lines.append(template_text)
            lines.append(
                "【生成规则】\n"
                "1. 仅依据【上下文数据】中的事实填充话术模板，不得编造数据中不存在的"
                "数字、套餐名、优惠、功能或权益。\n"
                "2. 若某项信息缺失、为空或为 0（即【上下文数据】无对应 {占位符} 行），"
                "则跳过该占位符所在表述，既不提及、也不得用其他字段的值代替"
                "（例如语音为 0 则不谈语音；优惠月数为 0 则表述为“连续包月”而非“连续 0 个月”）；"
                "严禁把占位符原文（如 {current_package}）留在输出中。\n"
                "3. 占位符一一对应：【上下文数据】每行已用 {占位符} 标注槽位，"
                "请将【话术模板】中出现的同名 {占位符} 替换为该行冒号后的事实值"
                "（含 {域[子键]} 子字段占位符，须整串同名精确对应，不得拆开或改名）；"
                "有对应行则必须填入该行的值，不得留空或改用其他行。"
                "严禁串填：{current_package}/{current_package[…]} 只用当前套餐行，"
                "{pkg_brief}/{pkg_name}/{recommended_package} 只用推荐产品行，"
                "不得用推荐套餐名/资费冒充当前套餐，也不得用套餐内包含量"
                "（套餐流量/语音额度/月费）冒充历史使用量（月均流量/主叫时长/月均消费），反之亦然；"
                "若该行事实包含多个指标（如历史用量、用户标签），不得原样罗列、也不得因内容多而整体略过该槽位，"
                "应提炼其中最能支撑推荐理由的 1-3 个要点，口语化融入话术"
                "（如“您月均流量已达37GB、接近饱和”）。\n"
                "4. 保留话术模板的语义与结构，输出贴合用户痛点、可直接对客播报的完整话术，"
                "最终结果不得残留任何 {} 占位符或字段名。"
            )
            # 个性化润色规则（镜像 engine.prompt_builder._has_persona_context）
            _persona_vars = {"tags", "user_tags", "user_profile"}
            _persona_hints = ("style", "persona", "portrait", "性格", "画像", "风格", "偏好")
            _has_persona = bool(emitted & _persona_vars) or any(
                any(h in str(k).lower() for h in _persona_hints)
                for k in (passthrough_ctx or {})
            )
            rule_no = 5
            if _has_persona:
                lines.append(
                    f"{rule_no}. 个性化润色：结合【上下文数据】中的用户标签/画像/性格信息调整称呼、语气与卖点顺序"
                    "（如价格敏感型客户强调优惠与性价比、流量大户强调流量升级、性格沉稳者用平实可信的措辞），"
                    "标签与画像仅用于选择表达风格和卖点侧重，不得把标签名或画像字段名原样写进话术。"
                )
                rule_no += 1
            if script_requirement:
                lines.append(f"{rule_no}. 话术要求：{script_requirement}")
            lines.append("请直接输出话术文本，不需要任何前缀标签：\n话术：")
            return "\n".join(lines)

        # ── 旧格式：user_prompt_tpl（向后兼容） ──────────────────────
        tpl = user_prompt_tpl or (
            "用户当前套餐：{cur_brief}\n"
            "推荐套餐：{pkg_brief}\n"
            "套餐差异：{diff_str}\n"
            "近期用量：{usage_line}\n"
            "用户标签：{user_tags}\n"
            "用户基础信息：{user_info}\n"
            "用户画像：{user_profile}\n"
            "扩展信息：{domain_ext}\n"
            "意图：{intent}\n\n"
            "请用中文写一句{max_length}字以内的营销推荐话术，结尾带办理引导。\n话术："
        )
        try:
            out = tpl.format_map(fmt_vars)
            return self._append_prompt_extra_suffix(tpl, out, ei_txt, ec_txt)
        except (KeyError, ValueError):
            fb = (
                f"用户当前套餐：{rp['current_package']}\n推荐套餐：{pkg_brief}\n"
                f"套餐差异：{diff_str}\n近期用量：{rp['usage']}\n"
                f"用户标签：{rp['tags']}\n"
                f"用户基础信息：{rp['user_info']}\n用户画像：{rp['user_profile']}\n"
                f"扩展信息：{rp['domain_ext']}\n"
                f"意图：{ctx.intent}\n\n"
                f"请用中文写一句{self.max_length}字以内的营销推荐话术。\n话术："
            )
            return self._append_prompt_extra_suffix(tpl, fb, ei_txt, ec_txt)


# ── fixture 构造 ──────────────────────────────────────────────

def make_ctx(**overrides: Any) -> "FlowContext":
    """构造一份数据齐全的 FlowContext（各域均非空，覆盖全部格式化分支）。"""
    base: Dict[str, Any] = dict(
        phone="13800001111",
        intent="套餐推荐",
        province="shandong",
        current_package={
            "offerName": "畅享39元套餐",
            "initFee": 39,
            "offerFlow": 10,
            "offerVoice": 100,
            "备注": "在网老套餐",
            "offerDesc": "含来显",
        },
        usage={
            "data_usage": {"近6月平均流量(GB)": 25, "近6月平均流量饱和度": "180%"},
            "voice_usage": {"近6月平均主叫时长": 200},
            "consumption": {"近6月平均月消费": 45},
        },
        tags={"高频高额超套客户": "是", "是否老旧套餐": "否", "双卡槽状态": "单卡"},
        user_info={"星级": "五星", "网龄": "10年", "嵌套": {"终端类型": "5G手机"}},
        user_profile={"流量偏好": "视频类应用"},
        domain_ext={"活动": {"合约到期": "2026-09"}},
        extra_info={"目标产品卖点": "流量翻倍", "优惠": "首月半价"},
        extra_context={"stage": "犹豫挽留环节", "scence": "套餐升档"},
    )
    base.update(overrides)
    return FlowContext(**base)


_SAMPLE_PKG: Dict[str, Any] = {
    "offerId": "P100",
    "offerName": "畅享99元套餐",
    "initFee": 99,
    "offerFlow": 60,
    "offerVoice": 500,
    "offerDesc": "含视频会员",
    "rank": 1,
    "recommendation_reason": "更划算",
    "otherRight": ["视频会员"],
}

_FIELD_ALIASES: Dict[str, Any] = {
    "pkg_name": ["offerName", "package_name"],
    "pkg_fee": ["initFee", "monthly_fee"],
    "pkg_flow": ["offerFlow", "data_quota"],
    "pkg_voice": ["offerVoice", "voice_quota"],
    "product_id": ["offerId", "product_id"],
}


@unittest.skipUnless(_BASELINE_AVAILABLE, f"script_step 导入失败: {_BASELINE_IMPORT_ERROR}")
class EquivalenceBase(unittest.TestCase):
    """提供「新模块输出与 oracle 逐字符相等」的断言工具。"""

    def assert_same_prompt(
        self,
        *,
        ctx: "FlowContext",
        pkg: Dict[str, Any],
        user_prompt_tpl: str = "",
        template_text: str = "",
        linked_vars: Optional[List[str]] = None,
        script_requirement: str = "",
        extra_info_override: Optional[Dict[str, Any]] = None,
        field_aliases: Optional[Dict[str, Any]] = None,
        max_length: int = 100,
    ) -> str:
        diff = PackageDiff(ctx.current_package, pkg)

        oracle = _OraclePromptStep()
        oracle.field_aliases = field_aliases or {}
        oracle.max_length = max_length
        expected = oracle._build_prompt(
            user_prompt_tpl=user_prompt_tpl,
            template_text=template_text,
            ctx=ctx,
            pkg=pkg,
            diff=diff,
            linked_vars=linked_vars,
            script_requirement=script_requirement,
            extra_info_override=extra_info_override,
        )
        actual = build_prompt(
            user_prompt_tpl=user_prompt_tpl,
            template_text=template_text,
            ctx=ctx,
            pkg=pkg,
            diff=diff,
            linked_vars=linked_vars,
            script_requirement=script_requirement,
            extra_info_override=extra_info_override,
            field_aliases=field_aliases,
            max_length=max_length,
        )
        self.assertEqual(actual, expected)  # 逐字符相等
        return actual


class TestNewFormatLinkedVars(EquivalenceBase):
    """fixture 组 1：新格式 linked_vars 路径。"""

    TEMPLATE_TEXT = "尊敬的客户您好，您当前使用{cur_brief}，为您推荐{pkg_brief}，{diff_str}。"

    def test_full_linked_vars(self) -> None:
        """覆盖全部已知变量 + table/recommended_packages 跳过 + 未知变量原样标签。"""
        out = self.assert_same_prompt(
            ctx=make_ctx(),
            pkg=_SAMPLE_PKG,
            template_text=self.TEMPLATE_TEXT,
            linked_vars=[
                "cur_brief", "pkg_brief", "diff_str", "usage_line", "user_tags",
                "user_info", "user_profile", "domain_ext",
                "table", "recommended_packages",
                "extra_info", "extra_context", "unknown_var",
            ],
            script_requirement="不超过80字，语气亲切，结尾带办理引导",
            field_aliases=_FIELD_ALIASES,
            max_length=80,
        )
        self.assertIn("【话术模板】", out)
        self.assertIn("【上下文数据】", out)
        self.assertIn("【生成规则】", out)
        self.assertIn("话术要求：不超过80字", out)
        self.assertNotIn("unknown_var：", out)        # 未知变量取值为空 → 空事实不展示
        self.assertNotIn("recommended_packages：", out)  # 已下线变量被跳过

    def test_auto_append_extra_when_not_linked(self) -> None:
        """未勾选 extra_info/extra_context 但字段非空 → 自动追加行。"""
        out = self.assert_same_prompt(
            ctx=make_ctx(),
            pkg=_SAMPLE_PKG,
            template_text=self.TEMPLATE_TEXT,
            linked_vars=["cur_brief", "pkg_brief"],
            field_aliases=_FIELD_ALIASES,
        )
        self.assertIn("主服务补充信息(extra_info) {extra_info}：", out)
        self.assertIn("模板匹配上下文(extra_context) {extra_context}：", out)

    def test_template_text_without_linked_vars(self) -> None:
        """linked_vars 为空但模板正文非空 → 仍走新格式。"""
        out = self.assert_same_prompt(
            ctx=make_ctx(extra_info={}, extra_context={}),
            pkg=_SAMPLE_PKG,
            template_text="推荐话术正文，无占位符。",
            linked_vars=[],
        )
        self.assertTrue(out.startswith("你是套餐营销推荐坐席，"))

    def test_extra_info_override(self) -> None:
        """批量模式条目级 extra_info 覆盖 ctx.extra_info。"""
        out = self.assert_same_prompt(
            ctx=make_ctx(),
            pkg=_SAMPLE_PKG,
            template_text=self.TEMPLATE_TEXT,
            linked_vars=["cur_brief", "extra_info"],
            extra_info_override={"条目级信息": "覆盖生效"},
            field_aliases=_FIELD_ALIASES,
        )
        self.assertIn("条目级信息", out)
        self.assertNotIn("目标产品卖点", out)

    def test_empty_pkg_falls_back_to_fmt_package(self) -> None:
        """推荐条全量格式化为空 → 降级 _fmt_package（空包返回空串，事实行被跳过）。"""
        out = self.assert_same_prompt(
            ctx=make_ctx(),
            pkg={},
            template_text=self.TEMPLATE_TEXT,
            linked_vars=["pkg_brief", "diff_str"],
        )
        self.assertNotIn("未知套餐", out)

    def test_empty_current_package_skips_cur_and_diff(self) -> None:
        """生产回归：上游接口未返回当前套餐（current_package 为空）时，
        cur_brief 不得出现「未知套餐」占位，diff_str 不得出现「月费—」等
        无效差异标记；两行事实均按“空事实不展示”跳过。"""
        out = self.assert_same_prompt(
            ctx=make_ctx(current_package={}),
            pkg=_SAMPLE_PKG,
            template_text=self.TEMPLATE_TEXT,
            linked_vars=["cur_brief", "pkg_brief", "diff_str", "usage_line"],
            field_aliases=_FIELD_ALIASES,
        )
        self.assertNotIn("未知套餐", out)
        self.assertNotIn("月费—", out)
        self.assertNotIn("差异暂无数据", out)
        self.assertNotIn("{cur_brief}：", out)   # 当前套餐事实行整行跳过
        self.assertNotIn("{diff_str}：", out)    # 差异事实行整行跳过
        self.assertIn("{pkg_brief}：", out)      # 推荐产品事实仍在

    def test_alias_anchor_follows_template_token(self) -> None:
        """别名错位回归（生产真实场景）：模板占位符用旧别名 {cur_brief}/{usage_line}，
        linked_vars 是后端自动并入的标准域名 current_package/usage
        → 上下文行锚点必须对齐模板实际用名，否则模型按规则 2 跳过该槽。"""
        out = self.assert_same_prompt(
            ctx=make_ctx(),
            pkg=_SAMPLE_PKG,
            template_text="您当前使用{cur_brief}，近期用量{usage_line}，为您推荐{pkg_brief}。",
            linked_vars=["current_package", "usage", "pkg_brief"],
            field_aliases=_FIELD_ALIASES,
        )
        self.assertIn("当前套餐信息 {cur_brief}：", out)   # 锚点对齐模板 token
        self.assertIn("历史用量 {usage_line}：", out)
        self.assertNotIn("{current_package}：", out)       # 不再输出错位锚点
        self.assertNotIn("{usage}：", out)

    def test_alias_group_dedup(self) -> None:
        """同义组去重：linked_vars 同时含 cur_brief 与 current_package → 只注入一行。"""
        out = self.assert_same_prompt(
            ctx=make_ctx(),
            pkg=_SAMPLE_PKG,
            template_text="您当前使用{cur_brief}。",
            linked_vars=["cur_brief", "current_package"],
            field_aliases=_FIELD_ALIASES,
        )
        self.assertEqual(out.count("当前套餐信息 {"), 1)

    def test_auto_inject_known_var_missing_from_linked(self) -> None:
        """漏勾补注入：模板引用了 {diff_str}/{pkg_fee} 但 linked_vars 未勾选
        → 值非空时自动补入上下文（数据真实存在，不属于臆造）。"""
        out = self.assert_same_prompt(
            ctx=make_ctx(),
            pkg=_SAMPLE_PKG,
            template_text="推荐{pkg_brief}，月费{pkg_fee}元，{diff_str}。",
            linked_vars=["pkg_brief"],
            field_aliases=_FIELD_ALIASES,
        )
        self.assertIn("差异 {diff_str}：", out)
        self.assertIn("推荐套餐月费(元) {pkg_fee}：99", out)

    def test_derived_pkg_vars_unit_normalized(self) -> None:
        """派生变量单位归一：initFee 为分单位（18800）→ pkg_fee=188；
        offerFlow 为 MB（30720）→ pkg_flow=30。"""
        pkg = {"offerId": "P2", "offerName": "188元5G畅享套餐",
               "initFee": "18800", "offerFlow": "30720", "offerVoice": "1000"}
        out = self.assert_same_prompt(
            ctx=make_ctx(),
            pkg=pkg,
            template_text="月费{pkg_fee}元，含{pkg_flow}GB流量、{pkg_voice}分钟语音。",
            linked_vars=["pkg_fee", "pkg_flow", "pkg_voice"],
            field_aliases=_FIELD_ALIASES,
        )
        self.assertIn("推荐套餐月费(元) {pkg_fee}：188", out)
        self.assertIn("推荐套餐流量(GB) {pkg_flow}：30", out)
        self.assertIn("推荐套餐语音(分钟) {pkg_voice}：1000", out)

    def test_persona_rule_injection_and_numbering(self) -> None:
        """含用户标签/画像上下文 → 追加「个性化润色」规则且话术要求编号顺延为 6；
        无标签/画像 → 不追加、编号保持 5。零配置透传兜底同场景验证。"""
        pkg = dict(_SAMPLE_PKG)
        # ① 有 tags：追加润色规则
        out = self.assert_same_prompt(
            ctx=make_ctx(),
            pkg=pkg,
            template_text="推荐{pkg_brief}，{usage}。",
            linked_vars=["pkg_brief", "usage", "tags"],
            script_requirement="150字以内",
            field_aliases=_FIELD_ALIASES,
        )
        self.assertIn("5. 个性化润色", out)
        self.assertIn("6. 话术要求：150字以内", out)
        # ② 无标签/画像：不追加
        ctx2 = make_ctx(tags={}, user_profile={}, extra_info={}, extra_context={})
        out2 = self.assert_same_prompt(
            ctx=ctx2,
            pkg=pkg,
            template_text="推荐{pkg_brief}。",
            linked_vars=["pkg_brief"],
            script_requirement="简洁",
            field_aliases=_FIELD_ALIASES,
        )
        self.assertNotIn("个性化润色", out2)
        self.assertIn("5. 话术要求：简洁", out2)
        # ③ 多指标提炼规则文案存在
        self.assertIn("提炼其中最能支撑推荐理由的 1-3 个要点", out)

    def test_subfield_path_placeholders(self) -> None:
        """子字段路径占位符 {域[子键]} / {域[子键1][子键2]} → 从原始域字典按路径精确取值注入，
        锚点用完整 token 与模板同名对齐；取不到的子字段不入 Prompt。"""
        ctx = make_ctx()
        template = (
            "您好！您当前套餐{current_package[offerName]}，"
            "近6月流量约{usage[data_usage][近6月平均流量(GB)]}GB，"
            "语音{usage[voice_usage][近6月平均主叫时长]}分钟，"
            "标签{tags[高频高额超套客户]}，"
            "推荐{pkg_brief[offerName]}，"
            "缺失字段{current_package[不存在字段]}。"
        )
        out = build_prompt(
            user_prompt_tpl="",
            template_text=template,
            ctx=ctx,
            pkg=_SAMPLE_PKG,
            diff=PackageDiff(ctx.current_package, _SAMPLE_PKG),
            linked_vars=[],
            script_requirement="150字以内",
            field_aliases=_FIELD_ALIASES,
        )
        # 各子字段按完整 token 锚点 + 叶子标签注入，取值精确
        self.assertIn("offerName {current_package[offerName]}：畅享39元套餐", out)
        self.assertIn("近6月平均流量(GB) {usage[data_usage][近6月平均流量(GB)]}：25", out)
        self.assertIn("近6月平均主叫时长 {usage[voice_usage][近6月平均主叫时长]}：200", out)
        self.assertIn("高频高额超套客户 {tags[高频高额超套客户]}：是", out)
        self.assertIn("offerName {pkg_brief[offerName]}：畅享99元套餐", out)
        # 取不到值的子字段不注入
        self.assertNotIn("{current_package[不存在字段]}：", out)
        # 单级容错：usage 两级结构可用单括号命中叶子键
        out2 = build_prompt(
            user_prompt_tpl="", template_text="流量{usage[近6月平均流量(GB)]}",
            ctx=ctx, pkg=_SAMPLE_PKG,
            diff=PackageDiff(ctx.current_package, _SAMPLE_PKG),
            linked_vars=[], field_aliases=_FIELD_ALIASES,
        )
        self.assertIn("{usage[近6月平均流量(GB)]}：25", out2)
        # tags 子字段触发个性化润色规则
        self.assertIn("个性化润色", out)

    def test_subfield_bracket_fuzzy_match(self) -> None:
        """回归（北京「用户消费信息未生效」）：映射 field_rename 产出畸形键名
        「近6月平均流量((GB)）」（双括号+全角右括号）时，模板子键写成「((GB))」或
        「(GB)」任一形态都必须命中同一字段——按 精确→全半角归一→去括号 canonical 三档匹配。"""
        ctx = make_ctx(usage={
            "data_usage": {"近6月平均流量((GB)）": 37.23, "近6月平均流量饱和度": "0.95"},
            "voice_usage": {"近3月平均主叫时长": "100.00"},
            "consumption": {"近6月平均月消费": "130.42"},
        })
        tpl = (
            "平均消费{usage[consumption][近6月平均月消费]}元，"
            "流量用了{usage[data_usage][近6月平均流量((GB))]}GB，"
            "也可写{usage[data_usage][近6月平均流量(GB)]}GB，"
            "语音{usage[voice_usage][近3月平均主叫时长]}分钟。"
        )
        out = build_prompt(
            user_prompt_tpl="", template_text=tpl, ctx=ctx, pkg=_SAMPLE_PKG,
            diff=PackageDiff(ctx.current_package, _SAMPLE_PKG),
            linked_vars=[], field_aliases=_FIELD_ALIASES,
        )
        self.assertIn("{usage[consumption][近6月平均月消费]}：130.42", out)
        self.assertIn("{usage[data_usage][近6月平均流量((GB))]}：37.23", out)
        self.assertIn("{usage[data_usage][近6月平均流量(GB)]}：37.23", out)
        self.assertIn("{usage[voice_usage][近3月平均主叫时长]}：100.00", out)

    def test_rename_field_normalization_guard(self) -> None:
        """保存守护：field_transform 重命名目标名中的畸形括号在保存时被规范化。"""
        from routers.management import _normalize_field_transform_renames
        ft = {
            "usage.data_usage": {
                "from": "raw_tags", "type": "filter_include",
                "field_rename": {"近6月平均流量(MB）": "近6月平均流量((GB)）"},
            },
            "_unit_conversions": [
                {"target_path": "usage.data_usage", "field": "近6月平均流量(MB）",
                 "new_field": "近6月平均流量((GB)）", "converter": "mb_to_gb"},
            ],
        }
        fixed = _normalize_field_transform_renames(ft)
        self.assertEqual(len(fixed), 2)
        self.assertEqual(
            ft["usage.data_usage"]["field_rename"]["近6月平均流量(MB）"], "近6月平均流量(GB)")
        self.assertEqual(ft["_unit_conversions"][0]["new_field"], "近6月平均流量(GB)")
        # 已规范的名字不再改动
        self.assertEqual(_normalize_field_transform_renames(ft), [])

    def test_data_step_rename_normalizes_at_runtime(self) -> None:
        """运行时产出防护：即使存量配置的 field_rename 目标名含畸形括号，
        DataStep._apply_unit_convert 产出的数据键也必须是规范形态（(GB) 单括号）。"""
        from steps.data_step import DataStep
        data = {"近6月平均流量(MB）": 38122.17}
        rule = {
            "unit_convert": {"近6月平均流量(MB）": "mb_to_gb"},
            "field_rename": {"近6月平均流量(MB）": "近6月平均流量((GB)）"},   # 畸形目标名
        }
        out = DataStep._apply_unit_convert(data, rule)
        self.assertIn("近6月平均流量(GB)", out)          # 键名已规范化
        self.assertNotIn("近6月平均流量((GB)）", out)
        self.assertAlmostEqual(float(out["近6月平均流量(GB)"]), 37.23, places=1)

    def test_publish_choke_point_normalizes_whole_config(self) -> None:
        """写入 choke point 防护：整份 api_nodes（含多节点）保存前统一规范化重命名目标名。"""
        from utils.field_naming import normalize_api_nodes_renames
        api_nodes = {
            "_meta": {"comment": "跳过下划线节点"},
            "节点A": {
                "field_transform": {
                    "usage.data_usage": {
                        "field_rename": {"近3月平均流量(MB）": "近3月平均流量((GB))"},
                    },
                },
            },
            "节点B": {"field_transform": {}},
        }
        fixed = normalize_api_nodes_renames(api_nodes)
        self.assertEqual(len(fixed), 1)
        self.assertIn("节点A", fixed[0])
        self.assertEqual(
            api_nodes["节点A"]["field_transform"]["usage.data_usage"]["field_rename"]["近3月平均流量(MB）"],
            "近3月平均流量(GB)",
        )
        # 幂等：再跑一遍无改动
        self.assertEqual(normalize_api_nodes_renames(api_nodes), [])

    def test_guard_preserves_referenced_intermediate_slots(self) -> None:
        """保存守护（北京事故第二形态）：field_transform 引用的中间槽位（raw_tags）
        在新 response_extract 中缺失 → 自动保留；显式置空 = 有意删除，予以尊重。"""
        from routers.management import _guard_response_extract
        old_ext = {
            "current_package": "bean.mainoffer",
            "recommended_packages": "bean.recommend_results",
            "raw_tags": "bean.tags",
        }
        ft = {
            "usage.data_usage": {"from": "raw_tags", "type": "filter_include"},
            "tags": {"from": "raw_tags", "type": "filter_exclude"},
            "_unit_conversions": [],
        }
        # ① 智能分析回填漏掉 raw_tags 与 recommended_packages → 双双被保留
        body_ext = {"current_package": "bean.mainoffer"}
        new_ext, preserved = _guard_response_extract(old_ext, body_ext, ft)
        self.assertEqual(new_ext["raw_tags"], "bean.tags")
        self.assertEqual(new_ext["recommended_packages"], "bean.recommend_results")
        self.assertIn("raw_tags", preserved)
        self.assertIn("recommended_packages", preserved)
        # ② 显式置空 = 有意删除
        body_ext2 = {"current_package": "bean.mainoffer", "raw_tags": ""}
        new_ext2, preserved2 = _guard_response_extract(old_ext, body_ext2, ft)
        self.assertNotIn("raw_tags", new_ext2)
        self.assertNotIn("raw_tags", preserved2)
        # ③ 无 field_transform 引用的普通槽位不强留
        new_ext3, _ = _guard_response_extract(
            {"raw_other": "bean.other"}, {"current_package": "bean.mainoffer"}, {})
        self.assertNotIn("raw_other", new_ext3)

    @staticmethod
    def _beijing_node_cfg() -> Dict[str, Any]:
        """北京「套餐推荐」接口节点配置深拷贝（含 mock_response，供映射链路用例复用）。"""
        import json as _json
        cfg = _json.load(open(
            "skills-runtime/beijing/套餐推荐/config/api_nodes.json", encoding="utf-8"
        ))["北京测试接口_api"]
        return _json.loads(_json.dumps(cfg))

    @staticmethod
    def _beijing_legacy_node_cfg() -> Dict[str, Any]:
        """同上，但还原成**存量中间集写法**（from: raw_tags + response_extract 槽位）。

        出厂配置已统一为直连映射，而「丢槽位自愈 / 中间集转直连」这类用例要测的正是
        历史遗留形态，夹具自己造，避免用例依赖出厂配置的写法风格。
        """
        cfg = TestNewFormatLinkedVars._beijing_node_cfg()
        cfg["response_extract"]["raw_tags"] = "bean.tags"
        for rule in cfg["field_transform"].values():
            if isinstance(rule, dict) and rule.get("from") == "bean.tags":
                rule["from"] = "raw_tags"
        return cfg

    def test_missing_intermediate_slot_self_heals_at_runtime(self) -> None:
        """运行态自愈（北京事故第二形态存量坏配置）：response_extract 丢了 raw_tags，
        DataStep 按名从 bean.tags 探测回数据源，usage/tags 映射域照常产出。"""
        from steps.data_step import DataStep
        cfg = self._beijing_legacy_node_cfg()
        del cfg["response_extract"]["raw_tags"]   # 模拟生产 ES 丢失中间槽位
        ds = DataStep("beijing")
        extracted = ds._extract_fields(cfg["mock_response"], cfg)
        diag: List[Dict[str, Any]] = []
        resources = ds._transform_fields(extracted, cfg, cfg["mock_response"], diag)
        self.assertEqual(
            resources["usage"]["consumption"]["近6月平均月消费"], "130.42")
        self.assertEqual(
            resources["usage"]["data_usage"]["近6月平均流量(GB)"], 37.23)
        self.assertTrue(resources.get("tags"))
        self.assertTrue(all(d["status"] == "ok" for d in diag), diag)
        self.assertTrue(all("自愈探测 bean.tags" == d["source"] for d in diag), diag)

    def test_filter_keys_tolerate_bracket_variants(self) -> None:
        """键名容错：上游把「近6月平均流量(MB）」返回成全角括号形态时，
        filter_include / unit_convert / field_rename 仍整条命中（此前静默落空）。"""
        from steps.data_step import DataStep
        cfg = self._beijing_node_cfg()
        raw = cfg["mock_response"]
        raw["bean"]["tags"] = {
            k.replace("(", "（").replace(")", "）"): v
            for k, v in raw["bean"]["tags"].items()
        }
        ds = DataStep("beijing")
        resources = ds._transform_fields(ds._extract_fields(raw, cfg), cfg, raw)
        self.assertEqual(
            resources["usage"]["data_usage"]["近6月平均流量(GB)"], 37.23)
        self.assertEqual(
            resources["usage"]["consumption"]["近6月平均月消费"], "130.42")

    def test_upstream_renamed_keys_reported_as_no_key_matched(self) -> None:
        """上游把 tags 字段整体改名（配置键名对不上）：映射域为空但不再静默——
        诊断标记 no_key_matched 并同时给出配置键名与接口实际键名。"""
        from steps.data_step import DataStep
        cfg = self._beijing_node_cfg()
        raw = cfg["mock_response"]
        raw["bean"]["tags"] = {"月均消费金额": 29.02, "月均上网流量": 0.4}
        ds = DataStep("beijing")
        diag: List[Dict[str, Any]] = []
        resources = ds._transform_fields(ds._extract_fields(raw, cfg), cfg, raw, diag)
        self.assertFalse(resources.get("usage"))
        by_target = {d["target"]: d for d in diag}
        self.assertEqual(by_target["usage.consumption"]["status"], "no_key_matched")
        self.assertIn("近6月平均月消费", by_target["usage.consumption"]["config_keys"])
        self.assertIn("月均消费金额", by_target["usage.consumption"]["source_keys"])
        # filter_exclude 的 tags 域仍拿到全部字段（数值型标签由 _fmt_tags 带值进上下文）
        self.assertEqual(by_target["tags"]["status"], "ok")
        self.assertEqual(resources["tags"], {"月均消费金额": 29.02, "月均上网流量": 0.4})

    def test_beijing_config_covers_both_upstream_key_namings(self) -> None:
        """北京节点同时兼容上游两套 tags 键名（旧「近6月平均流量(MB）」/
        新「实际近6月平均流量（GB）」），产出键名都归到话术模板占位符用的名字。"""
        from steps.data_step import DataStep
        cfg = self._beijing_node_cfg()
        raw = cfg["mock_response"]
        raw["bean"]["tags"] = {
            "实际近6月平均消费（元）": 29.02,
            "实际近6月平均流量（GB）": 0.4,
            "实际近6月平均语音（分钟）": 12.83,
            "融合状态": "合约融合",
        }
        ds = DataStep("beijing")
        res = ds._transform_fields(ds._extract_fields(raw, cfg), cfg, raw)
        self.assertEqual(res["usage"]["consumption"]["近6月平均月消费"], 29.02)
        self.assertEqual(res["usage"]["data_usage"]["近6月平均流量(GB)"], 0.4)
        self.assertEqual(res["usage"]["voice_usage"]["近6月平均主叫时长"], 12.83)
        self.assertEqual(res["tags"], {"融合状态": "合约融合"})   # 用量字段不再混进标签

    def test_prompt_flags_missing_slots_without_cross_fill(self) -> None:
        """映射域确实无数据时（接口响应里根本没有 tags）：模板引用的
        {usage}/{tags}/子字段槽位不入上下文、不被套餐数据顶替，并生成缺失事实负向约束。"""
        from steps.data_step import DataStep
        cfg = self._beijing_node_cfg()
        raw = cfg["mock_response"]
        raw["bean"].pop("tags")        # 上游未返回标签块，自愈探测也无从取值
        ds = DataStep("beijing")
        resources = ds._transform_fields(ds._extract_fields(raw, cfg), cfg, raw)
        self.assertFalse(resources.get("usage"))
        self.assertFalse(resources.get("tags"))
        self.assertTrue(resources.get("current_package"))
        ctx = make_ctx(
            current_package=resources["current_package"], usage={}, tags={},
            user_info={}, user_profile={}, domain_ext={},
            extra_info={}, extra_context={},
        )
        pkg = resources["recommended_packages"][0]
        tpl = ("您当前{current_package}，平均消费{usage[consumption][近6月平均月消费]}元，"
               "月均流量{usage}，标签{tags}。推荐{pkg_brief}。")
        parts: Dict[str, Any] = {}
        out = build_prompt(
            user_prompt_tpl="", template_text=tpl, ctx=ctx, pkg=pkg,
            diff=PackageDiff(ctx.current_package, pkg),
            linked_vars=["current_package", "usage", "tags", "pkg_brief"],
            parts_out=parts,
        )
        self.assertNotIn("{usage}：", out)          # 空域不入上下文
        self.assertNotIn("{tags}：", out)
        self.assertNotIn("近6月平均月消费]}：", out)  # 子字段槽位取不到值不注入
        self.assertIn("{current_package}：", out)
        # 缺口显式写进 Prompt，并禁止用套餐包含量冒充历史用量
        self.assertIn("【缺失事实】", out)
        self.assertIn("不得用当前套餐或推荐套餐的包含量", out)
        self.assertIn("usage", parts["missing_slots"])
        self.assertIn("usage[consumption][近6月平均月消费]", parts["missing_slots"])

    def test_guard_whole_package_save_preserves_slots_and_meta(self) -> None:
        """整份 api_nodes 保存（技能管理页 / 填槽设置保存走的通道）同样受守护：
        表单未回显的中间槽位与顶层 `_` 元数据不会被一次保存冲掉。"""
        from routers.management import _guard_api_nodes_package
        old = {
            "_domain_fallbacks": {"current_package": "extra_data.currentMainOffer"},
            "节点A": {
                "response_extract": {
                    "current_package": "bean.mainoffer",
                    "recommended_packages": "bean.recommend_results",
                    "raw_tags": "bean.tags",
                },
                "field_transform": {"tags": {"from": "raw_tags", "type": "filter_exclude"}},
            },
        }
        new = {"节点A": {
            "response_extract": {"current_package": "bean.mainoffer"},
            "field_transform": {"tags": {"from": "raw_tags", "type": "filter_exclude"}},
        }}
        merged, notes = _guard_api_nodes_package(old, new)
        ext = merged["节点A"]["response_extract"]
        self.assertEqual(ext["raw_tags"], "bean.tags")
        self.assertEqual(ext["recommended_packages"], "bean.recommend_results")
        self.assertIn("_domain_fallbacks", merged)
        self.assertTrue(notes)
        # 显式置空 = 有意删除，守护不复活
        new2 = {"节点A": {
            "response_extract": {"current_package": "bean.mainoffer", "raw_tags": ""},
            "field_transform": {"tags": {"from": "raw_tags", "type": "filter_exclude"}},
        }}
        merged2, _ = _guard_api_nodes_package(old, new2)
        self.assertNotIn("raw_tags", merged2["节点A"]["response_extract"])

    def test_direct_response_path_from_needs_no_intermediate_slot(self) -> None:
        """免中间集直连写法：from 直接写响应路径（bean.tags），运行时产出与走
        raw_tags 中间集完全一致，且不应被 lint 判成悬空引用、不应进修复的 unfixed。"""
        from management.config_agent.linter import lint_api_nodes
        from management.config_agent.repairer import repair_api_nodes
        from steps.data_step import DataStep

        node = self._beijing_node_cfg()       # 出厂配置本身已是直连写法
        cfg = {"北京测试接口_api": node}
        self.assertNotIn("raw_tags", node["response_extract"])
        self.assertEqual(node["field_transform"]["tags"]["from"], "bean.tags")

        self.assertEqual(lint_api_nodes(cfg, "beijing", "套餐推荐")["errors"], [])
        rep = repair_api_nodes(cfg, "beijing", "套餐推荐")
        self.assertEqual(rep["fixes"], [])
        self.assertEqual(rep["unfixed"], [])

        raw = node["mock_response"]
        extracted = {k: DataStep._get_path(raw, v) for k, v in node["response_extract"].items()}
        out = DataStep.__new__(DataStep)._transform_fields(extracted, node, raw)
        self.assertTrue(out.get("tags"))
        self.assertEqual(
            sorted((out.get("usage") or {}).keys()),
            ["consumption", "data_usage", "voice_usage"],
        )

    def test_inline_intermediate_slots_is_behavior_preserving(self) -> None:
        """存量中间集写法在保存时自动转直连：产出必须逐字节等价，标准域槽位不许动，
        重复执行幂等。"""
        from management.config_agent.repairer import inline_intermediate_slots
        from steps.data_step import DataStep

        def _run(node):
            raw = node["mock_response"]
            extracted = {k: DataStep._get_path(raw, v)
                         for k, v in (node.get("response_extract") or {}).items()}
            return DataStep.__new__(DataStep)._transform_fields(extracted, node, raw)

        before = _run(self._beijing_legacy_node_cfg())
        cfg = {"北京测试接口_api": self._beijing_legacy_node_cfg()}
        notes = inline_intermediate_slots(cfg)
        node = cfg["北京测试接口_api"]

        self.assertTrue(any("raw_tags" in n for n in notes))
        self.assertNotIn("raw_tags", node["response_extract"])
        self.assertEqual(node["field_transform"]["tags"]["from"], "bean.tags")
        self.assertEqual(_run(node), before)
        # 标准域槽位是第①步自动透传的依据，不得被当成中间集删掉
        self.assertIn("current_package", node["response_extract"])
        self.assertEqual(inline_intermediate_slots(cfg), [])   # 幂等

    def test_inline_skips_slot_referenced_without_explicit_from(self) -> None:
        """省略 from 的规则运行时不走路径回退，改写会变语义 —— 该槽位必须原样保留。"""
        from management.config_agent.repairer import inline_intermediate_slots
        cfg = {"n": {
            "source_type": "api",
            "response_extract": {"combo": "bean.combo"},
            "field_transform": {
                "combo": {"type": "passthrough"},                     # 隐式 from=combo
                "usage.data_usage": {"from": "combo", "type": "filter_include"},
            },
            "mock_response": {"bean": {"combo": {"近6月平均流量(GB)": 12}}},
        }}
        self.assertEqual(inline_intermediate_slots(cfg), [])
        self.assertIn("combo", cfg["n"]["response_extract"])

    def test_inline_skips_node_without_sample_response(self) -> None:
        """没存样例出参就无法自证路径有效，转完反而会被 E201 判成悬空 —— 保持不转。"""
        from management.config_agent.repairer import inline_intermediate_slots
        cfg = {"n": {
            "source_type": "api",
            "response_extract": {"raw_tags": "bean.tags"},
            "field_transform": {"tags": {"from": "raw_tags", "type": "filter_exclude"}},
        }}
        self.assertEqual(inline_intermediate_slots(cfg), [])
        self.assertIn("raw_tags", cfg["n"]["response_extract"])

    def test_dangling_from_still_errors_without_sample(self) -> None:
        """严格边界：没存样例出参就无从区分"直连路径"与"写错的槽位名"，
        必须继续报 E201，否则北京那类丢槽位事故会被放过。"""
        from management.config_agent.linter import lint_api_nodes
        node = self._beijing_node_cfg()
        cfg = {"北京测试接口_api": node}
        node.pop("mock_response")
        self.assertTrue(lint_api_nodes(cfg, "beijing", "套餐推荐")["errors"])

    def test_save_autofills_slots_missing_from_both_sides(self) -> None:
        """保存即补齐：旧配置本就缺了 raw_tags（守护无从保留）时，重新编辑保存一次
        也应按 field_transform 引用 + mock_response 自证把槽位补回来，
        不必再另去点「修复」。"""
        from routers.management import _autofill_api_nodes
        nodes = {"节点A": {
            "source_type": "api",
            "response_extract": {"current_package": "bean.mainoffer"},
            "field_transform": {"tags": {"from": "raw_tags", "type": "filter_exclude"}},
            "mock_response": {"bean": {"mainoffer": {}, "tags": {"网龄": "10年"}}},
        }}
        filled, notes, _unfixed = _autofill_api_nodes(nodes, "beijing", "套餐推荐")
        node = filled["节点A"]
        # 数据源恢复即达成目的：补回的中间槽位会紧接着被转成直连写法，
        # 终态是 from 直指响应路径、不再有 raw_tags 槽位（两者运行时等价）。
        self.assertEqual(node["field_transform"]["tags"]["from"], "bean.tags")
        self.assertNotIn("raw_tags", node["response_extract"])
        self.assertTrue(any("raw_tags" in n for n in notes))
        # 不动已有的标准域映射
        self.assertEqual(node["response_extract"]["current_package"], "bean.mainoffer")

    def test_save_autofill_respects_explicit_delete(self) -> None:
        """显式置空 = 有意删除：补齐不得把运营刚删掉的槽位又探测回来。"""
        from routers.management import _autofill_api_nodes, _explicit_removed_slots
        body_ext = {"current_package": "bean.mainoffer", "raw_tags": ""}
        nodes = {"节点A": {
            "source_type": "api",
            "response_extract": {"current_package": "bean.mainoffer"},
            "field_transform": {"tags": {"from": "raw_tags", "type": "filter_exclude"}},
            "mock_response": {"bean": {"mainoffer": {}, "tags": {"网龄": "10年"}}},
        }}
        filled, notes, _unfixed = _autofill_api_nodes(
            nodes, "beijing", "套餐推荐", {"节点A": _explicit_removed_slots(body_ext)})
        self.assertNotIn("raw_tags", filled["节点A"]["response_extract"])
        self.assertFalse([n for n in notes if "raw_tags" in n])

    def test_template_save_fills_subfield_placeholder_vars(self) -> None:
        """保存话术模板时按正文占位符补齐 linked_vars，子字段占位符取根名
        （历史推断的精确层只认 {xxx}，会漏掉 {usage[consumption][...]}）。"""
        from routers.management import _fill_placeholder_vars
        content = "您当前套餐 {current_package[curOfferDesc]}，月均消费 {usage[consumption][近6月平均月消费]} 元"
        merged, added = _fill_placeholder_vars(content, ["pkg_brief"])
        self.assertEqual(merged[:1], ["pkg_brief"])       # 已有的保持原序在前
        self.assertIn("current_package", merged)
        self.assertIn("usage", merged)
        self.assertEqual(sorted(added), ["current_package", "usage"])
        # 纯固定文案不凭空补
        self.assertEqual(_fill_placeholder_vars("您好，简单给您介绍一下。", []), ([], []))

    def test_fmt_tags_keeps_numeric_values(self) -> None:
        """数值型标签必须带值进上下文（北京把月均消费/流量放在 tags 里），
        标记型标签仍只报标签名，假值整条丢弃。"""
        from steps.script_step import ScriptStep
        out = ScriptStep._fmt_tags({
            "高频高额超套客户": "1",
            "低频低额超套客户": "0",
            "融合状态": "合约融合",
            "实际近6月平均消费（元）": 29.02,
        })
        self.assertIn("高频高额超套客户", out)
        self.assertNotIn("低频低额超套客户", out)
        self.assertIn("融合状态:合约融合", out)
        self.assertIn("实际近6月平均消费（元）:29.02", out)

    def test_publish_lint_reports_dangling_from_slot(self) -> None:
        """保存时巡检：from 槽位不存在（E201）在 lint_api_nodes 中可检出
        （publish_config 保存时调用同一函数并写入 result.warnings）。"""
        from management.config_agent.linter import lint_api_nodes
        api_nodes = {
            "节点A": {
                "response_extract": {"current_package": "bean.mainoffer"},   # 缺 raw_tags
                "field_transform": {
                    "usage.data_usage": {"from": "raw_tags", "type": "filter_include"},
                },
            },
        }
        report = lint_api_nodes(api_nodes, "beijing", "套餐推荐")
        msgs = [i["message"] for i in report["errors"]]
        self.assertTrue(any("raw_tags" in m for m in msgs))

    def test_zero_config_passthrough(self) -> None:
        """接口查询模式无任何映射规则 → 按同名标准域零配置透传（含 bean 层与推荐列表别名）。"""
        from steps.data_step import DataStep
        raw = {
            "bean": {
                "current_package": {"offerName": "A套餐"},
                "recommend_results": [{"offerId": "1"}],
            },
            "tags": {"x": "1"},
        }
        out = DataStep._zero_config_passthrough(raw)
        self.assertEqual(out["current_package"], {"offerName": "A套餐"})
        self.assertEqual(out["recommended_packages"], [{"offerId": "1"}])
        self.assertEqual(out["tags"], {"x": "1"})
        self.assertEqual(DataStep._zero_config_passthrough("not a dict"), {})
        self.assertEqual(DataStep._zero_config_passthrough({"a": 1}), {})

    def test_fee_uses_executed_fee_not_name_price(self) -> None:
        """回归：月费差须以客户【实付执行月费】(费用字段)为准，而非套餐名里的宣传标价。

        北京场景：当前「128元5G畅享套餐」(initFee=128)，推荐「云宽带139元档合约方案」但其
        实际执行月费 initFee=129（"139元档"仅为对外标价）。正确涨幅应为 129-128=+1 元，
        而非按名字标价算的 139-128=+11 元（那会把"标价-现价"错当涨幅、虚高月费差）。
        """
        cur = {"offerName": "128元5G畅享套餐", "initFee": 128,
               "offerFlow": "30", "offerVoice": "200"}
        rec = {"offerId": "P139", "offerName": "云宽带139元档合约方案",
               "initFee": 129, "offerFlow": "55", "offerVoice": "180", "rank": 1}
        diff = PackageDiff(cur, rec)
        self.assertEqual(diff.cur_fee, 128.0)
        self.assertEqual(diff.tgt_fee, 129.0)   # 取执行价 initFee=129，非名字里的"139元档"
        self.assertEqual(diff.fee_diff_yuan, 1.0)
        self.assertIn("月费+1", diff.summary_str())
        self.assertNotIn("月费+11", diff.summary_str())

    def test_fee_from_name_only_when_no_fee_field(self) -> None:
        """无任何显式费用字段时，才从套餐名解析档位价兜底。"""
        cur = {"offerName": "神州行"}            # 无费用字段、名字无价 → None
        rec = {"offerName": "99元套餐"}           # 无费用字段 → 从名字取 99
        diff = PackageDiff(cur, rec)
        self.assertIsNone(diff.cur_fee)
        self.assertEqual(diff.tgt_fee, 99.0)

    def test_partial_diff_only_computable_dims(self) -> None:
        """当前套餐仅有月费（无流量/语音）→ diff_str 只含月费差异，
        不得出现「流量—」「语音—」占位。"""
        out = self.assert_same_prompt(
            ctx=make_ctx(current_package={"offerName": "神州行", "initFee": 19}),
            pkg=_SAMPLE_PKG,
            template_text=self.TEMPLATE_TEXT,
            linked_vars=["cur_brief", "pkg_brief", "diff_str"],
            field_aliases=_FIELD_ALIASES,
        )
        self.assertIn("月费+80", out)
        self.assertNotIn("流量—", out)
        self.assertNotIn("语音—", out)

    def test_passthrough_extra_info_fields(self) -> None:
        """直传透传：extra_info 顶层字段作为 context。
        ① linked_vars 显式关联的透传字段从 extra_info 解析；
        ② 模板引用但未显式关联的透传字段自动注入（仅注入 extra_info 中真实存在的字段）。
        """
        out = self.assert_same_prompt(
            ctx=make_ctx(
                extra_info={
                    "recommend_actual_price": 39,
                    "recommend_preferential_period": 12,
                    "activity_name": "老友焕新",
                },
                extra_context={},
            ),
            pkg=_SAMPLE_PKG,
            template_text=(
                "活动{activity_name}：每月{recommend_actual_price}元，"
                "连续{recommend_preferential_period}个月！"
            ),
            linked_vars=["recommend_actual_price"],
            field_aliases=_FIELD_ALIASES,
        )
        # linked_vars 显式关联的透传字段（带 {占位符} 锚点）
        self.assertIn("recommend_actual_price {recommend_actual_price}：39", out)
        # 模板引用但未显式关联的透传字段自动注入
        self.assertIn("activity_name {activity_name}：老友焕新", out)
        self.assertIn(
            "recommend_preferential_period {recommend_preferential_period}：12", out
        )

    def test_passthrough_context_channel(self) -> None:
        """直传透传通道（DataStep 写入 ctx.passthrough_context）：
        无论模板占位符风格（**（xx）而非 {xx}）、linked_vars 是否为空，
        passthrough_context 字段都逐条注入【上下文数据】，且不再重复整包 extra_info。"""
        ctx = make_ctx(
            extra_info={"recommend_actual_price": 39, "recommend_voice_minutes": 100},
            extra_context={},
        )
        ctx.passthrough_context = {
            "recommend_actual_price": 39,
            "recommend_preferential_period": 12,
            "recommend_base_flow": 20,
        }
        out = self.assert_same_prompt(
            ctx=ctx,
            pkg=_SAMPLE_PKG,
            template_text=(
                "连续**（recommend_preferential_period）个月每月只需要"
                "**（recommend_actual_price）元，套内**GB（recommend_base_flow）流量！"
            ),
            linked_vars=[],
            field_aliases=_FIELD_ALIASES,
        )
        self.assertIn("recommend_actual_price {recommend_actual_price}：39", out)
        self.assertIn("recommend_preferential_period {recommend_preferential_period}：12", out)
        self.assertIn("recommend_base_flow {recommend_base_flow}：20", out)
        # 透传模式下不再整包 dump extra_info
        self.assertNotIn("主服务补充信息(extra_info) {extra_info}：", out)

    def test_passthrough_subfield_resolves_from_dict_field(self) -> None:
        """透传字段为字典时，{字段[子键]} 能像映射模式一样精确取值（issue 2）。
        取数根登记自 passthrough_context / extra_info 的顶层字典字段，纯增量，
        不影响未使用子字段占位符的既有配置生成。"""
        ctx = make_ctx(
            extra_info={"portrait_style": {"communication_style": "理性简洁", "tone": "专业"}},
            extra_context={},
        )
        ctx.passthrough_context = {
            "portrait_style": {"communication_style": "理性简洁", "tone": "专业"},
        }
        tpl = "请用{portrait_style[communication_style]}的风格，语气{portrait_style[tone]}介绍。"
        out = build_prompt(
            user_prompt_tpl="", template_text=tpl, ctx=ctx, pkg=_SAMPLE_PKG,
            diff=PackageDiff(ctx.current_package, _SAMPLE_PKG),
            linked_vars=[],
        )
        # 子字段精确注入，锚点为完整 token
        self.assertIn("{portrait_style[communication_style]}：理性简洁", out)
        self.assertIn("{portrait_style[tone]}：专业", out)

    def test_passthrough_subfield_absent_not_injected(self) -> None:
        """无对应子字段数据时不注入、不臆造（安全）。"""
        ctx = make_ctx(extra_info={"portrait_style": {"tone": "专业"}}, extra_context={})
        ctx.passthrough_context = {"portrait_style": {"tone": "专业"}}
        out = build_prompt(
            user_prompt_tpl="", template_text="风格{portrait_style[communication_style]}。",
            ctx=ctx, pkg=_SAMPLE_PKG, diff=PackageDiff(ctx.current_package, _SAMPLE_PKG),
            linked_vars=[],
        )
        self.assertNotIn("communication_style]}：", out)


class TestOldFormatUserPromptTpl(EquivalenceBase):
    """fixture 组 2：旧格式 user_prompt_tpl format_map 路径。"""

    def test_custom_tpl_with_placeholders(self) -> None:
        tpl = (
            "当前:{cur_brief}\n推荐:{pkg_brief}\n差异:{diff_str}\n"
            "用量:{usage_line}\n标签:{user_tags}\n画像:{user_profile}\n"
            "扩展:{domain_ext}\n意图:{intent}\n限{max_length}字\n话术："
        )
        out = self.assert_same_prompt(
            ctx=make_ctx(),
            pkg=_SAMPLE_PKG,
            user_prompt_tpl=tpl,
            field_aliases=_FIELD_ALIASES,
            max_length=120,
        )
        # 模板未含 {extra_info}/{extra_context} 且字段非空 → 追加两段
        self.assertIn("主服务补充信息(extra_info)：", out)
        self.assertIn("模板匹配上下文(extra_context)：", out)
        self.assertIn("限120字", out)

    def test_tpl_with_extra_placeholders_no_suffix(self) -> None:
        """模板已含 {extra_info}/{extra_context} 占位符 → 不再追加后缀。"""
        tpl = "推荐:{pkg_brief}\n补充:{extra_info}\n上下文:{extra_context}\n话术："
        out = self.assert_same_prompt(
            ctx=make_ctx(),
            pkg=_SAMPLE_PKG,
            user_prompt_tpl=tpl,
        )
        self.assertEqual(out.count("主服务补充信息(extra_info)："), 0)

    def test_empty_tpl_uses_builtin_default(self) -> None:
        """user_prompt_tpl 为空 → 使用内置缺省模板。"""
        out = self.assert_same_prompt(
            ctx=make_ctx(extra_info={}, extra_context={}),
            pkg=_SAMPLE_PKG,
            user_prompt_tpl="",
            max_length=100,
        )
        self.assertIn("用户当前套餐：", out)
        self.assertIn("请用中文写一句100字以内的营销推荐话术，结尾带办理引导。", out)


class TestFormatExceptionFallback(EquivalenceBase):
    """fixture 组 3：format 异常兜底路径。"""

    def test_keyerror_fallback(self) -> None:
        """未知占位符 → KeyError → 走 fb 兜底文本（并追加 extra 后缀）。"""
        out = self.assert_same_prompt(
            ctx=make_ctx(),
            pkg=_SAMPLE_PKG,
            user_prompt_tpl="你好{no_such_var}，推荐{pkg_brief}",
            field_aliases=_FIELD_ALIASES,
            max_length=90,
        )
        self.assertIn("请用中文写一句90字以内的营销推荐话术。", out)
        self.assertIn("主服务补充信息(extra_info)：", out)

    def test_valueerror_fallback(self) -> None:
        """畸形占位符（单花括号未闭合）→ ValueError → 走 fb 兜底文本。"""
        out = self.assert_same_prompt(
            ctx=make_ctx(extra_info={}, extra_context={}),
            pkg=_SAMPLE_PKG,
            user_prompt_tpl="推荐{pkg_brief，畸形占位符{",
        )
        self.assertIn("请用中文写一句100字以内的营销推荐话术。", out)


@unittest.skipUnless(_BASELINE_AVAILABLE, f"script_step 导入失败: {_BASELINE_IMPORT_ERROR}")
class TestMultiProductContext(unittest.TestCase):
    """多产品：上下文必须带上各自的推荐产品事实，否则 N 条话术内容雷同。

    广东现网问题：linked_vars 只勾了 current_package / business_conte，产品信息一条都没进
    Prompt，18 个产品拿到完全相同的上下文 → 生成 18 条一样的话术。
    """

    # 广东式产品条：字段名与模板占位符 **（recommend_actual_price）同名
    _GD_PKGS = [
        {
            "offerId": "202607011329144580103550" + str(i),
            "offerName": name,
            "recommend_package_name": name,
            "recommend_actual_price": f"{99 + i * 10}元",
            "recommend_preferential_period": f"{6 + i}个月",
            "rank": i + 1,
            "_batch_product_id_hint": "流量",
        }
        for i, name in enumerate(("升99元套餐_东莞", "升109元套餐_东莞", "升119元套餐_东莞"))
    ]
    _GD_TPL = ("顺便跟您讲一下，现在连续**（recommend_preferential_period）"
               "每月只需**（recommend_actual_price），现在给您办理好吗？")

    def _build(self, pkg: Dict[str, Any], recs: List[Dict[str, Any]],
               linked_vars: Optional[List[str]] = None,
               template_text: Optional[str] = None) -> str:
        ctx = make_ctx(final_recommendations=recs)
        return build_prompt(
            user_prompt_tpl="", template_text=(self._GD_TPL if template_text is None else template_text),
            ctx=ctx, pkg=pkg, diff=PackageDiff(ctx.current_package, pkg),
            linked_vars=list(linked_vars or ["current_package"]),
        )

    def test_each_product_gets_its_own_facts(self) -> None:
        """多产品且未勾选任何产品变量：逐条注入产品字段，上下文互不相同。"""
        outs = [self._build(p, self._GD_PKGS) for p in self._GD_PKGS]
        self.assertEqual(len(set(outs)), 3)
        for pkgd, out in zip(self._GD_PKGS, outs):
            # 字段名与模板占位符同名，模型才能对齐填槽
            self.assertIn(f"{{recommend_actual_price}}：{pkgd['recommend_actual_price']}", out)
            self.assertIn(f"{{recommend_preferential_period}}：{pkgd['recommend_preferential_period']}", out)
            self.assertIn(pkgd["offerName"], out)

    def test_internal_and_id_fields_not_leaked(self) -> None:
        """内部标记与 ID/排序字段不进 Prompt（避免模型把串号念进话术）。"""
        out = self._build(self._GD_PKGS[0], self._GD_PKGS)
        self.assertNotIn("_batch_product_id_hint", out)
        self.assertNotIn(self._GD_PKGS[0]["offerId"], out)
        self.assertNotIn("{rank}", out)

    def test_single_product_unchanged(self) -> None:
        """单产品不触发兜底：沿用 linked_vars 驱动语义，产品字段不注入。"""
        out = self._build(self._GD_PKGS[0], self._GD_PKGS[:1])
        self.assertNotIn("{recommend_actual_price}", out)
        self.assertIn("{current_package}", out)

    def test_no_injection_when_product_var_already_linked(self) -> None:
        """已勾选产品变量（pkg_brief）时不重复兜底注入，避免同一数值出现两次。"""
        out = self._build(self._GD_PKGS[0], self._GD_PKGS,
                          linked_vars=["current_package", "pkg_brief"],
                          template_text="推荐{pkg_brief}。")
        self.assertIn("{pkg_brief}：", out)
        self.assertNotIn("{recommend_actual_price}：", out)

    def test_product_field_never_fills_standard_domain(self) -> None:
        """产品条自带与标准域同名的字段（北京推荐条自带 tags）时严禁顶替标准域。"""
        pkgs = [dict(p, tags=[], usage="产品侧无关值") for p in self._GD_PKGS]
        ctx = make_ctx(final_recommendations=pkgs, tags={}, usage={})
        out = build_prompt(
            user_prompt_tpl="", template_text="您的标签{tags}，用量{usage}。",
            ctx=ctx, pkg=pkgs[0], diff=PackageDiff(ctx.current_package, pkgs[0]),
            linked_vars=["tags", "usage"],
        )
        self.assertNotIn("{tags}：", out)
        self.assertNotIn("{usage}：", out)

    def test_template_referenced_product_field_resolves(self) -> None:
        """模板直接写 {产品字段} 时可取到当前产品的值（单产品也生效）。"""
        out = self._build(self._GD_PKGS[0], self._GD_PKGS[:1],
                          template_text="每月只需{recommend_actual_price}。")
        self.assertIn("{recommend_actual_price}：99元", out)

    def test_product_brief_excludes_internal_fields(self) -> None:
        """{pkg_brief} 摘要不含内部标记（批量路径会给产品挂 _batch_product_id_hint）。"""
        from steps.script_step import ScriptStep

        brief = ScriptStep._fmt_recommended_product_full(self._GD_PKGS[0], {})
        self.assertNotIn("_batch_product_id_hint", brief)
        self.assertIn("offerName", brief)


class TestVarLabelsAndHelpers(unittest.TestCase):
    """VAR_LABELS 单一真源与辅助函数的等价。"""

    def test_var_labels_equal_original(self) -> None:
        """schemas 构建的 VAR_LABELS 与原硬编码字典键值完全一致。"""
        self.assertEqual(VAR_LABELS, _OraclePromptStep._VAR_LABELS)

    def test_resource_context_prompt_vars_equal(self) -> None:
        ctx = make_ctx()
        oracle = _OraclePromptStep()
        self.assertEqual(
            resource_context_prompt_vars(ctx, _FIELD_ALIASES),
            oracle._resource_context_prompt_vars(ctx, _FIELD_ALIASES),
        )

    def test_append_prompt_extra_suffix_equal(self) -> None:
        cases = [
            ("", "正文", "ei", "ec"),
            ("{extra_info}", "正文", "ei", "ec"),
            ("{extra_info}{extra_context}", "正文", "ei", "ec"),
            ("模板", "正文", "", ""),
            (None, "正文", "ei", ""),
        ]
        for tpl_raw, body, ei, ec in cases:
            self.assertEqual(
                append_prompt_extra_suffix(tpl_raw, body, ei, ec),
                _OraclePromptStep._append_prompt_extra_suffix(tpl_raw, body, ei, ec),
                msg=f"case={tpl_raw!r}",
            )

    def test_flatten_subfields_empty_skeleton(self) -> None:
        """透传/extra_info 骨架样例（叶子全空）：include_empty=True 时按键结构产出子字段，
        include_empty=False（标准域映射）时仍跳过空值。对应北京「等等」全空 mock_response。"""
        from routers.management import _flatten_domain_subfields

        skeleton = {
            "current_package": {"package_name": "", "actual_price": ""},
            "diff": {"voice_minutes_diff": ""},
        }
        # 默认（映射标准域）：空值叶子被跳过 → 无子字段
        self.assertEqual(_flatten_domain_subfields("extra_info", skeleton), [])
        # 透传骨架：按键结构展开，token 与 build_prompt 解析一致（多层方括号）
        subs = _flatten_domain_subfields("extra_info", skeleton, include_empty=True)
        tokens = {s["token"] for s in subs}
        self.assertIn("extra_info[current_package][package_name]", tokens)
        self.assertIn("extra_info[current_package][actual_price]", tokens)
        self.assertIn("extra_info[diff][voice_minutes_diff]", tokens)
        # 非空值仍照常产出（不因引入 include_empty 破坏原行为）
        subs2 = _flatten_domain_subfields(
            "extra_info", {"a": {"b": "有值"}}, include_empty=False)
        self.assertEqual([s["token"] for s in subs2], ["extra_info[a][b]"])


class TestProductFieldPalette(unittest.TestCase):
    """配置页调色板：入参为多个产品（数组）时，产品字段必须可见可拖入。

    此前 context_vars 只从「接口映射结果」取推荐条，直传省份（产品在
    extra_info.recommended_packages 数组里）取不到，调色板一个产品字段都看不到。
    """

    def _data(
        self,
        mock: Dict[str, Any],
        selected: Optional[List[str]] = None,
        passthrough: bool = True,
    ) -> List[Dict[str, Any]]:
        import asyncio
        from unittest.mock import patch
        from routers import management as mg

        # selected=None 表示「不勾选任何字段」= 默认暴露全部顶层字段
        cfg: Dict[str, Any] = {
            "enabled": True, "source_type": "direct", "mock_response": mock,
        }
        if selected is not None:
            cfg["passthrough_fields"] = selected
        if passthrough:
            cfg["direct_mode"] = "passthrough"
        fake = type("P", (), {"config": {"api_nodes": {"cc": cfg}, "biz_config": {}}})()
        with patch.object(mg.skill_registry, "get", return_value=fake):
            res = asyncio.run(mg.get_context_vars("guangdong", "营销活动"))
        return res["data"]

    def _vars(self, mock: Dict[str, Any], selected: Optional[List[str]] = None,
              passthrough: bool = True) -> List[str]:
        """取推荐产品字段的裸占位符 token 名。
        - 映射/接口口径：source=recommended_product 分组；
        - 透传口径：source=passthrough 的 recommended_packages 直传大变量（展开=勾选的产品字段）。
        """
        tokens: List[str] = []
        for g in self._data(mock, selected, passthrough):
            is_prod_group = g.get("source") == "recommended_product"
            is_pt_prod = (g.get("source") == "passthrough"
                          and g.get("key") == "recommended_packages")
            if not (is_prod_group or is_pt_prod):
                continue
            for s in (g.get("subfields") or []):
                tokens.append(s.get("token"))
        return tokens

    _ARR_MOCK = {"extra_info": {"recommended_packages": [
        {"offerName": "升99元套餐", "recommend_actual_price": "99元",
         "recommend_base_flow": "40GB/月"}]}}

    def test_passthrough_no_selection_uses_dict_name_only(self) -> None:
        """透传模式未勾产品字段：不摊开字段，只给透传字典名 {recommended_packages}。"""
        data = self._data(self._ARR_MOCK)
        rp = next((v for v in data if v["key"] == "recommended_packages"), None)
        self.assertIsNotNone(rp, "recommended_packages 应作为透传字典名出现")
        self.assertEqual(rp["source"], "passthrough")
        self.assertFalse(rp.get("subfields"), "透传模式不应摊开产品字段")
        self.assertEqual(
            [v for v in data if v.get("source") == "recommended_product"], [],
            "未勾选时不应出现产品字段分组")

    def test_passthrough_selected_product_fields_only(self) -> None:
        """透传模式按 recommended_packages.<字段> 精确勾选：只暴露勾中的产品字段。"""
        keys = self._vars(self._ARR_MOCK, selected=[
            "recommended_packages.offerName",
            "recommended_packages.recommend_actual_price",
        ])
        self.assertEqual(set(keys), {"offerName", "recommend_actual_price"})
        self.assertNotIn("recommend_base_flow", keys, "未勾选的产品字段不应暴露")

    def test_mapping_mode_still_exposes_product_fields(self) -> None:
        """直传映射 / 接口查询模式口径不变：产品字段仍自动收敛为单个可展开分组。"""
        data = self._data(self._ARR_MOCK, passthrough=False)
        groups = [v for v in data if v.get("source") == "recommended_product"]
        self.assertEqual(len(groups), 1, "产品字段应收敛为单个分组变量")
        self.assertEqual(groups[0]["key"], "recommended_packages")
        self.assertGreaterEqual(len(groups[0].get("subfields") or []), 3)

    def test_legacy_single_product_skeleton_exposed(self) -> None:
        """旧版单产品字典 + 全空骨架样例（只声明字段名）→ 按结构暴露。"""
        keys = self._vars({
            "extra_info": {"final_recommendations": {
                "recommend_package_name": "", "recommend_actual_price": "",
            }},
        }, passthrough=False)
        self.assertEqual(set(keys), {"recommend_package_name", "recommend_actual_price"})

    def test_id_and_standard_domain_names_excluded(self) -> None:
        """ID/排序字段与标准域同名字段不作为话术槽位暴露（与运行时注入规则一致）。"""
        keys = self._vars({
            "extra_info": {"recommended_packages": [{
                "offerId": "20260701132914458", "rank": 1, "tags": ["x"],
                "current_package": "冲突名", "offerName": "升99元套餐",
            }]},
        }, passthrough=False)
        self.assertEqual(keys, ["offerName"])

    def test_save_path_normalizes_wrapped_mock(self) -> None:
        """保存直传节点（生产 ES 写路径）：整请求体 / params 包裹的 mock 归一为 extra_info 本体，
        并清理指向包裹层的脏 passthrough_fields；对已干净数据幂等。"""
        from routers.management import _clean_direct_node_for_save
        node = {
            "source_type": "direct", "direct_mode": "passthrough",
            "passthrough_fields": ["extra_info", "current_package", "recommended_packages"],
            "mock_response": {"callId": "x", "province": "gd", "extra_info": {
                "current_package": {"package_name": "139全家享"},
                "recommended_packages": [{"offerName": "升169"}]},
                "batch_contexts": [{"stage": "个人市场"}]},
        }
        notes = _clean_direct_node_for_save(node)
        self.assertTrue(notes)
        self.assertEqual(set(node["mock_response"].keys()),
                         {"current_package", "recommended_packages"})
        self.assertNotIn("extra_info", node["passthrough_fields"])
        self.assertEqual(_clean_direct_node_for_save(node), [])  # 幂等

        wrapped = {"source_type": "direct", "direct_mode": "passthrough",
                   "mock_response": {"params": {"phone": "x", "extra_info": {"a": "1"},
                                                "batch_contexts": []}}}
        _clean_direct_node_for_save(wrapped)
        self.assertEqual(wrapped["mock_response"], {"a": "1"})

        api_node = {"source_type": "api", "mock_response": {"bean": {"x": 1}}}
        self.assertEqual(_clean_direct_node_for_save(api_node), [])
        self.assertEqual(api_node["mock_response"], {"bean": {"x": 1}})

    def test_current_package_visible_in_direct_mode(self) -> None:
        """直传纯 passthrough 节点：current_package 标准域也要出现在调色板（可展开子字段）。"""
        import asyncio
        from unittest.mock import patch
        from routers import management as mg
        node = {"cc": {"enabled": True, "source_type": "direct",
                       "direct_mode": "passthrough",
                       "passthrough_fields": ["current_package", "recommended_packages"],
                       "mock_response": {"extra_info": {
                           "current_package": {"package_name": "139全家享", "actual_price": "139"},
                           "recommended_packages": [{"offerName": "a"}]}}}}
        fake = type("P", (), {"config": {"api_nodes": node, "biz_config": {}}})()
        with patch.object(mg.skill_registry, "get", return_value=fake):
            res = asyncio.run(mg.get_context_vars("guangdong", "营销活动"))
        cp = next((v for v in res["data"] if v["key"] == "current_package"), None)
        self.assertIsNotNone(cp, "current_package 应出现在调色板")
        subs = [s["token"] for s in (cp.get("subfields") or [])]
        self.assertIn("current_package[package_name]", subs)

    def test_renamed_passthrough_list_drops_stale_std_name(self) -> None:
        """透传产品列表改名（recommended_packages → recommended_packages11）：
        旧标准域名 recommended_packages 已不在样例里 → 属残留脏项，调色板不再复活它，
        只出最新的 recommended_packages11（且带产品子字段），避免重复占位符。"""
        import asyncio
        from unittest.mock import patch
        from routers import management as mg
        node = {"cc": {"enabled": True, "source_type": "direct",
                       "direct_mode": "passthrough",
                       # 残留旧 recommended_packages 键 + 新 recommended_packages11 键
                       "passthrough_fields": ["recommended_packages",
                                              "recommended_packages.recommend_actual_price",
                                              "recommended_packages11",
                                              "recommended_packages11.recommend_actual_price"],
                       "mock_response": {"extra_info": {
                           "recommended_packages11": [
                               {"offerName": "升169", "recommend_actual_price": "169元"}]}}}}
        fake = type("P", (), {"config": {"api_nodes": node, "biz_config": {}}})()
        with patch.object(mg.skill_registry, "get", return_value=fake):
            res = asyncio.run(mg.get_context_vars("guangdong", "营销活动"))
        keys = {v["key"] for v in res["data"]}
        self.assertNotIn("recommended_packages", keys,
                         "改名后旧 recommended_packages 不应再出现（残留脏项）")
        self.assertIn("recommended_packages11", keys, "只保留最新透传列表字段")
        rp = next(v for v in res["data"] if v["key"] == "recommended_packages11")
        subs = {s["token"] for s in (rp.get("subfields") or [])}
        self.assertIn("recommend_actual_price", subs)

    def test_clean_save_drops_renamed_std_list(self) -> None:
        """保存写路径：样例里已不存在的旧标准域名（改名残留）从 passthrough_fields 清理掉。"""
        from routers.management import _clean_direct_node_for_save
        node = {
            "source_type": "direct", "direct_mode": "passthrough",
            "passthrough_fields": ["recommended_packages",
                                   "recommended_packages.recommend_actual_price",
                                   "recommended_packages11",
                                   "recommended_packages11.recommend_actual_price"],
            "mock_response": {"extra_info": {"recommended_packages11": [
                {"offerName": "升169", "recommend_actual_price": "169元"}]}},
        }
        _clean_direct_node_for_save(node)
        self.assertEqual(node["passthrough_fields"],
                         ["recommended_packages11",
                          "recommended_packages11.recommend_actual_price"])
        self.assertEqual(_clean_direct_node_for_save(node), [])  # 幂等

    def test_subpath_passthrough_field_in_palette(self) -> None:
        """透传大变量与占位符一一对应：勾选的子字段收进「父级大变量」的 subfields，
        不再平铺成顶层 leaf chip；调色板只出 portrait_style / current_package 两个大变量。"""
        import asyncio
        from unittest.mock import patch
        from routers import management as mg
        node = {"cc": {"enabled": True, "source_type": "direct",
                       "direct_mode": "passthrough",
                       "passthrough_fields": ["portrait_style.communication_style",
                                              "current_package.package_name"],
                       "mock_response": {"extra_info": {
                           "portrait_style": {"communication_style": "直接爽快",
                                              "business_conte": "关注性价比"},
                           "current_package": {"package_name": "139全家享"}}}}}
        fake = type("P", (), {"config": {"api_nodes": node, "biz_config": {}}})()
        with patch.object(mg.skill_registry, "get", return_value=fake):
            res = asyncio.run(mg.get_context_vars("guangdong", "营销活动"))
        by_key = {v["key"]: v for v in res["data"]}
        # 大变量作为顶层占位符出现，子字段不再平铺
        self.assertIn("portrait_style", by_key, "父级大变量应作为占位符出现")
        self.assertIn("current_package", by_key)
        self.assertNotIn("communication_style", by_key, "子字段不再平铺成顶层 chip")
        self.assertNotIn("package_name", by_key, "子字段不再平铺成顶层 chip")
        # portrait_style（非标准域）子字段用裸叶子 token；未勾的兄弟不暴露
        pt_subs = {s["token"] for s in (by_key["portrait_style"].get("subfields") or [])}
        self.assertIn("communication_style", pt_subs, "勾选的子字段收进父级 subfields（裸叶子名）")
        self.assertNotIn("business_conte", pt_subs, "未勾选的兄弟子字段不应暴露")
        # current_package（标准域）子字段用 {域[子键]} token，走 resource_context 精确解析
        cp_subs = {s["token"] for s in (by_key["current_package"].get("subfields") or [])}
        self.assertIn("current_package[package_name]", cp_subs)

    def test_runtime_subpath_passthrough(self) -> None:
        """运行时：点路径透传只暴露被勾选的子字段（按叶子名），兄弟子字段不外泄；
        标准域仍照旧写入 resources（多产品展开依赖 recommended_packages）。"""
        import asyncio
        from steps.data_step import DataStep
        raw = {
            "portrait_style": {"communication_style": "直接爽快",
                               "business_conte": "关注性价比"},
            "current_package": {"package_name": "139全家享"},
            "recommended_packages": [{"offerName": "升169"}],
        }
        ctx = make_ctx(extra_info=raw)
        cfg = {"source_type": "direct", "direct_mode": "passthrough",
               "passthrough_fields": ["portrait_style.communication_style"]}
        out = asyncio.run(DataStep("guangdong")._call_one("cc", cfg, ctx))
        self.assertEqual(out["passthrough"], {"communication_style": "直接爽快"})
        self.assertIn("recommended_packages", out["resources"])
        self.assertIn("current_package", out["resources"])

        # 勾选父级（或不勾选）时保留父级大变量 portrait_style 本身 + 展开子字段：
        # 大变量占位符 {portrait_style} 需在上下文里可读地体现，不再 pop 掉父级。
        cfg2 = {"source_type": "direct", "direct_mode": "passthrough",
                "passthrough_fields": ["portrait_style"]}
        out2 = asyncio.run(DataStep("guangdong")._call_one("cc", cfg2, ctx))
        self.assertEqual(set(out2["passthrough"]),
                         {"portrait_style", "communication_style", "business_conte"})
        self.assertEqual(out2["passthrough"]["portrait_style"],
                         {"communication_style": "直接爽快", "business_conte": "关注性价比"})
        out3 = asyncio.run(DataStep("guangdong")._call_one(
            "cc", {"source_type": "direct", "direct_mode": "passthrough"}, ctx))
        self.assertEqual(set(out3["passthrough"]),
                         {"portrait_style", "communication_style", "business_conte"})

    def test_runtime_product_field_whitelist(self) -> None:
        """运行时：recommended_packages.<字段> 勾选进产品字段白名单，多产品逐条注入只给这些字段；
        不勾选时沿用「注入全部非空产品字段」的原行为。"""
        import asyncio
        from steps.data_step import DataStep
        raw = {
            "recommended_packages": [
                {"offerId": "A1", "offerName": "升169", "recommend_actual_price": "169元",
                 "recommend_base_flow": "40GB/月", "rank": 1},
                {"offerId": "A2", "offerName": "扩容20G", "recommend_actual_price": "20元",
                 "recommend_base_flow": "20GB/月", "rank": 2},
            ],
        }
        ctx = make_ctx(extra_info=raw)
        out = asyncio.run(DataStep("guangdong")._call_one(
            "cc", {"source_type": "direct", "direct_mode": "passthrough",
                   "passthrough_fields": ["recommended_packages",
                                          "recommended_packages.recommend_actual_price"]}, ctx))
        self.assertEqual(out["product_field_allow"], ["recommend_actual_price"])
        self.assertEqual(out["passthrough"], {}, "列表域子字段不进扁平透传通道")
        self.assertEqual(len(out["resources"]["recommended_packages"]), 2)

        # 白名单生效：只有勾中的产品字段进【上下文数据】
        ctx2 = make_ctx(extra_info=raw)
        ctx2.final_recommendations = raw["recommended_packages"]
        ctx2.product_field_allow = ["recommend_actual_price"]
        # 模板不引用产品字段 → 走多产品逐字段兜底注入（白名单在此生效）
        _pkg = raw["recommended_packages"][0]
        _tpl = "您好，给您介绍一个优惠。"
        txt = build_prompt(
            user_prompt_tpl="", template_text=_tpl,
            ctx=ctx2, pkg=_pkg, diff=PackageDiff(ctx2.current_package, _pkg),
            linked_vars=[],
        )
        self.assertIn("{recommend_actual_price}：169元", txt)
        self.assertNotIn("{recommend_base_flow}：", txt, "未勾选的产品字段不应注入")
        self.assertNotIn("{offerName}：", txt)

        # 不勾选 → 原行为（全部非空产品字段）
        ctx3 = make_ctx(extra_info=raw)
        ctx3.final_recommendations = raw["recommended_packages"]
        txt3 = build_prompt(
            user_prompt_tpl="", template_text=_tpl,
            ctx=ctx3, pkg=_pkg, diff=PackageDiff(ctx3.current_package, _pkg),
            linked_vars=[],
        )
        self.assertIn("{recommend_actual_price}：169元", txt3)
        self.assertIn("{recommend_base_flow}：40GB/月", txt3)
        self.assertIn("{offerName}：升169", txt3)

    def test_cache_key_busts_on_config_change(self) -> None:
        """保存 api_nodes 后配置立即生效：缓存 key 必须随节点配置变化。

        只按接口 URL 做 key 时，改 response_extract / field_transform /
        passthrough_fields 这类不改 URL 的配置不会失效缓存，TTL 窗口内测试页仍返回
        改配置前的映射结果（表现为「保存了但没生效」）。
        """
        from steps.data_step import DataStep
        step = DataStep("guangdong")
        ctx = make_ctx(extra_info={"a": "1"})

        base = {"cc": {"source_type": "direct", "direct_mode": "passthrough",
                       "passthrough_fields": ["a"]}}
        k1 = step._cache_key(ctx, base)
        self.assertEqual(k1, step._cache_key(ctx, {"cc": dict(base["cc"])}),
                         "配置未变时 key 必须稳定（否则缓存永不命中）")

        changed = {"cc": {**base["cc"], "passthrough_fields": ["a", "b"]}}
        self.assertNotEqual(k1, step._cache_key(ctx, changed), "透传字段变化应失效缓存")

        for field_name, val in (
            ("response_extract", {"current_package": "bean.offer"}),
            ("field_transform", {"usage": {"type": "passthrough"}}),
            ("mock_mode", True),
            ("mock_response", {"bean": {"x": 1}}),
            ("enabled", False),
        ):
            self.assertNotEqual(
                k1, step._cache_key(ctx, {"cc": {**base["cc"], field_name: val}}),
                f"{field_name} 变化应失效缓存")

    def test_save_path_keeps_product_field_subpaths(self) -> None:
        """保存（ES 写路径）：列表域下的产品字段路径按数组元素校验保留。"""
        from routers.management import _clean_direct_node_for_save
        node = {
            "source_type": "direct", "direct_mode": "passthrough",
            "passthrough_fields": ["recommended_packages.recommend_actual_price",
                                   "recommended_packages.不存在字段"],
            "mock_response": {"extra_info": {"recommended_packages": [
                {"offerName": "a", "recommend_actual_price": "9"}]}},
        }
        _clean_direct_node_for_save(node)
        self.assertEqual(node["passthrough_fields"],
                         ["recommended_packages.recommend_actual_price"])
        self.assertEqual(_clean_direct_node_for_save(node), [])  # 幂等

    def test_save_path_keeps_subpath_fields(self) -> None:
        """保存（ES 写路径）：点路径透传字段按路径校验保留，不存在的路径才清理。"""
        from routers.management import _clean_direct_node_for_save
        node = {
            "source_type": "direct", "direct_mode": "passthrough",
            "passthrough_fields": ["portrait_style.communication_style",
                                   "portrait_style.not_exist", "extra_info"],
            "mock_response": {"extra_info": {
                "portrait_style": {"communication_style": "直接爽快"}}},
        }
        _clean_direct_node_for_save(node)
        self.assertEqual(node["passthrough_fields"],
                         ["portrait_style.communication_style"])
        self.assertEqual(_clean_direct_node_for_save(node), [])  # 幂等


@unittest.skipUnless(_BASELINE_AVAILABLE, f"script_step 导入失败: {_BASELINE_IMPORT_ERROR}")
class TestPreviewPrompt(unittest.TestCase):
    """preview_prompt：示例数据走同一 build_prompt 路径。"""

    def test_preview_new_format_template(self) -> None:
        tpl = {
            "template_name": "预览模板",
            "template_content": "您好，当前{cur_brief}，推荐{pkg_brief}。",
            "linked_vars": ["cur_brief", "pkg_brief", "diff_str"],
            "script_requirement": "礼貌热情",
            "intent": "套餐推荐",
        }
        out = preview_prompt(tpl)
        self.assertIsInstance(out, str)
        self.assertIn("【话术模板】\n您好，当前{cur_brief}，推荐{pkg_brief}。", out)
        self.assertIn("话术要求：礼貌热情", out)

    def test_preview_old_format_template(self) -> None:
        """无模板正文/关联变量、仅 prompt_template → 走旧格式路径。"""
        tpl = {
            "template_name": "旧格式",
            "template_content": "",
            "prompt_template": "推荐套餐:{pkg_brief}\n意图:{intent}\n话术：",
        }
        out = preview_prompt(tpl, intent="携转挽留")
        self.assertIn("推荐套餐:", out)
        self.assertIn("意图:携转挽留", out)

    def test_preview_sample_data_override(self) -> None:
        """sample_ctx_data 按顶层 key 覆盖内置示例。"""
        tpl = {
            "template_content": "推荐{pkg_brief}",
            "linked_vars": ["pkg_brief"],
        }
        out = preview_prompt(
            tpl, sample_ctx_data={"recommended_package": {"offerName": "自定义测试套餐X"}}
        )
        self.assertIn("自定义测试套餐X", out)

    def test_preview_passthrough_no_default_domain_leak(self) -> None:
        """直传透传预览：上下文严格以选择的透传入参为准，不掺内置默认样例的标准域
        （current_package=畅享套餐59元档 / usage / tags / user_profile 不得混入），且直传
        current_package 按入参原值呈现（不映射成 offerName 口径）。"""
        ei = {
            "portrait_style": {"communication_style": "直接爽快"},
            "current_package": {"package_name": "139全家享", "actual_price": "139元"},
            "recommended_packages": [{"offerName": "升169", "recommend_actual_price": "169元"}],
        }
        tpl = {
            "template_content": "当前{current_package}，用{communication_style}推荐{recommend_actual_price}。",
            "linked_vars": ["current_package"],
            "intent": "营销活动",
        }
        out = preview_prompt(
            tpl, sample_ctx_data={"extra_info": ei},
            passthrough_fields=["current_package", "portrait_style", "recommended_packages"],
        )
        # 默认样例的标准域不得泄漏进上下文
        for leaked in ("畅享套餐59元档", "175%", "五星", "视频类应用为主"):
            self.assertNotIn(leaked, out, f"默认样例 {leaked} 不应混入透传预览")
        # 直传入参按原值呈现 + 透传叶子 + 产品字段
        self.assertIn("139全家享", out)
        self.assertIn("直接爽快", out)
        self.assertIn("169元", out)

    def test_preview_passthrough_parent_bigvar_reflected(self) -> None:
        """直传大变量 {portrait_style} 必须在上下文里体现（可读展开）；已被父级整块体现的
        子字段（communication_style / business_conte）不再单列一行，避免重复。"""
        ei = {
            "portrait_style": {"communication_style": "直接爽快",
                               "business_conte": "关注性价比，近期有升档意向"},
            "recommended_packages": [{"offerName": "升169", "recommend_actual_price": "169元"}],
        }
        tpl = {
            "template_content": "用 {portrait_style} 风格推荐 {recommend_actual_price}。",
            "linked_vars": [],
            "intent": "营销活动",
        }
        out = preview_prompt(
            tpl, sample_ctx_data={"extra_info": ei},
            passthrough_fields=["portrait_style", "portrait_style.communication_style",
                                "portrait_style.business_conte", "recommended_packages"],
        )
        # 父级大变量占位符锚点出现，且可读展开（非生 JSON blob）
        self.assertIn("{portrait_style}", out, "大变量 {portrait_style} 应在上下文中体现")
        self.assertNotIn('{"communication_style"', out, "父级不应以生 JSON blob 呈现")
        self.assertIn("communication_style：直接爽快", out)
        # 未被模板引用的子字段不再单列成独立行（父级已整块体现，去重）
        self.assertNotIn("{communication_style}：", out, "子字段行应被父级去重")
        self.assertNotIn("{business_conte}：", out, "子字段行应被父级去重")

    def test_preview_passthrough_referenced_leaf_still_emitted(self) -> None:
        """模板显式引用某子字段 {communication_style} 时，仍单列该子字段行以保证可填。"""
        ei = {"portrait_style": {"communication_style": "直接爽快",
                                 "business_conte": "关注性价比"}}
        tpl = {
            "template_content": "请用 {communication_style} 的口吻沟通。",
            "linked_vars": [], "intent": "营销活动",
        }
        out = preview_prompt(
            tpl, sample_ctx_data={"extra_info": ei},
            passthrough_fields=["portrait_style", "portrait_style.communication_style",
                                "portrait_style.business_conte"],
        )
        self.assertIn("{communication_style}：直接爽快", out, "被引用的子字段应单列可填")
        # 未被引用的兄弟子字段仍被父级去重，不单列
        self.assertNotIn("{business_conte}：", out)

    def test_preview_matches_manual_build(self) -> None:
        """preview_prompt 输出与手工用相同示例数据调 build_prompt 一致（同一路径）。"""
        tpl = {
            "template_content": "推荐{pkg_brief}，{diff_str}",
            "linked_vars": ["cur_brief", "pkg_brief", "diff_str"],
            "script_requirement": "简短",
        }
        sample = {
            "current_package": {"offerName": "对照当前套餐", "initFee": 29},
            "recommended_package": {"offerName": "对照推荐套餐", "initFee": 59},
            "usage": {"data_usage": {"月均流量": 12}},
            "tags": {"标签A": "是"},
            "user_info": {"星级": "三星"},
            "user_profile": {},
            "domain_ext": {},
            "extra_info": {"卖点": "对照"},
            "extra_context": {},
            "max_length": 66,
        }
        out = preview_prompt(tpl, sample_ctx_data=json.loads(json.dumps(sample)),
                             province="shandong", intent="套餐推荐")

        ctx = FlowContext(
            phone="13800000000", intent="套餐推荐", province="shandong",
            current_package=sample["current_package"], usage=sample["usage"],
            tags=sample["tags"], user_info=sample["user_info"],
            user_profile=sample["user_profile"], domain_ext=sample["domain_ext"],
            extra_info=sample["extra_info"], extra_context=sample["extra_context"],
        )
        pkg = sample["recommended_package"]
        expected = build_prompt(
            user_prompt_tpl=tpl["template_content"],
            template_text=tpl["template_content"],
            ctx=ctx, pkg=pkg, diff=PackageDiff(ctx.current_package, pkg),
            linked_vars=tpl["linked_vars"],
            script_requirement=tpl["script_requirement"],
            field_aliases={}, max_length=66,
        )
        self.assertEqual(out, expected)


class TestRepublishLocal(unittest.TestCase):
    """从本地标准配置整包重发布到 ES（生产事故恢复）。

    不依赖真实 ES：patch publish_config 断言「读到的本地配置」被原样整包发布，
    并覆盖本地文件缺失 / 非法类型的降级返回。
    """

    def test_read_local_config_beijing_is_direct_mapped(self) -> None:
        """本地标准配置必须是直连写法：from 直指响应路径、不含 raw_xxx 中间槽位。

        republish_local 会把这份文件整包覆盖回 ES，它若还带中间集，一次事故恢复就把
        「两处同名才成立」的脆弱契约又写回生产。
        """
        from services.skill_publisher import read_local_config
        cfg = read_local_config("beijing", "套餐推荐", "api_nodes")
        self.assertIsInstance(cfg, dict)
        node = cfg["北京测试接口_api"]
        self.assertEqual(node["field_transform"]["tags"]["from"], "bean.tags")
        self.assertNotIn("raw_tags", node["response_extract"])

    def test_read_local_config_bad_inputs_return_none(self) -> None:
        from services.skill_publisher import read_local_config
        self.assertIsNone(read_local_config("beijing", "不存在的意图", "api_nodes"))
        self.assertIsNone(read_local_config("beijing", "套餐推荐", "not_allowed"))
        self.assertIsNone(read_local_config("../etc", "x", "api_nodes"))  # 路径穿越

    def test_republish_local_publishes_local_config(self) -> None:
        import services.skill_publisher as sp
        from services.skill_publisher import PublishResult
        captured: Dict[str, Any] = {}

        def _fake_publish(province, intent, config_type, data, **kw):
            captured["province"] = province
            captured["config_type"] = config_type
            captured["data"] = data
            return PublishResult(True, "ok", version=7, es_written=True, file_written=True)

        with unittest.mock.patch.object(sp, "publish_config", _fake_publish), \
                unittest.mock.patch.object(sp, "_reload_registry", lambda *a, **k: None):
            results = sp.republish_local("beijing", "套餐推荐", config_types=("api_nodes",))
        self.assertTrue(results["api_nodes"].success)
        self.assertEqual(captured["config_type"], "api_nodes")
        # 发布的就是本地标准配置整包（直连写法）
        node = captured["data"]["北京测试接口_api"]
        self.assertEqual(node["field_transform"]["tags"]["from"], "bean.tags")
        self.assertNotIn("raw_tags", node["response_extract"])

    def test_republish_local_missing_file_fails_gracefully(self) -> None:
        import services.skill_publisher as sp
        with unittest.mock.patch.object(sp, "publish_config") as m:
            results = sp.republish_local("beijing", "不存在的意图", config_types=("api_nodes",))
        self.assertFalse(results["api_nodes"].success)
        self.assertIn("本地配置文件不存在", results["api_nodes"].message)
        m.assert_not_called()   # 本地无文件时不应触发发布


class TestSubfieldMissingHint(unittest.TestCase):
    """子键失配诊断：区分「域为空」与「域有数据但叶子子键写错」。"""

    def setUp(self) -> None:
        self.roots = {
            "usage": {
                "data_usage": {"近3月平均流量(GB)": 12.5, "近6月平均流量(GB)": 37.23},
                "voice_usage": {"近3月平均主叫时长": 45, "近6月平均主叫时长": 50},
                "consumption": {"近3月平均月消费": 29.02, "近6月平均月消费": 130.42},
            }
        }

    def test_leaf_name_off_by_one_char_returns_candidate(self) -> None:
        from engine.prompt_builder import _subfield_missing_hint
        hint = _subfield_missing_hint(self.roots, "usage[consumption][近6月平均消费]")
        self.assertIn("近6月平均月消费", hint)   # 给出最接近的产出键
        self.assertIn("未命中", hint)

    def test_leaf_gone_from_upstream_still_reports_domain_has_data(self) -> None:
        from engine.prompt_builder import _subfield_missing_hint
        hint = _subfield_missing_hint(self.roots, "usage[voice_usage][近6月平均语音饱和度]")
        self.assertTrue(hint)
        self.assertIn("已产出", hint)

    def test_canonically_equal_leaf_is_not_a_mismatch(self) -> None:
        from engine.prompt_builder import _subfield_missing_hint
        # 括号形态差异（全/半角）能被 fuzzy_get 取到，不算失配 → 无提示
        hint = _subfield_missing_hint(self.roots, "usage[data_usage][近6月平均流量（GB）]")
        self.assertEqual(hint, "")

    def test_empty_domain_returns_no_hint(self) -> None:
        from engine.prompt_builder import _subfield_missing_hint
        # 父域根本不存在/为空 → 真·无数据，交常规缺失逻辑，本函数不产出提示
        self.assertEqual(
            _subfield_missing_hint(self.roots, "current_package[curOfferDesc]"), "")
        self.assertEqual(
            _subfield_missing_hint({"usage": {}}, "usage[consumption][近6月平均消费]"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
