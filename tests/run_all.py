"""
tests.run_all —— 统一测试入口

用法：cd ROOT && python -m tests.run_all
行为：unittest discover 收集 tests/ 下全部 test_*.py，任何失败/错误以非零退出码结束
（供 CI / 编排脚本判定全绿）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# tests/ 目录与项目根（保证以 tests.xxx 包形式导入，与手工 python -m unittest 一致）
TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent


def main() -> int:
    """发现并运行全部测试，返回进程退出码（0=全绿，1=有失败/错误）"""
    # 确保项目根在 sys.path（直接 python tests/run_all.py 运行时也可用）
    root_str = str(ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    loader = unittest.TestLoader()
    suite = loader.discover(
        start_dir=str(TESTS_DIR),
        pattern="test_*.py",
        top_level_dir=root_str,
    )
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
