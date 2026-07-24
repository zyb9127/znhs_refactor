"""分省接口日志（province_logger.log_api_call）单测。"""
import json
import tempfile
import unittest
from pathlib import Path

from utils import province_logger as pl


class TestLogApiCall(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_root = pl._LOG_ROOT
        pl._LOG_ROOT = Path(self._tmp.name)
        pl._cleaned.clear()

    def tearDown(self):
        pl._LOG_ROOT = self._orig_root
        self._tmp.cleanup()

    def _read_last(self, province: str) -> dict:
        f = Path(self._tmp.name) / province / f"api_{pl._day()}.jsonl"
        return json.loads(f.read_text(encoding="utf-8").strip().splitlines()[-1])

    def test_success_call_logged_with_masking(self):
        pl.log_api_call(
            "beijing", "套餐推荐", "t1", "13800138000",
            api_name="北京测试接口_api", url="http://x/productRecommend", method="POST",
            request={"params": {"phone": "13800138000", "intent": "套餐推荐"}},
            response={"rtnCode": "0", "bean": {"recommend_results": [{"offerId": "1"}]}},
            elapsed_ms=12.3, timeout_s=30.0, error=None,
        )
        rec = self._read_last("beijing")
        self.assertTrue(rec["success"])
        self.assertEqual(rec["phone"], "138****8000")
        self.assertEqual(rec["request"]["params"]["phone"], "138****8000")  # 内嵌手机号脱敏
        self.assertEqual(rec["api_name"], "北京测试接口_api")
        self.assertEqual(rec["elapsed_ms"], 12.3)
        self.assertEqual(rec["response"]["bean"]["recommend_results"], [{"offerId": "1"}])

    def test_error_call_logged(self):
        pl.log_api_call(
            "shandong", "套餐推荐", "t2", "15012342072",
            api_name="node", url="http://y", method="POST",
            request={"a": 1}, response=None,
            elapsed_ms=30000.0, timeout_s=30.0, error="timeout>30.0s",
        )
        rec = self._read_last("shandong")
        self.assertFalse(rec["success"])
        self.assertEqual(rec["error"], "timeout>30.0s")
        self.assertIsNone(rec["response"])

    def test_large_response_truncated(self):
        big = {"blob": "x" * (pl._MAX_TEXT + 500)}
        pl.log_api_call(
            "beijing", "套餐推荐", "t3", "13800138000",
            api_name="node", request={}, response=big, elapsed_ms=1.0,
        )
        rec = self._read_last("beijing")
        self.assertIsInstance(rec["response"], str)      # 体积超限降级为截断字符串
        self.assertIn("truncated", rec["response"])


if __name__ == "__main__":
    unittest.main()
