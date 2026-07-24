"""
核心管道模块
三步线性管道：数据采集 → 推荐筛选 → 话术生成
"""
from core.context import FlowContext
from core.pipeline import MarketingPipeline

__all__ = ["FlowContext", "MarketingPipeline"]
