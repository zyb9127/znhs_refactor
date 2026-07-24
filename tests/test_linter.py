"""
test_linter — management/config_agent/linter.py 单元测试

逐检查项构造反例断言 code 命中（E101/E102/E103/E201/W101-W107/I101），
另验证干净配置不误报与 stats 统计。不调用任何真实网络/ES/Redis/LLM。

运行：cd ROOT && python -m unittest tests.test_linter -v
"""
import unittest

from management.config_agent.linter import (
    lint_api_nodes,
    lint_biz_config,
    lint_template,
)


def _make_template(**overrides):
    """构造一条各检查项全通过的基准模板。"""
    tpl = {
        "template_id": "tpl_base_001",
        "template_name": "基准模板",
        "template_content": "您好，您当前套餐是{cur_brief}，推荐{pkg_brief}。",
        "intent": "套餐推荐",
        "province": "shandong",
        "product_id": "",
        "scene": "",
        "stage": "",
        "status": "online",
        "linked_vars": ["cur_brief", "pkg_brief"],
        "priority": 0,
    }
    tpl.update(overrides)
    return tpl


def _codes(report):
    """收集报告中出现过的全部 code 集合。"""
    codes = set()
    for level in ("errors", "warnings", "info"):
        for item in report.get(level, []):
            codes.add(item["code"])
    return codes


def _items_of(report, code):
    """取指定 code 的全部条目。"""
    out = []
    for level in ("errors", "warnings", "info"):
        for item in report.get(level, []):
            if item["code"] == code:
                out.append(item)
    return out


class TestLintTemplate(unittest.TestCase):
    """单模板检查项（lint_template 入口）"""

    PROVINCE = "shandong"
    INTENT = "套餐推荐"

    def _lint(self, tpl):
        return lint_template(tpl, self.PROVINCE, self.INTENT)

    def test_clean_template_no_issue(self):
        """基准模板不应触发任何 error/warning。"""
        report = self._lint(_make_template())
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["warnings"], [])

    def test_e101_missing_content(self):
        report = self._lint(_make_template(template_content=""))
        items = _items_of(report, "E101")
        self.assertTrue(any("template_content" in i["path"] for i in items))

    def test_e101_missing_name(self):
        report = self._lint(_make_template(template_name=None))
        items = _items_of(report, "E101")
        self.assertTrue(any("template_name" in i["path"] for i in items))

    def test_e102_invalid_status(self):
        report = self._lint(_make_template(status="published"))
        self.assertIn("E102", _codes(report))

    def test_e102_empty_status_is_legal(self):
        """status 缺省/空串按 online 处理，不报 E102。"""
        tpl = _make_template()
        del tpl["status"]
        self.assertNotIn("E102", _codes(self._lint(tpl)))
        self.assertNotIn("E102", _codes(self._lint(_make_template(status=""))))

    def test_w101_province_mismatch(self):
        report = self._lint(_make_template(province="beijing"))
        items = _items_of(report, "W101")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["level"], "warning")

    def test_w101_empty_province_no_warning(self):
        """模板 province 为空不比较，不告警。"""
        report = self._lint(_make_template(province=""))
        self.assertNotIn("W101", _codes(report))

    def test_w102_intent_mismatch(self):
        report = self._lint(_make_template(intent="流量包推荐"))
        self.assertIn("W102", _codes(report))

    def test_w103_mixed_placeholder_syntax(self):
        report = self._lint(_make_template(
            template_content="您好{cur_brief}，尾号{{PHONE}}",
        ))
        self.assertIn("W103", _codes(report))

    def test_w103_single_syntax_only_no_warning(self):
        report = self._lint(_make_template(
            template_content="您好{cur_brief}，推荐{pkg_brief}",
        ))
        self.assertNotIn("W103", _codes(report))

    def test_w104_unknown_placeholder(self):
        report = self._lint(_make_template(
            template_content="您好{cur_brief}，{zzz_unknown_var_xyz}",
        ))
        items = _items_of(report, "W104")
        self.assertEqual(len(items), 1)
        self.assertIn("zzz_unknown_var_xyz", items[0]["message"])

    def test_w104_linked_var_placeholder_is_known(self):
        """占位符在本模板 linked_vars 内即视为已知，即便不是标准变量。"""
        report = self._lint(_make_template(
            template_content="活动信息：{my_custom_var}",
            linked_vars=["my_custom_var"],
        ))
        self.assertNotIn("W104", _codes(report))
        # 但该自定义名会命中 W105（linked_vars 未知变量名）
        self.assertIn("W105", _codes(report))

    def test_w104_generated_vars_known(self):
        """生成变量（table/reason_line 等）不告警。"""
        report = self._lint(_make_template(
            template_content="差异如下：{table}，理由：{reason_line}",
            linked_vars=[],
        ))
        self.assertNotIn("W104", _codes(report))

    def test_subfield_known_root_no_warning(self):
        """子字段占位符 {域[子键]} 根域为已知标准域 → 不告警。"""
        report = self._lint(_make_template(
            template_content="您当前{current_package[offerName]}，"
                             "流量{usage[data_usage][近6月平均流量(GB)]}，标签{tags[融合状态]}",
            linked_vars=[],
        ))
        self.assertNotIn("W104", _codes(report))

    def test_subfield_unknown_root_warns(self):
        """子字段占位符根域未知 → 触发 W104（提示无法取值）。"""
        report = self._lint(_make_template(
            template_content="乱写{zzz_bad_root[xx]}",
            linked_vars=[],
        ))
        items = _items_of(report, "W104")
        self.assertTrue(any("zzz_bad_root" in i["message"] for i in items))

    def test_w108_malformed_rename_target(self):
        """W108：field_rename / _unit_conversions.new_field 目标名含畸形括号 → 巡检告警。"""
        from management.config_agent.linter import lint_api_nodes
        api_nodes = {
            "节点A": {
                "response_extract": {"raw_tags": "bean.tags"},
                "field_transform": {
                    "usage.data_usage": {
                        "from": "raw_tags", "type": "filter_include",
                        "field_rename": {"近6月平均流量(MB）": "近6月平均流量((GB)）"},
                    },
                    "_unit_conversions": [
                        {"target_path": "usage.data_usage", "field": "近6月平均流量(MB）",
                         "new_field": "近6月平均流量((GB)）", "converter": "mb_to_gb"},
                    ],
                },
            },
        }
        report = lint_api_nodes(api_nodes, "beijing", "套餐推荐")
        items = _items_of(report, "W108")
        self.assertEqual(len(items), 2)   # field_rename + _unit_conversions 各一条
        self.assertTrue(all("近6月平均流量(GB)" in i["message"] for i in items))
        # 规范名不告警
        api_nodes["节点A"]["field_transform"]["usage.data_usage"]["field_rename"] = {
            "近6月平均流量(MB）": "近6月平均流量(GB)"}
        api_nodes["节点A"]["field_transform"]["_unit_conversions"][0]["new_field"] = "近6月平均流量(GB)"
        report2 = lint_api_nodes(api_nodes, "beijing", "套餐推荐")
        self.assertEqual(_items_of(report2, "W108"), [])

    def test_w105_unknown_linked_var(self):
        report = self._lint(_make_template(
            linked_vars=["cur_brief", "zzz_not_a_var"],
        ))
        items = _items_of(report, "W105")
        self.assertEqual(len(items), 1)
        self.assertIn("zzz_not_a_var", items[0]["message"])

    def test_i101_missing_priority(self):
        tpl = _make_template()
        del tpl["priority"]
        report = self._lint(tpl)
        items = _items_of(report, "I101")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["level"], "info")

    def test_i101_priority_zero_present(self):
        """priority=0 也算已设置，不提示 I101。"""
        report = self._lint(_make_template(priority=0))
        self.assertNotIn("I101", _codes(report))

    def test_non_dict_template(self):
        report = lint_template("not a dict", self.PROVINCE, self.INTENT)
        self.assertIn("E101", _codes(report))


