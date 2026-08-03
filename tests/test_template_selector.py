"""
test_template_selector — engine.template_selector 的等价与行为测试

覆盖：
1. select_template(index=TemplateIndex) 与 select_template_linear 在随机模板集上等价
   （复用 tests.test_template_index 的模板生成器 make_tpl，seed 固定可复现）
2. select_template(index=None) 恒等于 select_template_linear（回退路径）
3. select_template_linear / select_templates_expand 与 ScriptStep 原实现对照
   （script_step 导入失败时跳过；后续 ScriptStep 改薄委托后该对照退化为一致性冒烟）
4. select_templates_expand 行为：scene 去重/空 scene 排除/offline 排除/通用降级
5. fuzzy_match_pid 语义
6. build_selector_index 开关与阈值（ZNHS_TEMPLATE_INDEX=="0" 或 <30 条返回 None）

运行：cd ROOT && python -m unittest tests.test_template_selector -v
约束：不调真实网络/ES/Redis/LLM（纯内存匹配）。
"""
from __future__ import annotations

import os
import random
import unittest
from typing import Any, Dict, List

from engine.template_index import TemplateIndex
from engine.template_selector import (
    build_selector_index,
    fuzzy_match_pid,
    select_template,
    select_template_linear,
    select_templates_expand,
)
from tests.test_template_index import make_tpl

# 对照基准：ScriptStep（导入失败时跳过对照测试，仅保留纯行为测试）
_BASELINE_IMPORT_ERROR = ""
try:
    from steps.script_step import ScriptStep
    _BASELINE_AVAILABLE = True
except Exception as exc:  # noqa: BLE001 - 记录任意导入失败原因
    ScriptStep = None  # type: ignore[assignment]
    _BASELINE_AVAILABLE = False
    _BASELINE_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


INTENT = "套餐推荐"


def _random_fixture(seed: int, n_templates: int = 200, n_queries: int = 120):
    """随机模板集 + 随机查询（与 test_template_index 的池子同源，seed 固定）。"""
    rng = random.Random(seed)
    pid_pool = [
        "", "P100", "P200", "p100", "5G畅享套餐", "39元升档包",
        "套餐,升档", "体验,视频会员", "ABC", "abcde", "K1，K2\nK3",
        " P100 ", "视频",
    ]
    stage_pool = ["", "犹豫挽留环节", "营销切入环节", "s1", " s1 ", "S1"]
    scene_pool = ["", "套餐升档", "开口话术", "c1", " c1 ", "C1"]
    status_pool: List[Any] = ["online", "online", "online", "offline", "deleted", None]
    intent_pool = [INTENT, INTENT, INTENT, "", "其他意图"]

    templates = [
        make_tpl(
            pid=rng.choice(pid_pool),
            stage=rng.choice(stage_pool),
            scene=rng.choice(scene_pool),
            intent=rng.choice(intent_pool),
            status=rng.choice(status_pool),
        )
        for _ in range(n_templates)
    ]
    query_pid_pool = pid_pool + [
        "5G畅享套餐活动", "39元档升级", "含abc的产品", "K2专属", "不存在的产品X",
    ]
    queries = [
        (
            rng.choice(query_pid_pool),
            rng.choice(stage_pool + ["未知环节"]),
            rng.choice(scene_pool + ["未知场景"]),
        )
        for _ in range(n_queries)
    ]
    return templates, queries


