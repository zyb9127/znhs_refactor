"""
TemplateIndex — 话术模板哈希索引（12 档匹配的等价加速 + priority 支持）

背景：
    steps/script_step.py 的 ScriptStep._select_template 对每次选模板都做
    「预过滤 + 三阶段 12 档线性扫描」，模板量大（数百~数千条）时属于热路径开销。
    本模块在读取 biz_config 后一次性构建哈希索引，select() 与旧函数逐语义等价：
    对相同的 (templates_v2, intent, product_id, stage, scene) 输入，
    在所有模板 priority 缺省（=0）时返回与 ScriptStep._select_template 完全相同的模板。

新增能力（仅索引路径生效）：
    模板可选字段 priority（int，缺省 0，越大越优先）——同一 (product_id, stage, scene)
    归一化维度桶内按 (-priority, 列表序) 排序；priority 全为 0 时即原列表序，行为不变。
    注意：模糊匹配池（阶段2）保持原列表顺序，priority 不参与，与旧线性扫描一致。

线程安全：
    索引构建完成后只读，可在 asyncio.to_thread 的工作线程中安全并发调用 select()。

用法：
    index = build_index(templates_v2, ctx.intent)   # 失败返回 None，调用方回退旧线性扫描
    tpl = index.select(product_id, stage, scene) if index else None
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


def _norm(value: Any) -> str:
    """维度值归一化：None → ""，其余 str() 后 strip。

    与 ScriptStep._select_template 内 _match 的 str(x or "").strip() 语义一致。
    """
    return str(value or "").strip()


def fuzzy_match_pid(t_pid: str, req_pid: str) -> bool:
    """产品 ID 模糊匹配：支持模板 product_id 存储多个关键词（逗号/换行分隔）。

    逻辑从 steps/script_step.py 的 _fuzzy_match_pid（L645-668）复制而来
    （复制而非 import，避免 engine 与 steps 的循环依赖）：
    - 任一为空返回 False（空串代表「不限产品」的通用模板，走精确/兜底路径）
    - 模板 product_id 按 [,，\\n]+ 正则拆成多个关键词
    - 每个关键词与请求 product_id 小写后互相包含，任一命中即 True
    """
    if not t_pid or not req_pid:
        return False
    r_lower = req_pid.lower()
    # 模板 product_id 按逗号、换行拆分为多个关键词，逐一尝试
    keywords = [k.strip() for k in re.split(r"[,，\n]+", t_pid) if k.strip()]
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower in r_lower or r_lower in kw_lower:
            return True
    return False


def _priority_of(tpl: Dict[str, Any]) -> int:
    """读取模板 priority 字段，缺省/非法值一律按 0 处理（不因脏数据导致索引构建失败）。"""
    try:
        return int(tpl.get("priority") or 0)
    except (TypeError, ValueError):
        return 0


class TemplateIndex:
    """script_templates_v2 的三阶段哈希索引。

    构建（与 ScriptStep._select_template L616-631 的预过滤逐语义一致）：
    - pool：模板 intent 为空或等于传入 intent，且 status != "deleted"
    - 优先只用 status == "online"（status 缺省视为 online）；
      无 online 时降级用全 pool 并 logger.warning 一次
    - exact_buckets：{(pid, stage, scene) 归一化 strip 后的三元组: [模板...]}
    - fuzzy_pool：归一化 product_id 非空的模板（保持原列表顺序，供阶段2线性扫描）
    - fallback_buckets：exact_buckets 中 pid 为空的子集（阶段3兜底）
    - 桶内排序 key = (-priority, 原列表序)，priority 缺省 0 时即原顺序
    """

    def __init__(self, templates_v2: List[Dict[str, Any]], intent: str) -> None:
        self.intent = intent

        # ── 预过滤（复刻 _select_template 的 pool / online 降级逻辑）──────
        pool = [
            t for t in (templates_v2 or [])
            if (not t.get("intent") or t.get("intent") == intent)
            and t.get("status") != "deleted"
        ]
        online_only = [t for t in pool if t.get("status", "online") == "online"]
        if online_only:
            search_list = online_only
        elif pool:
            search_list = pool
            logger.warning(
                f"[TemplateIndex] intent={intent!r} 无 status=online 的话术模板，"
                f"已降级使用 offline/其他状态模板共 {len(pool)} 条（仍带话术模板进 LLM）"
            )
        else:
            search_list = []

        # pool 为空时 select() 恒返回 None（对应旧逻辑 L621-622 的提前返回）
        self._empty: bool = not search_list

        # ── 构建索引结构 ─────────────────────────────────────────────
        # 桶暂存 (order_idx, tpl)，随后按 (-priority, order_idx) 排序
        raw_buckets: Dict[Tuple[str, str, str], List[Tuple[int, Dict[str, Any]]]] = {}
        # 模糊池条目：(归一化pid, 归一化stage, 归一化scene, 模板)，保持原顺序
        fuzzy_pool: List[Tuple[str, str, str, Dict[str, Any]]] = []

        for order_idx, tpl in enumerate(search_list):
            key = (
                _norm(tpl.get("product_id")),
                _norm(tpl.get("stage")),
                _norm(tpl.get("scene")),
            )
            raw_buckets.setdefault(key, []).append((order_idx, tpl))
            if key[0]:
                fuzzy_pool.append((key[0], key[1], key[2], tpl))

        exact_buckets: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
        for key, items in raw_buckets.items():
            items.sort(key=lambda pair: (-_priority_of(pair[1]), pair[0]))
            exact_buckets[key] = [tpl for _, tpl in items]

        self._exact_buckets = exact_buckets
        # 兜底桶：pid 为空的模板桶（与 exact_buckets 共享列表对象，零拷贝）
        self._fallback_buckets = {
            key: bucket for key, bucket in exact_buckets.items() if key[0] == ""
        }
        self._fuzzy_pool = fuzzy_pool

    # ── 查询 ─────────────────────────────────────────────────────

    def select(
        self,
        product_id: str,
        stage: str = "",
        scene: str = "",
    ) -> Optional[Dict[str, Any]]:
        """按 12 档优先级选模板，与 ScriptStep._select_template 逐语义等价。

        三阶段（每档取桶首/首个命中）：
        阶段1（档1-4）：产品精确 —— (pid,stg,scn)/(pid,stg,"")/(pid,"",scn)/(pid,"","")
        阶段2（档5-8，仅 pid 非空且阶段1未命中）：产品模糊 —— 逐组合线性扫 fuzzy_pool
        阶段3（档9-12）：通用兜底 —— ("",stg,scn)/("",stg,"")/("","",scn)/("","","")
        全未命中返回 None（调用方降级旧 prompts）。
        """
        if self._empty:
            return None

        pid = _norm(product_id)
        stg = _norm(stage)
        scn = _norm(scene)

        # ── 阶段1：产品精确匹配（档1-4，候选组合去重后依次查桶）──────────
        # 旧代码对原始值组合去重、_match 内再归一化；此处先归一化再去重，
        # 归一化后组合集合与扫描顺序与旧逻辑一致（重复组合重扫不改变首个命中结果）。
        exact_candidates = [
            (pid, stg, scn),   # 1. 产品精确+环节+场景
            (pid, stg, ""),    # 2. 产品精确+环节
            (pid, "", scn),    # 3. 产品精确+场景
            (pid, "", ""),     # 4. 仅产品精确
        ]
        seen_exact: List[Tuple[str, str, str]] = []
        for combo in exact_candidates:
            if combo not in seen_exact:
                seen_exact.append(combo)

        for key in seen_exact:
            bucket = self._exact_buckets.get(key)
            if bucket:
                return bucket[0]

        # ── 阶段2：产品模糊匹配（档5-8，仅 pid 非空时触发）────────────────
        if pid:
            fuzzy_stage_scene_combos: List[Tuple[str, str]] = []
            if stg and scn:
                fuzzy_stage_scene_combos.append((stg, scn))   # 5. 模糊+环节+场景
            if stg:
                fuzzy_stage_scene_combos.append((stg, ""))    # 6. 模糊+环节
            if scn:
                fuzzy_stage_scene_combos.append(("", scn))    # 7. 模糊+场景
            fuzzy_stage_scene_combos.append(("", ""))         # 8. 仅模糊产品

            seen_fuzzy: List[Tuple[str, str]] = []
            for combo in fuzzy_stage_scene_combos:
                if combo not in seen_fuzzy:
                    seen_fuzzy.append(combo)

            for f_stg, f_scn in seen_fuzzy:
                for t_pid, t_stg, t_scn, tpl in self._fuzzy_pool:
                    if (fuzzy_match_pid(t_pid, pid)
                            and t_stg == f_stg
                            and t_scn == f_scn):
                        logger.info(
                            f"[TemplateIndex] 产品模糊匹配命中: "
                            f"req_pid={pid!r} → tpl_pid={t_pid!r} "
                            f"stage={f_stg!r} scene={f_scn!r}"
                        )
                        return tpl

        # ── 阶段3：通用兜底（档9-12，模板 pid 为空）─────────────────────
        fallback_candidates = [
            ("", stg, scn),   # 9.  通用+环节+场景
            ("", stg, ""),    # 10. 通用+环节
            ("", "", scn),    # 11. 通用+场景
            ("", "", ""),     # 12. 兜底全空
        ]
        seen_fallback: List[Tuple[str, str, str]] = []
        for combo in fallback_candidates:
            if combo not in seen_fallback:
                seen_fallback.append(combo)

        for key in seen_fallback:
            bucket = self._fallback_buckets.get(key)
            if bucket:
                return bucket[0]

        return None


def build_index(
    templates_v2: List[Dict[str, Any]],
    intent: str,
) -> Optional[TemplateIndex]:
    """构建模板索引；任意异常返回 None 并 warning（调用方回退旧线性扫描）。"""
    try:
        return TemplateIndex(templates_v2, intent)
    except Exception as exc:
        logger.warning(f"[TemplateIndex] 构建模板索引失败，回退旧线性扫描: {exc}")
        return None
