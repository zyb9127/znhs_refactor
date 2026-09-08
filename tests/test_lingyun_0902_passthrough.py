"""
test_lingyun_0902_passthrough — 灵运平台交叉营销接口规范-new 0902 全字段透传验证

依据文档 ``docs/交叉营销接口/灵运平台交叉营销接口规范-new 0902.docx``（入参 JSON 骨架已
提取为 ``docs/交叉营销接口/本地测试/lingyun_0902_payload.json``，纯本地、不触网）验证：

 1. 报文识别与 servNumber 兜底：0902 报文被识别为营销助手统一接口；``servNumber``
    按 ``_PHONE_PATHS``（顶层 → userinfo.userExtra → userinfo）兜底取到；
 2. 全字段透传：``build_extra_info`` 产物中原样包含
    ``products[].product_attr``（嵌套 Map 不改结构）、三个新活动数组
    （ordered_activity_list / cancel_activity_3m_list / end_activity_6m_list）、
    ``userinfo_json`` 天津动参字段（over_flow/over_voice/over_flow_fee/over_voice_fee 等）、
    ``products`` 价格/流量/描述字段（price/discount_price/Cmn_flow/voice/direct_flow/
    gift_cmn_flow/product_desc/broadband 等）；
    网关元数据（``_META_KEYS``：sequenceNo/servNumber/provinceCode/callId/staffId/
    staffNo/touchNumber）不进话术上下文；
 3. 回调结构：``build_callback_value`` 输出与文档《交叉营销结果获取接口》字段对齐
    （result 项：activityId/productId/words/rank/activityType/aiPitchMarketingDesc/
    aiRetentionMarketingDesc/aiRecommendReason/aiRecommendScore）；
 4. product_attr 嵌套子字段占位符：``_SUBFIELD_TOKEN_RE`` 解析 + ``build_prompt``
    子字段取值链路；
 5. 营销标志：``is_marketable`` 对 marketingProductFlag/marketingActivityFlag
    组合的判断（两者都为真值才生成）。

运行：cd ROOT && python -m pytest tests/test_lingyun_0902_passthrough.py -q
约束：不调真实网络/ES/Redis/LLM。
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from core.context import FlowContext
from engine.prompt_builder import (
    _SUBFIELD_TOKEN_RE,
    _subfield_walk,
    build_prompt,
)
from plugins.package_diff import PackageDiff
from routers.management import _flatten_domain_subfields
from steps.script_step import _apply_slot_facts
from utils import marketing_assistant as ma

_ROOT = Path(__file__).resolve().parents[1]
_PAYLOAD_PATH = (
    _ROOT / "docs" / "交叉营销接口" / "本地测试" / "lingyun_0902_payload.json"
)


def _load_payload() -> dict:
    with open(_PAYLOAD_PATH, encoding="utf-8") as f:
        return json.load(f)


def _parse_payload() -> ma.MarketingAssistantRequest:
    req = ma.parse(_load_payload())
    assert req is not None, "0902 报文未被识别为营销助手统一接口"
    return req


def _make_ctx_and_pkg(extra_info: dict, product: dict):
    """按运行态（DataStep 直传透传 + 营销助手节点）构造最小 FlowContext 与当前推荐产品。"""
    ctx = FlowContext(phone="15012340511", intent="语音共享", province="河南")
    ctx.recommended_packages = [p for p in extra_info.get("products", []) if isinstance(p, dict)]
    ctx.extra_info = extra_info
    return ctx, product


class Test0902PayloadDetection(unittest.TestCase):
    """报文识别 + servNumber 按 _PHONE_PATHS 兜底。"""

    def test_payload_detected_as_marketing_assistant(self):
        payload = _load_payload()
        self.assertTrue(ma.is_marketing_assistant_payload(payload))
        req = ma.parse(payload)
        self.assertIsNotNone(req)
        self.assertEqual(req.system_id, "NGRC")
        self.assertTrue(req.wants_script)  # optType 含 0（营销话术）

    def test_serv_number_from_top_level(self):
        req = _parse_payload()
        self.assertEqual(req.phone, "15012340511")

    def test_serv_number_falls_back_to_user_extra(self):
        payload = _load_payload()
        inputs = payload["params"]["inputs"]
        inputs["userinfo"]["userExtra"] = {"servNumber": "15012340000"}
        inputs["servNumber"] = ""          # 顶层为空 → 兜底 userinfo.userExtra
        req = ma.parse(payload)
        self.assertIsNotNone(req)
        self.assertEqual(req.phone, "15012340000")

    def test_serv_number_falls_back_to_userinfo(self):
        payload = _load_payload()
        inputs = payload["params"]["inputs"]
        inputs["userinfo"]["servNumber"] = "15012341111"
        inputs["servNumber"] = ""          # 顶层为空且无 userExtra → 兜底 userinfo
        req = ma.parse(payload)
        self.assertIsNotNone(req)
        self.assertEqual(req.phone, "15012341111")


class Test0902ExtraInfoPassthrough(unittest.TestCase):
    """build_extra_info：0902 全部业务字段原名原样透传，网关元数据剥离。"""

    @classmethod
    def setUpClass(cls):
        cls.ei = _parse_payload().extra_info

    def test_product_attr_nested_map_kept_as_is(self):
        products = self.ei["products"]
        self.assertEqual(len(products), 2)
        attr = products[0]["product_attr"]
        # 嵌套 Map 原样保留（不被拍平/改名/丢弃）
        self.assertEqual(
            attr,
            {"voiceShareGift": "200分钟", "giftBusiMcdsName": "亲情网", "discountRate": "0.5"},
        )

    def test_three_new_activity_lists_passthrough(self):
        self.assertEqual(
            self.ei["ordered_activity_list"],
            [{"package_id": "PKG_ORD_001", "package_name": "视频彩铃体验包"}],
        )
        self.assertEqual(
            self.ei["cancel_activity_3m_list"],
            [{"package_id": "PKG_CAN_001", "package_name": "流量翻倍活动"}],
        )
        self.assertEqual(
            self.ei["end_activity_6m_list"],
            [{"package_id": "PKG_END_001", "package_name": "5G会员权益包"}],
        )

    def test_userinfo_json_tianjin_fields_passthrough(self):
        uj = self.ei["userinfo_json"]
        self.assertIsInstance(uj, list)
        cur = next(item for item in uj if item["timeType"] == "0")
        for key in ("total_flow", "used_flow", "remain_flow", "over_flow",
                    "total_cmn_flow", "used_cmn_flow", "remain_cmn_flow", "over_cmn_flow",
                    "total_voice", "used_voice", "remain_voice", "over_voice",
                    "user_consume", "over_flow_fee", "over_voice_fee"):
            self.assertIn(key, cur, f"userinfo_json 缺字段 {key}")
        self.assertEqual(cur["over_flow"], "2")
        self.assertEqual(cur["over_voice"], "20")
        self.assertEqual(cur["over_flow_fee"], "6")
        self.assertEqual(cur["over_voice_fee"], "3")

    def test_products_price_flow_desc_fields_passthrough(self):
        p = self.ei["products"][1]
        for key in ("price", "discount_price", "Cmn_flow", "voice", "direct_flow",
                    "gift_cmn_flow", "product_desc", "broadband", "business_type",
                    "productId", "productName", "activityTypeCode", "activityTypeName",
                    "marketingProductFlag", "marketingActivityFlag", "activityId"):
            self.assertIn(key, p, f"products[] 缺字段 {key}")
        self.assertEqual(p["price"], "20")
        self.assertEqual(p["discount_price"], "15")
        self.assertEqual(p["Cmn_flow"], "20")

    def test_userinfo_new_fields_passthrough(self):
        u = self.ei["userinfo"]
        self.assertEqual(u["currBroadband"], "300M")
        self.assertEqual(u["is_sub_card"], "0")
        self.assertEqual(u["currPrice"], "59")

    def test_gateway_meta_keys_not_in_extra_info(self):
        for key in ma._META_KEYS:
            self.assertNotIn(key, self.ei, f"网关元数据 {key} 不应进入话术上下文")
        # 任务点名的几个元数据字段再显式核一遍
        for key in ("touchNumber", "staffId", "staffNo", "sequenceNo", "servNumber",
                    "provinceCode", "callId"):
            self.assertNotIn(key, self.ei)

    def test_no_derived_or_renamed_keys(self):
        """透传不改名、不派生：extra_info 顶层键 = inputs 业务键全集。"""
        inputs = _load_payload()["params"]["inputs"]
        expected = {k for k in inputs if k not in ma._META_KEYS}
        self.assertEqual(set(self.ei.keys()), expected)


class Test0902CallbackValue(unittest.TestCase):
    """build_callback_value 输出结构与文档回调字段完全对齐。"""

    # 文档《交叉营销结果获取接口》result 项字段全集
    _RESULT_KEYS = {
        "activityId", "productId", "words", "rank", "activityType",
        "aiPitchMarketingDesc", "aiRetentionMarketingDesc",
        "aiRecommendReason", "aiRecommendScore",
    }

    def _results(self):
        return [
            {"product_id": "jtppv.2021999900002456", "marketing_text": "推荐话术A"},
            {"product_id": "jtppv.2021999900002456", "marketing_text": "切入话术A",
             "stage": "切入"},
            {"product_id": "jtppv.2021999900002456", "marketing_text": "挽留话术A",
             "stage": "挽留"},
            {"product_id": "jtppv.2021999900002457", "marketing_text": "推荐话术B"},
        ]

    def test_callback_structure_matches_doc(self):
        req = _parse_payload()
        value = ma.build_callback_value(
            req, self._results(), pitch_stage="切入", retention_stage="挽留",
        )
        for key in ("sequenceNo", "servNumber", "callId", "optType",
                    "touchNumber", "result", "recommendResult"):
            self.assertIn(key, value, f"回调 value 缺顶层字段 {key}")
        self.assertEqual(value["sequenceNo"], "bb45ccc4-de6c-4d9b-b92f-e27e72ac9e20")
        self.assertEqual(value["servNumber"], "15012340511")
        self.assertEqual(value["touchNumber"], "TOUCH_20260902_0001")
        self.assertIsInstance(value["result"], list)
        self.assertIsInstance(value["recommendResult"], list)

        item0 = value["result"][0]
        self.assertEqual(set(item0.keys()), self._RESULT_KEYS)
        self.assertEqual(item0["productId"], "jtppv.2021999900002456")
        self.assertEqual(item0["activityId"], "20230424511581")
        self.assertEqual(item0["activityType"], "YYGX")   # 取自入参 activityTypeCode
        self.assertEqual(item0["words"], "推荐话术A")
        self.assertEqual(item0["aiPitchMarketingDesc"], "切入话术A")
        self.assertEqual(item0["aiRetentionMarketingDesc"], "挽留话术A")
        self.assertEqual(item0["rank"], "1")              # 密集排序 1..N
        # 本服务不产出推荐理由/评分：留空不编造（对齐 build_callback_value 文档串）
        self.assertEqual(item0["aiRecommendReason"], "")
        self.assertEqual(item0["aiRecommendScore"], "")

        item1 = value["result"][1]
        self.assertEqual(item1["productId"], "jtppv.2021999900002457")
        self.assertEqual(item1["words"], "推荐话术B")
        self.assertEqual(item1["aiPitchMarketingDesc"], "")
        self.assertEqual(item1["aiRetentionMarketingDesc"], "")
        self.assertEqual(item1["rank"], "2")

    def test_products_without_script_are_dropped(self):
        """未生成话术的产品不出现在回调 result 里。"""
        req = _parse_payload()
        value = ma.build_callback_value(
            req, [{"product_id": "jtppv.2021999900002456", "marketing_text": "推荐话术A"}],
        )
        self.assertEqual(len(value["result"]), 1)
        self.assertEqual(value["result"][0]["productId"], "jtppv.2021999900002456")


class Test0902ProductAttrSubfieldPlaceholder(unittest.TestCase):
    """product_attr 嵌套子字段占位符：regex 解析 + build_prompt 取值链路。

    结论速览（均为对现行代码的实证结果）：
    - ``_SUBFIELD_TOKEN_RE`` 能解析 ``{recommended_packages[product_attr][voiceShareGift]}``
      这类两级嵌套 token；
    - ``_subfield_walk`` 本身支持下钻 list 根（在数组成员 dict 中按键搜索）；
    - build_prompt 的子字段根注册表（``_subfield_roots``）登记的是单数 ``recommended_package``
      （= 当前正在生成话术的那条产品）以及 extra_info 顶层 dict 字段，因此运行态可解析的写法是
      ``{recommended_package[product_attr][voiceShareGift]}`` / ``{pkg_brief[product_attr][voiceShareGift]}``
      / ``{extra_info[products][product_attr][voiceShareGift]}``；
    - ⚠️ 复数根 ``{recommended_packages[product_attr][voiceShareGift]}`` 当前**不可解析**
      （root 未注册，落入 missing_slots）——见 test_plural_root_token_currently_unresolved，
      这是对"0902 全字段可透传"结论的一个真实边界，已在交付报告中单独说明。
    """

    TOKEN = "recommended_package[product_attr][voiceShareGift]"
    TOKEN_PKG_BRIEF = "pkg_brief[product_attr][voiceShareGift]"
    TOKEN_EXTRA_INFO = "extra_info[products][product_attr][voiceShareGift]"
    TOKEN_PLURAL = "recommended_packages[product_attr][voiceShareGift]"

    @classmethod
    def setUpClass(cls):
        cls.req = _parse_payload()
        cls.ei = cls.req.extra_info
        cls.product = cls.ei["products"][0]   # 语音共享优惠包（含 product_attr）

    def _build(self, template_text: str):
        ctx, pkg = _make_ctx_and_pkg(self.ei, self.product)
        facts, parts = {}, {}
        build_prompt(
            "", template_text, ctx, pkg, PackageDiff({}, pkg),
            slot_facts_out=facts, parts_out=parts,
        )
        return facts, parts

    def test_token_regex_parses_nested_subfield(self):
        for token in (self.TOKEN, self.TOKEN_PLURAL):
            m = _SUBFIELD_TOKEN_RE.fullmatch("{" + token + "}")
            self.assertIsNotNone(m, f"regex 未解析 token {token}")
            self.assertEqual(m.group(2), "[product_attr][voiceShareGift]")

    def test_subfield_walk_supports_list_root(self):
        """取值机制本身支持 list 根：在数组成员 dict 中按键搜索后逐级下钻。"""
        val = _subfield_walk(self.ei["products"], ["product_attr", "voiceShareGift"])
        self.assertEqual(val, "200分钟")

    def test_recommended_package_root_resolves_from_passthrough(self):
        """运行态推荐写法：{recommended_package[product_attr][voiceShareGift]} → 当前产品取值。"""
        facts, parts = self._build(f"可享{{{self.TOKEN}}}赠送")
        self.assertEqual(facts.get(self.TOKEN), "200分钟")
        self.assertEqual(parts["missing_slots"], [])

    def test_pkg_brief_root_resolves(self):
        facts, parts = self._build(f"可享{{{self.TOKEN_PKG_BRIEF}}}赠送")
        self.assertEqual(facts.get(self.TOKEN_PKG_BRIEF), "200分钟")
        self.assertEqual(parts["missing_slots"], [])

    def test_extra_info_root_resolves_from_passthrough_payload(self):
        """从透传后的 extra_info 整包按 products 列表路径取值。"""
        facts, parts = self._build(f"可享{{{self.TOKEN_EXTRA_INFO}}}赠送")
        self.assertEqual(facts.get(self.TOKEN_EXTRA_INFO), "200分钟")
        self.assertEqual(parts["missing_slots"], [])

    def test_slot_facts_fill_template_deterministically(self):
        """确定性填槽（ScriptStep 后处理同一条路径）：占位符被 product_attr 子字段值替换。"""
        template = f"尊敬的客户，可享{{{self.TOKEN}}}赠送。"
        facts, _ = self._build(template)
        filled = _apply_slot_facts(template, facts)
        self.assertEqual(filled, "尊敬的客户，可享200分钟赠送。")

    def test_plural_root_token_currently_unresolved(self):
        """⚠️ 已知边界：复数根 recommended_packages 未在 _subfield_roots 注册。

        现行 build_prompt 只登记单数 ``recommended_package``（当前产品）与 extra_info 顶层
        dict 字段为子字段根；``recommended_packages``（标准域列表）不在其中，故该写法落入
        missing_slots、槽位取不到值。此处固化当前行为作见证：若后续把
        ``recommended_packages`` 注册为子字段根（指向当前产品或列表），本测试应同步改为
        断言可解析。
        """
        facts, parts = self._build(f"可享{{{self.TOKEN_PLURAL}}}赠送")
        self.assertNotIn(self.TOKEN_PLURAL, facts)
        self.assertIn(self.TOKEN_PLURAL, parts["missing_slots"])

    def test_flatten_domain_subfields_expands_product_attr(self):
        """调色板递归展开：product_attr.voiceShareGift 子键出现在子字段列表里。"""
        subs = _flatten_domain_subfields("recommended_packages", self.product)
        by_path = {s["path"]: s for s in subs}
        self.assertIn("product_attr.voiceShareGift", by_path)
        self.assertEqual(
            by_path["product_attr.voiceShareGift"]["token"],
            "recommended_packages[product_attr][voiceShareGift]",
        )
        self.assertEqual(by_path["product_attr.voiceShareGift"]["sample"], "200分钟")
        # 其余河南自定义子键同样展开
        self.assertIn("product_attr.giftBusiMcdsName", by_path)
        self.assertIn("product_attr.discountRate", by_path)


class Test0902MarketableFlag(unittest.TestCase):
    """is_marketable：marketingProductFlag / marketingActivityFlag 两个都为真值才生成。"""

    def _prod(self, product_flag=None, activity_flag=None):
        p = {"productId": "P1", "productName": "测试产品"}
        if product_flag is not None:
            p["marketingProductFlag"] = product_flag
        if activity_flag is not None:
            p["marketingActivityFlag"] = activity_flag
        return p

    def test_both_flags_truthy_is_marketable(self):
        for pf in ("1", "true", "yes"):
            self.assertTrue(ma.is_marketable(self._prod(pf, "1")), f"productFlag={pf}")

    def test_either_flag_zero_not_marketable(self):
        self.assertFalse(ma.is_marketable(self._prod("1", "0")))
        self.assertFalse(ma.is_marketable(self._prod("0", "1")))
        self.assertFalse(ma.is_marketable(self._prod("0", "0")))

    def test_missing_or_blank_flag_not_marketable(self):
        self.assertFalse(ma.is_marketable(self._prod("1")))          # activityFlag 缺失
        self.assertFalse(ma.is_marketable(self._prod(None, "1")))    # productFlag 缺失
        self.assertFalse(ma.is_marketable(self._prod("", "1")))      # 空字符串
        self.assertFalse(ma.is_marketable(self._prod("2", "1")))     # 非真值

    def test_payload_products_split(self):
        """0902 样例：产品1 双 1 放行；产品2 activityFlag=0 被挡。"""
        keep, skip = ma.split_marketable(self.req_products())
        self.assertEqual([p["productId"] for p in keep], ["jtppv.2021999900002456"])
        self.assertEqual([p["productId"] for p in skip], ["jtppv.2021999900002457"])

    @staticmethod
    def req_products():
        return _parse_payload().extra_info["products"]


if __name__ == "__main__":
    unittest.main()
