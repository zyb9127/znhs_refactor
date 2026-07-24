"""
test_pipeline_runner —— engine.pipeline_runner 的行为测试（stub 组件，无真实网络/ES/Redis/LLM）

覆盖（规格 V1 §12）：
1. stub 组件顺序执行：执行顺序记入 ctx.metadata["order"]，断言与管道定义一致
2. when 条件：nonempty/empty/always 的满足与跳过语义
3. 单步异常不中断：抛异常的步骤记 ctx.errors，后续步骤照常执行
4. script_generate 兜底注入：batch_contexts 为空时 runner 注入空条目
5. validate_pipeline_def：结构非法/未知组件/when 非法的错误报告
6. output_guard 组件：mask/drop/flag 与句号截断行为（纯字符串处理，可直接测）

运行：cd ROOT && python -m unittest tests.test_pipeline_runner -v
"""
from __future__ import annotations

import asyncio
import unittest
from typing import Any, ClassVar, Dict

from core.context import FlowContext
from engine.component import StepComponent
from engine.registry import (
    COMPONENT_CLASSES,
    invalidate_components,
    register_component,
)
from engine.pipeline_runner import (
    DEFAULT_PIPELINE,
    run_pipeline,
    validate_pipeline_def,
)

# 触发内置组件注册（data_fetch/recommend/script_generate/output_guard）
import engine.components  # noqa: F401


# ══════════════════════════════════════════════════════════════
# stub 组件：把执行痕迹写入 ctx.metadata["order"]
# ══════════════════════════════════════════════════════════════

def _make_stub(stub_name: str, boom: bool = False):
    """构造并注册一个 stub 组件类：运行时把自身名字追加到 ctx.metadata['order']"""

    @register_component
    class _Stub(StepComponent):
        """测试用 stub 组件"""

        name: ClassVar[str] = stub_name

        async def run(
            self,
            ctx: "FlowContext",
            skill_config: Dict[str, Any],
            params: Dict[str, Any],
        ) -> None:
            ctx.metadata.setdefault("order", []).append(self.name)
            ctx.metadata.setdefault("params_seen", {})[self.name] = dict(params or {})
            if boom:
                raise RuntimeError("stub 故意异常")

    return _Stub


# 模块加载时注册全部 stub（名字带前缀，避免与内置组件冲突）
_make_stub("stub_a")
_make_stub("stub_b")
_make_stub("stub_c")
_make_stub("stub_boom", boom=True)


def make_ctx(**kwargs: Any) -> FlowContext:
    """构造最小 FlowContext（省份用测试专属值，避免与其他实例缓存串扰）"""
    base = dict(phone="13800000000", intent="unittest", province="ut_runner")
    base.update(kwargs)
    return FlowContext(**base)


def run(ctx: FlowContext, pipeline_def: Dict[str, Any], skill_config: Dict[str, Any] = None) -> None:
    asyncio.run(run_pipeline(ctx, skill_config or {}, pipeline_def))


class TestRunOrder(unittest.TestCase):
    """顺序执行语义"""

    def test_steps_run_in_definition_order(self) -> None:
        ctx = make_ctx()
        run(ctx, {"steps": [
            {"component": "stub_b"},
            {"component": "stub_a"},
            {"component": "stub_c"},
        ]})
        self.assertEqual(ctx.metadata.get("order"), ["stub_b", "stub_a", "stub_c"])
        self.assertEqual(ctx.errors, [])

    def test_params_passed_to_component(self) -> None:
        ctx = make_ctx()
        run(ctx, {"steps": [{"component": "stub_a", "params": {"k": 1}}]})
        self.assertEqual(ctx.metadata["params_seen"]["stub_a"], {"k": 1})

    def test_same_component_can_repeat(self) -> None:
        ctx = make_ctx()
        run(ctx, {"steps": [{"component": "stub_a"}, {"component": "stub_a"}]})
        self.assertEqual(ctx.metadata.get("order"), ["stub_a", "stub_a"])


class TestWhenCondition(unittest.TestCase):
    """when 条件的满足与跳过"""

    def test_nonempty_skips_when_field_empty(self) -> None:
        ctx = make_ctx()  # recommended_packages 默认 []
        run(ctx, {"steps": [
            {"component": "stub_a",
             "when": {"ctx_field": "recommended_packages", "op": "nonempty"}},
            {"component": "stub_b"},
        ]})
        # stub_a 被跳过，stub_b 照常执行
        self.assertEqual(ctx.metadata.get("order"), ["stub_b"])

    def test_nonempty_runs_when_field_nonempty(self) -> None:
        ctx = make_ctx()
        ctx.recommended_packages = [{"product_id": "P1"}]
        run(ctx, {"steps": [
            {"component": "stub_a",
             "when": {"ctx_field": "recommended_packages", "op": "nonempty"}},
        ]})
        self.assertEqual(ctx.metadata.get("order"), ["stub_a"])

    def test_empty_op(self) -> None:
        ctx = make_ctx()
        ctx.recommended_packages = [{"product_id": "P1"}]
        run(ctx, {"steps": [
            {"component": "stub_a",
             "when": {"ctx_field": "recommended_packages", "op": "empty"}},
            {"component": "stub_b",
             "when": {"ctx_field": "marketing_scripts", "op": "empty"}},
        ]})
        # recommended_packages 非空 → stub_a 跳过；marketing_scripts 空 → stub_b 执行
        self.assertEqual(ctx.metadata.get("order"), ["stub_b"])

    def test_always_and_missing_when(self) -> None:
        ctx = make_ctx()
        run(ctx, {"steps": [
            {"component": "stub_a", "when": {"op": "always"}},
            {"component": "stub_b", "when": None},
            {"component": "stub_c"},
        ]})
        self.assertEqual(ctx.metadata.get("order"), ["stub_a", "stub_b", "stub_c"])


