"""
test_multi_product_recommend — 多产品话术生成（recommended_packages 数组）适配测试

背景：对外规范《营销话术推荐服务接口规范（对外标准版）v1.0》要求单/多产品统一走
extra_info.recommended_packages 数组，服务端对 TopN 个产品逐产品各生成一条话术并发返回，
响应 recommend_results[].offerId / product_id 回显产品 offerId 供调用方关联。

覆盖：
1. 多产品展开：N 个产品 → N 条话术，offerId 与 product_id 均回显产品 offerId，rank 保序
2. 模板匹配候选回退：数字 offerId 匹配不到关键词模板时改用 offerName 命中，
   但回显的 offerId / product_id 仍是产品 offerId
3. 单产品行为不变：无推荐列表时 batch_contexts 指定 product_id 仍只生成 1 条、走精确匹配
4. 配了通用兜底模板的省份第一候选即命中，不触发名称回退（既有行为零变化）
5. 多产品确实并发（同时在途 > 1），且并发上限按待生成条数自适应
6. 路由层顶层直传折叠 _fold_top_level_extra_info
7. 宽松兜底档 select_template_loose（下游漏传 stage 时仍能命中模板）

运行：cd ROOT && python -m pytest tests/test_multi_product_recommend.py -q
约束：不调真实网络/ES/Redis/LLM（LLM 以内存桩替换）。
"""
from __future__ import annotations

import asyncio
import unittest
from typing import Any, Dict, List

from core.context import FlowContext
from engine.template_selector import select_template, select_template_loose
from routers.realtime import _build_success_payload, _fold_top_level_extra_info
from steps.script_step import ScriptStep

INTENT = "营销活动"


def _tpl(pid: str, stage: str = "个人市场", scene: str = "开口话术", name: str = "") -> Dict[str, Any]:
    return {
        "template_id": f"tpl_{pid or 'generic'}",
        "template_name": name or f"模板_{pid or '通用'}",
        "template_content": f"这是 {pid or '通用'} 的话术正文",
        "intent": INTENT,
        "product_id": pid,
        "stage": stage,
        "scene": scene,
        "status": "online",
        "script_requirement": "口语化",
        "linked_vars": [],
    }


# 广东式「按产品名关键词配模板」，没有 product_id 为空的通用兜底模板
_KEYWORD_TEMPLATES: List[Dict[str, Any]] = [
    _tpl("流量"), _tpl("套餐"), _tpl("升"), _tpl("全家享", stage="家庭市场"),
]

_BIZ = {"script_templates_v2": _KEYWORD_TEMPLATES}

_PKG_A = {
    "offerId": "2026060810324218501011930",
    "offerName": "【广州】【纯裸升】升169套餐-2606",
    "recommend_package_name": "【广州】【纯裸升】升169套餐-2606",
    "recommend_actual_price": "169元",
    "rank": 1,
}
_PKG_B = {
    "offerId": "2026060810324218501011931",
    "offerName": "【广州】流量扩容包-20G",
    "recommend_package_name": "【广州】流量扩容包-20G",
    "recommend_actual_price": "20元",
    "rank": 2,
}


class _FakeLLM:
    """记录并发在途峰值的 LLM 桩。"""

    def __init__(self, delay: float = 0.01) -> None:
        self.delay = delay
        self.calls = 0
        self.in_flight = 0
        self.peak_in_flight = 0

    async def generate(self, prompt: str, *args: Any, **kwargs: Any) -> str:
        self.calls += 1
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            return "生成的话术正文"
        finally:
            self.in_flight -= 1


def _make_ctx(**overrides: Any) -> FlowContext:
    base: Dict[str, Any] = dict(
        phone="13900000000",
        intent=INTENT,
        province="guangdong",
        current_package={"package_name": "139元全家享套餐", "actual_price": "139"},
    )
    base.update(overrides)
    return FlowContext(**base)


def _run_batch(ctx: FlowContext, biz: Dict[str, Any], llm: _FakeLLM) -> List[Dict[str, Any]]:
    """以桩 LLM 驱动 ScriptStep.run_batch，返回 marketing_scripts。"""
    step = ScriptStep(province=ctx.province)
    import steps.script_step as ss

    original = ss.llm_service.generate
    ss.llm_service.generate = llm.generate  # type: ignore[assignment]
    try:
        asyncio.run(step.run_batch(ctx, biz_config=biz))
    finally:
        ss.llm_service.generate = original  # type: ignore[assignment]
    return ctx.marketing_scripts


