"""派生字段（plugins/derived_fields.py）单测 + 天津端到端样例。

覆盖三件事：
1. 三个算子（array_find / sum / bucket）的语义与边界，重点是「取不到值不伪造 0」；
2. DataStep 直传透传链路：派生结果进 passthrough → ctx.passthrough_context；
3. 天津真实报文：数组按 timeType 选账期、两费求和分档选场景、0 值异常提示，
   以及与 slot_fallback 默认口径（照实填充、0 填 0）的联动。

天津报文的关键形态：over_flow / over_voice 是字符串 "0"，其余资源字段是空串 ——
必须保证 0 照实进上下文（否则规则 2「费用≠0 但超额量=0」永远无从触发），
而空串按 ** 处理。
"""
from __future__ import annotations

import asyncio
import copy
import unittest
from typing import Any, Dict

from core.context import FlowContext
from plugins.derived_fields import _parse_number, compute_derived_fields
from steps.data_step import DataStep
from utils import marketing_assistant as ma

# ── 天津营销活动报文（节选自需求单，保留 0 值与空串的原始形态）────────────
TJ_PAYLOAD: Dict[str, Any] = {
    "params": {
        "optType": "0,1",
        "systemId": "NGRC",
        "inputs": {
            "sequenceNo": "b2763380-e2f9-4efa-9163-e5a7c424722e",
            "servNumber": "135****6367",
            "provinceCode": "220",
            "callId": "",
            "staffId": "TJ00850",
            "staffNo": "AEY00423",
            "touchNumber": "1564161651112154",
            "userinfo": {
                "userExtra": {
                    "last3mVoiceSaturation": "14.0",
                    "last3mFlowSaturation": "73.08",
                },
                "currPrice": "",
                "currentPackageProductName": "",
            },
            "userinfo_json": [
                # 当月：上网费 1.5 有超额、通话费 2 但超套分钟为 0（规则 2 的异常形态）
                {"timeType": "0", "over_flow": "3", "over_flow_fee": "1.5",
                 "over_voice": "0", "over_voice_fee": "2", "user_consume": "58"},
                {"timeType": "3", "over_flow": "0", "over_flow_fee": "",
                 "over_voice": "0", "over_voice_fee": "", "user_consume": ""},
                {"timeType": "1", "over_flow": "0", "over_flow_fee": "0.2",
                 "over_voice": "0", "over_voice_fee": "0.3", "user_consume": ""},
                {"timeType": "2", "over_flow": "0", "over_flow_fee": "",
                 "over_voice": "0", "over_voice_fee": "", "user_consume": ""},
            ],
            "products": [
                {"productId": "2284056", "productName": "流量日包1GB版【2284056】",
                 "activityId": "2083122460714274821", "gift_cmn_flow": "0",
                 "businessType": "自有业务",
                 "marketingProductFlag": "1", "marketingActivityFlag": "1"},
            ],
        },
    }
}

# 天津的派生字段配置：账期选取 → 两费求和 → 分档选场景 → 账单异常提示
TJ_DERIVED: Dict[str, Any] = {
    "当月资源": {"type": "array_find", "from": "userinfo_json",
                 "where": {"timeType": "0"}},
    "上月资源": {"type": "array_find", "from": "userinfo_json",
                 "where": {"timeType": "1"}},
    "超套费用合计": {"type": "sum", "from": [
        "当月资源.over_flow_fee", "当月资源.over_voice_fee",
    ]},
    "推荐场景": {"type": "bucket", "from": "超套费用合计", "rules": [
        {"lt": 1, "value": "场景一"},
        {"value": "场景二"},
    ]},
    "账单解释提示": {"type": "bucket", "rules": [
        {"when": {"当月资源.over_flow_fee": {"ne": 0},
                  "当月资源.over_flow": {"eq": 0}},
         "value": "请结合账单与资源使用情况为客户解释"},
        {"when": {"当月资源.over_voice_fee": {"ne": 0},
                  "当月资源.over_voice": {"eq": 0}},
         "value": "请结合账单与资源使用情况为客户解释"},
        {"value": ""},
    ]},
}


