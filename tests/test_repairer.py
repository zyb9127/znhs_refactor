"""
test_repairer — management/config_agent/repairer.py 单元测试

覆盖基于 ES 当前配置的自愈修复：
- 北京事故第二形态：raw_tags 从 response_extract 丢失 → mock_response 探测补回
- 北京事故第一形态：recommended_packages 丢失 → 探测 bean.recommend_results 补回
- 干净配置零改动、无 mock_response 时列 unfixed、direct 节点豁免、入参不被修改
"""
import copy
import json
import unittest

from management.config_agent.repairer import repair_api_nodes

_BEIJING_FILE = "skills-runtime/beijing/套餐推荐/config/api_nodes.json"


def _load_beijing():
    with open(_BEIJING_FILE, encoding="utf-8") as f:
        return json.load(f)


class TestRepairApiNodes(unittest.TestCase):

    def test_restores_missing_raw_tags_from_mock(self):
        """事故形态②：raw_tags 被冲掉 → 依据 field_transform 引用 + mock_response 探测补回。"""
        cfg = _load_beijing()
        del cfg["北京测试接口_api"]["response_extract"]["raw_tags"]
        rep = repair_api_nodes(cfg, "beijing", "套餐推荐")
        fixed_ext = rep["config"]["北京测试接口_api"]["response_extract"]
        self.assertEqual(fixed_ext["raw_tags"], "bean.tags")
        self.assertTrue(any("raw_tags" in fx for fx in rep["fixes"]))
        # 修复前有 E201，修复后干净
        self.assertTrue(rep["lint_before"]["errors"])
        self.assertFalse(rep["lint_after"]["errors"])
        self.assertFalse(rep["unfixed"])

    def test_restores_missing_recommended_packages(self):
        """事故形态①：recommended_packages 被冲掉 → 探测 bean.recommend_results 补回。"""
        cfg = _load_beijing()
        del cfg["北京测试接口_api"]["response_extract"]["recommended_packages"]
        rep = repair_api_nodes(cfg, "beijing", "套餐推荐")
        fixed_ext = rep["config"]["北京测试接口_api"]["response_extract"]
        self.assertEqual(fixed_ext["recommended_packages"], "bean.recommend_results")
        self.assertTrue(any("recommended_packages" in fx for fx in rep["fixes"]))

    def test_clean_config_untouched(self):
        """校验通过的配置：fixes 为空，修正结果与入参等值（幂等安全，可放心不发布）。"""
        cfg = _load_beijing()
        rep = repair_api_nodes(cfg, "beijing", "套餐推荐")
        self.assertEqual(rep["fixes"], [])
        self.assertEqual(rep["unfixed"], [])
        self.assertEqual(rep["config"], cfg)

    def test_unfixable_without_mock_response(self):
        """无 mock_response 可探测 → 问题列入 unfixed，不臆造映射。"""
        cfg = _load_beijing()
        node = cfg["北京测试接口_api"]
        del node["response_extract"]["raw_tags"]
        del node["mock_response"]
        rep = repair_api_nodes(cfg, "beijing", "套餐推荐")
        self.assertEqual(rep["fixes"], [])
        self.assertTrue(any("raw_tags" in u for u in rep["unfixed"]))
        self.assertNotIn("raw_tags", rep["config"]["北京测试接口_api"]["response_extract"])

    def test_direct_node_exempt(self):
        """direct 节点语义不同（extra_info 为源），不做探测修复。"""
        cfg = {
            "直传节点": {
                "source_type": "direct",
                "response_extract": {"current_package": "cur"},
                "field_transform": {"usage": {"from": "raw_x", "type": "passthrough"}},
                "mock_response": {"raw_x": {"a": 1}},
            },
        }
        rep = repair_api_nodes(cfg, "p", "i")
        self.assertEqual(rep["fixes"], [])
        self.assertNotIn("raw_x", rep["config"]["直传节点"]["response_extract"])

    def test_input_not_mutated_and_rename_normalized(self):
        """入参不被修改（deepcopy）；畸形重命名目标名一并规范化。"""
        cfg = _load_beijing()
        cfg["北京测试接口_api"]["field_transform"]["usage.data_usage"][
            "field_rename"]["近3月平均流量(MB）"] = "近3月平均流量((GB)）"
        snapshot = copy.deepcopy(cfg)
        rep = repair_api_nodes(cfg, "beijing", "套餐推荐")
        self.assertEqual(cfg, snapshot)   # 入参原样
        self.assertEqual(
            rep["config"]["北京测试接口_api"]["field_transform"]["usage.data_usage"]
            ["field_rename"]["近3月平均流量(MB）"],
            "近3月平均流量(GB)",
        )
        self.assertTrue(any("规范化" in fx for fx in rep["fixes"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
