"""
test_marketing_assistant_0902 — 《灵运平台交叉营销接口规范-new 0902.docx》全字段适配验证

目的：证明 0902 版接口文档相对已实现能力"差异很小、后端零改造"——现有营销助手适配层是
「零改名、全量透传」架构，0902 新增/正式化的字段无需改解析代码即自动可用。逐项验证：

A. 解析 & 透传（TestParseAndPassthrough）
   - 网关元数据（servNumber/provinceCode/touchNumber/staffId/staffNo/callId/sequenceNo）
     被剥出 extra_info，仅挂在 req 上；
   - inputs 下业务对象按原名原层级进 extra_info：products / userinfo / userinfo_json /
     三个活动列表；
   - 0902 新增字段随原对象透传：products.product_attr（嵌套 Map）、products.broadband、
     userinfo.currBroadband / is_sub_card、userinfo_json 超套字段（over_flow/over_voice/
     over_flow_fee/over_voice_fee）、ordered_activity_list / cancel_activity_3m_list /
     end_activity_6m_list。

B. 营销标志过滤（TestMarketableFilter）
   - 双标志=1 的产品 A 生成话术；活动标志=0 的产品 B 被挡。

C. 回调结构（TestCallbackShape）
   - build_callback_value 结果项字段与《交叉营销结果获取接口》一致：activityType 取
     activityTypeCode，words / aiPitchMarketingDesc / aiRetentionMarketingDesc 分角色。

D. 嵌套子字段占位符（TestProductAttrPlaceholder）
   - 话术模板用 {pkg_brief[product_attr][voiceShareGift]} 能取到当前产品的嵌套自定义属性
     （河南 product_attr 场景），证明 product_attr 无需后端改造即可被话术精确引用。

运行：cd ROOT && python -m pytest tests/test_marketing_assistant_0902.py -q
约束：不调真实网络/ES/Redis/LLM。
"""
from __future__ import annotations

import copy
import json
import os
import unittest
from typing import Any, Dict

from core.context import FlowContext
from plugins.package_diff import PackageDiff
from utils import marketing_assistant as ma

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "marketing_assistant_0902.json")


def _payload() -> Dict[str, Any]:
    with open(_FIXTURE, encoding="utf-8") as f:
        return json.load(f)


class TestParseAndPassthrough(unittest.TestCase):
    """A. 0902 报文解析 + 全量透传（含新增字段）。"""

    def setUp(self):
        self.req = ma.parse(_payload())
        self.assertIsNotNone(self.req, "0902 报文应被识别为营销助手报文")
        self.ei = self.req.extra_info

    def test_recognized_as_marketing_assistant(self):
        self.assertTrue(ma.is_marketing_assistant_payload(_payload()))

    def test_gateway_meta_on_request_not_in_extra_info(self):
        # 元数据挂在 req 上供回调寻址
        self.assertEqual(self.req.phone, "150****0511")
        self.assertEqual(self.req.province_code, "371")
        self.assertEqual(self.req.touch_number, "20260902000123456")
        self.assertEqual(self.req.staff_id, "HE06209")
        self.assertEqual(self.req.staff_no, "6209")
        self.assertEqual(self.req.sequence_no, "91579d34-1ea8-4635-90d7-76700281a64e")
        self.assertEqual(self.req.touch_id, "20260902000123456")
        # 且不污染话术上下文
        for meta in ("servNumber", "provinceCode", "touchNumber", "staffId",
                     "staffNo", "callId", "sequenceNo"):
            self.assertNotIn(meta, self.ei, f"{meta} 不应进 extra_info")

    def test_opt_type_wants_script(self):
        self.assertEqual(self.req.opt_types, ["0", "1", "2"])
        self.assertTrue(self.req.wants_script, "optType 含 0 → 需要生成话术")

    def test_business_objects_passthrough_by_original_name(self):
        for key in ("products", "userinfo", "userinfo_json",
                    "ordered_activity_list", "cancel_activity_3m_list",
                    "end_activity_6m_list"):
            self.assertIn(key, self.ei, f"{key} 应原名进 extra_info")

    def test_userinfo_new_fields(self):
        ui = self.ei["userinfo"]
        self.assertEqual(ui.get("currBroadband"), "500M")
        self.assertEqual(ui.get("is_sub_card"), "0")

    def test_product_attr_nested_map_survives(self):
        """0902 新增 product_attr（嵌套 Map）随 products 整块透传，不丢子键。"""
        pa = self.ei["products"][0]["product_attr"]
        self.assertEqual(pa["voiceShareGift"], "50分钟")
        self.assertEqual(pa["giftBusiMcdsName"], "视频会员")
        self.assertEqual(pa["discountRate"], "5折")

    def test_products_other_new_fields(self):
        p0 = self.ei["products"][0]
        # 价格/流量/描述等原名字段均在
        for k in ("price", "discount_price", "Cmn_flow", "voice", "direct_flow",
                  "business_type", "gift_cmn_flow", "product_desc",
                  "marketingProductFlag", "marketingActivityFlag",
                  "productName", "activityTypeCode", "activityTypeName"):
            self.assertIn(k, p0, f"products.{k} 应透传")

    def test_userinfo_json_overage_fields(self):
        """天津动参字段（超套流量/语音/费用）正式化，随 userinfo_json 透传，0 值照实保留。"""
        m0 = self.ei["userinfo_json"][0]
        self.assertEqual(m0["timeType"], "0")
        self.assertEqual(m0["over_flow"], "3")
        self.assertEqual(m0["over_voice"], "0")       # 0 照实保留
        self.assertEqual(m0["over_flow_fee"], "1.5")
        self.assertEqual(m0["over_voice_fee"], "2.0")

    def test_new_activity_arrays_passthrough(self):
        self.assertEqual(self.ei["ordered_activity_list"][0]["package_name"], "宽带提速包")
        self.assertEqual(self.ei["cancel_activity_3m_list"][0]["package_name"], "视频会员月包")
        self.assertEqual(self.ei["end_activity_6m_list"][0]["package_name"], "定向流量包")


