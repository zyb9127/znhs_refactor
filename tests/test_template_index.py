"""
test_template_index — engine.template_index 与 ScriptStep._select_template 的等价性测试

对照基准：steps.script_step.ScriptStep._select_template（staticmethod，旧线性扫描）。
覆盖：
1. 构造覆盖 12 档优先级的模板集，枚举 (pid, stage, scene) 组合逐一断言二者一致
2. 随机模糊测试：200 模板 x 100 查询（random.seed 固定）
3. priority 字段：同维度高 priority 胜；无 priority 时列表序在前者胜（与基准一致）
4. skills-runtime/shandong 真实配置抽样 30 组对照（文件不存在则跳过）

运行：cd ROOT && python -m unittest tests.test_template_index -v
约束：不调真实网络/ES/Redis/LLM（本测试只做纯内存的模板匹配）。
"""
from __future__ import annotations

import json
import random
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

from engine.template_index import TemplateIndex, build_index, fuzzy_match_pid

# 项目根目录（tests/ 的上一级）
ROOT = Path(__file__).resolve().parent.parent

# 对照基准：ScriptStep._select_template。
# script_step 模块加载会触发配置文件读取，环境缺配置时导入可能失败；
# 失败则跳过所有对照测试（仅保留索引自身的行为测试）。
_BASELINE_IMPORT_ERROR = ""
try:
    from steps.script_step import ScriptStep
    _BASELINE_AVAILABLE = True
except Exception as exc:  # noqa: BLE001 - 记录任意导入失败原因
    ScriptStep = None  # type: ignore[assignment]
    _BASELINE_AVAILABLE = False
    _BASELINE_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


def setUpModule() -> None:
    """基准不可用时打印原因（对照测试用 skipUnless 逐类跳过并注明）。"""
    if not _BASELINE_AVAILABLE:
        print(
            "[test_template_index] steps.script_step 导入失败，"
            f"跳过与 ScriptStep._select_template 的对照测试: {_BASELINE_IMPORT_ERROR}"
        )


_TPL_SEQ = 0


def make_tpl(
    pid: str = "",
    stage: str = "",
    scene: str = "",
    intent: str = "套餐推荐",
    status: Any = "online",
    **extra: Any,
) -> Dict[str, Any]:
    """构造一条最小合法模板。status 传 None 表示不带 status 键（缺省视为 online）。"""
    global _TPL_SEQ
    _TPL_SEQ += 1
    tpl: Dict[str, Any] = {
        "template_id": f"tpl_{_TPL_SEQ:05d}",
        "template_name": f"模板{_TPL_SEQ}",
        "template_content": f"话术正文 {_TPL_SEQ}",
        "intent": intent,
        "product_id": pid,
        "stage": stage,
        "scene": scene,
    }
    if status is not None:
        tpl["status"] = status
    tpl.update(extra)
    return tpl


def baseline_select(
    templates: List[Dict[str, Any]],
    intent: str,
    pid: str,
    stage: str,
    scene: str,
) -> Optional[Dict[str, Any]]:
    """调用旧线性扫描基准（staticmethod，直接经类调用）。"""
    return ScriptStep._select_template(templates, intent, pid, stage, scene)


class EquivalenceMixin:
    """提供「索引结果与基准逐条一致」的断言工具。"""

    def assert_same_choice(
        self,
        templates: List[Dict[str, Any]],
        intent: str,
        queries: List[Any],
    ) -> None:
        """对每个 (pid, stage, scene) 查询断言 TemplateIndex.select 与基准同对象。"""
        index = TemplateIndex(templates, intent)
        for pid, stage, scene in queries:
            expected = baseline_select(templates, intent, pid, stage, scene)
            actual = index.select(pid, stage, scene)
            self.assertIs(  # type: ignore[attr-defined]
                actual, expected,
                msg=(
                    f"查询 pid={pid!r} stage={stage!r} scene={scene!r} 不一致: "
                    f"index={actual and actual.get('template_id')!r} "
                    f"baseline={expected and expected.get('template_id')!r}"
                ),
            )


