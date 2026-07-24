"""
模板冲突/重复检测器（设计态治理工具，纯内存计算，不做任何网络/存储调用）

对齐规格书 §8：
- exact_conflicts : 同一 (product_id, stage, scene) 维度下的多条 online 模板（运行时只会命中一条，其余被遮蔽）
- fuzzy_shadows   : 多关键词 product_id 模板的关键词与其他模板的精确 product_id 互包含（模糊档可能遮蔽/被遮蔽，仅提示）
- near_duplicates : 归一化正文（去占位符/空白）后高度相似的模板对（difflib ratio > 0.9）

约束：仅当同意图 online 模板数 <= _PAIRWISE_LIMIT 时执行两两比较，否则跳过并在结果标记 skipped_pairwise=true。
"""
from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

# 两两比较的规模上限（超过则跳过 near_duplicates，避免 O(n^2) 卡死）
_PAIRWISE_LIMIT = 800

# 相似度阈值（严格大于）
_SIMILARITY_THRESHOLD = 0.9

# product_id 多关键词拆分（与 steps/script_step.py `_fuzzy_match_pid` 一致：逗号/中文逗号/换行）
_PID_SPLIT_RE = re.compile(r"[,，\n]+")

# 占位符（{var} 与 {{var}} 两种语法，与规格 §7 的提取 regex 一致）
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}|\{(\w+)\}")

# 空白字符
_WHITESPACE_RE = re.compile(r"\s+")


def _template_id_of(tpl: Dict[str, Any], idx: int) -> str:
    """取模板标识：template_id > template_name > 列表下标兜底。"""
    return str(
        tpl.get("template_id")
        or tpl.get("template_name")
        or f"index_{idx}"
    )


