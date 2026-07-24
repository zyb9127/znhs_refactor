"""
推荐策略插件

策略说明：
  direct: 直取策略 — 按上游 rank 升序取 TopN，保留原始 rank 值不变

扩展方式：
  1. 继承 RecommendationStrategy，实现 execute()
  2. 调用 register_strategy(name, cls) 注册到 STRATEGY_MAP
  调用方（RecommendStep）无需修改。

工厂方法：get_strategy(name) 根据 biz_config 中的字符串名称返回策略实例。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from loguru import logger


# ══════════════════════════════════════════════════════════════
# 基类
# ══════════════════════════════════════════════════════════════

class RecommendationStrategy(ABC):
    """推荐策略基类

    新增策略步骤：
    1. 继承本类，实现 execute() 方法
    2. 调用 register_strategy("策略名", YourStrategy) 注册
    """

    @abstractmethod
    async def execute(
        self,
        packages: List[Dict[str, Any]],
        ctx: Any,
        top_n: int,
    ) -> List[Dict[str, Any]]:
        """执行策略，返回截断为 top_n 的推荐列表（rank 值由策略自行决定是否修改）"""
        ...


# ══════════════════════════════════════════════════════════════
# direct — 直取（按上游 rank 升序，保留原始 rank 值）
# ══════════════════════════════════════════════════════════════

class DirectStrategy(RecommendationStrategy):
    """直取策略：按上游 rank 字段升序取 TopN，不修改 rank 值

    适用场景：
    - 上游接口已按业务规则排好序（如北京营销推荐接口）
    - 任何无需二次筛选/打分的省份
    """

    async def execute(
        self,
        packages: List[Dict[str, Any]],
        ctx: Any,
        top_n: int,
    ) -> List[Dict[str, Any]]:
        sorted_pkgs = sorted(packages, key=lambda x: int(x.get("rank") or 9999))
        result = sorted_pkgs[:top_n]
        logger.debug(f"[DirectStrategy] 直取 {len(result)}/{len(packages)}")
        return result


# ══════════════════════════════════════════════════════════════
# 注册表 & 工厂
# ══════════════════════════════════════════════════════════════

# 存储策略单例（策略无状态，类级别共享）
STRATEGY_MAP: Dict[str, RecommendationStrategy] = {
    "direct": DirectStrategy(),
}


def get_strategy(name: str) -> RecommendationStrategy:
    """工厂方法：按名称返回策略实例，未匹配时返回 DirectStrategy

    biz_config.json 中配置 strategy.default_strategy = "direct" 即可选择。
    """
    return STRATEGY_MAP.get(str(name).lower().strip(), STRATEGY_MAP["direct"])


def register_strategy(name: str, instance: RecommendationStrategy) -> None:
    """注册自定义策略实例（省份 main_flow.py 或测试中调用）

    Args:
        name:     策略名称（小写字符串，对应 biz_config 中的配置值）
        instance: 策略实例（需继承 RecommendationStrategy）

    示例：
        class MyStrategy(RecommendationStrategy):
            async def execute(self, packages, ctx, top_n): ...

        register_strategy("my_strategy", MyStrategy())
    """
    STRATEGY_MAP[name] = instance
    logger.info(f"[recommend_strategy] 注册策略: {name} -> {type(instance).__name__}")
