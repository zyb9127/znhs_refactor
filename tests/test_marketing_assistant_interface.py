"""
test_marketing_assistant_interface — 营销助手统一接口（灵运前置交叉营销）适配测试

需求（开发设计思路 一、需要单独适配营销助手的统一接口入参）：
  1. 接口配置页透传模式下新增「营销助手统一接口」，不影响已上线「标准接口」配置；
  2. 该模式下入参能正常解析、话术模板能正常选取占位符；
  3. 话术生成后不直接返回，回调交叉营销网关 preload/cache 写缓存。

覆盖：
 1. 报文识别：灵运报文命中；标准接口 4 种写法（含山东现网 params 包裹）一律不误判
 2. 入参零改名：inputs 下业务对象一律按原名原层级进 extra_info（products / userinfo /
    userinfo.userExtra / userinfo_json），只剥 params 外壳与网关元数据（sequenceNo/staffId 等）
 3. 配置期与运行期同口径：management._normalize_direct_extra_info == 运行期归一结果，
    且标准接口三种历史写法归一行为零变化
 4. 保存守护：营销助手节点保留原始报文样例不被改写；透传字段按归一样例清理
 5. 标准请求体转换：phone/province/topN/batch_contexts 语义
 6. 省份编码：371→henan、200/020→guangdong、中文省名、已是 code 时原样
 7. 意图路由：按接口配置标记定位技能包，未标记则不处理
 8. 异步语义：主接口立即回 ack（rtnCode=0），后台生成完成后回调，回调 value 结构对齐
    《交叉营销结果获取接口》
 9. optType 不含 0（营销话术）时只收妥不生成
10. 产品字段别名：productId 可作为 product_id 回显/模板匹配取值
11. 运行期多产品：products（原名）由 DataStep 喂进标准域 recommended_packages，
    键名不变、逐产品各出一条话术，且不限并发

运行：cd ROOT && python -m pytest tests/test_marketing_assistant_interface.py -q
约束：不调真实网络/ES/Redis/LLM。
"""
from __future__ import annotations

import asyncio
import copy
import json
import unittest
from typing import Any, Dict, List

from core.context import FlowContext
from routers import cross_sell, management
from routers.management import _clean_direct_node_for_save, _normalize_direct_extra_info
from steps.data_step import DataStep
from steps.script_step import ScriptStep
from utils import marketing_assistant as ma
from utils.placeholder import dig_subfield
from utils.province_code import resolve_province

# ── 灵运前置报文样例（对齐《灵运平台交叉营销接口规范》，两个产品便于验证多产品）──
MA_PAYLOAD: Dict[str, Any] = {
    "params": {
        "systemId": "NGRC",
        "optType": "0,1,2",
        "inputs": {
            "sequenceNo": "91579d34-1ea8-4635-90d7-76700281a64e",
            "servNumber": "13800138000",
            "provinceCode": "371",
            "callId": "CALL_20230810_0001",
            "staffId": "A10086",
            "staffNo": "10086",
            "touchNumber": "TOUCH_20230810_0001",
            "userinfo": {
                "currentPackageProductName": "5G畅享融合套餐129元档",
                "currPrice": "129",
                "currDomesticTraffic": "30",
                "currDomesticVoice": "500",
                "userExtra": {"last3mFlowSaturation": "10", "last3mVoiceSaturation": "10"},
            },
            "products": [
                {
                    "activityId": "ACT_001", "productId": "PROD_001",
                    "price": "59", "discount_price": "39.58", "Cmn_flow": "20",
                    "voice": "200", "productName": "5G特惠月租卡",
                    "activityTypeCode": "TYPE_01",
                    "marketingProductFlag": "1", "marketingActivityFlag": "1",
                },
                {
                    "activityId": "ACT_002", "productId": "PROD_002",
                    "price": "30", "discount_price": "19.90", "Cmn_flow": "30",
                    "voice": "0", "productName": "30GB流量畅享包",
                    "activityTypeCode": "TYPE_02",
                    "marketingProductFlag": "1", "marketingActivityFlag": "1",
                },
            ],
            "userinfo_json": [
                {"timeType": "0", "over_cmn_flow": "1", "over_voice": "20", "user_consume": "145"},
                {"timeType": "1", "over_cmn_flow": "0", "over_voice": "0", "user_consume": "129"},
            ],
        },
    }
}


def _payload() -> Dict[str, Any]:
    return copy.deepcopy(MA_PAYLOAD)


class TestPayloadDetection(unittest.TestCase):
    """报文识别：命中营销助手报文，且绝不误判标准接口写法。"""

    def test_marketing_assistant_payload_detected(self):
        self.assertTrue(ma.is_marketing_assistant_payload(_payload()))
        # 无最外层 params 包裹也认（对端可能直接下发 envelope）
        self.assertTrue(ma.is_marketing_assistant_payload(_payload()["params"]))

    def test_standard_payloads_never_misdetected(self):
        standards: List[Any] = [
            # 对外规范标准写法
            {"phone": "1", "intent": "营销活动", "province": "guangdong",
             "extra_info": {"recommended_packages": [{"offerId": "1"}]}},
            # 山东现网：网关把标准体包在 params 里
            {"params": {"phone": "1", "intent": "套餐推荐", "province": "shandong",
                        "extra_info": {"current_package": {}}}},
            # 裸 extra_info 样例（配置页粘贴）
            {"recommended_packages": [{"offerId": "1"}], "current_package": {}},
            # inputs 存在但缺营销助手特征字段
            {"params": {"inputs": {"foo": 1}}},
            {},
            None,
        ]
        for raw in standards:
            self.assertFalse(
                ma.is_marketing_assistant_payload(raw),
                f"标准写法被误判为营销助手报文: {json.dumps(raw, ensure_ascii=False)}",
            )


