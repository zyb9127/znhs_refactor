"""
RecommendStep — Step2: 推荐筛选

职责：
从 ctx.recommended_packages（Step1 采集的上游推荐产品）中，
按策略选出 TopN 最终推荐结果，写入 ctx.final_recommendations。

策略选择：
- biz_config.strategy.default_strategy 配置驱动（默认 "direct"）
- 当前支持策略: direct（按上游 rank 升序取 TopN，不修改 rank 值）
- 扩展：在 recommend_strategy.py 中增加新策略并注册到 STRATEGY_MAP，无需修改此文件
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from loguru import logger

from core.context import FlowContext
from plugins.recommend_strategy import get_strategy
from utils.observability import record_stage


class RecommendStep:
    """推荐筛选步骤

    通过 plugins/recommend_strategy.py 中的策略对象执行筛选，
    策略可插拔，当前默认 DirectStrategy（按 rank 取 TopN）。
    """

    def __init__(self, province: str = "default") -> None:
        self.province = province

    @staticmethod
    def _clean_package(pkg: Dict[str, Any]) -> Dict[str, Any]:
        """去除产品 dict 中的空值字段（空字符串 / 空列表 / None）"""
        return {k: v for k, v in pkg.items() if v not in ("", [], None)}

    @staticmethod
    def _caller_supplied_count(ctx: FlowContext) -> int:
        """调用方在 extra_info 里直传的产品数（非直传返回 0）。

        topN 的语义是「从推荐接口返回的大候选池里取前 N 个」。直传模式下候选已由
        上游挑定并整包传入，再按 topN 截断会静默少出话术（广东 18 个产品只出 8 条
        即为此），故直传时不截断，传多少个产品就生成多少条话术。

        产品数组的键名不写死（营销助手统一接口叫 products），判定与 ScriptStep 的
        「直传不限并发」共用 FlowContext.caller_supplied_package_count。
        """
        return ctx.caller_supplied_package_count()

    async def run(
        self,
        ctx: FlowContext,
        strategy: str = "direct",
    ) -> None:
        """执行推荐筛选，结果写入 ctx.final_recommendations

        Args:
            ctx:      FlowContext，需已填充 recommended_packages / top_n
            strategy: 策略名称，对应 STRATEGY_MAP 中的 key，默认 "direct"
        """
        t0 = time.perf_counter()
        packages = ctx.recommended_packages
        top_n    = ctx.top_n

        # 直传模式：产品列表由调用方给定，topN 不参与截断（仍按 rank 排序）
        direct_count = self._caller_supplied_count(ctx)
        if direct_count and top_n < len(packages):
            logger.info(
                f"[RecommendStep] 直传模式（extra_info.recommended_packages "
                f"{direct_count} 个产品）：忽略 topN={top_n} 的截断，全量生成话术"
            )
            top_n = len(packages)

        logger.info(
            f"[RecommendStep] strategy={strategy} "
            f"candidates={len(packages)} top_n={top_n}"
        )

        if not packages:
            logger.warning("[RecommendStep] 候选产品为空，推荐结果为空")
            ctx.final_recommendations = []
            return

        degrade = False
        try:
            strategy_obj = get_strategy(strategy)
            results = await strategy_obj.execute(
                packages=packages,
                ctx=ctx,
                top_n=top_n,
            )
            ctx.final_recommendations = [self._clean_package(p) for p in results]
            logger.info(
                f"[RecommendStep] ✅ 推荐完成，共 {len(ctx.final_recommendations)} 个产品 "
                f"strategy={strategy}"
            )
        except Exception as exc:
            degrade = True
            logger.error(f"[RecommendStep] ❌ 推荐失败: {exc}，回退 direct 策略")
            ctx.add_error(f"推荐步骤失败: {exc}")
            # 降级：按 rank 升序直取，保留原始 rank 值
            ctx.final_recommendations = [
                self._clean_package(p)
                for p in sorted(packages, key=lambda x: int(x.get("rank") or 9999))[:top_n]
            ]
        finally:
            record_stage(
                stage="recommend_step",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                cache_hit=False,
                provider=f"recommend.{strategy}",
                degrade_flag=degrade,
                recommendation_count=len(ctx.final_recommendations),
            )
