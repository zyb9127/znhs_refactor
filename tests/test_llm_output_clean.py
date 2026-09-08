"""LLM 输出清洗回归测试。"""

import unittest

from services.llm_service import LLMService


class TestLlmOutputClean(unittest.TestCase):
    def test_keeps_independent_slot_placeholders(self):
        raw = "我看您现在是**元套餐，您是移动18年**星级客户。"
        self.assertEqual(LLMService._clean_llm_output(raw), raw)

    def test_still_removes_local_bold_markers(self):
        raw = "请关注**专属优惠**，现在即可办理。"
        self.assertEqual(
            LLMService._clean_llm_output(raw),
            "请关注专属优惠，现在即可办理。",
        )