@unittest.skipUnless(_BASELINE_AVAILABLE, f"script_step 导入失败: {_BASELINE_IMPORT_ERROR}")
class TestTwelveTierEquivalence(unittest.TestCase, EquivalenceMixin):
    """覆盖 12 档匹配的构造用例：逐组合与基准对照。"""

    INTENT = "套餐推荐"

    def _build_full_tier_templates(self) -> List[Dict[str, Any]]:
        """构造一套覆盖全部 12 档的模板（每档一条），并混入应被预过滤的噪声。"""
        pid_exact = "P100"
        fuzzy_pid = "套餐,升档"     # 关键词模板，供模糊档命中
        stg, scn = "犹豫挽留环节", "套餐升档"
        tpls = [
            # 阶段1：产品精确（档1-4）
            make_tpl(pid_exact, stg, scn),
            make_tpl(pid_exact, stg, ""),
            make_tpl(pid_exact, "", scn),
            make_tpl(pid_exact, "", ""),
            # 阶段2：产品模糊（档5-8）
            make_tpl(fuzzy_pid, stg, scn),
            make_tpl(fuzzy_pid, stg, ""),
            make_tpl(fuzzy_pid, "", scn),
            make_tpl(fuzzy_pid, "", ""),
            # 阶段3：通用兜底（档9-12）
            make_tpl("", stg, scn),
            make_tpl("", stg, ""),
            make_tpl("", "", scn),
            make_tpl("", "", ""),
            # 噪声：应被预过滤/不参与命中
            make_tpl(pid_exact, stg, scn, status="deleted"),
            make_tpl(pid_exact, stg, scn, intent="其他意图"),
            make_tpl(pid_exact, stg, scn, status="offline"),
        ]
        return tpls

    def _all_queries(self) -> List[Any]:
        """枚举查询组合：命中精确/模糊/兜底/未命中及空维度的所有交叉。"""
        pids = ["P100", "5G畅享套餐活动", "39元档升级产品", "P999不存在", "", "  P100  "]
        stages = ["犹豫挽留环节", "未知环节", "", " 犹豫挽留环节 "]
        scenes = ["套餐升档", "未知场景", "", " 套餐升档 "]
        return [(p, s, c) for p in pids for s in stages for c in scenes]

    def test_full_tier_enumeration(self) -> None:
        """12 档全量模板下，全部查询组合与基准一致。"""
        self.assert_same_choice(
            self._build_full_tier_templates(), self.INTENT, self._all_queries()
        )

    def test_tier_peeling(self) -> None:
        """剥洋葱：每次删掉当前命中的模板重建索引，验证 12 档降级次序逐档一致。"""
        templates = self._build_full_tier_templates()
        query = ("5G畅享套餐活动P100", "犹豫挽留环节", "套餐升档")
        # 该查询 pid 同时可精确失败、模糊命中"套餐"关键词；先换一个可精确命中的查询
        exact_query = ("P100", "犹豫挽留环节", "套餐升档")
        for _ in range(20):  # 上限防死循环
            index = TemplateIndex(templates, self.INTENT)
            expected = baseline_select(templates, self.INTENT, *exact_query)
            actual = index.select(*exact_query)
            self.assertIs(actual, expected)
            if expected is None:
                break
            templates = [t for t in templates if t is not expected]
        else:
            self.fail("剥离循环未收敛到 None")

    def test_prefilter_deleted_and_intent(self) -> None:
        """deleted 与异 intent 模板不参与匹配；intent 为空的模板可命中任意 intent。"""
        tpls = [
            make_tpl("P1", "s", "c", status="deleted"),
            make_tpl("P1", "s", "c", intent="别的意图"),
            make_tpl("P1", "s", "c", intent=""),   # intent 不限，应命中
        ]
        self.assert_same_choice(tpls, self.INTENT, [("P1", "s", "c")])
        index = TemplateIndex(tpls, self.INTENT)
        chosen = index.select("P1", "s", "c")
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["intent"], "")

    def test_offline_downgrade(self) -> None:
        """无 online 模板时降级使用全 pool（offline 等），与基准一致。"""
        tpls = [
            make_tpl("P1", "s", "c", status="offline"),
            make_tpl("", "", "", status="offline"),
        ]
        queries = [("P1", "s", "c"), ("P2", "", ""), ("", "", "")]
        self.assert_same_choice(tpls, self.INTENT, queries)
        self.assertIsNotNone(TemplateIndex(tpls, self.INTENT).select("P1", "s", "c"))

    def test_status_missing_defaults_online(self) -> None:
        """无 status 键的模板缺省视为 online。"""
        tpls = [make_tpl("P1", "", "", status=None)]
        self.assert_same_choice(tpls, self.INTENT, [("P1", "", "")])
        self.assertIsNotNone(TemplateIndex(tpls, self.INTENT).select("P1"))

    def test_empty_pool_returns_none(self) -> None:
        """空列表 / 全 deleted：select 恒为 None，与基准一致。"""
        for tpls in ([], [make_tpl("P1", "s", "c", status="deleted")]):
            self.assert_same_choice(tpls, self.INTENT, [("P1", "s", "c"), ("", "", "")])
            self.assertIsNone(TemplateIndex(tpls, self.INTENT).select("P1", "s", "c"))

    def test_fuzzy_keyword_semantics(self) -> None:
        """模糊匹配关键词拆分（[,，\\n]+）与小写互包含语义。"""
        self.assertTrue(fuzzy_match_pid("套餐,升档", "5G畅享套餐活动"))
        self.assertTrue(fuzzy_match_pid("ABC", "xx abc yy"))     # 大小写不敏感
        self.assertTrue(fuzzy_match_pid("超长关键词包含abc", "abc"))  # 请求含于关键词
        self.assertTrue(fuzzy_match_pid("k1，k2\nk3", "带k3的产品"))  # 中文逗号/换行分隔
        self.assertFalse(fuzzy_match_pid("", "abc"))
        self.assertFalse(fuzzy_match_pid("abc", ""))
        self.assertFalse(fuzzy_match_pid("xyz", "abc"))