def _tj_payload() -> Dict[str, Any]:
    return copy.deepcopy(TJ_PAYLOAD)


class TestParseNumber(unittest.TestCase):
    """宽松数值解析：带单位可解析，空值不当 0，bool 不当数字。"""

    def test_plain_and_unit_suffixed(self):
        self.assertEqual(_parse_number("0"), 0.0)
        self.assertEqual(_parse_number("1.5"), 1.5)
        self.assertEqual(_parse_number(3), 3.0)
        self.assertEqual(_parse_number("3元"), 3.0)
        self.assertEqual(_parse_number("1.5GB"), 1.5)
        self.assertEqual(_parse_number("12,000"), 12000.0)
        self.assertEqual(_parse_number("-2分钟"), -2.0)

    def test_blank_and_nonnumeric_are_none(self):
        for v in ("", "  ", None, "不限", [], {}):
            self.assertIsNone(_parse_number(v), f"{v!r} 不该被解析成数字")

    def test_bool_is_not_number(self):
        """True 若被当成 1 会让「是否为 0」的判定出现假命中。"""
        self.assertIsNone(_parse_number(True))
        self.assertIsNone(_parse_number(False))


class TestArrayFind(unittest.TestCase):
    """数组按条件选元素 —— 这是 userinfo_json 这类账期数组能填槽的前提。"""

    def _find(self, where: Dict[str, Any]) -> Any:
        cfg = {"x": {"type": "array_find", "from": "arr", "where": where}}
        raw = {"arr": [
            {"timeType": "0", "v": "current"},
            {"timeType": "1", "v": "last"},
        ]}
        return compute_derived_fields(raw, cfg).get("x")

    def test_selects_matching_element_as_dict(self):
        """返回整个元素 dict：dict 型透传值会被注册为子字段根，
        于是 {当月资源} 与 {当月资源[over_flow_fee]} 两种引用都可用。"""
        self.assertEqual(self._find({"timeType": "1"}), {"timeType": "1", "v": "last"})

    def test_where_compares_as_string(self):
        """报文里 timeType 可能是 "0" 也可能是 0，配置只写一种也要命中。"""
        self.assertEqual(self._find({"timeType": 0})["v"], "current")

    def test_no_match_produces_nothing(self):
        self.assertIsNone(self._find({"timeType": "9"}))

    def test_empty_where_takes_first_element(self):
        self.assertEqual(self._find({})["v"], "current")

    def test_any_comparison_takes_first_element_matching_one_condition(self):
        """任一条件命中时，返回数组中第一条命中的完整对象。"""
        raw = {"arr": [
            {"timeType": "2", "over_flow": "0", "over_voice": "3"},
            {"timeType": "0", "over_flow": "0", "over_voice": "0"},
            {"timeType": "1", "over_flow": "2", "over_voice": "0"},
        ]}
        cfg = {"x": {"type": "array_find", "from": "arr", "match": "any",
                      "where": {
                          "over_flow": {"gt": 0},
                          "over_voice": {"gt": 0},
                      }}}
        out = compute_derived_fields(raw, cfg)
        self.assertEqual(out["x"]["timeType"], "1")
        self.assertEqual(out["x"]["over_flow"], "2")

    def test_all_comparison_requires_every_condition(self):
        raw = {"arr": [
            {"a": "1", "b": "0"},
            {"a": "2", "b": "3"},
        ]}
        cfg = {"x": {"type": "array_find", "from": "arr",
                      "where": {"a": {"gt": 0}, "b": {"gt": 0}}}}
        self.assertEqual(compute_derived_fields(raw, cfg)["x"]["a"], "2")

    def test_non_list_source_produces_nothing(self):
        out = compute_derived_fields(
            {"arr": {"timeType": "0"}},
            {"x": {"type": "array_find", "from": "arr", "where": {"timeType": "0"}}},
        )
        self.assertNotIn("x", out)

    def test_numeric_index_path(self):
        """点路径支持 list 数字下标（原 _get_path 遇到 list 直接返回 None）。"""
        out = compute_derived_fields(
            {"arr": [{"v": "a"}, {"v": "b"}]},
            {"x": {"type": "bucket", "from": "arr.1.v",
                   "rules": [{"eq": "b", "value": "命中"}, {"value": "未命中"}]}},
        )
        self.assertEqual(out["x"], "命中")