class TestLintBizConfig(unittest.TestCase):
    """跨模板检查项与统计（lint_biz_config 入口）"""

    PROVINCE = "shandong"
    INTENT = "套餐推荐"

    def _lint(self, templates):
        return lint_biz_config(
            {"script_templates_v2": templates}, self.PROVINCE, self.INTENT
        )

    def test_e103_duplicate_template_id(self):
        report = self._lint([
            _make_template(template_id="dup_id", scene="a"),
            _make_template(template_id="dup_id", scene="b"),
        ])
        items = _items_of(report, "E103")
        self.assertEqual(len(items), 1)
        self.assertIn("script_templates_v2[1]", items[0]["path"])

    def test_e103_empty_ids_not_duplicate(self):
        """template_id 为空的多条模板不算重复。"""
        report = self._lint([
            _make_template(template_id="", scene="a"),
            _make_template(template_id="", scene="b"),
        ])
        self.assertNotIn("E103", _codes(report))

    def test_w107_same_dims_online_conflict(self):
        report = self._lint([
            _make_template(template_id="t1", product_id="P1", stage="s", scene="c"),
            _make_template(template_id="t2", product_id="P1", stage="s", scene="c"),
        ])
        items = _items_of(report, "W107")
        self.assertEqual(len(items), 1)
        self.assertIn("t1", items[0]["message"])
        self.assertIn("t2", items[0]["message"])

    def test_w107_offline_not_conflict(self):
        """offline 模板不参与冲突分组。"""
        report = self._lint([
            _make_template(template_id="t1", product_id="P1"),
            _make_template(template_id="t2", product_id="P1", status="offline"),
        ])
        self.assertNotIn("W107", _codes(report))

    def test_path_contains_index(self):
        """单模板问题的 path 应带列表下标。"""
        report = self._lint([
            _make_template(),
            _make_template(template_id="t2", template_content=""),
        ])
        items = _items_of(report, "E101")
        self.assertTrue(any("script_templates_v2[1]" in i["path"] for i in items))

    def test_stats(self):
        report = self._lint([
            _make_template(template_id="t1", product_id="P1", scene="c1"),
            _make_template(template_id="t2", product_id="P1", scene="c1"),
            _make_template(template_id="t3", product_id="P2", scene="c2",
                           status="offline"),
        ])
        stats = report["stats"]
        self.assertEqual(stats["template_total"], 3)
        self.assertEqual(stats["online_count"], 2)
        self.assertEqual(stats["scene_count"], 2)
        self.assertEqual(stats["product_count"], 2)
        self.assertEqual(stats["max_conflict_group"], 2)

    def test_empty_biz_config(self):
        report = lint_biz_config({}, self.PROVINCE, self.INTENT)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["stats"]["template_total"], 0)