class TestSelectTemplateIndexEquivalence(unittest.TestCase):
    """索引实现与线性实现的严格档位等价性。

    注意：等价性的主体是两个**底层实现**（TemplateIndex.select vs select_template_linear），
    而不是包装层 select_template —— 后者在严格 12 档未命中后还会追加「宽松兜底」策略档
    （见 TestSelectTemplateLooseFallback），两条路径经包装层后行为一致但不等于裸线性结果。
    """

    def test_random_equivalence_index_vs_linear(self) -> None:
        templates, queries = _random_fixture(seed=20260703)
        index = TemplateIndex(templates, INTENT)
        for pid, stage, scene in queries:
            expected = select_template_linear(templates, INTENT, pid, stage=stage, scene=scene)
            actual = index.select(pid, stage=stage, scene=scene)
            self.assertIs(
                actual, expected,
                msg=(
                    f"查询 pid={pid!r} stage={stage!r} scene={scene!r} 不一致: "
                    f"index={actual and actual.get('template_id')!r} "
                    f"linear={expected and expected.get('template_id')!r}"
                ),
            )

    def test_second_seed_equivalence(self) -> None:
        """换一个 seed 再跑一轮，降低偶然性。"""
        templates, queries = _random_fixture(seed=42, n_templates=150, n_queries=80)
        index = TemplateIndex(templates, INTENT)
        for pid, stage, scene in queries:
            self.assertIs(
                index.select(pid, stage=stage, scene=scene),
                select_template_linear(templates, INTENT, pid, stage=stage, scene=scene),
            )

    def test_wrapper_same_result_with_and_without_index(self) -> None:
        """包装层 select_template 走索引与走线性，对外结果必须一致（含宽松兜底档）。"""
        templates, queries = _random_fixture(seed=7, n_templates=60, n_queries=30)
        index = TemplateIndex(templates, INTENT)
        for pid, stage, scene in queries:
            self.assertIs(
                select_template(templates, INTENT, pid, stage=stage, scene=scene, index=index),
                select_template(templates, INTENT, pid, stage=stage, scene=scene, index=None),
            )

    def test_index_none_delegates_to_linear_when_matched(self) -> None:
        """index=None 且严格档位命中时，包装层返回线性实现的同一对象（不改写既有命中）。"""
        templates, queries = _random_fixture(seed=7, n_templates=60, n_queries=30)
        for pid, stage, scene in queries:
            expected = select_template_linear(templates, INTENT, pid, stage=stage, scene=scene)
            if expected is None:
                continue
            self.assertIs(
                select_template(templates, INTENT, pid, stage=stage, scene=scene, index=None),
                expected,
            )

    def test_empty_templates(self) -> None:
        self.assertIsNone(select_template([], INTENT, "P1"))
        self.assertIsNone(select_template_linear([], INTENT, "P1"))


@unittest.skipUnless(_BASELINE_AVAILABLE, f"script_step 导入失败: {_BASELINE_IMPORT_ERROR}")
class TestMigrationAgainstScriptStep(unittest.TestCase):
    """迁移函数与 ScriptStep 原 staticmethod 的对照（迁移期为逐行等价验证）。"""

    def test_select_template_linear_matches_scriptstep(self) -> None:
        templates, queries = _random_fixture(seed=99, n_templates=120, n_queries=60)
        for pid, stage, scene in queries:
            self.assertIs(
                select_template_linear(templates, INTENT, pid, stage=stage, scene=scene),
                ScriptStep._select_template(templates, INTENT, pid, stage=stage, scene=scene),
            )

    def test_select_templates_expand_matches_scriptstep(self) -> None:
        templates, _ = _random_fixture(seed=11, n_templates=120, n_queries=0)
        pids = ["", "P100", "5G畅享套餐", "套餐,升档", "不存在的产品X", " P100 "]
        stages = ["", "犹豫挽留环节", "s1", "未知环节"]
        for pid in pids:
            for stage in stages:
                self.assertEqual(
                    select_templates_expand(templates, INTENT, pid, stage=stage),
                    ScriptStep._select_templates_expand(templates, INTENT, pid, stage=stage),
                    msg=f"expand pid={pid!r} stage={stage!r} 不一致",
                )


