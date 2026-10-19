"""空槽位口径（biz_config.slot_fallback）测试

默认口径 passthrough：平台只忠实填槽，不替业务判断「这句该不该说」。
  - 有事实 → 原样填入，值为 0 也照实填 0；
  - 缺失 / 值为空 / 入参是掩码（**）→ 保留原句，槽位处填占位符（默认 **）。
「某句无数据时干脆不说」属个性化诉求，由该模板自己的【话术要求】表达。
drop 口径是旧行为（空值/零值删整句），保留作按技能包回退的开关。

守四件事：
  1. 「传 **」与「不传」落到完全相同的产出（槽位对称，山东诉求本体）；
  2. 零值照实填 0，不再被当成"无有效内容"删句；
  3. drop 口径仍能完整回退到旧行为；
  4. 规则正文两种口径复用同一批共用片段（防编造/防串填条文不因拆分而分叉）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.prompt_builder import (  # noqa: E402
    DEFAULT_SLOT_PLACEHOLDER,
    SLOT_FALLBACK_DROP,
    SLOT_FALLBACK_PASSTHROUGH,
    _drop_masked_values,
    build_prompt,
    normalize_slot_fallback,
)
from prompt.script_generation import (  # noqa: E402
    _RULE3_HEAD,
    _RULE3_TAIL,
    _RULE4_HEAD,
    _RULE4_TAIL,
    _RULES_HEAD,
    SCRIPT_GEN_RULES,
    SCRIPT_MISSING_FACTS_TAIL,
    build_gen_rules,
    build_missing_facts_tail,
)

_BASELINE_IMPORT_ERROR = ""
try:
    from core.context import FlowContext
    from plugins.package_diff import PackageDiff
    from steps.script_step import ScriptStep
    _BASELINE_AVAILABLE = True
except Exception as exc:  # noqa: BLE001 - 环境缺配置时跳过而非报错
    _BASELINE_AVAILABLE = False
    _BASELINE_IMPORT_ERROR = str(exc)


class TestNormalizeSlotFallback(unittest.TestCase):
    """配置归一：缺省即照实填槽，掩码集合按口径给出合理默认。"""

    def test_default_is_passthrough_and_masks_placeholder(self) -> None:
        """未配置 → passthrough，且默认把占位符自身当掩码，
        否则「传 **」会被当成有效事实，与「不传」走两条路径。"""
        sf = normalize_slot_fallback(None)
        self.assertEqual(sf["mode"], SLOT_FALLBACK_PASSTHROUGH)
        self.assertEqual(sf["placeholder"], DEFAULT_SLOT_PLACEHOLDER)
        self.assertEqual(sf["treat_as_empty"], {"**"})

    def test_drop_mode_does_not_mask(self) -> None:
        """回退到旧口径时不做掩码归一，与历史行为一致。"""
        sf = normalize_slot_fallback({"mode": "drop"})
        self.assertEqual(sf["mode"], SLOT_FALLBACK_DROP)
        self.assertEqual(sf["treat_as_empty"], set())

    def test_placeholder_is_accepted_as_legacy_alias(self) -> None:
        """passthrough 口径最初叫 placeholder，已落库的配置不能因改名而失效。"""
        self.assertEqual(
            normalize_slot_fallback({"mode": "placeholder"})["mode"],
            SLOT_FALLBACK_PASSTHROUGH,
        )

    def test_custom_placeholder_becomes_default_mask(self) -> None:
        sf = normalize_slot_fallback({"placeholder": "XX"})
        self.assertEqual(sf["placeholder"], "XX")
        self.assertEqual(sf["treat_as_empty"], {"XX"})

    def test_treat_as_empty_accepts_list_and_string(self) -> None:
        """前端标签输入可能给数组，也可能给逗号分隔字符串（含中文逗号）。"""
        self.assertEqual(
            normalize_slot_fallback({"treat_as_empty": ["**", " - ", ""]})["treat_as_empty"],
            {"**", "-"},
        )
        self.assertEqual(
            normalize_slot_fallback({"treat_as_empty": "**，无, N/A"})["treat_as_empty"],
            {"**", "无", "N/A"},
        )

    def test_unknown_mode_falls_back_to_passthrough(self) -> None:
        """配错 mode 不能让话术生成崩，回退默认口径。"""
        self.assertEqual(
            normalize_slot_fallback({"mode": "keep_raw"})["mode"], SLOT_FALLBACK_PASSTHROUGH)

    def test_blank_placeholder_falls_back_to_default(self) -> None:
        sf = normalize_slot_fallback({"placeholder": "   "})
        self.assertEqual(sf["placeholder"], DEFAULT_SLOT_PLACEHOLDER)

    def test_idempotent(self) -> None:
        """归一结果会被再次传入（ScriptStep 存归一值、_post_process 再归一一次）。"""
        for cfg in (None, {"mode": "drop"}, {"placeholder": "XX"}):
            once = normalize_slot_fallback(cfg)
            self.assertEqual(normalize_slot_fallback(once), once, cfg)


class TestGenRulesVariants(unittest.TestCase):
    """规则正文：默认即照实填槽口径，drop 为可回退的旧口径。"""

    def test_default_constant_equals_passthrough_build(self) -> None:
        self.assertEqual(build_gen_rules(), SCRIPT_GEN_RULES)
        self.assertEqual(build_gen_rules(SLOT_FALLBACK_PASSTHROUGH), SCRIPT_GEN_RULES)
        self.assertEqual(build_missing_facts_tail(), build_missing_facts_tail(
            SLOT_FALLBACK_PASSTHROUGH))

    def test_drop_variant_restores_old_wording(self) -> None:
        drop = build_gen_rules(SLOT_FALLBACK_DROP)
        self.assertNotEqual(drop, SCRIPT_GEN_RULES)
        self.assertIn("必须整体删除包含该占位符的那句话/短语", drop)
        self.assertIn("只有第 2 条要求略过空值/零值时", drop)
        self.assertEqual(build_missing_facts_tail(SLOT_FALLBACK_DROP), SCRIPT_MISSING_FACTS_TAIL)

    def test_shared_fragments_reused_by_both_modes(self) -> None:
        """防编造/防串填/模板为主体框架等共用条文只有一份 —— 拆片段的目的就是
        避免两种口径各存一份全文后改了一份忘另一份。"""
        drop = build_gen_rules(SLOT_FALLBACK_DROP)
        for shared in (_RULES_HEAD, _RULE3_HEAD, _RULE3_TAIL, _RULE4_HEAD, _RULE4_TAIL):
            self.assertIn(shared, SCRIPT_GEN_RULES)
            self.assertIn(shared, drop)

    def test_默认口径要求保留原句而非删句(self) -> None:
        self.assertIn("一律不得删句", SCRIPT_GEN_RULES)
        self.assertIn("保留包含该占位符的那句话/短语", SCRIPT_GEN_RULES)
        self.assertNotIn("必须整体删除包含该占位符的那句话/短语", SCRIPT_GEN_RULES)

    def test_默认口径零值照实填0(self) -> None:
        """本次口径反转的核心：0 是真事实，不再当作"无有效内容"删句。"""
        self.assertIn("值为 0、0元、0分钟、0个月、0GB 时也照实说出", SCRIPT_GEN_RULES)
        self.assertIn("不得因为是 0 就略过该句", SCRIPT_GEN_RULES)
        self.assertIn("含 0，见第 2 条", SCRIPT_GEN_RULES)
        # 旧口径的"严禁说出 0元/0分钟"负向约束不得残留在默认规则里（会与上面自相矛盾）
        self.assertNotIn("严禁说出“0元/0分钟/0个月/0GB”这类表述", SCRIPT_GEN_RULES)
        # 但 drop 口径里仍保留
        self.assertIn("严禁说出“0元/0分钟/0个月/0GB”这类表述",
                      build_gen_rules(SLOT_FALLBACK_DROP))

    def test_话术要求可覆盖默认口径(self) -> None:
        """运营要某段在无数据/为 0 时干脆不说时，靠模板「话术要求」里的提示词覆盖本条
        （与字数规则让位于【话术要求】的既有约定一致）。"""
        self.assertIn("以【话术要求】为准", SCRIPT_GEN_RULES)
        self.assertIn("可为一句、连续几句或一整段", SCRIPT_GEN_RULES)
        self.assertIn("只有【话术要求】明确要求不说的内容", SCRIPT_GEN_RULES)

    def test_sentinel_never_leaks_and_custom_placeholder_applied(self) -> None:
        """<PH> 是内部哨兵，绝不能出现在发给大模型的正文里。"""
        for text in (build_gen_rules(SLOT_FALLBACK_PASSTHROUGH, "XX"),
                     build_missing_facts_tail(SLOT_FALLBACK_PASSTHROUGH, "XX")):
            self.assertNotIn("<PH>", text)
            self.assertIn("XX", text)
        for text in (SCRIPT_GEN_RULES, build_missing_facts_tail(),
                     build_gen_rules(SLOT_FALLBACK_DROP)):
            self.assertNotIn("<PH>", text)


class TestNoSlotGenRules(unittest.TestCase):
    """零槽位模板口径（build_gen_rules(has_slots=False)）。

    山东生产（千问3-14B / temperature 1.1）：1500 条 online 模板是纯文案、无任何槽位，
    但规则 2、3 仍把整套填槽机制连同字面示例塞进提示词——包括
    「例如月均消费无数据时输出“您月均消费**元”」。模板没有槽位、上下文也没有资费数据时，
    这些示例成了模型唯一的范本，于是稳定造出「您现在的套餐是**，月费是**元」。
    叠加这些模板的【话术要求】多为推荐场景沿用（仍在索取当前套餐/历史用量/推荐资费），
    与规则 2「一律不得删句、缺失填 **」直接冲突，小模型无力仲裁。

    守三件事：
      1. 有槽位模板的规则正文逐字不变（其他省份 1542 条 online 模板零影响）；
      2. 零槽位时不再出现任何填槽示例，且显式禁止自造数值与占位符；
      3. 运营在正文里手写了占位记号的零槽位模板，改为「保留自带、禁止新增」。
    """

    def test_有槽位口径逐字不变(self) -> None:
        """护栏：has_slots 参数引入后，原有两种口径的产出必须与引入前完全一致。"""
        self.assertEqual(build_gen_rules(SLOT_FALLBACK_PASSTHROUGH), SCRIPT_GEN_RULES)
        self.assertEqual(
            build_gen_rules(SLOT_FALLBACK_PASSTHROUGH, DEFAULT_SLOT_PLACEHOLDER,
                            has_slots=True),
            SCRIPT_GEN_RULES,
        )
        # template_has_placeholder 对有槽位口径无效（只作用于零槽位分支）
        self.assertEqual(
            build_gen_rules(SLOT_FALLBACK_PASSTHROUGH, DEFAULT_SLOT_PLACEHOLDER,
                            has_slots=True, template_has_placeholder=True),
            SCRIPT_GEN_RULES,
        )
        self.assertEqual(
            build_gen_rules(SLOT_FALLBACK_DROP, DEFAULT_SLOT_PLACEHOLDER, has_slots=True),
            build_gen_rules(SLOT_FALLBACK_DROP),
        )

    def test_零槽位不再出现填槽示例(self) -> None:
        """根因回归：正是这条字面示例被 14B 照抄成了「月均消费**元」。"""
        rules = build_gen_rules(has_slots=False)
        self.assertIn("您月均消费**元", SCRIPT_GEN_RULES)      # 原口径确实带这个示例
        self.assertNotIn("您月均消费**元", rules)
        self.assertNotIn("您月均流量已达37GB", rules)
        self.assertNotIn("保留包含该占位符的那句话/短语", rules)

    def test_零槽位显式禁止自造数值与占位符(self) -> None:
        rules = build_gen_rules(has_slots=False)
        self.assertIn("不含任何 {占位符}", rules)
        self.assertIn("严禁写入模板正文里没有的具体数值", rules)
        self.assertIn("严禁用 **", rules)
        self.assertIn("而不是写个占位符", rules)

    def test_零槽位解决话术要求与基础规则的冲突(self) -> None:
        """1509 条零槽位模板的【话术要求】仍在索取资费/用量数据 —— 明确按「没有就不说」。"""
        rules = build_gen_rules(has_slots=False)
        self.assertIn("没有数据就不说", rules)
        self.assertIn("整句略过该信息点", rules)
        self.assertIn("不要用占位符顶替", rules)

    def test_零槽位仍保留泛化要求(self) -> None:
        """换一换要有变化：规则 4 的"换措辞/换句式"必须留着（山东场景6 无变化的另一半原因）。"""
        rules = build_gen_rules(has_slots=False)
        self.assertIn("不要逐字复述模板", rules)
        self.assertIn("更换措辞、句式、连接词与语气", rules)
        self.assertIn("没有任何槽位", rules)

    def test_模板自带占位记号时改为保留(self) -> None:
        """山东 24 条零槽位模板正文自带 **（运营预留给坐席口头补充），不能一并删掉。"""
        keep = build_gen_rules(has_slots=False, template_has_placeholder=True)
        self.assertIn("必须原样保留在输出里", keep)
        self.assertIn("不得自己新增模板里原本没有的 **", keep)
        # 不能再出现一刀切的"严禁用 **"，否则与"保留自带"自相矛盾
        self.assertNotIn("严禁用 **", keep)
        # 仍然禁止编造模板外的数值
        self.assertIn("严禁写入模板正文里没有的具体数值", keep)

    def test_零槽位规则条数与编号衔接(self) -> None:
        """提示词编号由调用方从第 5 条续接，故三种口径都必须正好 4 条。"""
        for rules in (
            SCRIPT_GEN_RULES,
            build_gen_rules(has_slots=False),
            build_gen_rules(has_slots=False, template_has_placeholder=True),
        ):
            for n in ("1. ", "2. ", "3. ", "4. "):
                self.assertIn(n, rules)
            self.assertNotIn("5. ", rules)

    def test_零槽位口径也替换自定义占位符哨兵(self) -> None:
        for kwargs in ({}, {"template_has_placeholder": True}):
            text = build_gen_rules(SLOT_FALLBACK_PASSTHROUGH, "▲▲",
                                   has_slots=False, **kwargs)
            self.assertNotIn("<PH>", text)
            self.assertIn("▲▲", text)

    def test_零槽位与mode无关(self) -> None:
        """没有槽位，填槽/删句两种口径都无从谈起 —— 产出不应随 mode 变化。"""
        self.assertEqual(
            build_gen_rules(SLOT_FALLBACK_PASSTHROUGH, has_slots=False),
            build_gen_rules(SLOT_FALLBACK_DROP, has_slots=False),
        )


class TestNoSlotPromptWiring(unittest.TestCase):
    """build_prompt 的槽位判定：{token} / {域[子键]} / linked_vars 任一命中即走原口径。"""

    _PROSE = "您现在的套餐用起来还挺合适，各项资源基本够用，建议先保留一段时间看看。"

    def _rules_of(self, template_text: str, linked_vars=None) -> str:
        ctx = FlowContext(
            phone="13811601301", intent="套餐升档", province="shandong",
            current_package={}, usage={}, tags={}, user_info={},
            user_profile={}, domain_ext={}, extra_info={},
            extra_context={"stage": "营销挽留", "scence": "仅当月不小心超出，以后不会"},
        )
        parts: dict = {}
        build_prompt(
            user_prompt_tpl="",
            template_text=template_text,
            ctx=ctx,
            pkg={},
            diff=PackageDiff(ctx.current_package, {}),
            linked_vars=linked_vars or [],
            script_requirement="",
            parts_out=parts,
        )
        return parts.get("other") or ""

    def test_纯文案模板走零槽位口径(self) -> None:
        rules = self._rules_of(self._PROSE)
        self.assertIn("不含任何 {占位符}", rules)
        self.assertNotIn("您月均消费**元", rules)

    def test_含花括号槽位走原口径(self) -> None:
        rules = self._rules_of("您当前套餐是{current_package}，建议升档。")
        self.assertIn("您月均消费**元", rules)

    def test_含子字段槽位走原口径(self) -> None:
        rules = self._rules_of("您近期流量{usage[data_usage][近3月平均流量(GB)]}，建议升档。")
        self.assertIn("您月均消费**元", rules)

    def test_勾了关联变量的纯文案走原口径(self) -> None:
        """北京 269 条模板勾了关联变量却不在正文写 {}，靠上下文事实行驱动 —— 不能误判。"""
        rules = self._rules_of(self._PROSE, linked_vars=["current_package"])
        self.assertIn("您月均消费**元", rules)
        self.assertNotIn("不含任何 {占位符}", rules)

    def test_正文自带占位记号的纯文案走保留口径(self) -> None:
        rules = self._rules_of("我看您这**月流量已用超**元啦，我帮您优化一下。")
        self.assertIn("必须原样保留在输出里", rules)
        self.assertNotIn("严禁用 **", rules)


class TestGuangxiProseTemplate(unittest.TestCase):
    """广西纯文案挽留模板：不因 linked_vars / extra_info.current_package 改写成套餐说明书。

    其他省份判定不变：山东/北京勾了关联变量的纯文案仍走原填槽口径。
    """

    _PROSE = (
        "移动号码现在是全国通用，您在国内通话费用是一致的，资费不会因为离开本地而变贵，"
        "号码不改变，重要的朋友也能联系上您，而且很多账号、银行卡都绑定了这个号码，"
        "建议您保留原套餐。"
    )
    _REQ = (
        "严格只用给定字段的真实值，不虚构数字/套餐名/优惠；"
        "任一字段缺失或为 0 时不提及该项，也不得用其他字段代替；150 字以内，结尾引导办理。"
    )
    _PACKAGE = {
        "packageBucktime": "2027-04-01",
        "packageOriginal": "79.00",
        "packageLastIll": "1.70GB",
        "packageVoice": "300",
        "packageJoinage": "7年3个月",
        "packageData": "79元套餐优惠至39元活动-2年合约",
        "packageMonthll": "15.00GB",
        "packageDiscount": "39.00",
    }

    def _build(self, *, province: str, template_text: str, linked_vars=None):
        ctx = FlowContext(
            phone="13800000000", intent="资费挽留", province=province,
            current_package=dict(self._PACKAGE), usage={}, tags={}, user_info={},
            user_profile={}, domain_ext={},
            extra_info={"current_package": dict(self._PACKAGE)},
            extra_context={"stage": "客户降档原因", "scence": "生活变化/工作地点变更"},
        )
        ctx.passthrough_context = {"current_package": dict(self._PACKAGE)}
        parts: dict = {}
        prompt = build_prompt(
            user_prompt_tpl="",
            template_text=template_text,
            ctx=ctx,
            pkg={"product_id": "prod009"},
            diff=PackageDiff(ctx.current_package, {}),
            linked_vars=linked_vars or ["pkg_brief", "current_package"],
            script_requirement=self._REQ,
            parts_out=parts,
        )
        return prompt, parts

    def test_广西纯文案走零槽位且不灌套餐事实(self) -> None:
        prompt, parts = self._build(province="guangxi", template_text=self._PROSE)
        self.assertIn("不含任何 {占位符}", parts.get("other") or "")
        self.assertIn("禁止把套餐名、月费、流量", parts.get("other") or "")
        ctx = parts.get("context_data") or ""
        self.assertNotIn("79元套餐优惠至39元", ctx)
        self.assertNotIn("2027-04-01", ctx)
        self.assertNotIn("{current_package}", ctx)
        self.assertNotIn("您月均消费**元", prompt)
        self.assertIn(self._PROSE, prompt)

    def test_广西有槽位模板仍填真实值(self) -> None:
        """prod001/prod002 这类带 {current_package[…]} 的模板不受影响。"""
        tpl = "您的套餐包括{current_package[packageData]}流量，建议您保留原套餐。"
        prompt, parts = self._build(province="guangxi", template_text=tpl)
        self.assertIn("您月均消费**元", parts.get("other") or "")
        self.assertNotIn("不含任何 {占位符}", parts.get("other") or "")
        self.assertIn("packageData", parts.get("context_data") or prompt)

    _SLOTTED = (
        "您的套餐包含{current_package[packageMonthll]}流量和{current_package[packageVoice]}语音通话，"
        "通话和流量都够好够用，建议您保留原套餐。"
    )

    def test_广西有槽位模板上下文只含被引用的槽位(self) -> None:
        """回归：整行 {current_package}/{pkg_brief} 里的原价、折扣、活动会被模型编成「原价×现价×」。"""
        prompt, parts = self._build(province="guangxi", template_text=self._SLOTTED)
        ctx = parts.get("context_data") or ""
        self.assertIn("{current_package[packageMonthll]}：15.00GB", ctx)
        self.assertIn("{current_package[packageVoice]}：300", ctx)
        for leaked in ("79.00", "39.00", "79元套餐优惠至39元", "2027-04-01", "prod009",
                       "{current_package}：", "{pkg_brief}"):
            self.assertNotIn(leaked, ctx, leaked)
        self.assertIn("模板没有写到的套餐名", parts.get("other") or "")

    def test_广西模板引用整块变量时仍注入(self) -> None:
        prompt, parts = self._build(
            province="guangxi", template_text="您当前套餐：{current_package}，建议保留。")
        self.assertIn("{current_package}：packageBucktime", parts.get("context_data") or "")

    def test_其他省份有槽位模板仍注入关联变量(self) -> None:
        """北京/山东靠关联变量事实行驱动，不得被广西收窄误伤。"""
        prompt, parts = self._build(province="shandong", template_text=self._SLOTTED)
        ctx = parts.get("context_data") or ""
        self.assertIn("{current_package}：packageBucktime", ctx)
        # 规则收紧对所有省份生效（上下文不收窄，约束填槽）
        self.assertIn("模板没有写到的套餐名", parts.get("other") or "")

    def test_无花括号槽位的模板不加槽位约束(self) -> None:
        """北京关联变量驱动、广东 ** 填槽的模板正文没有 {}，规则保持原样。"""
        prompt, parts = self._build(province="shandong", template_text=self._PROSE)
        self.assertNotIn("模板没有写到的套餐名", parts.get("other") or "")

    def test_其他省份勾关联变量的纯文案仍走原口径(self) -> None:
        """北京/山东护栏：同一模板在 shandong 下不得被广西例外误伤。"""
        prompt, parts = self._build(province="shandong", template_text=self._PROSE)
        self.assertIn("您月均消费**元", parts.get("other") or "")
        self.assertNotIn("不含任何 {占位符}", parts.get("other") or "")
        self.assertNotIn("禁止把套餐名、月费、流量", parts.get("other") or "")


@unittest.skipUnless(_BASELINE_AVAILABLE, f"script_step 导入失败: {_BASELINE_IMPORT_ERROR}")
class TestZeroSlotContextHasNoFacts(unittest.TestCase):
    """零槽位模板（非广西）：规则禁止写入模板外数值，上下文也不得再递数字。

    回归：此前规则切到零槽位口径，却仍把整包 extra_info / 多产品字段灌进上下文，自相矛盾。
    """

    _PROSE = "移动号码全国通用，资费不会因离开本地而变化，号码不改变，建议您保留原套餐。"
    _PKG = {"packageOriginal": "79.00", "packageDiscount": "39.00"}
    _REC = {"product_id": "p1", "offerName": "5G畅享129", "recommend_actual_price": "129"}

    def _ctx_of(self, *, province="shandong", linked=None, extra=None,
                passthrough=None, recs=1, template=None):
        ctx = FlowContext(
            phone="1", intent="资费挽留", province=province,
            current_package=dict(self._PKG), usage={}, tags={}, user_info={},
            user_profile={}, domain_ext={}, extra_info=extra or {},
            extra_context={"stage": "x", "scence": "y"},
        )
        if passthrough:
            ctx.passthrough_context = passthrough
        ctx.final_recommendations = [self._REC] * recs
        parts: dict = {}
        build_prompt(
            user_prompt_tpl="", template_text=template or self._PROSE, ctx=ctx,
            pkg=dict(self._REC), diff=PackageDiff(ctx.current_package, {}),
            linked_vars=linked or [], script_requirement="", parts_out=parts,
        )
        return parts.get("context_data") or "", parts.get("other") or ""

    def test_零槽位不灌extra_info整包(self) -> None:
        for province in ("shandong", "beijing"):
            ctx, other = self._ctx_of(province=province, extra={"current_package": self._PKG})
            self.assertIn("不含任何 {占位符}", other)
            self.assertNotIn("79.00", ctx, province)
            self.assertNotIn("{extra_info}", ctx, province)

    def test_有槽位仍灌extra_info(self) -> None:
        """对照：关联变量驱动的模板（北京）行为不变。"""
        ctx, _ = self._ctx_of(extra={"current_package": self._PKG}, linked=["usage"])
        self.assertIn("{extra_info}", ctx)


class TestDropMaskedValues(unittest.TestCase):
    """掩码归一的边界：只吃「整值等于掩码」的条目，不动正文。"""

    MASKS = {"**", "-"}

    def test_only_whole_value_matches(self) -> None:
        """产品卖点里合法出现的星号不得被当成空值抹掉。"""
        src = {"price": "**", "desc": "限时**特惠**，用满即送", "note": " - "}
        self.assertEqual(
            _drop_masked_values(src, self.MASKS), {"desc": "限时**特惠**，用满即送"})

    def test_nested_dict_and_list(self) -> None:
        src = {"portrait": {"style": "**", "tone": "直接"},
               "products": [{"fee": "**", "name": "5G畅享"}]}
        self.assertEqual(
            _drop_masked_values(src, self.MASKS),
            {"portrait": {"tone": "直接"}, "products": [{"name": "5G畅享"}]})

    def test_no_masks_returns_input_unchanged(self) -> None:
        src = {"price": "**"}
        self.assertIs(_drop_masked_values(src, set()), src)

    def test_zero_survives_masking(self) -> None:
        """0 是真事实，必须活到上下文里才能被"照实填 0"。"""
        self.assertEqual(_drop_masked_values({"fee": 0, "voice": "0"}, self.MASKS),
                         {"fee": 0, "voice": "0"})


@unittest.skipUnless(_BASELINE_AVAILABLE, f"script_step 导入失败: {_BASELINE_IMPORT_ERROR}")
class TestBuildPromptMaskNormalization(unittest.TestCase):
    """层1：入参掩码值不得作为「事实」进【上下文数据】。"""

    TEMPLATE = "推荐{recommend_package_name}，月费{recommend_actual_price}元。"

    def _build(self, extra_info, slot_fallback=None):
        ctx = FlowContext(
            phone="13800000000", intent="套餐推荐", province="shandong",
            current_package={}, usage={}, tags={}, user_info={},
            user_profile={}, domain_ext={}, extra_info=extra_info, extra_context={},
        )
        pkg = {}
        parts = {}
        facts = {}
        prompt = build_prompt(
            user_prompt_tpl="", template_text=self.TEMPLATE, ctx=ctx, pkg=pkg,
            diff=PackageDiff(ctx.current_package, pkg), linked_vars=[],
            slot_facts_out=facts, parts_out=parts, slot_fallback=slot_fallback,
        )
        return prompt, parts, facts

    def test_mask_and_absent_are_symmetric_by_default(self) -> None:
        """山东诉求本体：「传 **」与「不传」必须落到完全相同的上下文与产出。"""
        masked, m_parts, m_facts = self._build(
            {"recommend_package_name": "5G畅享", "recommend_actual_price": "**"})
        absent, a_parts, a_facts = self._build({"recommend_package_name": "5G畅享"})

        self.assertEqual(masked, absent, "传 ** 与不传应生成完全一致的 Prompt")
        self.assertEqual(m_facts, a_facts)
        # 掩码值不得作为事实行出现，否则模型可能照念 ** 或据此臆造
        self.assertNotIn("{recommend_actual_price}", m_parts["context_data"])
        # 有值的槽位照常注入
        self.assertIn("5G畅享", m_parts["context_data"])
        self.assertEqual(m_facts.get("recommend_package_name"), "5G畅享")

    def test_drop_mode_treats_mask_as_real_fact(self) -> None:
        """回退到 drop 口径时不做掩码归一：** 仍被当成有效值注入（历史行为）。"""
        _, parts, facts = self._build(
            {"recommend_package_name": "5G畅享", "recommend_actual_price": "**"},
            {"mode": "drop"})
        self.assertIn("{recommend_actual_price}：**", parts["context_data"])
        self.assertEqual(facts.get("recommend_actual_price"), "**")

    def test_zero_value_is_injected_as_fact(self) -> None:
        """0 必须作为事实进上下文，模型才可能"照实填 0"。"""
        _, parts, facts = self._build(
            {"recommend_package_name": "5G畅享", "recommend_actual_price": "0"})
        self.assertIn("{recommend_actual_price}：0", parts["context_data"])
        self.assertEqual(facts.get("recommend_actual_price"), "0")

    def test_prompt_tells_model_to_keep_sentence(self) -> None:
        prompt, _, _ = self._build({"recommend_package_name": "5G畅享"})
        self.assertIn("一律不得删句", prompt)
        self.assertNotIn("<PH>", prompt)


@unittest.skipUnless(_BASELINE_AVAILABLE, f"script_step 导入失败: {_BASELINE_IMPORT_ERROR}")
class TestPostProcessResidualPlaceholders(unittest.TestCase):
    """层3：确定性兜底 —— 模型没填的占位符默认换成 **，drop 口径才删句。"""

    RAW = "您好，推荐5G畅享套餐，月费{recommend_actual_price}元，流量30GB。"

    def test_default_keeps_clause_and_fills(self) -> None:
        self.assertEqual(
            ScriptStep._post_process(self.RAW),
            "您好，推荐5G畅享套餐，月费**元，流量30GB。")

    def test_drop_mode_removes_the_clause(self) -> None:
        out = ScriptStep._post_process(self.RAW, None, {"mode": "drop"})
        self.assertNotIn("{", out)
        self.assertNotIn("月费", out)
        self.assertIn("流量30GB", out)

    def test_custom_placeholder(self) -> None:
        out = ScriptStep._post_process(self.RAW, None, {"placeholder": "＿＿"})
        self.assertIn("月费＿＿元", out)

    def test_subfield_token_also_filled(self) -> None:
        out = ScriptStep._post_process("您当前套餐是{current_package[curOfferDesc]}，建议升级。")
        self.assertEqual(out, "您当前套餐是**，建议升级。")

    def test_real_facts_still_win_over_placeholder(self) -> None:
        """有映射事实的槽位必须填真值，不能被占位符兜底顶掉。"""
        out = ScriptStep._post_process(self.RAW, {"recommend_actual_price": "59"})
        self.assertIn("月费59元", out)
        self.assertNotIn("**", out)

    def test_zero_fact_is_filled_literally(self) -> None:
        """零值照实填 0，不被当成空值兜底成 ** 或删句。"""
        out = ScriptStep._post_process(
            "套餐含语音{pkg_voice}分钟。", {"pkg_voice": "0"})
        self.assertEqual(out, "套餐含语音0分钟。")

    def test_no_placeholder_text_untouched(self) -> None:
        clean = "您好，推荐5G畅享套餐，月费59元。"
        self.assertEqual(ScriptStep._post_process(clean), clean)

    def test_digit_leading_token_filled(self) -> None:
        """数字开头字段名 {3MonthsOutFlowFee} 也要被识别为残留占位符、换成 **，
        不能泄漏到成品话术（放宽正则前会原样残留）。"""
        out = ScriptStep._post_process("您近三月超套费用{3MonthsOutFlowFee}元。")
        self.assertEqual(out, "您近三月超套费用**元。")

    def test_chinese_and_dotpath_token_filled(self) -> None:
        """中文名 / 点路径占位符（{用户标签.星级}）同样被清理，不残留花括号。"""
        out = ScriptStep._post_process("您是{用户标签.星级}客户，专享权益。")
        self.assertNotIn("{", out)
        self.assertIn("**", out)

    def test_normal_text_braces_not_over_matched(self) -> None:
        """正文里带空格/标点的花括号不是占位符，不应被误删（正则限定字符集）。"""
        text = "活动说明：满 {100 元, 减 20} 元。"
        self.assertEqual(ScriptStep._post_process(text), text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
