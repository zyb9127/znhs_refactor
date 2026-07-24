"""
test_conflict_detector — management/config_agent/conflict_detector.py 单元测试

覆盖：exact_conflicts 分组与判胜（priority/list_order）、状态与意图过滤、
fuzzy_shadows 互包含提示、near_duplicates 相似度阈值与占位符归一化、
超过规模上限时跳过两两比较。不调用任何真实网络/ES/Redis/LLM。

运行：cd ROOT && python -m unittest tests.test_conflict_detector -v
"""
import unittest

from management.config_agent.conflict_detector import detect_conflicts


INTENT = "套餐推荐"


def _tpl(tid, content="话术正文示例", pid="", stage="", scene="", **overrides):
    """构造最小模板。"""
    tpl = {
        "template_id": tid,
        "template_name": f"名称_{tid}",
        "template_content": content,
        "product_id": pid,
        "stage": stage,
        "scene": scene,
        "intent": INTENT,
        "status": "online",
    }
    tpl.update(overrides)
    return tpl


class TestExactConflicts(unittest.TestCase):
    """同维度 online 模板冲突分组与判胜"""

    def test_no_conflict_different_dims(self):
        result = detect_conflicts([
            _tpl("t1", pid="P1"),
            _tpl("t2", pid="P2"),
        ], INTENT)
        self.assertEqual(result["exact_conflicts"], [])

    def test_conflict_winner_by_list_order(self):
        """无 priority 时列表序在前者胜。"""
        result = detect_conflicts([
            _tpl("t1", content="正文甲", pid="P1", stage="s", scene="c"),
            _tpl("t2", content="完全不同的另一段内容乙", pid="P1", stage="s", scene="c"),
        ], INTENT)
        conflicts = result["exact_conflicts"]
        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertEqual(conflict["dims"],
                         {"product_id": "P1", "stage": "s", "scene": "c"})
        self.assertEqual(sorted(conflict["template_ids"]), ["t1", "t2"])
        self.assertEqual(conflict["winner"], "t1")
        self.assertEqual(conflict["winner_reason"], "list_order")

    def test_conflict_winner_by_priority(self):
        """priority 高者胜（即使列表序靠后）。"""
        result = detect_conflicts([
            _tpl("t1", content="正文甲", pid="P1"),
            _tpl("t2", content="完全不同的另一段内容乙", pid="P1", priority=5),
        ], INTENT)
        conflict = result["exact_conflicts"][0]
        self.assertEqual(conflict["winner"], "t2")
        self.assertEqual(conflict["winner_reason"], "priority")

    def test_equal_priority_falls_back_to_list_order(self):
        result = detect_conflicts([
            _tpl("t1", content="正文甲", pid="P1", priority=3),
            _tpl("t2", content="完全不同的另一段内容乙", pid="P1", priority=3),
        ], INTENT)
        conflict = result["exact_conflicts"][0]
        self.assertEqual(conflict["winner"], "t1")
        self.assertEqual(conflict["winner_reason"], "list_order")

    def test_dims_normalized_by_strip(self):
        """维度字段 strip 后比较（' P1 ' 与 'P1' 同组）。"""
        result = detect_conflicts([
            _tpl("t1", content="正文甲", pid=" P1 "),
            _tpl("t2", content="完全不同的另一段内容乙", pid="P1"),
        ], INTENT)
        self.assertEqual(len(result["exact_conflicts"]), 1)

    def test_offline_and_deleted_excluded(self):
        result = detect_conflicts([
            _tpl("t1", pid="P1"),
            _tpl("t2", pid="P1", status="offline"),
            _tpl("t3", pid="P1", status="deleted"),
        ], INTENT)
        self.assertEqual(result["exact_conflicts"], [])
        self.assertEqual(result["online_count"], 1)

    def test_missing_status_defaults_online(self):
        """status 缺省视为 online，参与冲突分组。"""
        t1 = _tpl("t1", content="正文甲", pid="P1")
        del t1["status"]
        result = detect_conflicts([
            t1,
            _tpl("t2", content="完全不同的另一段内容乙", pid="P1"),
        ], INTENT)
        self.assertEqual(len(result["exact_conflicts"]), 1)

    def test_other_intent_excluded(self):
        result = detect_conflicts([
            _tpl("t1", pid="P1"),
            _tpl("t2", pid="P1", intent="流量包推荐"),
        ], INTENT)
        self.assertEqual(result["exact_conflicts"], [])

    def test_empty_intent_is_wildcard(self):
        """模板 intent 为空视为通配，参与当前意图检测。"""
        result = detect_conflicts([
            _tpl("t1", content="正文甲", pid="P1", intent=""),
            _tpl("t2", content="完全不同的另一段内容乙", pid="P1"),
        ], INTENT)
        self.assertEqual(len(result["exact_conflicts"]), 1)