class TestExtraInfoNormalization(unittest.TestCase):
    """入参零改名：业务对象名/层级完全以灵运报文原文为准，只剥传输层。"""

    def setUp(self):
        self.req = ma.parse(_payload())
        self.ei = self.req.extra_info

    def test_products_keeps_original_key(self):
        # 产品列表键名就是报文原名 products，不改写成 recommended_packages
        self.assertIn("products", self.ei)
        self.assertNotIn("recommended_packages", self.ei)
        self.assertEqual(len(self.ei["products"]), 2)
        # 产品内字段名同样一律不动
        first = self.ei["products"][0]
        for key in ("activityId", "productId", "price", "discount_price", "Cmn_flow", "productName"):
            self.assertIn(key, first)
        self.assertEqual([p["productId"] for p in self.req.products], ["PROD_001", "PROD_002"])

    def test_userinfo_keeps_original_nesting(self):
        self.assertEqual(self.ei["userinfo"]["currPrice"], "129")
        # userExtra 保持嵌在 userinfo 下（不提层），各省个性化字段按原路径勾选
        self.assertEqual(self.ei["userinfo"]["userExtra"]["last3mFlowSaturation"], "10")
        self.assertNotIn("last3mFlowSaturation", self.ei)

    def test_userinfo_json_kept_as_is(self):
        self.assertEqual(len(self.ei["userinfo_json"]), 2)      # 原始账期数组原样保留
        self.assertEqual(self.ei["userinfo_json"][0]["timeType"], "0")
        # 不派生任何别名字段（当月账期请用 {userinfo_json} 整块或按原路径勾选）
        self.assertNotIn("usage_current", self.ei)

    def test_no_derived_or_renamed_keys(self):
        """extra_info 的键集合 == inputs 的业务键集合（只少了网关元数据）。"""
        inputs = _payload()["params"]["inputs"]
        expected = {k for k in inputs if k not in (
            "sequenceNo", "servNumber", "provinceCode", "callId",
            "staffId", "staffNo", "touchNumber",
        )}
        self.assertEqual(set(self.ei), expected)

    def test_gateway_meta_not_in_script_context(self):
        for meta in ("sequenceNo", "servNumber", "provinceCode", "callId",
                     "staffId", "staffNo", "touchNumber", "systemId", "optType"):
            self.assertNotIn(meta, self.ei, f"网关元数据 {meta} 泄漏进话术上下文")
        # 元数据在解析结果上，供回调用
        self.assertEqual(self.req.phone, "13800138000")
        self.assertEqual(self.req.sequence_no, "91579d34-1ea8-4635-90d7-76700281a64e")
        self.assertEqual(self.req.touch_number, "TOUCH_20230810_0001")


class TestConfigTimeSameAsRuntime(unittest.TestCase):
    """配置期调色板/预览与运行期归一必须同口径，否则勾的字段和跑的字段不一致。"""

    def test_palette_normalization_matches_runtime(self):
        runtime = ma.parse(_payload()).extra_info
        config_time = _normalize_direct_extra_info(_payload())
        self.assertEqual(config_time, runtime)
        # 占位符按原名可选取：顶层大字段 + 子字段（含 userExtra 深一层）都能解析出来
        self.assertIn("products", config_time)
        self.assertIn("currPrice", config_time["userinfo"])
        self.assertEqual(dig_subfield(config_time, "userinfo.userExtra.last3mFlowSaturation"),
                         ("last3mFlowSaturation", "10"))
        self.assertEqual(dig_subfield(config_time, "products.productName")[0], "",
                         "列表域子字段不走扁平取值，由产品字段白名单逐条注入")

    def test_legacy_direct_sample_forms_unchanged(self):
        bare = {"uniProdGrade": "58", "recommended_packages": [{"offerId": "1"}]}
        self.assertEqual(_normalize_direct_extra_info(bare), bare)
        self.assertEqual(
            _normalize_direct_extra_info({"phone": "1", "extra_info": bare, "batch_contexts": []}),
            bare,
        )
        self.assertEqual(
            _normalize_direct_extra_info({"params": {"phone": "1", "extra_info": bare}}),
            bare,
        )

    def test_save_keeps_original_marketing_assistant_sample(self):
        node = {
            "source_type": "direct", "direct_mode": "passthrough",
            ma.REQUEST_VARIANT_KEY: ma.VARIANT_MARKETING_ASSISTANT,
            "mock_response": _payload(),
            "passthrough_fields": [
                "products", "userinfo", "userinfo.currPrice",
                "products.discount_price", "userinfo.userExtra.last3mFlowSaturation",
                "params",                     # 指向包裹层的脏项，应被清掉
                "recommended_packages",       # 改名后的键，样例里不存在 → 清掉
            ],
        }
        _clean_direct_node_for_save(node)
        # 原始报文是该节点的样例真源（测试页要按它造 preload 请求体），不得被改写
        self.assertEqual(node["mock_response"], _payload())
        kept = node["passthrough_fields"]
        for key in ("products", "userinfo", "userinfo.currPrice",
                    "products.discount_price", "userinfo.userExtra.last3mFlowSaturation"):
            self.assertIn(key, kept)
        self.assertNotIn("params", kept)
        self.assertNotIn("recommended_packages", kept)

    def test_save_still_normalizes_wrapped_standard_sample(self):
        bare = {"recommended_packages": [{"offerId": "1"}]}
        node = {
            "source_type": "direct", "direct_mode": "passthrough",
            "mock_response": {"phone": "1", "extra_info": bare},
            "passthrough_fields": ["recommended_packages"],
        }
        notes = _clean_direct_node_for_save(node)
        self.assertEqual(node["mock_response"], bare)
        self.assertTrue(any("归一" in n for n in notes))


class TestPaletteUsesOriginalNames(unittest.TestCase):
    """话术模板调色板：占位符一律用报文原名，且不出现同义的产品列表占位符。"""

    def setUp(self):
        self._orig = management.skill_registry
        node = {
            "enabled": True, "source_type": "direct", "direct_mode": "passthrough",
            ma.REQUEST_VARIANT_KEY: ma.VARIANT_MARKETING_ASSISTANT,
            "mock_response": _payload(),
            "passthrough_fields": [
                "userinfo", "userinfo.currPrice", "userinfo.userExtra.last3mFlowSaturation",
                "products", "products.productName", "products.discount_price", "userinfo_json",
            ],
        }
        management.skill_registry = _FakeRegistry({"henan:营销活动": _FakePkg({"cc": node})})

    def tearDown(self):
        management.skill_registry = self._orig

    def test_palette_chips_and_subfields(self):
        data = asyncio.run(management.get_context_vars("henan", "营销活动"))["data"]
        chips = {v["key"]: v for v in data}
        subs = {k: [s.get("token") for s in (v.get("subfields") or [])] for k, v in chips.items()}

        self.assertEqual(chips["products"]["source"], "passthrough")
        self.assertEqual(sorted(subs["products"]), ["discount_price", "productName"])
        self.assertEqual(sorted(subs["userinfo"]), ["currPrice", "last3mFlowSaturation"])
        self.assertIn("userinfo_json", chips)
        # 不出现改名/派生出来的同义占位符
        for ghost in ("recommended_packages", "pkg_brief", "usage_current", "last3mFlowSaturation"):
            self.assertNotIn(ghost, chips, f"调色板出现了非原名占位符 {ghost}")