class TestSum(unittest.TestCase):
    def _sum(self, raw: Dict[str, Any], paths: Any) -> Any:
        return compute_derived_fields(
            raw, {"t": {"type": "sum", "from": paths}}
        ).get("t")

    def test_sums_numeric_strings(self):
        self.assertEqual(self._sum({"a": "1.5", "b": "2"}, ["a", "b"]), 3.5)

    def test_integral_result_has_no_decimal_point(self):
        """3.0 会让上下文出现「3.0 元」这种别扭表述。"""
        self.assertEqual(self._sum({"a": "1", "b": "2"}, ["a", "b"]), 3)

    def test_partial_data_sums_what_exists(self):
        self.assertEqual(self._sum({"a": "1.5", "b": ""}, ["a", "b"]), 1.5)

    def test_all_missing_yields_nothing_not_zero(self):
        """全取不到必须不产出，而不是 0 —— 否则「没数据」会被分档当成真的 0，
        天津规则 3 会把用户错误路由到场景一（推错产品）。"""
        self.assertIsNone(self._sum({"a": "", "b": None}, ["a", "b"]))
        self.assertIsNone(self._sum({}, ["nope1", "nope2"]))

    def test_zero_is_real_data(self):
        self.assertEqual(self._sum({"a": "0", "b": "0"}, ["a", "b"]), 0)

    def test_single_path_string_accepted(self):
        self.assertEqual(self._sum({"a": "7"}, "a"), 7)


class TestBucket(unittest.TestCase):
    def _bucket(self, raw: Dict[str, Any], spec: Dict[str, Any]) -> Any:
        return compute_derived_fields(raw, {"b": dict(spec, type="bucket")}).get("b")

    def test_threshold_first_match_wins(self):
        spec = {"from": "v", "rules": [
            {"lt": 1, "value": "小"}, {"lt": 10, "value": "中"}, {"value": "大"},
        ]}
        self.assertEqual(self._bucket({"v": "0.5"}, spec), "小")
        self.assertEqual(self._bucket({"v": "5"}, spec), "中")
        self.assertEqual(self._bucket({"v": "50"}, spec), "大")

    def test_boundary_uses_lt_not_lte(self):
        spec = {"from": "v", "rules": [{"lt": 1, "value": "小"}, {"value": "大"}]}
        self.assertEqual(self._bucket({"v": "1"}, spec), "大")
        self.assertEqual(self._bucket({"v": "0.99"}, spec), "小")

    def test_all_comparators(self):
        for op, ok, ng in (("lt", "1", "3"), ("lte", "2", "3"),
                           ("gt", "3", "1"), ("gte", "2", "1"),
                           ("eq", "2", "3"), ("ne", "3", "2")):
            spec = {"from": "v", "rules": [{op: 2, "value": "hit"}, {"value": "miss"}]}
            self.assertEqual(self._bucket({"v": ok}, spec), "hit", f"{op} 应命中 {ok}")
            self.assertEqual(self._bucket({"v": ng}, spec), "miss", f"{op} 不该命中 {ng}")

    def test_blank_from_produces_nothing_not_default(self):
        """声明了 from 却取不到值 → 整个分档不产出，**不落兜底档**。

        落兜底等于没有依据就选了一档；天津场景里那就是「没数据也推产品」。
        """
        spec = {"from": "v", "rules": [{"lt": 1, "value": "小"}, {"value": "兜底"}]}
        self.assertIsNone(self._bucket({"v": ""}, spec))
        self.assertIsNone(self._bucket({}, spec))

    def test_when_rules_still_reach_default_without_from(self):
        """纯 when 写法不声明 from，兜底档正常生效（否则「无提示」没法表达）。"""
        spec = {"rules": [{"when": {"v": {"eq": 0}}, "value": "命中"}, {"value": "兜底"}]}
        self.assertEqual(self._bucket({"v": "5"}, spec), "兜底")
        self.assertEqual(self._bucket({}, spec), "兜底")

    def test_multi_field_when_requires_all(self):
        spec = {"rules": [
            {"when": {"fee": {"ne": 0}, "vol": {"eq": 0}}, "value": "异常"},
            {"value": "正常"},
        ]}
        self.assertEqual(self._bucket({"fee": "2", "vol": "0"}, spec), "异常")
        self.assertEqual(self._bucket({"fee": "2", "vol": "5"}, spec), "正常")
        self.assertEqual(self._bucket({"fee": "0", "vol": "0"}, spec), "正常")
        # 费用取不到 → 不该报异常（空 ≠「非零」）
        self.assertEqual(self._bucket({"fee": "", "vol": "0"}, spec), "正常")

    def test_empty_default_value_produces_no_field(self):
        """兜底档写 "" 表示「不追加该提示」，字段不产出、上下文不出现空行。"""
        out = compute_derived_fields({"v": "5"}, {"tip": {
            "type": "bucket", "rules": [{"when": {"v": {"eq": 0}}, "value": "提示"},
                                        {"value": ""}],
        }})
        self.assertNotIn("tip", out)

    def test_string_equality_on_categorical_value(self):
        spec = {"from": "biz", "rules": [
            {"eq": "自有业务", "value": "A"}, {"value": "B"},
        ]}
        self.assertEqual(self._bucket({"biz": "自有业务"}, spec), "A")
        self.assertEqual(self._bucket({"biz": "第三方"}, spec), "B")