class TestMultiProductExpansion(unittest.TestCase):
    """多产品：TopN 个产品逐产品各一条话术。"""

    def test_expands_one_script_per_product_and_echoes_offer_id(self) -> None:
        ctx = _make_ctx(
            final_recommendations=[_PKG_A, _PKG_B],
            batch_contexts=[{"product_id": "", "stage": "个人市场", "scence": "开口话术"}],
        )
        scripts = _run_batch(ctx, _BIZ, _FakeLLM())

        self.assertEqual(len(scripts), 2, "两个推荐产品应各生成一条话术")
        # 回显 product_id / offerId 必须是产品 offerId，供调用方把话术关联回产品
        self.assertEqual(
            [s["product_id"] for s in scripts],
            [_PKG_A["offerId"], _PKG_B["offerId"]],
        )
        self.assertEqual(
            [s["offerId"] for s in scripts],
            [_PKG_A["offerId"], _PKG_B["offerId"]],
        )
        self.assertEqual([s["rank"] for s in scripts], [1, 2])
        for s in scripts:
            self.assertTrue(s["marketing_text"], "话术正文不应为空")
            self.assertEqual(s["stage"], "个人市场")
            self.assertEqual(s["scence"], "开口话术")

    def test_recommend_results_shape_matches_spec(self) -> None:
        """对外 data.recommend_results 字段与接口规范一致。"""
        ctx = _make_ctx(
            final_recommendations=[_PKG_A, _PKG_B],
            batch_contexts=[{"product_id": "", "stage": "个人市场", "scence": "开口话术"}],
        )
        _run_batch(ctx, _BIZ, _FakeLLM())
        results = ctx.to_result()["recommend_results"]

        self.assertEqual(len(results), 2)
        for item in results:
            self.assertEqual(
                set(item.keys()),
                {"product_id", "offerId", "rank", "marketing_text", "diff_table", "stage", "scence"},
            )
            self.assertEqual(item["offerId"], item["product_id"])
            self.assertTrue(item["offerId"])

    def test_topn_products_all_generated_concurrently(self) -> None:
        """多产品并发生成：同时在途数应 > 1（不是串行逐条等待）。"""
        pkgs = [
            {"offerId": f"OID{i}", "offerName": f"【广州】升{i}套餐", "rank": i}
            for i in range(1, 7)
        ]
        ctx = _make_ctx(
            final_recommendations=pkgs,
            batch_contexts=[{"product_id": "", "stage": "个人市场", "scence": "开口话术"}],
        )
        llm = _FakeLLM(delay=0.05)
        scripts = _run_batch(ctx, _BIZ, llm)

        self.assertEqual(len(scripts), 6)
        self.assertEqual(llm.calls, 6)
        self.assertGreater(llm.peak_in_flight, 1, "多产品话术必须并发生成")

    def test_estimate_batch_tasks(self) -> None:
        """待生成条数估算（供诊断；并发上限本身读 concurrency.json）。"""
        step = ScriptStep()
        ctx = _make_ctx(final_recommendations=[{"offerId": f"O{i}"} for i in range(10)])

        # product_id 为空 → 对 10 个产品全展开
        self.assertEqual(
            step._estimate_batch_tasks([{"product_id": ""}], ctx), 10
        )
        # 指定 product_id → 仅 1 条
        self.assertEqual(
            step._estimate_batch_tasks([{"product_id": "O1"}], ctx), 1
        )
        # 多条目累加
        self.assertEqual(
            step._estimate_batch_tasks([{"product_id": "O1"}, {"product_id": ""}], ctx), 11
        )

    def test_default_concurrency_from_config_file_is_8(self) -> None:
        """全局默认并发读 config/concurrency.json，值为 8。"""
        from utils.config_loader import config_loader
        self.assertEqual(config_loader.get_script_llm_max_concurrency(), 8)

    def test_high_topn_respects_global_concurrency_cap(self) -> None:
        """10 个产品时并发受 concurrency.json（默认 8）限制，不会串行逐条。"""
        pkgs = [{"offerId": f"OID{i}", "offerName": f"【广州】升{i}套餐", "rank": i}
                for i in range(1, 11)]
        ctx = _make_ctx(
            final_recommendations=pkgs,
            batch_contexts=[{"product_id": "", "stage": "个人市场", "scence": "开口话术"}],
        )
        llm = _FakeLLM(delay=0.05)
        _run_batch(ctx, _BIZ, llm)
        self.assertGreater(llm.peak_in_flight, 1, "多产品必须并发")
        self.assertLessEqual(llm.peak_in_flight, 8, "并发上限需遵守 concurrency.json")