class TestMarketableFilter(unittest.TestCase):
    """B. marketingProductFlag/marketingActivityFlag 过滤。"""

    def test_only_both_flags_true_generates(self):
        req = ma.parse(_payload())
        keep, skip = ma.split_marketable(req.products)
        keep_ids = [p["productId"] for p in keep]
        skip_ids = [p["productId"] for p in skip]
        self.assertIn("jtppv.2021999900002456", keep_ids)      # 双标志=1
        self.assertIn("jtppv.2021999900009999", skip_ids)      # 活动标志=0 被挡


class TestCallbackShape(unittest.TestCase):
    """C. 回调结构对齐《交叉营销结果获取接口》。"""

    def test_callback_value_fields(self):
        req = ma.parse(_payload())
        # 模拟话术结果：产品A 三个角色各一条（推荐/切入/挽留）
        pid = "jtppv.2021999900002456"
        recommend_results = [
            {"product_id": pid, "stage": "推荐", "marketing_text": "为您推荐19元10G流量包。"},
            {"product_id": pid, "stage": "切入", "marketing_text": "看您流量常超，切入话术。"},
            {"product_id": pid, "stage": "挽留", "marketing_text": "别走，挽留话术。"},
        ]
        val = ma.build_callback_value(
            req, recommend_results, pitch_stage="切入", retention_stage="挽留",
        )
        self.assertEqual(val["servNumber"], "150****0511")
        self.assertEqual(val["touchNumber"], "20260902000123456")
        self.assertEqual(val["sequenceNo"], req.sequence_no)
        # 产品A 应在结果里（产品B 无话术，不产出项）
        item = next(r for r in val["result"] if r["productId"] == pid)
        self.assertEqual(item["activityId"], "20230424511581")
        self.assertEqual(item["activityType"], "1")            # 取自 activityTypeCode
        self.assertEqual(item["words"], "为您推荐19元10G流量包。")
        self.assertEqual(item["aiPitchMarketingDesc"], "看您流量常超，切入话术。")
        self.assertEqual(item["aiRetentionMarketingDesc"], "别走，挽留话术。")
        self.assertEqual(item["rank"], "1")
        self.assertEqual(item["aiRecommendReason"], "")        # 本服务不编造
        self.assertEqual(item["aiRecommendScore"], "")


class TestProductAttrPlaceholder(unittest.TestCase):
    """D. product_attr 嵌套子键可被话术模板精确引用（无需后端改造）。

    子字段根登记里 pkg_brief/recommended_package = 当前推荐产品 pkg，
    故 {pkg_brief[product_attr][子键]} 逐级下钻即可取到河南自定义扩展属性。
    """

    def _build(self, template_text: str, pkg: Dict[str, Any]) -> str:
        from engine.prompt_builder import build_prompt
        ctx = FlowContext(phone="1", intent="套餐推荐", province="henan")
        return build_prompt(
            user_prompt_tpl="", template_text=template_text, ctx=ctx, pkg=pkg,
            diff=PackageDiff(ctx.current_package, {}), linked_vars=[],
        )

    def test_product_attr_subfields_resolve(self):
        pkg = _payload()["params"]["inputs"]["products"][0]  # 河南产品A（含 product_attr）
        prompt = self._build(
            "为您推荐{pkg_brief[productName]}，赠{pkg_brief[product_attr][voiceShareGift]}、"
            "连带开通{pkg_brief[product_attr][giftBusiMcdsName]}，"
            "折扣{pkg_brief[product_attr][discountRate]}。",
            pkg,
        )
        self.assertIn("{pkg_brief[product_attr][voiceShareGift]}：50分钟", prompt)
        self.assertIn("{pkg_brief[product_attr][giftBusiMcdsName]}：视频会员", prompt)
        self.assertIn("{pkg_brief[product_attr][discountRate]}：5折", prompt)
        self.assertIn("{pkg_brief[productName]}：19元10G流量包", prompt)

    def test_recommended_package_root_alias_also_works(self):
        """{recommended_package[...]} 亦登记为同一产品根，等价可用。"""
        pkg = _payload()["params"]["inputs"]["products"][0]
        prompt = self._build(
            "折扣{recommended_package[product_attr][discountRate]}。", pkg,
        )
        self.assertIn("{recommended_package[product_attr][discountRate]}：5折", prompt)


