"""
配置端智能 agent 包（设计态）

导出确定性 lint 与冲突检测函数（agent_tools/agent_loop/router 由并行任务提供，
此处不 import，避免包初始化引入服务依赖）。
"""
from management.config_agent.linter import (
    lint_api_nodes,
    lint_biz_config,
    lint_package,
    lint_template,
)
from management.config_agent.conflict_detector import detect_conflicts

__all__ = [
    "lint_biz_config",
    "lint_api_nodes",
    "lint_template",
    "lint_package",
    "detect_conflicts",
    "router",
    "run_config_agent",
]


def __getattr__(name):
    """router 与 run_config_agent 惰性导出（PEP 562）。

    避免包初始化即引入 fastapi / skill_registry / LLM 服务依赖，
    保证 tests 中仅使用 linter/conflict_detector 的场景零副作用；
    main.py 可直接 `from management.config_agent import router` 挂载。
    """
    if name == "router":
        from management.config_agent.router import router as _router
        # 子模块 router 导入完成时 import 机制会把包属性 router 设为该子模块，
        # 这里显式覆盖为 APIRouter 实例，保证 from management.config_agent import router 语义稳定
        globals()["router"] = _router
        return _router
    if name == "run_config_agent":
        from management.config_agent.agent_loop import run_config_agent as _run_config_agent
        globals()["run_config_agent"] = _run_config_agent
        return _run_config_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