class TestTemplateMatchCandidates(unittest.TestCase):
    """产品标识候选：offerId 未命中时回退产品名，回显仍为 offerId。"""

    def test_offer_name_fallback_hits_keyword_template(self) -> None:
        step = ScriptStep()
        step._load_biz(_BIZ)
        cands = step._match_product_id_candidates(_PKG_A)

        self.assertEqual(cands[0], _PKG_A["offerId"], "首选仍是 offerId，保证既有省份行为不变")
        self.assertIn(_PKG_A["offerName"], cands)
        # 数字 offerId 匹配不到关键词模板，产品名可以
        self.assertIsNone(
            select_template(_KEYWORD_TEMPLATES, INTENT, _PKG_A["offerId"],
                            stage="个人市场", scene="开口话术")
        )
        self.assertIsNotNone(
            select_template(_KEYWORD_TEMPLATES, INTENT, _PKG_A["offerName"],
                            stage="个人市场", scene="开口话术")
        )

    def test_generated_script_uses_matched_template_but_keeps_offer_id(self) -> None:
        ctx = _make_ctx(
            final_recommendations=[_PKG_A],
            batch_contexts=[{"product_id": "", "stage": "个人市场", "scence": "开口话术"}],
        )
        scripts = _run_batch(ctx, _BIZ, _FakeLLM())

        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]["product_id"], _PKG_A["offerId"])
        # 模板正文应进了 Prompt（说明确实命中了关键词模板，而非退化成无模板兜底）
        self.assertIn("的话术正文", scripts[0]["_llm_prompt"]["template"])

    def test_generic_template_wins_on_first_candidate(self) -> None:
        """配了通用兜底模板（product_id 为空）的省份：首选 offerId 即命中，不触发名称回退。"""
        biz = {"script_templates_v2": _KEYWORD_TEMPLATES + [_tpl("")]}
        matched = select_template(biz["script_templates_v2"], INTENT, _PKG_A["offerId"],
                                  stage="个人市场", scene="开口话术")
        self.assertIsNotNone(matched)
        self.assertEqual(matched["product_id"], "", "应命中通用兜底模板，行为与改动前一致")

    def test_name_fallback_can_be_disabled(self) -> None:
        step = ScriptStep()
        step._load_biz({**_BIZ, "template_match": {"disable_name_fallback": True}})
        cands = step._match_product_id_candidates(_PKG_A)
        self.assertEqual(cands, [_PKG_A["offerId"]])