@unittest.skipUnless(_BASELINE_AVAILABLE, f"script_step 导入失败: {_BASELINE_IMPORT_ERROR}")
class TestRandomFuzzEquivalence(unittest.TestCase, EquivalenceMixin):
    """随机模糊测试：200 模板 x 100 查询，逐一与基准对照（seed 固定可复现）。"""

    INTENT = "套餐推荐"

    def test_random_templates_and_queries(self) -> None:
        rng = random.Random(20260703)

        pid_pool = [
            "", "P100", "P200", "p100", "5G畅享套餐", "39元升档包",
            "套餐,升档", "体验,视频会员", "ABC", "abcde", "K1，K2\nK3",
            " P100 ", "视频",
        ]
        stage_pool = ["", "犹豫挽留环节", "营销切入环节", "s1", " s1 ", "S1"]
        scene_pool = ["", "套餐升档", "开口话术", "c1", " c1 ", "C1"]
        status_pool: List[Any] = ["online", "online", "online", "offline", "deleted", None]
        intent_pool = [self.INTENT, self.INTENT, self.INTENT, "", "其他意图"]

        templates = [
            make_tpl(
                pid=rng.choice(pid_pool),
                stage=rng.choice(stage_pool),
                scene=rng.choice(scene_pool),
                intent=rng.choice(intent_pool),
                status=rng.choice(status_pool),
            )
            for _ in range(200)
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
            for _ in range(100)
        ]
        self.assert_same_choice(templates, self.INTENT, queries)