class TestSelectTemplatesExpandBehavior(unittest.TestCase):
    """select_templates_expand 的枚举语义（不依赖 script_step）。"""

    def test_scene_enumeration_and_dedup(self) -> None:
        """同 pid+stage 下按 scene 枚举并去重（保留首个）；空 scene 兜底模板不纳入。"""
        t_a1 = make_tpl("P1", "s1", "sceneA")
        t_b = make_tpl("P1", "s1", "sceneB")
        t_a2 = make_tpl("P1", "s1", "sceneA")   # 重复 scene，应被去重
        t_empty = make_tpl("P1", "s1", "")      # 空 scene 兜底，不纳入枚举
        t_other_stage = make_tpl("P1", "s2", "sceneC")
        result = select_templates_expand(
            [t_a1, t_b, t_a2, t_empty, t_other_stage], INTENT, "P1", stage="s1"
        )
        self.assertEqual(result, [t_a1, t_b])

    def test_downgrade_to_generic_product(self) -> None:
        """pid 无专属模板 → 降级到通用产品（pid 为空）+ stage。"""
        generic1 = make_tpl("", "s1", "sceneA")
        generic2 = make_tpl("", "s1", "sceneB")
        other_pid = make_tpl("P9", "s1", "sceneC")
        result = select_templates_expand(
            [generic1, generic2, other_pid], INTENT, "P1", stage="s1"
        )
        self.assertEqual(result, [generic1, generic2])

    def test_empty_pid_only_generic(self) -> None:
        """pid 为空 → 只匹配通用产品模板。"""
        generic = make_tpl("", "s1", "sceneA")
        specific = make_tpl("P1", "s1", "sceneB")
        result = select_templates_expand([generic, specific], INTENT, "", stage="s1")
        self.assertEqual(result, [generic])

    def test_offline_and_deleted_excluded(self) -> None:
        """expand 池排除 offline 与 deleted（与单选逻辑不同：offline 也排除）。"""
        online = make_tpl("P1", "s1", "sceneA")
        offline = make_tpl("P1", "s1", "sceneB", status="offline")
        deleted = make_tpl("P1", "s1", "sceneC", status="deleted")
        result = select_templates_expand([offline, deleted, online], INTENT, "P1", stage="s1")
        self.assertEqual(result, [online])

    def test_intent_filter(self) -> None:
        """异 intent 模板不参与；intent 为空的模板可命中。"""
        no_intent = make_tpl("P1", "s1", "sceneA", intent="")
        other_intent = make_tpl("P1", "s1", "sceneB", intent="其他意图")
        result = select_templates_expand([no_intent, other_intent], INTENT, "P1", stage="s1")
        self.assertEqual(result, [no_intent])

    def test_empty_pool_returns_empty(self) -> None:
        self.assertEqual(select_templates_expand([], INTENT, "P1", stage="s1"), [])


class TestFuzzyMatchPid(unittest.TestCase):
    """fuzzy_match_pid 语义（关键词拆分 + 小写互包含）。"""

    def test_semantics(self) -> None:
        self.assertTrue(fuzzy_match_pid("套餐,升档", "5G畅享套餐活动"))
        self.assertTrue(fuzzy_match_pid("ABC", "xx abc yy"))          # 大小写不敏感
        self.assertTrue(fuzzy_match_pid("超长关键词包含abc", "abc"))   # 请求含于关键词
        self.assertTrue(fuzzy_match_pid("k1，k2\nk3", "带k3的产品"))   # 中文逗号/换行分隔
        self.assertFalse(fuzzy_match_pid("", "abc"))
        self.assertFalse(fuzzy_match_pid("abc", ""))
        self.assertFalse(fuzzy_match_pid("xyz", "abc"))


class TestBuildSelectorIndex(unittest.TestCase):
    """build_selector_index 的开关与阈值。"""

    ENV_KEY = "ZNHS_TEMPLATE_INDEX"

    def setUp(self) -> None:
        self._saved = os.environ.get(self.ENV_KEY)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop(self.ENV_KEY, None)
        else:
            os.environ[self.ENV_KEY] = self._saved

    def _templates(self, n: int) -> List[Dict[str, Any]]:
        return [make_tpl(f"P{i}", "s", "c") for i in range(n)]

    def test_env_zero_disables(self) -> None:
        os.environ[self.ENV_KEY] = "0"
        self.assertIsNone(build_selector_index(self._templates(50), INTENT))

    def test_below_threshold_returns_none(self) -> None:
        os.environ.pop(self.ENV_KEY, None)
        self.assertIsNone(build_selector_index(self._templates(29), INTENT))
        self.assertIsNone(build_selector_index([], INTENT))
        self.assertIsNone(build_selector_index(None, INTENT))  # type: ignore[arg-type]

    def test_at_threshold_builds_index(self) -> None:
        os.environ.pop(self.ENV_KEY, None)
        index = build_selector_index(self._templates(30), INTENT)
        self.assertIsInstance(index, TemplateIndex)

    def test_env_other_value_builds_index(self) -> None:
        os.environ[self.ENV_KEY] = "1"
        self.assertIsInstance(build_selector_index(self._templates(40), INTENT), TemplateIndex)

    def test_built_index_usable_by_select_template(self) -> None:
        templates = self._templates(35)
        index = build_selector_index(templates, INTENT)
        self.assertIsNotNone(index)
        chosen = select_template(templates, INTENT, "P3", stage="s", scene="c", index=index)
        self.assertIs(chosen, select_template_linear(templates, INTENT, "P3", stage="s", scene="c"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