class TestBatchContextTemplateMatch(unittest.TestCase):
    """多产品下 batch_contexts 的 product_id/stage/scence 与单产品同一套匹配规则。"""

    def test_empty_product_id_expands_with_stage_scence(self) -> None:
        """product_id 为空：TopN 产品各自用同一 stage/scence 匹配模板。"""
        ctx = _make_ctx(
            final_recommendations=[_PKG_A, _PKG_B],
            batch_contexts=[{"product_id": "", "stage": "个人市场", "scence": "开口话术"}],
        )
        scripts = _run_batch(ctx, _BIZ, _FakeLLM())
        self.assertEqual(len(scripts), 2)
        for s in scripts:
            self.assertEqual(s["stage"], "个人市场")
            self.assertEqual(s["scence"], "开口话术")
            self.assertIn("的话术正文", s["_llm_prompt"]["template"])

    def test_business_type_product_id_still_expands_all_and_echoes_offer_id(self) -> None:
        """有推荐列表时 product_id 只作业务类型匹配模板，仍展开全部产品，回显 offerId。"""
        ctx = _make_ctx(
            final_recommendations=[_PKG_A, _PKG_B],
            batch_contexts=[{"product_id": "流量升级包", "stage": "个人市场", "scence": "开口话术"}],
        )
        scripts = _run_batch(ctx, _BIZ, _FakeLLM())
        self.assertEqual(len(scripts), 2, "有 recommended_packages 时不得因 product_id 只出 1 条")
        self.assertEqual(
            [s["product_id"] for s in scripts],
            [_PKG_A["offerId"], _PKG_B["offerId"]],
        )
        for s in scripts:
            self.assertEqual(s["stage"], "个人市场")
            self.assertEqual(s["scence"], "开口话术")
            # 业务类型 hint 应进入模板匹配候选（优先于 offerId）
            self.assertEqual(s["_llm_prompt"].get("product_id"), s["product_id"])

    def test_offer_id_as_business_type_still_expands_all(self) -> None:
        """product_id=某个 offerId：有推荐列表时仍展开全部，不单出一条。"""
        ctx = _make_ctx(
            final_recommendations=[_PKG_A, _PKG_B],
            batch_contexts=[{"product_id": _PKG_B["offerId"], "stage": "个人市场", "scence": "开口话术"}],
        )
        scripts = _run_batch(ctx, _BIZ, _FakeLLM())
        self.assertEqual(len(scripts), 2)
        self.assertEqual(scripts[0]["product_id"], _PKG_A["offerId"])
        self.assertEqual(scripts[1]["product_id"], _PKG_B["offerId"])

    def test_attach_batch_pid_hint(self) -> None:
        out = ScriptStep._attach_batch_pid_hint(_PKG_A, "流量升级包")
        self.assertEqual(out["_batch_product_id_hint"], "流量升级包")
        self.assertEqual(out["offerId"], _PKG_A["offerId"])
        self.assertIs(ScriptStep._attach_batch_pid_hint(_PKG_A, ""), _PKG_A)

    def test_find_pkg_for_batch_pid_unit(self) -> None:
        step = ScriptStep()
        step._load_biz(_BIZ)
        rec = {_PKG_A["offerId"]: _PKG_A, _PKG_B["offerId"]: _PKG_B}
        pkgs = [_PKG_A, _PKG_B]
        self.assertIs(step._find_pkg_for_batch_pid(_PKG_A["offerId"], rec, pkgs), _PKG_A)
        self.assertIs(step._find_pkg_for_batch_pid(_PKG_B["offerName"], rec, pkgs), _PKG_B)
        hit = step._find_pkg_for_batch_pid("流量", rec, pkgs)
        self.assertEqual(hit["offerId"], _PKG_B["offerId"])
        stub = step._find_pkg_for_batch_pid("不存在的产品X", rec, pkgs)
        self.assertEqual(stub, {"product_id": "不存在的产品X"})


class TestSingleProductUnchanged(unittest.TestCase):
    """单产品入参与出参不受多产品适配影响。"""

    def test_explicit_product_id_generates_single_script(self) -> None:
        ctx = _make_ctx(
            final_recommendations=[],
            batch_contexts=[{"product_id": "流量升级包", "stage": "个人市场", "scence": "开口话术"}],
        )
        scripts = _run_batch(ctx, _BIZ, _FakeLLM())

        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]["product_id"], "流量升级包")
        self.assertEqual(scripts[0].get("offerId", ""), "", "占位单产品无真实 offerId")
        self.assertEqual(scripts[0]["stage"], "个人市场")
        results = ctx.to_result()["recommend_results"]
        self.assertEqual(results[0]["product_id"], "流量升级包")
        self.assertEqual(results[0]["offerId"], "")

    def test_no_recommendations_still_generates_one_script(self) -> None:
        """广东旧版单产品（final_recommendations 对象走 extra_info）：无推荐列表仍出 1 条。"""
        ctx = _make_ctx(
            final_recommendations=[],
            extra_info={"final_recommendations": {"recommend_package_name": "流量升级包"}},
            batch_contexts=[{"product_id": "", "stage": "个人市场", "scence": "开口话术"}],
        )
        scripts = _run_batch(ctx, _BIZ, _FakeLLM())
        self.assertEqual(len(scripts), 1)