class TestPriority(unittest.TestCase):
    """priority 字段（索引路径新增能力，仅影响精确/兜底桶内排序）。"""

    INTENT = "套餐推荐"

    def test_higher_priority_wins_in_exact_bucket(self) -> None:
        low = make_tpl("P1", "s", "c")                    # 无 priority = 0
        high = make_tpl("P1", "s", "c", priority=5)
        index = TemplateIndex([low, high], self.INTENT)
        self.assertIs(index.select("P1", "s", "c"), high)

    def test_no_priority_keeps_list_order(self) -> None:
        first = make_tpl("P1", "s", "c")
        second = make_tpl("P1", "s", "c")
        index = TemplateIndex([first, second], self.INTENT)
        self.assertIs(index.select("P1", "s", "c"), first)
        if _BASELINE_AVAILABLE:
            # 全无 priority 时与基准（列表序）一致
            self.assertIs(
                baseline_select([first, second], self.INTENT, "P1", "s", "c"), first
            )

    def test_priority_in_fallback_bucket(self) -> None:
        low = make_tpl("", "", "", priority=1)
        high = make_tpl("", "", "", priority=9)
        index = TemplateIndex([low, high], self.INTENT)
        self.assertIs(index.select("不存在产品", "x", "y"), high)

    def test_priority_string_value_tolerated(self) -> None:
        """priority 为数字字符串可解析，非法值按 0 处理不致索引失效。"""
        a = make_tpl("P1", "", "", priority="3")
        b = make_tpl("P1", "", "", priority="垃圾值")
        index = TemplateIndex([b, a], self.INTENT)
        self.assertIs(index.select("P1"), a)

    def test_priority_not_applied_to_fuzzy_pool(self) -> None:
        """模糊池保持原列表序，priority 不参与（与旧线性扫描一致）。"""
        first = make_tpl("套餐,升档", "", "")
        second = make_tpl("套餐", "", "", priority=99)
        index = TemplateIndex([first, second], self.INTENT)
        self.assertIs(index.select("5G畅享套餐活动"), first)


class TestBuildIndex(unittest.TestCase):
    """build_index 的容错行为。"""

    def test_build_index_ok(self) -> None:
        index = build_index([make_tpl("P1", "s", "c")], "套餐推荐")
        self.assertIsInstance(index, TemplateIndex)

    def test_build_index_returns_none_on_bad_input(self) -> None:
        """列表元素非 dict 触发异常时返回 None（调用方回退旧线性扫描）。"""
        self.assertIsNone(build_index([None], "套餐推荐"))  # type: ignore[list-item]


@unittest.skipUnless(_BASELINE_AVAILABLE, f"script_step 导入失败: {_BASELINE_IMPORT_ERROR}")
class TestRealShandongConfig(unittest.TestCase, EquivalenceMixin):
    """skills-runtime 山东真实配置抽样对照（目录名为中文，用 glob 定位）。"""

    def _load_real_templates(self):
        paths = sorted(
            ROOT.glob("skills-runtime/shandong/*/config/biz_config.json")
        )
        for path in paths:
            try:
                cfg = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            templates = cfg.get("script_templates_v2") or []
            if templates:
                return path, templates
        return None, []

    def test_real_config_sampled_equivalence(self) -> None:
        path, templates = self._load_real_templates()
        if not templates:
            self.skipTest("未找到 skills-runtime/shandong/*/config/biz_config.json 或其中无 script_templates_v2")

        # intent 取模板中出现的第一个非空 intent（真实配置内 intent 与技能目录一致）
        intent = next(
            (str(t.get("intent")) for t in templates if t.get("intent")), ""
        )

        rng = random.Random(42)
        real_pids = sorted({str(t.get("product_id") or "").strip() for t in templates})
        real_stages = sorted({str(t.get("stage") or "").strip() for t in templates})
        real_scenes = sorted({str(t.get("scene") or "").strip() for t in templates})
        # 抽样池：真实值 + 空值 + 未知值 + 真实 pid 的子串（触发模糊档）
        pid_pool = real_pids + ["", "不存在的产品X"] + [
            p[: max(2, len(p) // 2)] for p in real_pids if len(p) >= 4
        ]
        stage_pool = real_stages + ["", "未知环节"]
        scene_pool = real_scenes + ["", "未知场景"]

        queries = [
            (rng.choice(pid_pool), rng.choice(stage_pool), rng.choice(scene_pool))
            for _ in range(30)
        ]
        self.assert_same_choice(templates, intent, queries)


if __name__ == "__main__":
    unittest.main(verbosity=2)
