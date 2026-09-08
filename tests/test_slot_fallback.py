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

    def test_masked_direct_field_is_explicitly_listed_as_missing(self) -> None:
        """直传自定义槽位被掩码后，也要进入缺失事实约束，不能静默消失。"""
        ctx = FlowContext(
            phone="13800000000", intent="套餐推荐", province="shandong",
            current_package={}, usage={}, tags={}, user_info={},
            user_profile={}, domain_ext={},
            extra_info={"uniProdGrade": "**"}, extra_context={},
        )
        parts = {}
        prompt = build_prompt(
            user_prompt_tpl="", template_text="当前是{uniProdGrade}元套餐。",
            ctx=ctx, pkg={}, diff=PackageDiff(ctx.current_package, {}),
            linked_vars=[], parts_out=parts,
        )
        self.assertIn("【缺失事实】", prompt)
        self.assertIn("{uniProdGrade}", prompt)
        self.assertIn("把槽位本身写成 **", prompt)


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

    def test_strict_template_does_not_insert_context_facts(self) -> None:
        """模板没有槽位时，模型上下文里的资费/流量不能被自行插入正文。"""
        template = "费用上没有增加太多，但有了更多的流量，以后出门更方便。"
        out = ScriptStep._render_strict_template(
            template, {"pkg_flow": "20", "recommend_actual_price": "128"})
        self.assertEqual(out, template)
        self.assertNotIn("20", out)
        self.assertNotIn("128", out)

    def test_strict_template_fills_only_named_slots(self) -> None:
        template = "新套餐原价{origPrice}元，流量{flow}GB，网龄{internetAge}年。"
        out = ScriptStep._render_strict_template(
            template, {"origPrice": "99", "flow": "0"})
        self.assertEqual(out, "新套餐原价99元，流量0GB，网龄**年。")


if __name__ == "__main__":
    unittest.main(verbosity=2)
