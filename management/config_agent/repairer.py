"""
api_nodes 配置自愈修复器（基于 ES 当前配置，重新校验 → 发现问题 → 自动生成修正配置）

与 linter.py 配套：linter 只"发现问题"，repairer 负责"生成修正"。
修复依据是坏配置里的自证线索——field_transform 声明了它需要的 from 槽位
（如 raw_tags），而节点自带的 mock_response（接口出参样例）里就有对应字段
（bean.tags）：按名探测出 JSON 路径补回 response_extract，无需外部真源
（本地文件 / 历史版本）。

可自动修复的问题：
  ① E201 悬空 from 槽位：field_transform 引用的 from（如 raw_tags）不在
     response_extract → 在 mock_response 顶层/常见业务载体（bean/data/result/body）
     探测同名字段（raw_ 前缀自动脱掉再试，raw_tags → tags）→ 补回映射路径。
     —— 北京事故第二形态（raw_tags 丢失 → usage/tags 静默为空）。
  ② 标准域映射缺失：response_extract 已有其他映射、但缺 current_package /
     recommended_packages，而 mock_response 中存在可识别字段（mainoffer /
     recommend_results 等）→ 补回。
     —— 北京事故第一形态（recommended_packages 丢失 → 只回 1 条兜底话术）。
  ③ W108 畸形重命名目标名：双括号/全半角混用 → normalize_api_nodes_renames 规范化。

安全边界：
  - 只修 source_type=api 且已有非空 response_extract 的节点（已是映射模式），
    不把零配置/直传节点"升级"成映射模式，避免改变既有运行语义；
  - 只增不删：绝不移除既有映射；探测不到的问题列入 unfixed，由人工处置；
  - 修复前后各跑一遍 lint，调用方可对比验证问题确实被消除。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from management.config_agent.linter import (
    _STANDARD_DOMAIN_KEYS,
    is_direct_response_path,
    lint_api_nodes,
)
from utils.field_naming import normalize_api_nodes_renames

# mock_response 中常见的业务载体容器 key（与 data_step 零配置透传探测一致）
_CONTAINER_KEYS = ("bean", "data", "result", "body")

# 标准域 → mock_response 中的可识别字段名（与运行时兜底 _salvage_recommendations 对齐）
_STD_DOMAIN_PROBES: Dict[str, Tuple[str, ...]] = {
    "current_package": (
        "current_package", "mainoffer", "main_offer", "currentPackage", "cur_offer",
    ),
    "recommended_packages": (
        "recommended_packages", "recommend_results", "recommendResults", "recommendList",
    ),
}

_EMPTY = (None, "", [], {})


def _probe_path(raw: Any, names: Tuple[str, ...]) -> Optional[str]:
    """在响应样例顶层与常见业务载体下按名探测非空字段，返回 JSON 路径（如 bean.tags）。"""
    if not isinstance(raw, dict):
        return None
    for name in names:
        if not name:
            continue
        if raw.get(name) not in _EMPTY:
            return name
        for ck in _CONTAINER_KEYS:
            sub = raw.get(ck)
            if isinstance(sub, dict) and sub.get(name) not in _EMPTY:
                return f"{ck}.{name}"
    return None


def inline_intermediate_slots(api_nodes: Dict[str, Any]) -> List[str]:
    """就地把 ``from: raw_xxx`` 中间集写法改写成 ``from: <响应路径>`` 直连写法。

    中间集的问题是「一个契约要两处同名才成立」：``response_extract.raw_tags`` 与
    ``field_transform.*.from`` 必须字符串相等，而 ``raw_tags`` 这个名字本身不携带
    「数据从哪来」的信息。任一边丢失，相关映射域一起静默为空——北京两次事故都是
    这个形态。直连写法只有一处、且自描述，物理上不存在「另一半丢了」。

    改写是等价变换：中间集路径下 ``source = extracted[S] = _get_path(raw, P)``，
    直连路径下 ``source = _get_path(raw, P)``，两者恒等（见 DataStep._transform_fields）。

    安全边界（任一不满足即跳过该槽位，宁可不转）：
    - 标准域槽位不动：它们在 _transform_fields 第①步靠 response_extract 自动透传，
      删掉会丢域；
    - 仅当该槽位的引用**全部**是显式 ``from`` 时才删：省略 ``from`` 的规则运行时不走
      路径回退，改写会变更语义；
    - 目标路径与现存槽位名撞名时跳过：改写后会被中间槽位分支优先命中，语义漂移；
    - 路径必须能在节点自带样例出参中取到非空值：与 E201 的直连判定同一条谓词，
      否则转完的配置反而会被巡检判成悬空引用（没样例就无从自证）。

    Returns:
        改写说明列表（"节点: from raw_tags → bean.tags（已移除中间槽位）"）。
    """
    notes: List[str] = []
    if not isinstance(api_nodes, dict):
        return notes
    for node_name, node in api_nodes.items():
        if str(node_name).startswith("_") or not isinstance(node, dict):
            continue
        ext = node.get("response_extract")
        ft = node.get("field_transform")
        if not isinstance(ext, dict) or not isinstance(ft, dict):
            continue
        for slot in list(ext.keys()):
            slot_name = str(slot)
            if slot_name.startswith("_") or slot_name in _STANDARD_DOMAIN_KEYS:
                continue
            path = ext.get(slot)
            if not isinstance(path, str) or not path or path in ext:
                continue
            if not is_direct_response_path(node, path):
                continue
            explicit_refs, implicit_refs = [], 0
            for target, rule in ft.items():
                if str(target).startswith("_") or not isinstance(rule, dict):
                    continue
                if rule.get("from") == slot:
                    explicit_refs.append(target)
                elif rule.get("from") is None and str(target) == slot_name:
                    implicit_refs += 1
            if not explicit_refs or implicit_refs:
                continue
            for target in explicit_refs:
                ft[target]["from"] = path
            ext.pop(slot, None)
            notes.append(
                f"{node_name}: from {slot_name} → {path}（已移除中间槽位，"
                f"影响规则 {', '.join(explicit_refs)}）"
            )
    return notes


def repair_api_nodes(
    api_nodes: Dict[str, Any], province: str = "", intent: str = ""
) -> Dict[str, Any]:
    """校验并自动修复一份 api_nodes 配置（入参不被修改，返回修正后的副本）。

    Returns:
        {
          "config":  修正后的 api_nodes（fixes 为空时与入参等值）,
          "fixes":   [str, ...]  已自动修复项（人话描述）,
          "unfixed": [str, ...]  发现但无法自动修复项（如 mock_response 缺失/探测不到）,
          "lint_before": lint 报告（修复前）,
          "lint_after":  lint 报告（修复后）,
        }
    """
    cfg = copy.deepcopy(api_nodes if isinstance(api_nodes, dict) else {})
    fixes: List[str] = []
    unfixed: List[str] = []
    lint_before = lint_api_nodes(cfg, province, intent)

    for node_name, node in cfg.items():
        if str(node_name).startswith("_") or not isinstance(node, dict):
            continue
        # 只修映射模式的 api 节点：direct/零配置节点语义不同，不自动"升级"
        if (node.get("source_type") or "api") == "direct":
            continue
        ext = node.get("response_extract")
        if not isinstance(ext, dict) or not ext:
            continue
        ft = node.get("field_transform")
        ft = ft if isinstance(ft, dict) else {}
        mock = node.get("mock_response")

        # ① E201 悬空 from 槽位 → mock_response 探测补回
        for target, rule in ft.items():
            if str(target).startswith("_"):
                continue
            explicit_from = rule.get("from") if isinstance(rule, dict) else None
            frm = str(explicit_from or target)
            if frm in ext:
                continue
            # 直连写法（from 直接是响应路径，如 bean.tags）无需中间槽位，不是待修问题
            if explicit_from is not None and is_direct_response_path(node, frm):
                continue
            names: Tuple[str, ...] = (frm,)
            if frm.startswith("raw_"):
                names = (frm, frm[4:])
            path = _probe_path(mock, names)
            if path:
                ext[frm] = path
                fixes.append(
                    f"{node_name}: 补回 response_extract 缺失槽位 {frm!r} → {path!r}"
                    f"（被 field_transform.{target} 引用）"
                )
            else:
                unfixed.append(
                    f"{node_name}: field_transform.{target} 引用的槽位 {frm!r} 缺失，"
                    f"且 mock_response 中探测不到同名字段，需人工补映射"
                )

        # ② 标准域映射缺失 → mock_response 可识别字段探测补回
        for domain, probes in _STD_DOMAIN_PROBES.items():
            if domain in ext:
                continue
            path = _probe_path(mock, probes)
            if path:
                ext[domain] = path
                fixes.append(f"{node_name}: 补回标准域映射 {domain!r} → {path!r}")

    # ③ 畸形重命名目标名规范化（整份配置）
    renames_fixed = normalize_api_nodes_renames(cfg)
    fixes.extend(f"重命名目标名规范化: {r}" for r in renames_fixed)

    lint_after = lint_api_nodes(cfg, province, intent)
    return {
        "config": cfg,
        "fixes": fixes,
        "unfixed": unfixed,
        "lint_before": lint_before,
        "lint_after": lint_after,
    }
