"""空槽位占位符不被去 Markdown 逻辑吃掉（services.llm_service._clean_llm_output）。

背景（山东生产反馈）：`**` 同时是 Markdown 加粗语法、系统空槽位占位符
（prompt.script_generation.DEFAULT_SLOT_PLACEHOLDER），以及山东等省写在模板正文里
表示「此处填数值」的记号（山东 online 模板中 186 条含 `**`）。原通用去加粗正则
``\\*{1,2}([^*]+)\\*{1,2}`` 会把相邻两个 `**` 当成一对加粗标记连同中间文字一起脱壳：
    "您现在的月均消费**元，月流量**GB，主叫时长**分钟"
 →  "您现在的月均消费元，月流量GB，主叫时长**分钟"
数值静默消失、且随 `**` 个数奇偶变化（落单的反而留下），生产表现为
「话术缺数字、只剩 元/GB 单位」。

修复口径：话术链路传 preserve_slot_placeholder=True，按「占位符优先」清洗——
只剥离能安全判定为排版的加粗（整行小标题 / 字段名+冒号），其余星号串一律保留；
非话术链路（接口映射、refine_mapping 等）保持默认 False，行为与改动前完全一致。
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from services.llm_service import LLMService


def _script(raw: str) -> str:
    """话术链路口径。"""
    return LLMService._clean_llm_output(raw, preserve_slot_placeholder=True)


def _other(raw: str) -> str:
    """非话术链路（默认）口径。"""
    return LLMService._clean_llm_output(raw)


class TestPlaceholderSurvivesCleaning(unittest.TestCase):
    """话术链路：占位符必须原样保留，一个星号都不能少。"""

    def test_shandong_template_placeholders_kept(self) -> None:
        """山东营销切入模板正文自带 3 个 `**`，照抄后不得被成对吃掉。"""
        raw = "我看您这**月流量已用超**元啦，您关注过吗？咱超出后**元/G，太贵了。"
        self.assertEqual(_script(raw), raw)

    def test_missing_digit_artifact_fixed(self) -> None:
        """回归山东反馈的「月均消费元 / 月流量GB」缺数字形态。"""
        raw = "您现在的月均消费**元，月流量**GB，主叫时长**分钟。"
        out = _script(raw)
        self.assertEqual(out, raw)
        self.assertNotIn("月均消费元", out)
        self.assertNotIn("月流量GB", out)

    def test_regenerate_sample_kept(self) -> None:
        """换一换后模型自行补的资费句：占位符保留，不能塌成空白。"""
        raw = "推荐的套餐是 **，不仅月均消费比您现在的 ** 更划算，还多出了 ** 的流量。"
        self.assertEqual(_script(raw), raw)

    def test_multi_asterisk_run_kept(self) -> None:
        """连续多个星号（两个占位符相邻）整串保留。"""
        raw = "您当前套餐是****，月费**元。"
        self.assertEqual(_script(raw), raw)

    def test_odd_even_count_all_kept(self) -> None:
        """无论 `**` 个数奇偶，全部保留（原 bug 是偶数被吃、奇数残留）。"""
        for n in range(1, 6):
            raw = "月费" + "**元，流量".join(["**"] * n) + "。"
            self.assertEqual(raw.count("**"), _script(raw).count("**"), f"n={n}")


class TestRealMarkdownStillStripped(unittest.TestCase):
    """话术链路：能安全判定为排版的 Markdown 仍要清掉，避免其他省话术出现脏符号。"""

    def test_heading_line_stripped(self) -> None:
        out = _script("**推荐话术**\n您好，推荐您办理58元套餐。")
        self.assertNotIn("**", out)
        self.assertIn("推荐话术", out)

    def test_label_with_colon_stripped(self) -> None:
        self.assertEqual(
            _script("**话术**：您好，推荐您办理58元套餐。"),
            "您好，推荐您办理58元套餐。",
        )

    def test_whole_line_bold_wrapper_stripped(self) -> None:
        self.assertEqual(
            _script("**我明白，您这次确实是不小心使用超出的，您说对吧。**"),
            "我明白，您这次确实是不小心使用超出的，您说对吧。",
        )

    def test_single_asterisk_italic_still_stripped(self) -> None:
        """单个 * 不是占位符，斜体照常清理。"""
        self.assertEqual(
            _script("这个套餐*非常划算*，推荐您办理。"),
            "这个套餐非常划算，推荐您办理。",
        )

    def test_heading_hash_still_stripped(self) -> None:
        self.assertEqual(_script("# 话术\n您好。"), "话术\n您好。")

    def test_inline_bold_preserved_by_design(self) -> None:
        """句中加粗与占位符完全同形、无法区分 —— 按占位符保留（宁可留符号，不丢事实）。"""
        raw = "这个套餐**非常划算**，推荐您办理。"
        self.assertEqual(_script(raw), raw)


class TestOtherChainsUnchanged(unittest.TestCase):
    """非话术链路默认口径不变：接口映射等场景行为与改动前逐字一致。"""

    def test_default_still_strips_bold_pairs(self) -> None:
        self.assertEqual(
            _other("您现在的月均消费**元，月流量**GB，主叫时长**分钟。"),
            "您现在的月均消费元，月流量GB，主叫时长**分钟。",
        )

    def test_json_output_without_chinese_kept(self) -> None:
        """接口映射返回纯 JSON（无中文）不应被置空。"""
        self.assertEqual(_other('{"a": 1}'), '{"a": 1}')

    def test_non_chinese_non_json_blanked(self) -> None:
        self.assertEqual(_other("no chinese here"), "")

    def test_script_chain_also_blanks_non_chinese(self) -> None:
        """话术链路同样保留「无中文即异常」兜底。"""
        self.assertEqual(_script("no chinese here"), "")

    def test_empty_input(self) -> None:
        for fn in (_script, _other):
            self.assertEqual(fn(""), "")
            self.assertEqual(fn("   "), "")


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self._payload = {"choices": [{"message": {"content": content}}]}
        self.status_code = 200
        self.text = json.dumps(self._payload, ensure_ascii=False)

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    is_closed = False

    def __init__(self, content: str) -> None:
        self._content = content

    async def post(self, url, headers=None, data=None, timeout=None):
        return _FakeResponse(self._content)


class TestGenerateWiring(unittest.IsolatedAsyncioTestCase):
    """接线回归：话术链路（script_step）确实把开关传进了 generate()。

    只验证 generate() → _clean_llm_output 的参数透传，不调真实网关。
    """

    RAW = "您现在的月均消费**元，月流量**GB，主叫时长**分钟。"

    def _make_service(self, content: str):
        import sys

        from services.llm_service import LLMService

        # services/__init__.py 把单例实例 llm_service 挂到包命名空间，会遮蔽同名子模块，
        # 故模块对象必须从 sys.modules 取
        svc_mod = sys.modules["services.llm_service"]
        svc = LLMService(
            "shandong",
            config_override={
                "url": "http://fake-llm/v1/chat/completions",
                "model": "qwen-plus",
                "timeout": 5,
                "max_retries": 1,
            },
        )

        async def _client(cls):
            return _FakeHttpClient(content)

        orig_get_client = LLMService._get_client
        orig_stats = svc_mod.llm_stats
        LLMService._get_client = classmethod(_client)
        svc_mod.llm_stats = mock.MagicMock()
        svc._log_province_llm = lambda *a, **k: None

        def _restore() -> None:
            LLMService._get_client = orig_get_client
            svc_mod.llm_stats = orig_stats

        self.addCleanup(_restore)
        return svc

    async def test_script_chain_keeps_placeholders(self) -> None:
        svc = self._make_service(self.RAW)
        out = await svc.generate(
            "写一句山东话术", province="shandong", preserve_slot_placeholder=True,
        )
        self.assertEqual(out, self.RAW)

    async def test_default_chain_behaviour_unchanged(self) -> None:
        """不传开关时与改动前逐字一致，接口映射等链路零影响。"""
        svc = self._make_service(self.RAW)
        out = await svc.generate("写一句山东话术", province="shandong")
        self.assertEqual(out, "您现在的月均消费元，月流量GB，主叫时长**分钟。")

    async def test_script_step_passes_the_flag(self) -> None:
        """ScriptStep._generate_llm 必须带上 preserve_slot_placeholder=True。"""
        import inspect

        from steps.script_step import ScriptStep

        src = inspect.getsource(ScriptStep._generate_llm)
        # 两个分支（有/无信号量）都要传
        self.assertEqual(src.count("preserve_slot_placeholder=True"), 2, src)


if __name__ == "__main__":
    unittest.main()
