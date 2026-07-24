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

            lines = [
                "你是套餐营销推荐坐席，负责将【上下文数据】填充进【话术模板】，"
                "生成自然、口语化的个性化套餐营销推荐话术。"
            ]
            if context_lines:
                lines.append(
                    "【上下文数据】（经接口映射得到的真实用户与套餐数据，"
                    "是你唯一可依据的事实来源，请勿使用未在此列出的信息）"
                )
                lines.extend(context_lines)
            if template_text:
                lines.append("【话术模板】")
                lines.append(template_text)
            lines.append(
                "【生成规则】\n"
                "1. 仅依据【上下文数据】中的事实填充话术模板，不得编造数据中不存在的"
                "数字、套餐名、优惠、功能或权益。\n"
                "2. 若某项信息缺失、为空或为 0，则跳过对应表述，既不提及、也不得用其他字段的值代替"
                "（例如语音为 0 则不谈语音；优惠月数为 0 则表述为“连续包月”而非“连续 0 个月”）。\n"
                "3. 占位符对应关系：【上下文数据】每行已用 {占位符} 标注其对应的槽位，"
                "请将【话术模板】中出现的同名 {占位符} 替换为该行的事实值（按语义就近对应，不要张冠李戴）；"
                "模板中出现但【上下文数据】未列出的占位符按第 2 条处理（跳过、不臆造、不串填）。\n"
                "4. 保留话术模板的语义与结构，输出贴合用户痛点、可直接对客播报的完整话术，"
                "最终结果不得残留任何 {} 占位符或字段名。"
            )
            if script_requirement:
                lines.append(f"5. 话术要求：{script_requirement}")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
