"""本地日志留存开关（logging.local_file_enabled / 环境变量 ZNHS_LOCAL_LOG）单测。

验证解析优先级：环境变量 > config.json > 默认 True，以及 province_logger 在开关关闭时
不落本地 jsonl（用于降低提示词/出参等敏感信息在容器本地磁盘的留存风险）。
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from utils.config_loader import config_loader, local_log_enabled


class TestLocalLogEnabled(unittest.TestCase):
    def setUp(self) -> None:
        # 保存原值再清空：直接 pop 会把跑测试时外部显式设置的 ZNHS_LOCAL_LOG 一并抹掉，
        # 污染同一进程内后续依赖该开关的用例（如 test_province_logger 断言 jsonl 已落盘）。
        self._orig_env = os.environ.get("ZNHS_LOCAL_LOG")
        os.environ.pop("ZNHS_LOCAL_LOG", None)

    def tearDown(self) -> None:
        if self._orig_env is None:
            os.environ.pop("ZNHS_LOCAL_LOG", None)
        else:
            os.environ["ZNHS_LOCAL_LOG"] = self._orig_env

    def test_default_true(self) -> None:
        """既有行为：不设开关时默认落本地日志。"""
        with mock.patch.object(config_loader, "get", return_value=True):
            self.assertTrue(local_log_enabled())

    def test_config_false_disables(self) -> None:
        with mock.patch.object(config_loader, "get", return_value=False):
            self.assertFalse(local_log_enabled())

    def test_env_off_overrides_config(self) -> None:
        for val in ("0", "false", "no", "off", "OFF"):
            os.environ["ZNHS_LOCAL_LOG"] = val
            with mock.patch.object(config_loader, "get", return_value=True):
                self.assertFalse(local_log_enabled(), val)

    def test_env_on_overrides_config(self) -> None:
        for val in ("1", "true", "yes", "on"):
            os.environ["ZNHS_LOCAL_LOG"] = val
            with mock.patch.object(config_loader, "get", return_value=False):
                self.assertTrue(local_log_enabled(), val)

    def test_missing_config_falls_back_true(self) -> None:
        """config 读到 None（键缺失）时按默认 True，不因缺配置丢日志。"""
        with mock.patch.object(config_loader, "get", return_value=None):
            # get 的 default 参数是 True，这里模拟真实缺键返回 default
            self.assertTrue(local_log_enabled())


class TestProvinceLoggerRespectsToggle(unittest.TestCase):
    def test_disabled_skips_local_write(self) -> None:
        from utils import province_logger as pl

        with mock.patch.object(pl, "_local_file_enabled", return_value=False), \
                mock.patch.object(pl, "_write_one") as write_one:
            pl._write("shandong", "llm.jsonl", {"a": 1})
            write_one.assert_not_called()

    def test_enabled_writes_local(self) -> None:
        from utils import province_logger as pl

        with mock.patch.object(pl, "_local_file_enabled", return_value=True), \
                mock.patch.object(pl, "_ENABLED", True), \
                mock.patch.object(pl, "_write_one") as write_one:
            pl._write("shandong", "llm.jsonl", {"a": 1})
            write_one.assert_called_once()


if __name__ == "__main__":
    unittest.main()
