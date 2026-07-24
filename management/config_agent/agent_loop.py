"""
配置智能体多轮工具调用循环（人在环，只产生 proposal 不落盘）

消息格式约定照抄 management/interface_mapper/scripts/agent_runner.py：
- assistant 消息带 tool_calls 原样入历史
- tool 结果消息 {"role": "tool", "tool_call_id", "name", "content"}，content 截断 4000 字符
- arguments 为 JSON 字符串时 json.loads，解析失败把错误字符串回传给模型

外部接口：
  run_config_agent(user_messages, province, intent, max_turns) -> dict
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from loguru import logger

from management.config_agent.agent_tools import TOOLS, execute_tool
from prompt.config_agent import (
    CONFIG_AGENT_CONTEXT_SUFFIX,
    CONFIG_AGENT_SYSTEM_BASE,
)

# 工具结果入历史的截断长度（与 agent_runner.py 一致，防止上下文过长）
_TOOL_RESULT_MAX_LEN = 4000

# tool_trace 中结果摘要的截断长度
_RESULT_BRIEF_LEN = 200

# system prompt 基础文本（提示词已外置到 prompt/config_agent.py，此处保留别名兼容旧引用）
_SYSTEM_PROMPT_BASE = CONFIG_AGENT_SYSTEM_BASE


def _build_system_prompt(province: str, intent: str) -> str:
    """拼装 system prompt，附加当前会话的省份/意图上下文。"""
    prompt = _SYSTEM_PROMPT_BASE
    if province or intent:
        prompt += CONFIG_AGENT_CONTEXT_SUFFIX.format(
            province=province or "未指定",
            intent=intent or "未指定",
        )
    return prompt


async def run_config_agent(
    user_messages: List[Dict],
    province: str = "",
    intent: str = "",
    max_turns: int = 8,
) -> Dict:
    """配置智能体主循环。

    Args:
        user_messages: 对话消息列表 [{"role": "user"|"assistant", "content": str}, ...]
        province:      会话上下文省份（可空）
        intent:        会话上下文意图（可空）
        max_turns:     最大 LLM 轮数（每轮可含多个工具调用）

    Returns:
        {"reply": str, "proposals": [...], "tool_trace": [{"tool", "args", "result_brief"}], "turns": int}
    """
    from services.llm_service import llm_service  # 延迟 import，避免模块加载期服务依赖

    proposals: List[Dict[str, Any]] = []          # 会话级提议列表（不持久化）
    tool_trace: List[Dict[str, Any]] = []
    history: List[Dict[str, Any]] = [
        dict(m) for m in (user_messages or []) if isinstance(m, dict)
    ]
    if not history:
        return {"reply": "请提供对话消息", "proposals": [], "tool_trace": [], "turns": 0}

    system_prompt = _build_system_prompt(province, intent)
    reply = ""
    last_text = ""
    turns = 0

    for turn in range(1, max_turns + 1):
        turns = turn
        logger.info(f"[ConfigAgent] 轮次 {turn}/{max_turns} province={province} intent={intent}")

        llm_resp = await llm_service.chat_with_tools(
            system=system_prompt,
            messages=history,
            tools=TOOLS,
            stage=f"config_agent.iter{turn}",
        )

        # 网关失败 → 优雅降级
        if llm_resp is None:
            logger.error("[ConfigAgent] chat_with_tools 返回 None，降级结束")
            reply = "模型服务暂不可用，请稍后重试"
            break

        tool_calls = llm_resp.get("tool_calls") or []
        text_content = (llm_resp.get("content") or "").strip()
        if text_content:
            last_text = text_content

        # 无工具调用 → 最终回答
        if not tool_calls:
            reply = text_content
            break

        # assistant 消息（带 tool_calls）入历史
        history.append({
            "role":       "assistant",
            "content":    text_content,
            "tool_calls": tool_calls,
        })

        # 逐个执行工具调用
        for tc in tool_calls:
            tool_name = tc.get("function", {}).get("name", "")
            tool_args_raw = tc.get("function", {}).get("arguments", "{}")
            tool_call_id = tc.get("id", f"call_{turn}")

            # 解析 arguments，异常把错误字符串回传给模型
            parse_error = ""
            try:
                if isinstance(tool_args_raw, str):
                    tool_args = json.loads(tool_args_raw) if tool_args_raw.strip() else {}
                else:
                    tool_args = tool_args_raw or {}
                if not isinstance(tool_args, dict):
                    parse_error = f"arguments 应为 JSON 对象，实际为 {type(tool_args).__name__}"
                    tool_args = {}
            except Exception as exc:  # noqa: BLE001 - 解析失败回传模型修正
                parse_error = f"arguments 解析失败: {exc}"
                tool_args = {}

            logger.info(f"[ConfigAgent] 调用工具: {tool_name} args_keys={list(tool_args.keys())}")

            if parse_error:
                tool_result: Any = {"error": parse_error, "tool": tool_name}
            else:
                tool_result = await execute_tool(tool_name, tool_args, proposals)

            result_str = (
                tool_result
                if isinstance(tool_result, str)
                else json.dumps(tool_result, ensure_ascii=False, default=str)
            )

            tool_trace.append({
                "tool":         tool_name,
                "args":         tool_args,
                "result_brief": result_str[:_RESULT_BRIEF_LEN],
            })
            history.append({
                "role":         "tool",
                "tool_call_id": tool_call_id,
                "name":         tool_name,
                "content":      result_str[:_TOOL_RESULT_MAX_LEN],
            })
    else:
        # 达到最大轮数仍有未收敛的工具调用
        logger.warning(f"[ConfigAgent] 达到最大轮数 {max_turns}")
        reply = last_text or "已达到最大工具调用轮数，请精简问题或分步提问后重试"

    return {
        "reply":      reply,
        "proposals":  proposals,
        "tool_trace": tool_trace,
        "turns":      turns,
    }


__all__ = ["run_config_agent"]
