"""
output_guard 组件 —— 话术输出护栏（合规护栏的最小落点）

纯字符串处理，无 LLM、无网络调用。params 结构：

    {
      "forbidden_words": ["禁词1", "禁词2"],   # 敏感词列表
      "max_length": 100,                        # 话术最大长度（超长按句号边界截断）
      "action": "mask" | "drop" | "flag"        # 命中禁词的处理方式，默认 flag
    }

- mask：命中的禁词替换为等长的 *；
- drop：移除该条话术；
- flag：话术保持原样，仅在 ctx.metadata["guard_flags"] 登记命中信息；
- 所有命中（含 mask/drop）都会在 ctx.metadata["guard_flags"] 留痕；
- max_length 生效时超长话术按最后一个句号边界截断，无句号则硬截断；
- params 缺省（无禁词且无 max_length）时组件为 no-op，行为与未配置等价。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Dict, List

from loguru import logger

from engine.component import StepComponent
from engine.registry import register_component

if TYPE_CHECKING:  # 仅类型标注用
    from core.context import FlowContext

# 命中禁词的合法处理方式
_ACTIONS = ("mask", "drop", "flag")


def _truncate_by_sentence(text: str, max_length: int) -> str:
    """超长截断：优先按 max_length 范围内最后一个句号收尾，无句号则硬截断"""
    if len(text) <= max_length:
        return text
    cut = text[:max_length]
    pos = cut.rfind("。")
    if pos >= 0:
        return cut[: pos + 1]
    return cut


@register_component
class OutputGuardComponent(StepComponent):
    """话术输出护栏组件：敏感词处理（mask/drop/flag）+ 超长按句号边界截断，默认 no-op"""

    name: ClassVar[str] = "output_guard"

    config_schema: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {
            "forbidden_words": {
                "type": "array",
                "items": {"type": "string"},
                "description": "敏感词列表，命中后按 action 处理",
            },
            "max_length": {
                "type": "integer",
                "description": "话术最大长度，超长按句号边界截断；不设置则不限长",
            },
            "action": {
                "type": "string",
                "enum": list(_ACTIONS),
                "description": "命中禁词的处理方式：mask=替换为*，drop=移除该条，flag=仅标记（默认）",
            },
        },
    }

    async def run(
        self,
        ctx: "FlowContext",
        skill_config: Dict[str, Any],
        params: Dict[str, Any],
    ) -> None:
        params = params or {}
        forbidden = [str(w) for w in (params.get("forbidden_words") or []) if str(w)]
        max_length = params.get("max_length")
        if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length <= 0:
            max_length = 0
        action = str(params.get("action") or "flag").strip().lower()
        if action not in _ACTIONS:
            logger.warning(f"[OutputGuard] 未知 action={action}，回退为 flag")
            action = "flag"

        # 无任何护栏参数 → no-op（默认行为，与未配置该组件等价）
        if not forbidden and not max_length:
            return

        kept: List[Dict[str, Any]] = []
        flags: List[Dict[str, Any]] = []

        for item in ctx.marketing_scripts:
            if not isinstance(item, dict):
                kept.append(item)
                continue
            text = str(item.get("marketing_text", "") or "")
            hits = [w for w in forbidden if w in text]
            if hits:
                flags.append(
                    {
                        "product_id": item.get("product_id", ""),
                        "rank": item.get("rank"),
                        "hits": hits,
                        "action": action,
                    }
                )
                if action == "drop":
                    continue  # 移除该条话术
                if action == "mask":
                    for word in hits:
                        text = text.replace(word, "*" * len(word))
            if max_length and len(text) > max_length:
                text = _truncate_by_sentence(text, max_length)
            item["marketing_text"] = text
            kept.append(item)

        ctx.marketing_scripts = kept
        if flags:
            ctx.metadata.setdefault("guard_flags", []).extend(flags)
            logger.info(
                f"[OutputGuard] 命中禁词 {len(flags)} 条 action={action} "
                f"剩余话术 {len(kept)} 条"
            )