class TestEvaluationOrderAndRobustness(unittest.TestCase):
    def test_later_field_references_earlier_derived(self):
        out = compute_derived_fields(
            {"arr": [{"k": "0", "fee": "0.4"}]},
            {
                "cur": {"type": "array_find", "from": "arr", "where": {"k": "0"}},
                "total": {"type": "sum", "from": ["cur.fee"]},
                "scene": {"type": "bucket", "from": "total",
                          "rules": [{"lt": 1, "value": "场景一"}, {"value": "场景二"}]},
            },
        )
        self.assertEqual(out["total"], 0.4)
        self.assertEqual(out["scene"], "场景一")

    def test_forward_reference_yields_nothing(self):
        """引用还没算出来的名字取不到值，不该抛异常。"""
        out = compute_derived_fields(
            {"a": "1"},
            {"total": {"type": "sum", "from": ["later"]},
             "later": {"type": "sum", "from": ["a"]}},
        )
        self.assertNotIn("total", out)
        self.assertEqual(out["later"], 1)

    def test_unknown_type_skipped_without_breaking_others(self):
        out = compute_derived_fields(
            {"a": "1"},
            {"bad": {"type": "regex_extract", "from": "a"},
             "good": {"type": "sum", "from": ["a"]}},
        )
        self.assertNotIn("bad", out)
        self.assertEqual(out["good"], 1)

    def test_malformed_config_tolerated(self):
        for cfg in (None, {}, [], "x", {"a": "not a dict"}, {"_x": {"type": "sum"}}):
            self.assertEqual(compute_derived_fields({"a": "1"}, cfg), {})