class TestExternalResponseShape(unittest.TestCase):
    """对外默认出参精简：不含 resource_context / llm_prompts 等排障字段。"""

    def test_default_payload_matches_spec_keys(self) -> None:
        payload = _build_success_payload(
            call_id="c1", phone="139", intent=INTENT, province="guangdong",
            recommend_results=[{"product_id": "A", "offerId": "A", "rank": 1,
                                "marketing_text": "hi", "diff_table": None}],
            other_info=None,
        )
        self.assertEqual(
            set(payload.keys()),
            {"code", "message", "data", "other_info"},
        )
        self.assertEqual(
            set(payload["data"].keys()),
            {"callId", "phone", "intent", "province", "recommend_results"},
        )

    def test_debug_payload_includes_diagnostics(self) -> None:
        payload = _build_success_payload(
            call_id="c1", phone="139", intent=INTENT, province="guangdong",
            recommend_results=[], other_info=None, debug=True,
            resource_context={"usage": {}},
            llm_prompts=[{"full": "x"}],
        )
        self.assertIn("resource_context", payload)
        self.assertIn("llm_prompts", payload)
        self.assertIn("metadata", payload)
        self.assertIn("api_calls", payload)


class TestTopLevelExtraInfoFolding(unittest.TestCase):
    """顶层直传的标准域折叠进 extra_info（兼容与 phone 平级写法）。"""

    def test_folds_whitelisted_standard_domains(self) -> None:
        body = {
            "phone": "139", "intent": INTENT, "province": "200",
            "extra_info": {"portrait_style": {"communication_style": ""}},
            "recommended_packages": [_PKG_A],
            "current_package": {"package_name": "139元套餐"},
        }
        folded = _fold_top_level_extra_info(body)

        self.assertEqual(sorted(folded), ["current_package", "recommended_packages"])
        self.assertEqual(body["extra_info"]["recommended_packages"], [_PKG_A])
        self.assertIn("portrait_style", body["extra_info"], "原有 extra_info 内容不受影响")

    def test_extra_info_wins_over_top_level(self) -> None:
        body = {
            "extra_info": {"current_package": {"package_name": "内层"}},
            "current_package": {"package_name": "顶层"},
        }
        folded = _fold_top_level_extra_info(body)
        self.assertEqual(folded, [])
        self.assertEqual(body["extra_info"]["current_package"]["package_name"], "内层")

    def test_ignores_empty_and_unknown_top_level_keys(self) -> None:
        body = {
            "recommended_packages": [],          # 空值不折叠
            "gateway_meta": {"x": 1},            # 白名单外不折叠，避免噪声进话术上下文
            "usage": {"data_usage": {"a": 1}},
        }
        folded = _fold_top_level_extra_info(body)
        self.assertEqual(folded, ["usage"])
        self.assertNotIn("gateway_meta", body["extra_info"])

    def test_no_extra_info_key_is_created_when_nothing_folded(self) -> None:
        body = {"phone": "139", "intent": INTENT}
        self.assertEqual(_fold_top_level_extra_info(body), [])
        self.assertNotIn("extra_info", body)


class TestLooseFallbackMatching(unittest.TestCase):
    """宽松兜底：下游漏传 stage 时仍能命中模板，而不是退化成无模板 Prompt。"""

    def test_missing_stage_still_matches(self) -> None:
        # 严格 12 档：请求 stage 为空 → 要求模板 stage 也为空 → 全部落空
        matched = select_template(_KEYWORD_TEMPLATES, INTENT, "【广州】升169套餐",
                                  stage="", scene="开口话术")
        self.assertIsNotNone(matched)
        self.assertEqual(matched["scene"], "开口话术")
        self.assertEqual(matched["stage"], "个人市场")

    def test_no_loose_match_when_both_dims_given(self) -> None:
        """两维都给全时严格档位已穷尽组合，宽松档不应引入额外命中。"""
        self.assertIsNone(
            select_template_loose(_KEYWORD_TEMPLATES, INTENT, "升", stage="个人市场", scene="开口话术")
        )

    def test_loose_respects_given_scene(self) -> None:
        """已给定的维度仍必须一致，不能跨场景乱匹配。"""
        self.assertIsNone(
            select_template_loose(_KEYWORD_TEMPLATES, INTENT, "升", stage="", scene="不存在的场景")
        )


if __name__ == "__main__":
    unittest.main()