class TestRecommendBodyAndProvince(unittest.TestCase):

    def test_to_recommend_body(self):
        req = ma.parse(_payload())
        body = ma.to_recommend_body(req, intent="营销活动", province="henan")
        self.assertEqual(body["phone"], "13800138000")
        self.assertEqual(body["province"], "henan")
        self.assertEqual(body["intent"], "营销活动")
        self.assertEqual(body["callId"], "CALL_20230810_0001")
        self.assertEqual(body["topN"], 2)                  # 按产品数，不截断
        self.assertEqual(body["batch_contexts"], [])       # 由 pipeline 兜底展开全部产品
        # extra_info 就是原名原样的 inputs 本体
        self.assertEqual(len(body["extra_info"]["products"]), 2)
        self.assertNotIn("recommended_packages", body["extra_info"])

    def test_touch_id_prefers_touch_number(self):
        # 回调唯一标识（touchNumber）：优先 inputs.touchNumber
        req = ma.parse(_payload())
        self.assertEqual(req.touch_id, "TOUCH_20230810_0001")

    def test_touch_id_falls_back_to_call_id_then_sequence_no(self):
        raw = _payload()
        raw["params"]["inputs"].pop("touchNumber")
        self.assertEqual(ma.parse(raw).touch_id, "CALL_20230810_0001")  # 退 callId
        raw["params"]["inputs"].pop("callId")
        self.assertEqual(ma.parse(raw).touch_id, "91579d34-1ea8-4635-90d7-76700281a64e")  # 再退 sequenceNo

    def test_province_code_resolution(self):
        self.assertEqual(resolve_province("371"), "henan")     # 规范示例：河南
        self.assertEqual(resolve_province("311"), "hebei")     # 规范示例：河北
        self.assertEqual(resolve_province("200"), "guangdong")  # 广东现网既有内部码
        self.assertEqual(resolve_province("020"), "guangdong")
        self.assertEqual(resolve_province("20"), "guangdong")   # 兼容去前导零写法
        self.assertEqual(resolve_province("广东"), "guangdong")
        self.assertEqual(resolve_province("beijing"), "beijing")
        self.assertEqual(resolve_province("unknown"), "unknown")


class TestCallbackValue(unittest.TestCase):
    """回调 value 结构对齐《交叉营销结果获取接口》的 data。"""

    def test_result_items(self):
        req = ma.parse(_payload())
        results = [
            {"product_id": "PROD_002", "offerId": "", "rank": 1,
             "marketing_text": "30GB流量畅享包只要19.9元"},
            {"product_id": "PROD_001", "offerId": "", "rank": 2,
             "marketing_text": "5G特惠月租卡39.58元"},
        ]
        value = ma.build_callback_value(req, results)
        self.assertEqual(value["sequenceNo"], req.sequence_no)
        self.assertEqual(value["servNumber"], "13800138000")
        self.assertEqual(len(value["result"]), 2)
        # 一个产品一项，按入参产品顺序密集排 rank
        self.assertEqual([r["productId"] for r in value["result"]], ["PROD_001", "PROD_002"])
        self.assertEqual([r["rank"] for r in value["result"]], ["1", "2"])

        first = value["result"][0]
        self.assertEqual(first["activityId"], "ACT_001")       # 活动 ID/分类编码取自入参产品
        self.assertEqual(first["activityType"], "TYPE_01")
        self.assertEqual(first["words"], "5G特惠月租卡39.58元")
        # 没配切入/挽留模板时，两个 ai* 字段留空，只回 words（营销推荐话术）
        self.assertEqual(first["aiPitchMarketingDesc"], "")
        self.assertEqual(first["aiRetentionMarketingDesc"], "")
        # 本服务不产出推荐理由/分数，留空不编造
        self.assertEqual(first["aiRecommendReason"], "")
        self.assertEqual(first["aiRecommendScore"], "")

    def test_words_pitch_retention_merged_per_product(self):
        """同一产品的推荐/切入/挽留三条话术归并到同一项的不同字段。"""
        req = ma.parse(_payload())
        results = [
            {"product_id": "PROD_001", "stage": "", "marketing_text": "推荐话术A"},
            {"product_id": "PROD_001", "stage": "切入", "marketing_text": "切入话术A"},
            {"product_id": "PROD_001", "stage": "挽留", "marketing_text": "挽留话术A"},
            {"product_id": "PROD_002", "stage": "", "marketing_text": "推荐话术B"},
            {"product_id": "PROD_002", "stage": "切入", "marketing_text": "切入话术B"},
            {"product_id": "PROD_002", "stage": "挽留", "marketing_text": "挽留话术B"},
        ]
        result = ma.build_callback_value(
            req, results, pitch_stage="切入", retention_stage="挽留",
        )["result"]
        self.assertEqual(len(result), 2)                       # 6 条话术 → 2 项
        self.assertEqual(result[0]["words"], "推荐话术A")
        self.assertEqual(result[0]["aiPitchMarketingDesc"], "切入话术A")
        self.assertEqual(result[0]["aiRetentionMarketingDesc"], "挽留话术A")
        self.assertEqual(result[1]["words"], "推荐话术B")
        self.assertEqual(result[1]["aiRetentionMarketingDesc"], "挽留话术B")

    def test_only_pitch_configured_retention_stays_empty(self):
        """只配切入环节：words + aiPitchMarketingDesc 有值，挽留留空。"""
        req = ma.parse(_payload())
        results = [
            {"product_id": "PROD_001", "stage": "", "marketing_text": "推荐话术A"},
            {"product_id": "PROD_001", "stage": "切入", "marketing_text": "切入话术A"},
        ]
        result = ma.build_callback_value(req, results, pitch_stage="切入")["result"]
        self.assertEqual(result[0]["words"], "推荐话术A")
        self.assertEqual(result[0]["aiPitchMarketingDesc"], "切入话术A")
        self.assertEqual(result[0]["aiRetentionMarketingDesc"], "")

    def test_stage_scripts_ignored_when_stage_not_configured(self):
        """没把环节名传进来时，带 stage 的话术一律算 words（不塞进 ai* 字段）。"""
        req = ma.parse(_payload())
        results = [{"product_id": "PROD_001", "stage": "挽留", "marketing_text": "话术A"}]
        result = ma.build_callback_value(req, results)["result"]
        self.assertEqual(result[0]["words"], "话术A")
        self.assertEqual(result[0]["aiPitchMarketingDesc"], "")
        self.assertEqual(result[0]["aiRetentionMarketingDesc"], "")

    def test_unattributed_scripts_align_by_position(self):
        """话术没回显 product_id（模板匹配字段配错）时按位次兜底，不整批丢空。"""
        req = ma.parse(_payload())
        results = [
            {"product_id": "", "marketing_text": "话术1"},
            {"product_id": "", "marketing_text": "话术2"},
        ]
        result = ma.build_callback_value(req, results)["result"]
        self.assertEqual([r["productId"] for r in result], ["PROD_001", "PROD_002"])
        self.assertEqual([r["words"] for r in result], ["话术1", "话术2"])

    def test_unattributed_pitch_retention_align_by_position(self):
        """product_id 全没回显时，切入/挽留也按位次兜底（此前只兜 words，两条被静默丢弃）。

        触发场景：直传节点没把 products 喂进标准域 recommended_packages，话术用「空产品」
        生成 → 三个环节的结果 product_id 都为空。
        """
        req = ma.parse(_payload())
        results = [
            {"product_id": "", "stage": "推荐", "marketing_text": "推荐话术A"},
            {"product_id": "", "stage": "推荐", "marketing_text": "推荐话术B"},
            {"product_id": "", "stage": "切入", "marketing_text": "切入话术A"},
            {"product_id": "", "stage": "切入", "marketing_text": "切入话术B"},
            {"product_id": "", "stage": "挽留", "marketing_text": "挽留话术A"},
            {"product_id": "", "stage": "挽留", "marketing_text": "挽留话术B"},
        ]
        result = ma.build_callback_value(
            req, results, pitch_stage="切入", retention_stage="挽留",
        )["result"]
        self.assertEqual(len(result), 2)
        self.assertEqual([r["words"] for r in result], ["推荐话术A", "推荐话术B"])
        self.assertEqual(
            [r["aiPitchMarketingDesc"] for r in result], ["切入话术A", "切入话术B"],
        )
        self.assertEqual(
            [r["aiRetentionMarketingDesc"] for r in result], ["挽留话术A", "挽留话术B"],
        )

    def test_products_without_script_are_dropped(self):
        """被营销标志挡掉/无技能包可路由的产品不出现在回调结果里。"""
        req = ma.parse(_payload())
        results = [{"product_id": "PROD_002", "marketing_text": "只有它有话术"}]
        result = ma.build_callback_value(req, results)["result"]
        self.assertEqual([r["productId"] for r in result], ["PROD_002"])
        self.assertEqual(result[0]["rank"], "1")

    def test_empty_results(self):
        value = ma.build_callback_value(ma.parse(_payload()), [])
        self.assertEqual(value["result"], [])