class TestTianjinEndToEnd(unittest.TestCase):
    """天津报文经 DataStep 直传透传链路后，派生字段应可直接填槽。"""

    def setUp(self):
        self.ei = ma.parse(_tj_payload()).extra_info
        self.out = self._run({
            "source_type": "direct", "direct_mode": "passthrough",
            ma.REQUEST_VARIANT_KEY: ma.VARIANT_MARKETING_ASSISTANT,
            "passthrough_fields": ["userinfo", "products", "userinfo_json"],
            "derived_fields": copy.deepcopy(TJ_DERIVED),
        })
        self.pt = self.out["passthrough"]

    def _run(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        ctx = FlowContext(
            phone="13500006367", intent="营销活动", province="tianjin",
            extra_info=self.ei,
        )
        return asyncio.run(DataStep("tianjin")._call_one("cc", cfg, ctx))

    def test_account_period_selected_from_array(self):
        """按 timeType 选出当月/上月两条账期 —— 原来 userinfo_json 只能整包 JSON 转储。"""
        self.assertEqual(self.pt["当月资源"]["over_flow_fee"], "1.5")
        self.assertEqual(self.pt["当月资源"]["user_consume"], "58")
        self.assertEqual(self.pt["上月资源"]["over_flow_fee"], "0.2")

    def test_scene_routing_is_deterministic(self):
        """1.5 + 2 = 3.5 ≥ 1 → 场景二。两个场景推的是不同产品，必须确定性计算。"""
        self.assertEqual(self.pt["超套费用合计"], 3.5)
        self.assertEqual(self.pt["推荐场景"], "场景二")

    def test_scene_one_when_total_below_one(self):
        payload = _tj_payload()
        cur = payload["params"]["inputs"]["userinfo_json"][0]
        cur["over_flow_fee"], cur["over_voice_fee"] = "0.3", "0.4"
        self.ei = ma.parse(payload).extra_info
        pt = self._run({
            "source_type": "direct", "direct_mode": "passthrough",
            ma.REQUEST_VARIANT_KEY: ma.VARIANT_MARKETING_ASSISTANT,
            "derived_fields": copy.deepcopy(TJ_DERIVED),
        })["passthrough"]
        self.assertEqual(pt["超套费用合计"], 0.7)
        self.assertEqual(pt["推荐场景"], "场景一")

    def test_zero_overage_with_nonzero_fee_triggers_hint(self):
        """规则 2：通话费 2 元但超套分钟为 0 → 追加坐席提示。

        这条依赖 0 被当成真实数据（而非空值），也依赖 slot_fallback 新默认口径
        把 0 照实填进话术 —— 旧的删句口径下这句会被整句删掉，规则 2 无从触发。
        """
        self.assertEqual(self.pt["账单解释提示"], "请结合账单与资源使用情况为客户解释")

    def test_hint_absent_when_data_consistent(self):
        payload = _tj_payload()
        payload["params"]["inputs"]["userinfo_json"][0].update(
            {"over_flow": "3", "over_flow_fee": "1.5",
             "over_voice": "20", "over_voice_fee": "2"},
        )
        self.ei = ma.parse(payload).extra_info
        pt = self._run({
            "source_type": "direct", "direct_mode": "passthrough",
            ma.REQUEST_VARIANT_KEY: ma.VARIANT_MARKETING_ASSISTANT,
            "derived_fields": copy.deepcopy(TJ_DERIVED),
        })["passthrough"]
        self.assertNotIn("账单解释提示", pt)

    def test_all_periods_empty_yields_no_scene(self):
        """整月资源全空时不产出场景 —— 宁可让模板落空，也不要瞎推产品。"""
        payload = _tj_payload()
        for row in payload["params"]["inputs"]["userinfo_json"]:
            row.update({"over_flow_fee": "", "over_voice_fee": ""})
        self.ei = ma.parse(payload).extra_info
        pt = self._run({
            "source_type": "direct", "direct_mode": "passthrough",
            ma.REQUEST_VARIANT_KEY: ma.VARIANT_MARKETING_ASSISTANT,
            "derived_fields": copy.deepcopy(TJ_DERIVED),
        })["passthrough"]
        self.assertNotIn("超套费用合计", pt)
        self.assertNotIn("推荐场景", pt)

    def test_products_channel_unaffected(self):
        """派生字段不该干扰既有的多产品链路。"""
        self.assertEqual(len(self.out["resources"]["recommended_packages"]), 1)
        self.assertEqual(len(self.pt["products"]), 1)

    def test_derived_absent_when_not_configured(self):
        """没配 derived_fields 的节点行为完全不变（已上线省份零影响）。"""
        pt = self._run({
            "source_type": "direct", "direct_mode": "passthrough",
            ma.REQUEST_VARIANT_KEY: ma.VARIANT_MARKETING_ASSISTANT,
        })["passthrough"]
        self.assertNotIn("当月资源", pt)
        self.assertIn("userinfo_json", pt)

    def test_derived_cannot_shadow_standard_domain(self):
        """派生名撞标准域时跳过：标准域只走 resources 通道，否则产品链路会被污染。"""
        pt = self._run({
            "source_type": "direct", "direct_mode": "passthrough",
            ma.REQUEST_VARIANT_KEY: ma.VARIANT_MARKETING_ASSISTANT,
            "derived_fields": {"recommended_packages": {
                "type": "array_find", "from": "userinfo_json", "where": {"timeType": "0"},
            }},
        })["passthrough"]
        self.assertNotIn("recommended_packages", pt)


class TestSavePathPreservesDerivedFields(unittest.TestCase):
    """保存守护会就地重写直传节点，不能把派生字段顺手清掉。"""

    def test_clean_direct_node_keeps_derived_fields(self):
        from routers.management import _clean_direct_node_for_save
        node = {
            "source_type": "direct", "direct_mode": "passthrough",
            "mock_response": _tj_payload(),
            "passthrough_fields": ["userinfo", "userinfo_json", "products"],
            "derived_fields": copy.deepcopy(TJ_DERIVED),
        }
        _clean_direct_node_for_save(node)
        self.assertEqual(node["derived_fields"], TJ_DERIVED)

    def test_derived_field_names_need_no_sample_presence(self):
        """派生名不是入参字段，不该被 passthrough_fields 清理逻辑波及。"""
        from routers.management import _clean_direct_node_for_save
        node = {
            "source_type": "direct", "direct_mode": "passthrough",
            "mock_response": _tj_payload(),
            "passthrough_fields": ["userinfo_json"],
            "derived_fields": {"当月资源": {
                "type": "array_find", "from": "userinfo_json", "where": {"timeType": "0"},
            }},
        }
        _clean_direct_node_for_save(node)
        self.assertIn("当月资源", node["derived_fields"])
        self.assertEqual(node["passthrough_fields"], ["userinfo_json"])


class TestDerivedFieldsInPrompt(unittest.TestCase):
    """派生值要能真正填进话术：dict 型派生字段需支持 {名[子键]} 精确引用。"""

    def _build(self, template_text: str) -> str:
        from engine.prompt_builder import build_prompt
        from plugins.package_diff import PackageDiff
        ctx = FlowContext(phone="1", intent="营销活动", province="tianjin")
        ctx.passthrough_context.update(compute_derived_fields(
            ma.parse(_tj_payload()).extra_info, copy.deepcopy(TJ_DERIVED),
        ))
        return build_prompt(
            user_prompt_tpl="", template_text=template_text, ctx=ctx, pkg={},
            diff=PackageDiff(ctx.current_package, {}), linked_vars=[],
        )

    def test_subfield_reference_resolves(self):
        """array_find 返回 dict 后，{当月资源[over_flow_fee]} 可取到值 ——
        原来顶层 list 不会注册为子字段根，这类占位符一律落空。"""
        prompt = self._build("您本月流量超出{当月资源[over_flow]}，超套费用{当月资源[over_flow_fee]}元。")
        self.assertIn("{当月资源[over_flow_fee]}：1.5", prompt)
        self.assertIn("{当月资源[over_flow]}：3", prompt)

    def test_scalar_derived_injected_as_fact(self):
        prompt = self._build("请按{推荐场景}输出。")
        self.assertIn("{推荐场景}：场景二", prompt)
        self.assertIn("{超套费用合计}：3.5", prompt)

    def test_zero_overage_shown_not_dropped(self):
        """超套分钟 0 必须照实出现在上下文，配合坐席提示才说得通。"""
        prompt = self._build("分钟超出{当月资源[over_voice]}分钟。")
        self.assertIn("{当月资源[over_voice]}：0", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