class TestPaletteExposesProductAttrSubfields(unittest.TestCase):
    """F. 调色板（get_context_vars）要把 products 里字典型扩展属性 product_attr 的下一级子键
    暴露成可插入话术模板的占位符，且 token 用运行期可解析的 {pkg_brief[父][子]} 形态。
    营销助手是 passthrough 模式（pkg_brief 被抑制），修复前这些嵌套子键在调色板里完全缺失。
    """

    @classmethod
    def setUpClass(cls):
        import asyncio
        from pathlib import Path
        from utils.skill_runtime import skill_registry
        from routers import management
        skill_registry.initialize(Path(__file__).resolve().parent.parent / "skills-runtime")
        res = asyncio.run(management.get_context_vars("henan", "套餐推荐"))
        cls._items = {it["key"]: it for it in res["data"]}

    def test_products_item_present(self):
        self.assertIn("products", self._items, "passthrough 产品大变量应在调色板")

    def test_nested_product_attr_tokens_exposed(self):
        subs = self._items["products"].get("subfields") or []
        tokens = {s["token"] for s in subs}
        self.assertIn("pkg_brief[product_attr][voiceShareGift]", tokens)
        self.assertIn("pkg_brief[product_attr][giftBusiMcdsName]", tokens)
        self.assertIn("pkg_brief[product_attr][discountRate]", tokens)

    def test_top_level_product_fields_still_bare(self):
        """顶层产品字段仍是裸叶子 token（不改既有行为）。"""
        subs = self._items["products"].get("subfields") or []
        by_path = {s["path"]: s["token"] for s in subs}
        self.assertEqual(by_path.get("productName"), "productName")
        self.assertEqual(by_path.get("discount_price"), "discount_price")


class TestHenanProductAttrEndToEnd(unittest.TestCase):
    """E. 河南技能包全链路端到端（离线，桩 LLM 回声）：
    DataStep 透传 products（含 product_attr）→ recommended_packages →
    ScriptStep 模板匹配 + build_prompt → {pkg_brief[product_attr][子键]} 取到嵌套扩展属性。

    证明 0902 的 product_attr 不需后端改造，配好技能包即可端到端被话术引用。
    """

    @classmethod
    def setUpClass(cls):
        import asyncio
        from pathlib import Path
        from services.llm_service import llm_service
        from utils.skill_runtime import skill_registry

        cls._prompts: list = []

        async def _fake_generate(prompt, *a, **kw):
            cls._prompts.append(prompt or "")
            return "为您推荐更划算的流量包，办理更省心。"

        cls._orig_generate = llm_service.generate
        llm_service.generate = _fake_generate

        runtime = Path(__file__).resolve().parent.parent / "skills-runtime"
        skill_registry.initialize(runtime)
        cls._executor = skill_registry.get_executor("henan", "套餐推荐")

        # 加载河南演示用例 payload
        case_file = runtime / "henan" / "套餐推荐" / "config" / "test_cases.json"
        cls._payload = json.loads(case_file.read_text(encoding="utf-8"))["cases"][0]["payload"]
        cls._res = asyncio.run(cls._executor.execute(cls._payload)) if cls._executor else None

    @classmethod
    def tearDownClass(cls):
        from services.llm_service import llm_service
        llm_service.generate = cls._orig_generate

    def test_executor_loaded(self):
        self.assertIsNotNone(self._executor, "河南·套餐推荐技能包应被加载")

    def test_generated_one_script_non_fallback(self):
        scripts = (self._res or {}).get("marketing_scripts") or []
        self.assertEqual(len(scripts), 1, "应逐产品生成 1 条话术")
        text = scripts[0].get("marketing_text") or scripts[0].get("words") or ""
        self.assertNotIn("回复1立即办理", text, "不应走兜底话术")

    def test_prompt_contains_product_attr_subfield_values(self):
        """全链路 Prompt 里应出现 product_attr 三个嵌套子键的真实值（证明进了话术上下文）。"""
        joined = "\n".join(self._prompts)
        self.assertTrue(joined.strip(), "应捕获到至少一条 Prompt")
        self.assertIn("50分钟", joined)       # voiceShareGift
        self.assertIn("视频会员", joined)      # giftBusiMcdsName
        self.assertIn("5折", joined)           # discountRate
        self.assertIn("19元10G流量包", joined)  # productName


if __name__ == "__main__":
    unittest.main(verbosity=2)