# ── 路由/意图/异步语义 ───────────────────────────────────────────

class _FakePkg:
    def __init__(self, api_nodes: Dict[str, Any], templates: List[Dict[str, Any]] = None):
        self.config = {
            "api_nodes": api_nodes,
            "biz_config": {"script_templates_v2": templates or []},
        }


class _FakeRegistry:
    def __init__(self, packages: Dict[str, _FakePkg]):
        self._packages = packages          # "province:intent" → _FakePkg

    def list_all(self):
        out = []
        for key in self._packages:
            province, intent = key.split(":", 1)
            out.append({"province": province, "intent": intent, "enabled": True})
        return out

    def get(self, province: str, intent: str):
        return self._packages.get(f"{province}:{intent}")

    def get_executor(self, province: str, intent: str):
        key = f"{province}:{intent}"
        return _FakeExecutor(intent) if key in self._packages else None


class _FakeExecutor:
    """桩执行器：按 extra_info.products（原名）× batch_contexts 回话术，不调 LLM。"""

    def __init__(self, intent: str = ""):
        self.intent = intent

    async def execute(self, body: Dict[str, Any]) -> Dict[str, Any]:
        pkgs = (body.get("extra_info") or {}).get("products") or []
        stages = [c.get("stage", "") for c in (body.get("batch_contexts") or [{}])]
        results = []
        for stage in stages:
            for i, p in enumerate(pkgs, 1):
                results.append({
                    "product_id": p.get("productId", ""), "offerId": "", "rank": i,
                    "stage": stage,
                    "marketing_text": f"{stage or '话术'}-{p.get('productName', '')}"
                                      f"{'@' + self.intent if self.intent else ''}",
                })
        return {"recommend_results": results, "other_info": None}


def _ma_node() -> Dict[str, Any]:
    return {
        "cc": {
            "enabled": True, "source_type": "direct", "direct_mode": "passthrough",
            ma.REQUEST_VARIANT_KEY: ma.VARIANT_MARKETING_ASSISTANT,
        }
    }


def _standard_node() -> Dict[str, Any]:
    return {
        "cc": {"enabled": True, "source_type": "direct", "direct_mode": "passthrough"}
    }


class _StubProvinceLogger:
    def log_request(self, *a, **kw): pass
    def log_response(self, *a, **kw): pass
    def is_test_trace(self, *a, **kw): return False