def _priority_of(tpl: Dict[str, Any]) -> int:
    """priority 缺省 0，非法值按 0 处理（与 template_index 桶内排序语义一致）。"""
    try:
        return int(tpl.get("priority") or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_content(content: str) -> str:
    """归一化模板正文：去掉 {var}/{{var}} 占位符与全部空白，用于近似重复比较。"""
    text = _PLACEHOLDER_RE.sub("", content or "")
    return _WHITESPACE_RE.sub("", text)


def _split_keywords(pid: str) -> List[str]:
    """按逗号/换行拆分 product_id 关键词（复刻 _fuzzy_match_pid 的拆分规则）。"""
    return [k.strip() for k in _PID_SPLIT_RE.split(pid or "") if k.strip()]


def _online_pool(
    templates_v2: List[Dict[str, Any]], intent: str
) -> List[Tuple[int, Dict[str, Any]]]:
    """预过滤：intent 为空或等于传入 intent，且 status（缺省 online）为 online。

    返回 [(原始下标, 模板)]，保持列表顺序（下标用于 list_order 判胜与标识兜底）。
    """
    pool: List[Tuple[int, Dict[str, Any]]] = []
    for idx, tpl in enumerate(templates_v2 or []):
        if not isinstance(tpl, dict):
            continue
        t_intent = str(tpl.get("intent") or "").strip()
        if t_intent and t_intent != str(intent or "").strip():
            continue
        status = str(tpl.get("status") or "online").strip()
        if status != "online":
            continue
        pool.append((idx, tpl))
    return pool


def _detect_exact_conflicts(
    pool: List[Tuple[int, Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """按 (product_id, stage, scene) 归一化分组，组内 >1 条即冲突。

    winner 规则与 engine/template_index.py 桶内排序一致：priority 降序，其次列表顺序。
    winner_reason：winner 的 priority 严格大于第二名时为 "priority"，否则 "list_order"。
    """
    groups: Dict[Tuple[str, str, str], List[Tuple[int, Dict[str, Any]]]] = {}
    for idx, tpl in pool:
        key = (
            str(tpl.get("product_id") or "").strip(),
            str(tpl.get("stage") or "").strip(),
            str(tpl.get("scene") or "").strip(),
        )
        groups.setdefault(key, []).append((idx, tpl))

    conflicts: List[Dict[str, Any]] = []
    for (pid, stage, scene), members in groups.items():
        if len(members) <= 1:
            continue
        # 排序 key = (-priority, 原始顺序)
        ranked = sorted(members, key=lambda item: (-_priority_of(item[1]), item[0]))
        winner_idx, winner_tpl = ranked[0]
        second_tpl = ranked[1][1]
        if _priority_of(winner_tpl) > _priority_of(second_tpl):
            winner_reason = "priority"
        else:
            winner_reason = "list_order"
        conflicts.append({
            "dims": {"product_id": pid, "stage": stage, "scene": scene},
            "template_ids": [_template_id_of(t, i) for i, t in members],
            "winner": _template_id_of(winner_tpl, winner_idx),
            "winner_reason": winner_reason,
        })
    return conflicts


def _detect_fuzzy_shadows(
    pool: List[Tuple[int, Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """多关键词 product_id 模板的关键词与其他模板的精确 product_id 互包含 → 提示级信息。

    互包含语义照抄 _fuzzy_match_pid：小写后 kw in pid 或 pid in kw。
    """
    shadows: List[Dict[str, Any]] = []
    seen: set = set()
    for idx, tpl in pool:
        keywords = _split_keywords(str(tpl.get("product_id") or ""))
        if len(keywords) < 2:
            # 单关键词/空 pid 不视为"多关键词模糊模板"
            continue
        fuzzy_id = _template_id_of(tpl, idx)
        for kw in keywords:
            kw_lower = kw.lower()
            for other_idx, other in pool:
                if other_idx == idx:
                    continue
                other_pid = str(other.get("product_id") or "").strip()
                if not other_pid or len(_split_keywords(other_pid)) > 1:
                    # 只与"精确 product_id"（单值）的模板比较
                    continue
                o_lower = other_pid.lower()
                if kw_lower in o_lower or o_lower in kw_lower:
                    dedup_key = (fuzzy_id, kw, other_pid)
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    shadows.append({
                        "fuzzy_template_id": fuzzy_id,
                        "keyword": kw,
                        "shadowed_exact_pid": other_pid,
                    })
    return shadows


def _detect_near_duplicates(
    pool: List[Tuple[int, Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """归一化正文后两两比较，SequenceMatcher ratio > 0.9 判为近似重复。

    归一化后为空的正文（纯占位符模板）不参与比较，避免噪声。
    """
    normalized: List[Tuple[int, str, str]] = []  # (idx, template_id, 归一化正文)
    for idx, tpl in pool:
        norm = _normalize_content(str(tpl.get("template_content") or ""))
        if not norm:
            continue
        normalized.append((idx, _template_id_of(tpl, idx), norm))

    duplicates: List[Dict[str, Any]] = []
    for i in range(len(normalized)):
        _, id_a, text_a = normalized[i]
        for j in range(i + 1, len(normalized)):
            _, id_b, text_b = normalized[j]
            ratio = difflib.SequenceMatcher(None, text_a, text_b).ratio()
            if ratio > _SIMILARITY_THRESHOLD:
                duplicates.append({
                    "template_ids": [id_a, id_b],
                    "similarity": round(ratio, 4),
                })
    return duplicates


def detect_conflicts(
    templates_v2: Optional[List[Dict[str, Any]]], intent: str
) -> Dict[str, Any]:
    """检测同意图 online 模板的冲突与重复。

    参数:
        templates_v2: biz_config["script_templates_v2"] 模板列表
        intent:       意图名（模板 intent 为空视为通配，参与该意图检测）

    返回:
        {
          "exact_conflicts": [{"dims": {...}, "template_ids": [...],
                               "winner": id, "winner_reason": "priority"|"list_order"}],
          "fuzzy_shadows":   [{"fuzzy_template_id":, "keyword":, "shadowed_exact_pid":}],
          "near_duplicates": [{"template_ids": [a, b], "similarity": 0.93}],
          "skipped_pairwise": bool,   # online 模板数超限时为 True（跳过 near_duplicates）
          "online_count": int,
        }
    """
    pool = _online_pool(templates_v2 or [], intent)

    result: Dict[str, Any] = {
        "exact_conflicts": _detect_exact_conflicts(pool),
        "fuzzy_shadows": _detect_fuzzy_shadows(pool),
        "near_duplicates": [],
        "skipped_pairwise": False,
        "online_count": len(pool),
    }

    if len(pool) > _PAIRWISE_LIMIT:
        result["skipped_pairwise"] = True
        logger.info(
            f"[ConflictDetector] online 模板数 {len(pool)} 超过 {_PAIRWISE_LIMIT}，"
            f"跳过两两近似重复比较"
        )
    else:
        result["near_duplicates"] = _detect_near_duplicates(pool)

    return result
