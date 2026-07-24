"""
话术模板变量自动推断工具

根据话术模板内容，推断需要关联的 context 变量（linked_vars）。

推断策略（双层，取并集）：
1. 精确 key 匹配：模板中出现 {xxx} 格式占位符，直接映射到 context 变量 key
2. 语义关键词扫描：对模板全文扫描关键词，命中则关联对应 context 变量

无任何占位符 + 无关键词命中 → 返回空列表（不关联任何变量）

Context 变量列表（与 FlowContext 数据域对齐）：
  current_package  — 当前套餐信息
  pkg_brief        — 推荐产品信息（单条，话术生成层）
  usage            — 历史用量（流量/语音/消费）
  tags             — 用户标签
  diff_str         — 套餐差异
  user_info        — 用户基础信息
  user_profile     — 用户画像
  domain_ext       — 扩展信息
  extra_info       — 主服务补充信息
  extra_context    — 模板匹配上下文
  table            — 差异表格（仅话术展示）
"""
from __future__ import annotations

import re
from typing import List, Set

# ── 标准 key 别名映射 ─────────────────────────────────────────
# {xxx} 占位符的 key → context 变量 key
# 同时覆盖 script_step 里的历史别名（cur_brief / usage_line / user_tags 等）
_KEY_ALIAS: dict = {
    # current_package 系
    "cur_brief":        "current_package",
    "current_package":  "current_package",
    "cur_name":         "current_package",
    # pkg_brief 系
    "pkg_brief":        "pkg_brief",
    "pkg_name":         "pkg_brief",
    "recommended":      "pkg_brief",
    "recommend":        "pkg_brief",
    # usage 系
    "usage":            "usage",
    "usage_line":       "usage",
    "data_usage":       "usage",
    "voice_usage":      "usage",
    "consumption":      "usage",
    # tags 系
    "tags":             "tags",
    "user_tags":        "tags",
    # diff 系
    "diff_str":         "diff_str",
    "diff":             "diff_str",
    # 推荐套餐派生变量（单位归一，engine.prompt_builder 计算）
    "pkg_fee":          "pkg_fee",
    "pkg_flow":         "pkg_flow",
    "pkg_voice":        "pkg_voice",
    # table
    "table":            "table",
    # user_info
    "user_info":        "user_info",
    # user_profile
    "user_profile":     "user_profile",
    # domain_ext
    "domain_ext":       "domain_ext",
    # extra_info
    "extra_info":       "extra_info",
    # extra_context
    "extra_context":    "extra_context",
}

# ── 语义关键词 → context 变量 ─────────────────────────────────
# 每条规则：(关键词列表, context变量key)
# 关键词匹配整词或子串（中文分词不友好，直接用 in 包含匹配）
_SEMANTIC_RULES: List[tuple] = [
    # ── current_package：当前套餐 ──────────────────────────
    (["当前套餐", "现有套餐", "您现在的套餐", "您的套餐", "原套餐",
      "当前消费", "您现在消费"], "current_package"),

    # ── pkg_brief：推荐产品信息 ────────────────────────────
    (["推荐套餐", "推荐您", "建议您升级", "建议升级", "为您推荐","向您推荐"
      "推荐办理", "办理这个", "网龄回馈", "云宽带套餐",
      "升级办理", "这个活动", "这个业务", "这个套餐",
      "推荐产品", "推荐方案", "合约方案","消费不低于","活动合约期","福利","网龄回馈倍享礼","每月承诺","合约期内"], "pkg_brief"),

    # ── usage：历史用量 ────────────────────────────────────
    (["月流量", "流量用", "流量已用", "流量超", "流量饱和",
      "近3月", "近6月", "平均消费", "平均月消费", "通信消费",
      "话费账单", "月均消费", "平均流量", "用量", "使用情况",
      "消费情况", "每月话费", "账单消费", "消费稳定","当前消费","流量资源",
      "平均通信消费", "折后收入", "主叫时长"], "usage"),

    # ── tags：用户标签 ─────────────────────────────────────
    (["网龄", "老用户", "用户标签", "用户特征",
      "是否超套", "超套客户", "融合状态", "双卡", "老旧套餐"], "tags"),

    # ── diff_str：套餐差异 ─────────────────────────────────
    (["套餐差异", "对比", "相比", "差异", "比现在",
      "多了", "少了", "升级后", "变化"], "diff_str"),

    # ── user_info：用户基础信息 ────────────────────────────
    (["用户基础", "用户信息", "基础信息", "客户信息",
      "星级", "网龄年限", "入网时间", "用户等级"], "user_info"),

    # ── user_profile：用户画像 ─────────────────────────────
    (["用户画像", "使用偏好", "流量偏好", "行为特征"], "user_profile"),

    # ── domain_ext：扩展信息 ──────────────────────────────
    (["扩展信息", "合约信息", "当前合约", "参加的活动",
      "当前活动", "订购信息", "家庭业务"], "domain_ext"),

    # ── extra_info：主服务补充信息 ────────────────────────
    (["补充信息", "extra_info", "附加信息"], "extra_info"),

    # ── extra_context：模板匹配上下文 ─────────────────────
    (["匹配上下文", "extra_context", "模板上下文", "触发场景"], "extra_context"),
]


def infer_linked_vars(template_content: str) -> List[str]:
    """
    根据话术模板内容推断需要关联的 context 变量列表。

    Args:
        template_content: 话术模板文本

    Returns:
        去重有序的 context 变量 key 列表；无任何匹配返回 []

    Examples:
        >>> infer_linked_vars("{cur_brief}，{pkg_brief}，{usage_line}")
        ['current_package', 'pkg_brief', 'usage']

        >>> infer_linked_vars("根据您近6个月平均通信消费XX元，建议您升级办理***元云宽带套餐")
        ['usage', 'pkg_brief']

        >>> infer_linked_vars("没关系，我先帮您算算费用，您觉得合适再办。")
        []
    """
    if not template_content or not template_content.strip():
        return []

    matched: Set[str] = set()

    # ── 第一层：精确 key 匹配（{xxx} 格式） ──────────────────
    std_keys = re.findall(r"\{(\w+)\}", template_content)
    for key in std_keys:
        # 精确 key 别名映射
        ctx_var = _KEY_ALIAS.get(key.lower())
        if ctx_var:
            matched.add(ctx_var)
        # 如果 key 本身就是有效的 context 变量 key，直接使用
        elif key in {
            "current_package", "pkg_brief", "usage", "tags",
            "diff_str", "table", "user_info", "user_profile",
            "domain_ext", "extra_info", "extra_context",
        }:
            matched.add(key)

    # ── 第二层：语义关键词扫描（全文包含匹配） ─────────────────
    for keywords, ctx_var in _SEMANTIC_RULES:
        for kw in keywords:
            if kw in template_content:
                matched.add(ctx_var)
                break  # 该规则已命中，不需要再检查其他关键词

    # ── 返回有序列表（保持固定顺序便于展示） ─────────────────────
    _ORDER = [
        "current_package", "pkg_brief", "pkg_fee", "pkg_flow", "pkg_voice",
        "usage", "tags",
        "diff_str", "user_info", "user_profile", "domain_ext",
        "extra_info", "extra_context", "table",
    ]
    result = [v for v in _ORDER if v in matched]
    # 追加 _ORDER 中没有的（理论上不会有，保险起见）
    for v in sorted(matched):
        if v not in result:
            result.append(v)
    return result