class TestIntentRoutingAndAsync(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self._orig_registry = cross_sell.skill_registry
        self._orig_logger = cross_sell.province_logger
        self._orig_push = cross_sell.push_cache
        cross_sell.province_logger = _StubProvinceLogger()
        self.pushed: List[Dict[str, Any]] = []

        async def _fake_push(**kwargs):
            self.pushed.append(kwargs)
            return True

        cross_sell.push_cache = _fake_push

    def tearDown(self):
        cross_sell.skill_registry = self._orig_registry
        cross_sell.province_logger = self._orig_logger
        cross_sell.push_cache = self._orig_push

    @staticmethod
    async def _drain():
        """等待后台生成任务跑完（主接口是 ack 即返回的异步语义）。"""
        for _ in range(50):
            if not cross_sell._PENDING:
                return
            await asyncio.gather(*list(cross_sell._PENDING), return_exceptions=True)
        raise AssertionError("后台任务未在预期内结束")

    def test_intent_resolved_by_interface_flag(self):
        cross_sell.skill_registry = _FakeRegistry({
            "henan:营销活动": _FakePkg(_ma_node()),
            "henan:套餐推荐": _FakePkg(_standard_node()),
        })
        intent, note = cross_sell.resolve_marketing_assistant_intent("henan")
        self.assertEqual(intent, "营销活动")
        self.assertIn("接口配置标记", note)

    def test_no_flagged_skill_means_not_handled(self):
        cross_sell.skill_registry = _FakeRegistry({"henan:套餐推荐": _FakePkg(_standard_node())})
        intent, _ = cross_sell.resolve_marketing_assistant_intent("henan")
        self.assertEqual(intent, "")

    def test_multiple_flags_pick_deterministic(self):
        cross_sell.skill_registry = _FakeRegistry({
            "henan:营销活动": _FakePkg(_ma_node()),
            "henan:营销推荐": _FakePkg(_ma_node()),
        })
        intent, _ = cross_sell.resolve_marketing_assistant_intent("henan")
        self.assertEqual(intent, sorted(["营销活动", "营销推荐"])[0])

    def test_intent_resolved_by_activity_name(self):
        """活动名称与意图名同名 → 精确路由（不再受"同省只能一个营销助手技能包"限制）。"""
        cross_sell.skill_registry = _FakeRegistry({
            "henan:流量包": _FakePkg(_ma_node()),
            "henan:语音包": _FakePkg(_ma_node()),
        })
        intent, note = cross_sell.resolve_intent_for_activity("henan", "语音包")
        self.assertEqual(intent, "语音包")
        self.assertIn("活动名称精确匹配", note)

    def test_activity_name_without_same_name_skill_falls_back(self):
        cross_sell.skill_registry = _FakeRegistry({"henan:营销活动": _FakePkg(_ma_node())})
        intent, note = cross_sell.resolve_intent_for_activity("henan", "流量语音包")
        self.assertEqual(intent, "营销活动")
        self.assertIn("回退", note)

    def test_same_name_skill_without_flag_falls_back(self):
        """同名技能包没勾选营销助手接口 → 不用它（它的直传节点解析不了本报文）。"""
        cross_sell.skill_registry = _FakeRegistry({
            "henan:流量包": _FakePkg(_standard_node()),
            "henan:营销活动": _FakePkg(_ma_node()),
        })
        intent, _ = cross_sell.resolve_intent_for_activity("henan", "流量包")
        self.assertEqual(intent, "营销活动")

    def test_batch_contexts_follow_configured_stages(self):
        """切入/挽留话术只在技能包真配了对应环节模板时才各多生成一条（words 始终有）。"""
        cross_sell.skill_registry = _FakeRegistry({
            "henan:三环节": _FakePkg(_ma_node(), [
                {"stage": "推荐", "status": "online"},
                {"stage": "切入", "status": "online"},
                {"stage": "挽留", "status": "online"},
            ]),
            "henan:两环节": _FakePkg(_ma_node(), [
                {"stage": "切入", "status": "online"},
                {"stage": "挽留", "status": "online"},
            ]),
            "henan:只有切入": _FakePkg(_ma_node(), [{"stage": "切入", "status": "online"}]),
            "henan:只有推荐": _FakePkg(_ma_node(), [{"stage": "推荐", "status": "online"}]),
            "henan:没配环节": _FakePkg(_ma_node(), [{"stage": "", "status": "online"}]),
        })
        # 三环节：words 走「推荐」环节模板 + 切入 + 挽留
        three, pitch, retention = cross_sell.build_batch_contexts("henan", "三环节")
        self.assertEqual(three, [{"stage": "推荐"}, {"stage": "切入"}, {"stage": "挽留"}])
        self.assertEqual((pitch, retention), ("切入", "挽留"))

        # 没配「推荐」环节时，words 回退到无环节常规话术
        both, pitch, retention = cross_sell.build_batch_contexts("henan", "两环节")
        self.assertEqual(both, [{"stage": ""}, {"stage": "切入"}, {"stage": "挽留"}])
        self.assertEqual((pitch, retention), ("切入", "挽留"))

        pitch_only, pitch, retention = cross_sell.build_batch_contexts("henan", "只有切入")
        self.assertEqual(pitch_only, [{"stage": ""}, {"stage": "切入"}])
        self.assertEqual((pitch, retention), ("切入", ""))

        # 只配「推荐」环节：words 走该环节模板，切入/挽留留空
        rec_only, pitch, retention = cross_sell.build_batch_contexts("henan", "只有推荐")
        self.assertEqual(rec_only, [{"stage": "推荐"}])
        self.assertEqual((pitch, retention), ("", ""))

        # 三个环节都没配：完全维持既有行为（pipeline 自动构造一条空条目 → 只出 words）
        self.assertEqual(cross_sell.build_batch_contexts("henan", "没配环节"), ([], "", ""))

    async def test_groups_route_to_own_skill_and_merge_into_one_callback(self):
        cross_sell.skill_registry = _FakeRegistry({
            "henan:流量包": _FakePkg(_ma_node()),
            "henan:语音包": _FakePkg(_ma_node()),
        })
        payload = _payload()
        payload["params"]["inputs"]["products"][0]["activityTypeName"] = "流量包"
        payload["params"]["inputs"]["products"][1]["activityTypeName"] = "语音包"
        await cross_sell.handle_marketing_assistant_payload(payload)
        await self._drain()

        self.assertEqual(len(self.pushed), 1, "多活动分组也只回调一次")
        result = self.pushed[0]["value"]["result"]
        self.assertEqual([r["productId"] for r in result], ["PROD_001", "PROD_002"])
        self.assertIn("@流量包", result[0]["words"])
        self.assertIn("@语音包", result[1]["words"])

    async def test_unmatched_activity_skipped_when_batch_has_name_match(self):
        """本批已按活动名称精确匹配到技能包时，没匹配上的活动不回退（不串用别的活动模板）。"""
        cross_sell.skill_registry = _FakeRegistry({"henan:流量包": _FakePkg(_ma_node())})
        payload = _payload()
        payload["params"]["inputs"]["products"][0]["activityTypeName"] = "流量包"
        payload["params"]["inputs"]["products"][1]["activityTypeName"] = "宽带包"
        await cross_sell.handle_marketing_assistant_payload(payload)
        await self._drain()
        result = self.pushed[0]["value"]["result"]
        self.assertEqual([r["productId"] for r in result], ["PROD_001"])

    async def test_all_activities_unmatched_falls_back_to_flagged_skill(self):
        """整批活动名称都没同名技能包（存量配置）→ 回退唯一标记的技能包，照常出话术。"""
        cross_sell.skill_registry = _FakeRegistry({"henan:营销活动": _FakePkg(_ma_node())})
        payload = _payload()
        for p in payload["params"]["inputs"]["products"]:
            p["activityTypeName"] = "流量语音包"
        await cross_sell.handle_marketing_assistant_payload(payload)
        await self._drain()
        result = self.pushed[0]["value"]["result"]
        self.assertEqual([r["productId"] for r in result], ["PROD_001", "PROD_002"])
        self.assertIn("@营销活动", result[0]["words"])

    async def test_blocked_products_get_no_script(self):
        cross_sell.skill_registry = _FakeRegistry({"henan:营销活动": _FakePkg(_ma_node())})
        payload = _payload()
        payload["params"]["inputs"]["products"][0]["marketingProductFlag"] = "0"
        await cross_sell.handle_marketing_assistant_payload(payload)
        await self._drain()
        result = self.pushed[0]["value"]["result"]
        self.assertEqual([r["productId"] for r in result], ["PROD_002"])

    async def test_all_products_blocked_callbacks_empty_result(self):
        cross_sell.skill_registry = _FakeRegistry({"henan:营销活动": _FakePkg(_ma_node())})
        payload = _payload()
        for p in payload["params"]["inputs"]["products"]:
            p["marketingActivityFlag"] = "0"
        await cross_sell.handle_marketing_assistant_payload(payload)
        await self._drain()
        self.assertEqual(self.pushed[0]["value"]["result"], [])

    async def test_retention_script_fills_own_field(self):
        cross_sell.skill_registry = _FakeRegistry({
            "henan:营销活动": _FakePkg(_ma_node(), [
                {"stage": "切入", "status": "online"},
                {"stage": "挽留", "status": "online"},
            ]),
        })
        await cross_sell.handle_marketing_assistant_payload(_payload())
        await self._drain()
        first = self.pushed[0]["value"]["result"][0]
        # words 是无环节的营销推荐话术；切入/挽留各归其字段，互不相同
        self.assertTrue(first["words"].startswith("话术-"))
        self.assertTrue(first["aiPitchMarketingDesc"].startswith("切入-"))
        self.assertTrue(first["aiRetentionMarketingDesc"].startswith("挽留-"))

    async def test_three_stage_templates_merge_into_one_product(self):
        """一个产品配了推荐/切入/挽留三行模板 → 三条话术归并成一条产品话术（三字段各就位）。"""
        cross_sell.skill_registry = _FakeRegistry({
            "henan:营销活动": _FakePkg(_ma_node(), [
                {"stage": "推荐", "status": "online"},
                {"stage": "切入", "status": "online"},
                {"stage": "挽留", "status": "online"},
            ]),
        })
        await cross_sell.handle_marketing_assistant_payload(_payload())
        await self._drain()
        first = self.pushed[0]["value"]["result"][0]
        # words 取「推荐」环节话术，切入/挽留各归其字段，互不相同
        self.assertTrue(first["words"].startswith("推荐-"))
        self.assertTrue(first["aiPitchMarketingDesc"].startswith("切入-"))
        self.assertTrue(first["aiRetentionMarketingDesc"].startswith("挽留-"))

    async def test_ack_then_callback(self):
        cross_sell.skill_registry = _FakeRegistry({"henan:营销活动": _FakePkg(_ma_node())})
        resp = await cross_sell.handle_marketing_assistant_payload(_payload())
        body = json.loads(resp.body.decode("utf-8"))
        # 主接口只回「收到」，不回话术
        self.assertEqual(body["rtnCode"], "0")
        self.assertEqual(body["rtnMsg"], "数据接收成功！")
        self.assertNotIn("recommend_results", body)

        await self._drain()
        self.assertEqual(len(self.pushed), 1)
        call = self.pushed[0]
        # 回调唯一标识以 touchNumber 回传（优先 inputs.touchNumber）
        self.assertEqual(call["touch_number"], "TOUCH_20230810_0001")
        self.assertEqual(call["phone"], "13800138000")
        self.assertEqual(call["identifier"], ma.IDENTIFIER_SCRIPT)   # 话术 → hs
        result = call["value"]["result"]
        self.assertEqual([r["productId"] for r in result], ["PROD_001", "PROD_002"])
        self.assertTrue(result[0]["words"].startswith("话术-5G特惠月租卡"))

    async def test_province_without_flagged_skill_is_rejected(self):
        cross_sell.skill_registry = _FakeRegistry({"henan:套餐推荐": _FakePkg(_standard_node())})
        resp = await cross_sell.handle_marketing_assistant_payload(_payload())
        body = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(body["rtnCode"], "9999")
        self.assertFalse(self.pushed)

    async def test_bad_payload_rejected(self):
        cross_sell.skill_registry = _FakeRegistry({"henan:营销活动": _FakePkg(_ma_node())})
        resp = await cross_sell.handle_marketing_assistant_payload({"phone": "1"})
        self.assertEqual(json.loads(resp.body.decode("utf-8"))["rtnCode"], "9999")

    async def test_opt_type_without_script_is_only_acked(self):
        cross_sell.skill_registry = _FakeRegistry({"henan:营销活动": _FakePkg(_ma_node())})
        payload = _payload()
        payload["params"]["optType"] = "1,2"          # 仅推荐/比价，本期不产话术
        resp = await cross_sell.handle_marketing_assistant_payload(payload)
        self.assertEqual(json.loads(resp.body.decode("utf-8"))["rtnCode"], "0")
        await self._drain()
        self.assertFalse(self.pushed)


class TestRuntimePassthroughByOriginalName(unittest.TestCase):
    """运行期：产品列表按原名 products 直传，仍走多产品链路。

    键名对外不变（调色板/模板引用 {products}），只把值喂进标准域 recommended_packages
    —— 否则推荐/逐产品话术都取不到候选产品。
    """

    def _run_node(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        ei = ma.parse(_payload()).extra_info
        ctx = FlowContext(phone="13800138000", intent="营销活动", province="henan", extra_info=ei)
        return asyncio.run(DataStep("henan")._call_one("cc", cfg, ctx))

    def test_products_feeds_standard_domain_without_renaming(self):
        out = self._run_node({
            "source_type": "direct", "direct_mode": "passthrough",
            ma.REQUEST_VARIANT_KEY: ma.VARIANT_MARKETING_ASSISTANT,
            "passthrough_fields": [
                "userinfo", "userinfo.currPrice", "userinfo.userExtra.last3mFlowSaturation",
                "products", "products.productName", "userinfo_json",
            ],
        })
        # 多产品链路的候选产品（值同源，键名不变）
        self.assertEqual(len(out["resources"]["recommended_packages"]), 2)
        # 透传通道里是原字段名，模板用 {products} / {currPrice} / {last3mFlowSaturation}
        self.assertEqual(len(out["passthrough"]["products"]), 2)
        self.assertEqual(out["passthrough"]["currPrice"], "129")
        self.assertEqual(out["passthrough"]["last3mFlowSaturation"], "10")
        self.assertNotIn("recommended_packages", out["passthrough"])
        # 列表域子字段 → 产品字段白名单（逐条产品各取自己那份）
        self.assertEqual(out["product_field_allow"], ["productName"])

    def test_standard_product_list_not_fed_without_variant(self):
        """产品列表不带灵运独有字段时，漏配 variant 就**不喂**标准域（标准接口零影响）。

        判据是营销标志 / activityTypeName（见 looks_like_marketing_products）：
        已上线省份的直传产品列表没有这些字段，不会被兜底自愈误认成营销助手报文。
        """
        ctx = FlowContext(
            phone="1", intent="套餐推荐", province="guangdong",
            extra_info={"products": [{"offerId": "A", "offerName": "升169"}]},
        )
        out = asyncio.run(DataStep("guangdong")._call_one(
            "cc", {"source_type": "direct", "direct_mode": "passthrough"}, ctx,
        ))
        self.assertNotIn("recommended_packages", out["resources"])
        self.assertIn("products", out["passthrough"])

    def test_marketing_products_salvaged_when_variant_missing(self):
        """兜底自愈：报文明显是营销助手（带营销标志）但节点漏勾规范时，仍喂进标准域。

        否则话术会用「空产品」匹配模板，填了产品 ID 的模板一律落空、结果也不回显
        product_id（生产事故形态）。自愈同时套用营销标志规则，与配置正确时行为一致。
        """
        out = self._run_node({"source_type": "direct", "direct_mode": "passthrough"})
        self.assertEqual(len(out["resources"]["recommended_packages"]), 2)
        self.assertIn("products", out["passthrough"])

    def test_marketing_flags_apply_on_salvage(self):
        """自愈路径同样按营销标志过滤：两个 flag 未同时为 1 的产品不生成话术。"""
        payload = _payload()
        for p in payload["params"]["inputs"]["products"]:
            p["marketingProductFlag"] = "0"
        ei = ma.parse(payload).extra_info
        ctx = FlowContext(phone="1", intent="营销活动", province="henan", extra_info=ei)
        out = asyncio.run(DataStep("henan")._call_one(
            "cc", {"source_type": "direct", "direct_mode": "passthrough"}, ctx,
        ))
        self.assertNotIn("recommended_packages", out["resources"])
        self.assertEqual(len(out["passthrough"]["products"]), 2)

    def test_looks_like_marketing_products_gate(self):
        """自愈判据收紧到灵运独有字段，标准省份产品/空值一律不触发。"""
        self.assertTrue(ma.looks_like_marketing_products(
            [{"productId": "P1", "marketingProductFlag": "1"}]))
        self.assertTrue(ma.looks_like_marketing_products(
            [{"productId": "P1", "activityTypeName": "流量语音包"}]))
        self.assertFalse(ma.looks_like_marketing_products(
            [{"offerId": "A", "offerName": "升169套餐"}]))
        self.assertFalse(ma.looks_like_marketing_products([]))
        self.assertFalse(ma.looks_like_marketing_products(None))
        self.assertFalse(ma.looks_like_marketing_products("products"))

    def test_direct_mode_concurrency_not_capped(self):
        """产品列表按原名直传时，逐产品话术仍全部并发（不因键名不同退回全局上限）。"""
        ei = ma.parse(_payload()).extra_info
        ctx = FlowContext(phone="1", intent="营销活动", province="henan", extra_info=ei)
        self.assertFalse(ScriptStep._is_direct_mode(ctx))   # 还没喂进标准域
        ctx.recommended_packages = ei["products"]
        self.assertTrue(ScriptStep._is_direct_mode(ctx))


class TestProductFieldAlias(unittest.TestCase):
    """productId 追加在别名链尾：营销助手产品能回显/匹配，已有省份取值不变。"""

    def setUp(self):
        self.step = ScriptStep("henan")
        self.step.field_aliases = {}
        self.step._match_cfg = {}

    def test_marketing_assistant_product_id(self):
        pkg = {"productId": "PROD_001", "productName": "5G特惠月租卡"}
        self.assertEqual(self.step._match_product_id(pkg), "PROD_001")

    def test_existing_offer_id_still_wins(self):
        pkg = {"offerId": "OFFER_9", "productId": "PROD_001"}
        self.assertEqual(self.step._match_product_id(pkg), "OFFER_9")


class TestTemplateMatchDimensions(unittest.TestCase):
    """模板匹配维度：productId → business_type → productName（精确优先，逐个尝试）。"""

    def setUp(self):
        self.step = ScriptStep("henan")
        self.step.field_aliases = {}
        self.step._match_cfg = {}

    def test_candidate_order_precise_first(self):
        pkg = {"productId": "PROD_001", "business_type": "流量包", "productName": "5G特惠月租卡"}
        self.assertEqual(
            self.step._match_product_id_candidates(pkg),
            ["PROD_001", "流量包", "5G特惠月租卡"],
        )

    def test_business_type_absent_keeps_original_chain(self):
        """标准接口省份的产品没有 business_type：候选序列与原来逐项一致。"""
        pkg = {"offerId": "OFFER_9", "offerName": "【广州】升169套餐"}
        self.assertEqual(
            self.step._match_product_id_candidates(pkg),
            ["OFFER_9", "【广州】升169套餐"],
        )

    def test_business_type_from_override(self):
        self.step._match_cfg = {"business_type_from": "bizInfo.type"}
        pkg = {"productId": "P1", "business_type": "忽略", "bizInfo": {"type": "语音包"}}
        self.assertEqual(self.step._match_product_id_candidates(pkg), ["P1", "语音包"])


class TestMarketingFlags(unittest.TestCase):
    """marketingProductFlag / marketingActivityFlag 决定是否生成话术。"""

    def test_both_flags_truthy_are_marketable(self):
        # 严格口径：两个标志都必须显式为真值(1/true…)才生成
        for pkg in (
            {"productId": "P", "marketingProductFlag": "1", "marketingActivityFlag": "1"},
            {"productId": "P", "marketingProductFlag": 1, "marketingActivityFlag": True},
            {"productId": "P", "marketingProductFlag": "true", "marketingActivityFlag": "yes"},
            {"productId": "P", "marketingProductFlag": "是", "marketingActivityFlag": " 1 "},
        ):
            self.assertTrue(ma.is_marketable(pkg), pkg)

    def test_non_truthy_or_missing_flag_blocks(self):
        # 任一非 1——含 0/false、空字符串、字段缺失、其它非 1 值——都不生成
        for pkg in (
            {"productId": "P", "marketingProductFlag": "0", "marketingActivityFlag": "1"},
            {"productId": "P", "marketingProductFlag": "1", "marketingActivityFlag": "0"},
            {"productId": "P", "marketingProductFlag": "0", "marketingActivityFlag": "0"},
            {"productId": "P", "marketingActivityFlag": False},
            {"productId": "P", "marketingProductFlag": "N"},
            {"productId": "P"},                                                    # 字段缺失 → 不生成
            {"productId": "P", "marketingProductFlag": "", "marketingActivityFlag": ""},  # 空值 → 不生成
            {"productId": "P", "marketingProductFlag": "1"},                       # 只有一个 → 不生成
        ):
            self.assertFalse(ma.is_marketable(pkg), pkg)

    def test_split_marketable(self):
        keep, skip = ma.split_marketable([
            {"productId": "A", "marketingProductFlag": "1", "marketingActivityFlag": "1"},
            {"productId": "B", "marketingProductFlag": "1", "marketingActivityFlag": "0"},
            {"productId": "C"},                                   # 缺失标志 → 挡掉
            {},                                                   # 空项直接丢弃
        ])
        self.assertEqual([p["productId"] for p in keep], ["A"])
        self.assertEqual([p["productId"] for p in skip], ["B", "C"])

    def test_filter_only_applies_to_marketing_assistant_node(self):
        products = [{"productId": "A", "marketingProductFlag": "0"}]
        ma_cfg = {ma.REQUEST_VARIANT_KEY: ma.VARIANT_MARKETING_ASSISTANT}
        keep, skip = ma.select_marketable_products(products, ma_cfg)
        self.assertEqual((keep, len(skip)), ([], 1))
        # 标准接口节点：营销标志是灵运规范特有语义，不外溢
        keep, skip = ma.select_marketable_products(products, {})
        self.assertEqual((len(keep), skip), (1, []))

    def test_data_step_filters_blocked_products(self):
        payload = _payload()
        payload["params"]["inputs"]["products"][0]["marketingProductFlag"] = "0"
        payload["params"]["inputs"]["products"][1]["marketingActivityFlag"] = "1"
        ei = ma.parse(payload).extra_info
        ctx = FlowContext(phone="1", intent="营销活动", province="henan", extra_info=ei)
        out = asyncio.run(DataStep("henan")._call_one("cc", {
            "source_type": "direct", "direct_mode": "passthrough",
            ma.REQUEST_VARIANT_KEY: ma.VARIANT_MARKETING_ASSISTANT,
        }, ctx))
        pkgs = out["resources"]["recommended_packages"]
        self.assertEqual([p["productId"] for p in pkgs], ["PROD_002"])
        # 原样透传的 products 不受影响（入参对象零改写）
        self.assertEqual(len(out["passthrough"]["products"]), 2)


class TestActivityGrouping(unittest.TestCase):
    """activityTypeName（活动名称）是场景 skill 的匹配键。"""

    def test_group_preserves_first_seen_order(self):
        groups = ma.group_products_by_activity([
            {"productId": "A", "activityTypeName": "流量包"},
            {"productId": "B", "activityTypeName": "语音包"},
            {"productId": "C", "activityTypeName": "流量包"},
        ])
        self.assertEqual([name for name, _ in groups], ["流量包", "语音包"])
        self.assertEqual([p["productId"] for p in groups[0][1]], ["A", "C"])

    def test_missing_activity_name_groups_together(self):
        groups = ma.group_products_by_activity([{"productId": "A"}, {"productId": "B"}])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][0], "")

    def test_recommend_body_carries_product_subset(self):
        req = ma.parse(_payload())
        subset = [req.products[1]]
        body = ma.to_recommend_body(
            req, intent="流量包", province="henan",
            products=subset, batch_contexts=[{"stage": "切入"}],
        )
        self.assertEqual([p["productId"] for p in body["extra_info"]["products"]], ["PROD_002"])
        self.assertEqual(body["topN"], 1)
        self.assertEqual(body["batch_contexts"], [{"stage": "切入"}])
        # 不改动 req 本体（其他分组还要用）
        self.assertEqual(len(req.extra_info["products"]), 2)