class TestErrorTolerance(unittest.TestCase):
    """单步异常不中断整体（与旧 pipeline 容错一致）"""

    def test_exception_recorded_and_next_step_runs(self) -> None:
        ctx = make_ctx()
        run(ctx, {"steps": [
            {"component": "stub_a"},
            {"component": "stub_boom"},
            {"component": "stub_b"},
        ]})
        # boom 已执行（顺序里有它），异常后 stub_b 仍执行
        self.assertEqual(ctx.metadata.get("order"), ["stub_a", "stub_boom", "stub_b"])
        self.assertEqual(len(ctx.errors), 1)
        self.assertIn("stub_boom异常", ctx.errors[0])
        self.assertIn("stub 故意异常", ctx.errors[0])

    def test_unknown_component_recorded_and_continues(self) -> None:
        # validate_pipeline_def 会拦截未知组件；此处验证 runner 兜底容错
        ctx = make_ctx()
        run(ctx, {"steps": [
            {"component": "no_such_component"},
            {"component": "stub_a"},
        ]})
        self.assertEqual(ctx.metadata.get("order"), ["stub_a"])
        self.assertEqual(len(ctx.errors), 1)
        self.assertIn("no_such_component异常", ctx.errors[0])

    def test_non_dict_step_recorded_and_continues(self) -> None:
        ctx = make_ctx()
        run(ctx, {"steps": ["not-a-dict", {"component": "stub_a"}]})
        self.assertEqual(ctx.metadata.get("order"), ["stub_a"])
        self.assertEqual(len(ctx.errors), 1)


class TestScriptGenerateFallback(unittest.TestCase):
    """script_generate 运行前 batch_contexts 兜底注入（runner 特判）"""

    def setUp(self) -> None:
        # 临时用 stub 覆盖内置 script_generate 注册，测试后恢复，避免污染其他用例
        self._orig = COMPONENT_CLASSES.get("script_generate")
        _make_stub("script_generate")
        invalidate_components()

    def tearDown(self) -> None:
        if self._orig is not None:
            COMPONENT_CLASSES["script_generate"] = self._orig
        else:
            COMPONENT_CLASSES.pop("script_generate", None)
        invalidate_components()

    def test_empty_batch_contexts_injected(self) -> None:
        ctx = make_ctx()
        self.assertEqual(ctx.batch_contexts, [])
        run(ctx, {"steps": [{"component": "script_generate"}]})
        self.assertEqual(
            ctx.batch_contexts, [{"product_id": "", "stage": "", "scence": ""}]
        )
        self.assertEqual(ctx.metadata.get("order"), ["script_generate"])

    def test_nonempty_batch_contexts_untouched(self) -> None:
        ctx = make_ctx()
        ctx.batch_contexts = [{"product_id": "P9", "stage": "s", "scence": "c"}]
        run(ctx, {"steps": [{"component": "script_generate"}]})
        self.assertEqual(
            ctx.batch_contexts, [{"product_id": "P9", "stage": "s", "scence": "c"}]
        )