class TestLintApiNodes(unittest.TestCase):
    """api_nodes 检查项（E201/W106）"""

    PROVINCE = "shandong"
    INTENT = "套餐推荐"

    def _lint(self, api_nodes):
        return lint_api_nodes(api_nodes, self.PROVINCE, self.INTENT)

    def test_e201_missing_from_slot(self):
        report = self._lint({
            "main_api": {
                "url": "http://example.local/api",
                "response_extract": {"raw_pkg": "bean.mainoffer"},
                "field_transform": {
                    "current_package": {"from": "missing_slot", "type": "passthrough"},
                },
            },
        })
        items = _items_of(report, "E201")
        self.assertEqual(len(items), 1)
        self.assertIn("main_api.field_transform.current_package", items[0]["path"])

    def test_e201_default_from_equals_target(self):
        """rule 无 from 时缺省取目标路径本身，不在 response_extract 中则报错。"""
        report = self._lint({
            "main_api": {
                "response_extract": {"raw_pkg": "bean.mainoffer"},
                "field_transform": {"usage.data_usage": {"type": "passthrough"}},
            },
        })
        self.assertIn("E201", _codes(report))

    def test_e201_valid_from_no_error(self):
        report = self._lint({
            "main_api": {
                "response_extract": {"raw_pkg": "bean.mainoffer"},
                "field_transform": {
                    "current_package": {"from": "raw_pkg", "type": "passthrough"},
                },
            },
        })
        self.assertNotIn("E201", _codes(report))

    def test_e201_direct_zero_config_exempt(self):
        """direct 零配置节点（无 response_extract）豁免 E201。"""
        report = self._lint({
            "direct_node": {
                "source_type": "direct",
                "field_transform": {
                    "current_package": {"from": "whatever", "type": "passthrough"},
                },
            },
        })
        self.assertNotIn("E201", _codes(report))

    def test_e201_ignores_underscore_meta_keys(self):
        """field_transform 内下划线开头的 meta 键（_unit_conversions 是 list 而非转换规则）
        不应触发 E201 —— 回归北京标准配置误报 from 槽位 '_unit_conversions' 不存在。"""
        report = self._lint({
            "main_api": {
                "response_extract": {"raw_tags": "bean.tags"},
                "field_transform": {
                    "usage.data_usage": {"from": "raw_tags", "type": "filter_include"},
                    "_unit_conversions": [
                        {"target_path": "usage.data_usage", "field": "近3月平均流量(MB）",
                         "new_field": "近3月平均流量(GB)", "converter": "mb_to_gb"},
                    ],
                },
            },
        })
        self.assertNotIn("E201", _codes(report))

    def test_w106_dangling_slot(self):
        report = self._lint({
            "main_api": {
                "response_extract": {
                    "raw_tags": "bean.tags",              # 悬空：非标准域且无引用
                    "current_package": "bean.mainoffer",  # 标准域：自动透传
                    "raw_pkg": "bean.pkg",                # 被 field_transform 引用
                    "_comment_slot": "bean.x",            # 下划线开头：忽略
                },
                "field_transform": {
                    "user_profile": {"from": "raw_pkg", "type": "passthrough"},
                },
            },
        })
        items = _items_of(report, "W106")
        self.assertEqual(len(items), 1)
        self.assertIn("raw_tags", items[0]["path"])

    def test_underscore_node_ignored(self):
        """下划线开头的节点 key 是注释/元数据，整体忽略。"""
        report = self._lint({
            "_comment": {"field_transform": {"x": {"from": "nope"}}},
        })
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["stats"]["node_count"], 0)

    def test_stats(self):
        report = self._lint({
            "a": {"enabled": True},
            "b": {"enabled": False},
            "_meta": {},
        })
        self.assertEqual(report["stats"]["node_count"], 2)
        self.assertEqual(report["stats"]["enabled_count"], 1)


class TestReportShape(unittest.TestCase):
    """报告结构与条目字段完整性"""

    def test_item_fields(self):
        report = lint_biz_config(
            {"script_templates_v2": [_make_template(template_content="")]},
            "shandong", "套餐推荐",
        )
        for level in ("errors", "warnings", "info"):
            self.assertIn(level, report)
            for item in report[level]:
                self.assertIn("code", item)
                self.assertIn("level", item)
                self.assertIn("path", item)
                self.assertIn("message", item)
        self.assertIn("stats", report)


if __name__ == "__main__":
    unittest.main()
