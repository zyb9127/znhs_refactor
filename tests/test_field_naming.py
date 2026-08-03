"""utils.field_naming.autofill_usage_renames 单测

覆盖「上游给用量字段加了『实际』前缀、配置只补 include_keys 漏写 field_rename」这类漂移
的保存态自愈：为 usage.* 的『实际』include_key 自动补『去前缀规范名』重命名，
使运行产出键对齐话术模板占位符；同时保证幂等、尊重既有 rename、不误伤 exclude/非 usage 规则。
"""
from __future__ import annotations

import copy
import unittest

from utils.field_naming import autofill_usage_renames


def _node(ft):
    return {"n_api": {"field_transform": ft}}


class TestAutofillUsageRenames(unittest.TestCase):
    def test_fills_actual_prefixed_include_keys(self) -> None:
        nodes = _node({
            "usage.data_usage": {
                "type": "filter_include",
                "include_keys": ["实际近6月平均流量（GB）"],
            },
        })
        added = autofill_usage_renames(nodes)
        self.assertEqual(len(added), 1)
        rn = nodes["n_api"]["field_transform"]["usage.data_usage"]["field_rename"]
        self.assertEqual(rn["实际近6月平均流量（GB）"], "近6月平均流量(GB)")

    def test_respects_existing_rename(self) -> None:
        nodes = _node({
            "usage.voice_usage": {
                "type": "filter_include",
                "include_keys": ["实际近6月平均语音（分钟）"],
                "field_rename": {"实际近6月平均语音（分钟）": "近6月平均主叫时长"},
            },
        })
        added = autofill_usage_renames(nodes)
        self.assertEqual(added, [])   # 已有显式 rename → 不动
        rn = nodes["n_api"]["field_transform"]["usage.voice_usage"]["field_rename"]
        self.assertEqual(rn["实际近6月平均语音（分钟）"], "近6月平均主叫时长")

    def test_idempotent(self) -> None:
        nodes = _node({
            "usage.consumption": {
                "type": "filter_include",
                "include_keys": ["实际近6月平均消费（元）"],
            },
        })
        first = autofill_usage_renames(nodes)
        snapshot = copy.deepcopy(nodes)
        second = autofill_usage_renames(nodes)
        self.assertTrue(first)
        self.assertEqual(second, [])
        self.assertEqual(nodes, snapshot)   # 二次调用不再改动

    def test_skips_non_usage_and_exclude_rules(self) -> None:
        nodes = _node({
            "tags": {
                "type": "filter_exclude",
                "exclude_keys": ["实际近6月平均消费（元）"],
            },
            "current_package": {
                "type": "filter_include",
                "include_keys": ["实际套餐名"],   # 非 usage 域 → 不处理
            },
        })
        added = autofill_usage_renames(nodes)
        self.assertEqual(added, [])
        self.assertNotIn("field_rename", nodes["n_api"]["field_transform"]["tags"])
        self.assertNotIn(
            "field_rename", nodes["n_api"]["field_transform"]["current_package"])

    def test_ignores_keys_without_actual_prefix(self) -> None:
        nodes = _node({
            "usage.data_usage": {
                "type": "filter_include",
                # 无「实际」前缀，仅括号形态差异 → 交运行时模糊匹配，不生成 rename
                "include_keys": ["近6月平均流量（GB）"],
            },
        })
        self.assertEqual(autofill_usage_renames(nodes), [])
        self.assertNotIn(
            "field_rename", nodes["n_api"]["field_transform"]["usage.data_usage"])

    def test_robust_to_bad_shapes(self) -> None:
        self.assertEqual(autofill_usage_renames(None), [])
        self.assertEqual(autofill_usage_renames({"_meta": 1}), [])
        self.assertEqual(autofill_usage_renames({"n": {"field_transform": "x"}}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