class TestFuzzyShadows(unittest.TestCase):
    """多关键词 product_id 的模糊遮蔽提示"""

    def test_shadow_detected(self):
        result = detect_conflicts([
            _tpl("fz", content="正文甲", pid="畅享套餐,升级包"),
            _tpl("ex", content="完全不同的另一段内容乙", pid="5G畅享套餐活动"),
        ], INTENT)
        shadows = result["fuzzy_shadows"]
        self.assertEqual(len(shadows), 1)
        self.assertEqual(shadows[0]["fuzzy_template_id"], "fz")
        self.assertEqual(shadows[0]["keyword"], "畅享套餐")
        self.assertEqual(shadows[0]["shadowed_exact_pid"], "5G畅享套餐活动")

    def test_case_insensitive_containment(self):
        result = detect_conflicts([
            _tpl("fz", content="正文甲", pid="5g PLUS,体验包"),
            _tpl("ex", content="完全不同的另一段内容乙", pid="超值5G plus流量款"),
        ], INTENT)
        self.assertEqual(len(result["fuzzy_shadows"]), 1)
        self.assertEqual(result["fuzzy_shadows"][0]["keyword"], "5g PLUS")

    def test_single_keyword_pid_not_fuzzy(self):
        """单关键词 pid 不视为多关键词模糊模板，不产生提示。"""
        result = detect_conflicts([
            _tpl("a", content="正文甲", pid="畅享套餐"),
            _tpl("b", content="完全不同的另一段内容乙", pid="5G畅享套餐活动"),
        ], INTENT)
        self.assertEqual(result["fuzzy_shadows"], [])

    def test_no_containment_no_shadow(self):
        result = detect_conflicts([
            _tpl("fz", content="正文甲", pid="宽带提速,权益包"),
            _tpl("ex", content="完全不同的另一段内容乙", pid="5G流量王"),
        ], INTENT)
        self.assertEqual(result["fuzzy_shadows"], [])


class TestNearDuplicates(unittest.TestCase):
    """归一化正文近似重复检测"""

    def test_identical_after_placeholder_strip(self):
        """仅占位符不同的两条正文归一化后相同 → 相似度 1.0。"""
        result = detect_conflicts([
            _tpl("a", content="您好，您当前套餐是{cur_brief}，推荐升级。", pid="P1"),
            _tpl("b", content="您好，您当前套餐是{{current_package}}，推荐升级。", pid="P2"),
        ], INTENT)
        dups = result["near_duplicates"]
        self.assertEqual(len(dups), 1)
        self.assertEqual(sorted(dups[0]["template_ids"]), ["a", "b"])
        self.assertGreater(dups[0]["similarity"], 0.9)

    def test_high_similarity_detected(self):
        result = detect_conflicts([
            _tpl("a", content="尊敬的用户您好，为您推荐更划算的套餐，流量翻倍月费不变。", pid="P1"),
            _tpl("b", content="尊敬的用户您好，为您推荐更划算的套餐，流量翻倍月费不变哦。", pid="P2"),
        ], INTENT)
        self.assertEqual(len(result["near_duplicates"]), 1)

    def test_different_content_not_duplicate(self):
        result = detect_conflicts([
            _tpl("a", content="您好，本次来电为您介绍宽带提速活动。", pid="P1"),
            _tpl("b", content="流量月月送，充值立减十元，机会难得。", pid="P2"),
        ], INTENT)
        self.assertEqual(result["near_duplicates"], [])

    def test_skipped_pairwise_over_limit(self):
        """online 模板数超过 800 时跳过两两比较并标记。"""
        templates = [
            _tpl(f"t{i}", content=f"第{i}号正文内容", pid=f"P{i}")
            for i in range(801)
        ]
        result = detect_conflicts(templates, INTENT)
        self.assertTrue(result["skipped_pairwise"])
        self.assertEqual(result["near_duplicates"], [])
        self.assertEqual(result["online_count"], 801)

    def test_not_skipped_at_limit(self):
        """恰好 800 条不跳过。"""
        templates = [
            _tpl(f"t{i}", content=f"第{i}号正文内容", pid=f"P{i}")
            for i in range(800)
        ]
        result = detect_conflicts(templates, INTENT)
        self.assertFalse(result["skipped_pairwise"])


class TestEdgeCases(unittest.TestCase):
    """边界输入"""

    def test_empty_input(self):
        result = detect_conflicts([], INTENT)
        self.assertEqual(result["exact_conflicts"], [])
        self.assertEqual(result["fuzzy_shadows"], [])
        self.assertEqual(result["near_duplicates"], [])
        self.assertFalse(result["skipped_pairwise"])

    def test_none_input(self):
        result = detect_conflicts(None, INTENT)
        self.assertEqual(result["online_count"], 0)

    def test_non_dict_entries_ignored(self):
        result = detect_conflicts(["not a dict", None, _tpl("t1", pid="P1")], INTENT)
        self.assertEqual(result["online_count"], 1)

    def test_template_id_fallback(self):
        """无 template_id 时用 template_name，再无则用下标兜底。"""
        t1 = _tpl("", content="正文甲", pid="P1")
        t1.pop("template_id")
        t1["template_name"] = "命名模板"
        t2 = _tpl("", content="完全不同的另一段内容乙", pid="P1")
        t2.pop("template_id")
        t2.pop("template_name")
        result = detect_conflicts([t1, t2], INTENT)
        conflict = result["exact_conflicts"][0]
        self.assertIn("命名模板", conflict["template_ids"])
        self.assertIn("index_1", conflict["template_ids"])


if __name__ == "__main__":
    unittest.main()