class TestStandardModeUnaffected(unittest.TestCase):
    """守住「营销助手改动不外溢到已上线通用模式」这条底线。

    营销助手模式新增了三条产品维度规则（活动名称选技能包、营销标志决定是否生成、业务类型
    参与模板匹配），本类逐条验证它们在标准接口/接口查询模式下不生效或不改变取值。
    """

    def test_product_list_field_only_for_marketing_assistant(self):
        """产品数组喂标准域的开关：标准接口节点恒为空串 → DataStep 整段逻辑不进入。"""
        self.assertEqual(ma.resolve_product_list_field(
            {"source_type": "direct", "direct_mode": "passthrough"}), "")
        self.assertEqual(ma.resolve_product_list_field({}), "")
        self.assertEqual(ma.resolve_product_list_field(
            {ma.REQUEST_VARIANT_KEY: ma.VARIANT_MARKETING_ASSISTANT}), "products")
        # 显式配置优先（留给后续别的接口规范复用）
        self.assertEqual(ma.resolve_product_list_field(
            {ma.PRODUCT_LIST_FIELD_KEY: "offerList"}), "offerList")

    def test_direct_count_ignores_same_named_list_in_interface_query_mode(self):
        """接口查询模式下入参里恰好有个 products 列表，不能被误判成「调用方直传」。

        误判的后果是 topN 截断被跳过、LLM 并发上限被取消，都属于线上行为变化。
        """
        ctx = FlowContext(
            phone="1", intent="套餐推荐", province="guangdong",
            extra_info={"products": [{"foo": 1}, {"foo": 2}]},
        )
        ctx.recommended_packages = [{"offerId": "A"}, {"offerId": "B"}, {"offerId": "C"}]
        self.assertEqual(ctx.caller_supplied_package_count(), 0)
        self.assertFalse(ScriptStep._is_direct_mode(ctx))

    def test_direct_count_standard_passthrough_unchanged(self):
        pkgs = [{"offerId": "A"}, {"offerId": "B"}]
        ctx = FlowContext(
            phone="1", intent="套餐推荐", province="guangdong",
            extra_info={"recommended_packages": pkgs},
        )
        ctx.recommended_packages = list(pkgs)
        self.assertEqual(ctx.caller_supplied_package_count(), 2)
        self.assertTrue(ScriptStep._is_direct_mode(ctx))

    def test_direct_count_marketing_assistant_products(self):
        """营销助手的 products 同样算直传（否则 18 个产品会被 topN 截断成 3 条）。"""
        ei = ma.parse(_payload()).extra_info
        ctx = FlowContext(phone="1", intent="营销活动", province="henan", extra_info=ei)
        ctx.recommended_packages = ei["products"]
        self.assertEqual(ctx.caller_supplied_package_count(), 2)

    def test_existing_province_product_fields_still_injectable(self):
        """已上线省份的产品字段没有被新增的排除项误伤（否则话术会少上下文）。"""
        from engine.prompt_builder import PKG_FIELD_EXCLUDED_KEYS
        for field in ("offerName", "initFee", "offerFlow", "offerVoice",
                      "recommend_actual_price", "recommend_package_name", "business_type"):
            self.assertNotIn(field, PKG_FIELD_EXCLUDED_KEYS, field)
        # 营销助手的 ID / 标志位字段不作为话术槽位（串号、标志值念进话术无意义）
        for field in ("productId", "activityId", "activityTypeCode",
                      "marketingProductFlag", "marketingActivityFlag"):
            self.assertIn(field, PKG_FIELD_EXCLUDED_KEYS, field)


if __name__ == "__main__":
    unittest.main()