class TestValidatePipelineDef(unittest.TestCase):
    """validate_pipeline_def 的错误报告"""

    def test_default_pipeline_is_valid(self) -> None:
        # 内置组件已注册，默认管道定义应校验通过
        self.assertEqual(validate_pipeline_def(DEFAULT_PIPELINE), [])

    def test_not_a_dict(self) -> None:
        errors = validate_pipeline_def([1, 2])
        self.assertTrue(errors and "object" in errors[0])

    def test_steps_missing_or_empty(self) -> None:
        self.assertTrue(validate_pipeline_def({}))
        self.assertTrue(validate_pipeline_def({"steps": "x"}))
        self.assertTrue(validate_pipeline_def({"steps": []}))

    def test_unknown_component_name(self) -> None:
        errors = validate_pipeline_def({"steps": [{"component": "no_such_component"}]})
        self.assertEqual(len(errors), 1)
        self.assertIn("未知组件名", errors[0])
        self.assertIn("no_such_component", errors[0])

    def test_component_name_required(self) -> None:
        errors = validate_pipeline_def({"steps": [{"component": ""}, {"params": {}}]})
        self.assertEqual(len(errors), 2)

    def test_params_must_be_object(self) -> None:
        errors = validate_pipeline_def(
            {"steps": [{"component": "stub_a", "params": [1]}]}
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("params", errors[0])

    def test_when_validation(self) -> None:
        # when 非 object
        self.assertTrue(validate_pipeline_def(
            {"steps": [{"component": "stub_a", "when": "x"}]}
        ))
        # op 非法
        errors = validate_pipeline_def(
            {"steps": [{"component": "stub_a", "when": {"op": "gt"}}]}
        )
        self.assertTrue(any("when.op" in e for e in errors))
        # 非 always 缺 ctx_field
        errors = validate_pipeline_def(
            {"steps": [{"component": "stub_a", "when": {"op": "nonempty"}}]}
        )
        self.assertTrue(any("ctx_field" in e for e in errors))
        # 合法 when
        self.assertEqual(
            validate_pipeline_def({"steps": [{
                "component": "stub_a",
                "when": {"ctx_field": "recommended_packages", "op": "nonempty"},
            }]}),
            [],
        )


class TestOutputGuardComponent(unittest.TestCase):
    """output_guard 组件行为（纯字符串处理，直接经 runner 调用）"""

    @staticmethod
    def _ctx_with_scripts() -> FlowContext:
        ctx = make_ctx()
        ctx.marketing_scripts = [
            {"product_id": "P1", "rank": 1, "marketing_text": "推荐办理超值套餐。绝对最便宜。"},
            {"product_id": "P2", "rank": 2, "marketing_text": "流量翻倍不加价。"},
        ]
        return ctx

    def test_noop_without_params(self) -> None:
        ctx = self._ctx_with_scripts()
        before = [dict(x) for x in ctx.marketing_scripts]
        run(ctx, {"steps": [{"component": "output_guard"}]})
        self.assertEqual(ctx.marketing_scripts, before)
        self.assertNotIn("guard_flags", ctx.metadata)

    def test_mask(self) -> None:
        ctx = self._ctx_with_scripts()
        run(ctx, {"steps": [{"component": "output_guard", "params": {
            "forbidden_words": ["绝对", "最便宜"], "action": "mask",
        }}]})
        # "绝对"(2字) -> "**"，"最便宜"(3字) -> "***"
        self.assertEqual(
            ctx.marketing_scripts[0]["marketing_text"],
            "推荐办理超值套餐。" + "*" * 2 + "*" * 3 + "。",
        )
        self.assertEqual(len(ctx.metadata["guard_flags"]), 1)
        self.assertEqual(ctx.metadata["guard_flags"][0]["hits"], ["绝对", "最便宜"])

    def test_drop(self) -> None:
        ctx = self._ctx_with_scripts()
        run(ctx, {"steps": [{"component": "output_guard", "params": {
            "forbidden_words": ["绝对"], "action": "drop",
        }}]})
        self.assertEqual(len(ctx.marketing_scripts), 1)
        self.assertEqual(ctx.marketing_scripts[0]["product_id"], "P2")
        self.assertEqual(ctx.metadata["guard_flags"][0]["action"], "drop")

    def test_flag_keeps_text(self) -> None:
        ctx = self._ctx_with_scripts()
        run(ctx, {"steps": [{"component": "output_guard", "params": {
            "forbidden_words": ["绝对"], "action": "flag",
        }}]})
        self.assertIn("绝对", ctx.marketing_scripts[0]["marketing_text"])
        self.assertEqual(ctx.metadata["guard_flags"][0]["hits"], ["绝对"])

    def test_max_length_truncates_at_sentence(self) -> None:
        ctx = make_ctx()
        ctx.marketing_scripts = [
            {"product_id": "P1", "rank": 1,
             "marketing_text": "第一句话。第二句话比较长会被切掉"},
        ]
        run(ctx, {"steps": [{"component": "output_guard", "params": {"max_length": 10}}]})
        self.assertEqual(ctx.marketing_scripts[0]["marketing_text"], "第一句话。")

    def test_max_length_hard_cut_without_period(self) -> None:
        ctx = make_ctx()
        ctx.marketing_scripts = [
            {"product_id": "P1", "rank": 1, "marketing_text": "没有句号的一大段话术内容"},
        ]
        run(ctx, {"steps": [{"component": "output_guard", "params": {"max_length": 5}}]})
        self.assertEqual(ctx.marketing_scripts[0]["marketing_text"], "没有句号的")


class TestBuiltinRegistration(unittest.TestCase):
    """内置组件均已注册"""

    def test_builtin_components_registered(self) -> None:
        for name in ("data_fetch", "recommend", "script_generate", "output_guard"):
            self.assertIn(name, COMPONENT_CLASSES, f"内置组件未注册: {name}")


if __name__ == "__main__":
    unittest.main()
