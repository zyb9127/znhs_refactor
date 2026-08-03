"""
Interface Mapper Agent Loop

工作原理：ReAct 模式
  1. 加载 SKILL.md 作为 LLM 系统指令（行动手册）
  2. 注册 tools.py 中的所有工具函数（OpenAI Function Calling 格式）
  3. 循环：LLM 决策 → 工具执行 → 结果回馈 → LLM 继续推理
  4. 直到 LLM 不再调用工具或达到最大迭代次数

外部接口：
  run(docx_content_b64, province, intent) -> dict
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from prompt.interface_agent import INTERFACE_AGENT_TASK_TEMPLATE

# ── 路径定位 ──────────────────────────────────────────────────
_MGMT_DIR = Path(__file__).parent.parent   # management/interface_mapper/


class InterfaceMapperAgent:
    """基于 Function Calling 的 ReAct Agent

    执行流：
      SKILL.md 作为 System Prompt
      工具函数集（tools.py）作为可调用能力
      循环：调用 LLM → 解析工具调用 → 执行工具 → 结果入历史 → 继续
    """

    def __init__(
        self,
        skill_doc: str,
        tools: Dict[str, Callable],
        max_iterations: int = 10,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> None:
        self.system_prompt   = skill_doc
        self.tools           = tools                     # name → Python 函数
        self.tool_schemas    = _build_tool_schemas(tools)
        self.max_iterations  = max_iterations
        self.temperature     = temperature
        self.max_tokens      = max_tokens
        self.history: List[Dict[str, Any]] = []

    async def run(self, task: str) -> Dict[str, Any]:
        """启动 Agent Loop，返回最终结果。

        Args:
            task: 用户任务描述（包含 province/intent/docx_content_b64）

        Returns:
            {
              "skill_path": "...",
              "files":      [...],
              "preview":    {...},
              "analysis":   "...",
              "iterations": N,
            }
        """
        # 支持注入自定义 LLM（DashScope 用于接口映射Agent）
        from services.llm_service import llm_service
        llm = getattr(self, 'llm_service', llm_service)

        self.history = [{"role": "user", "content": task}]
        last_result: Dict[str, Any] = {}

        for iteration in range(1, self.max_iterations + 1):
            logger.info(f"[Agent] 迭代 {iteration}/{self.max_iterations} (using {type(llm).__name__})")

            # ── 调用 LLM（带工具定义）────────────────────────────────────────
            llm_resp = await llm.chat_with_tools(
                system=self.system_prompt,
                messages=self.history,
                tools=self.tool_schemas,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stage=f"interface_mapper.iter{iteration}",
            )

            if llm_resp is None:
                logger.error("[Agent] LLM 返回为空，中止")
                break

            tool_calls   = llm_resp.get("tool_calls") or []
            text_content = (llm_resp.get("content") or "").strip()

            # ── LLM 没有再次调用工具 → 已完成 ─────────────────────────────
            if not tool_calls:
                logger.info(f"[Agent] 工具循环结束，最终回答: {text_content[:120]}")
                if last_result:
                    last_result["iterations"]       = iteration
                    last_result["llm_final_answer"] = text_content
                return last_result

            # ── 添加 assistant 回复到历史记录 ─────────────────────────────
            self.history.append({
                "role":       "assistant",
                "content":    text_content,
                "tool_calls": tool_calls,
            })

            # ── 执行每个工具调用 ───────────────────────────────────────────
            for tc in tool_calls:
                tool_name     = tc.get("function", {}).get("name", "")
                tool_args_raw = tc.get("function", {}).get("arguments", "{}")
                tool_call_id  = tc.get("id", f"call_{iteration}")

                # 解析 arguments
                try:
                    tool_args = (
                        json.loads(tool_args_raw)
                        if isinstance(tool_args_raw, str)
                        else tool_args_raw
                    )
                except Exception:
                    tool_args = {}

                logger.info(f"[Agent] 调用工具: {tool_name}  args_keys={list(tool_args.keys())}")

                # 执行工具
                tool_result = await _execute_tool(self.tools, tool_name, tool_args)

                # 记录工具返回（特殊：若 generate_skill 完成，记录为最终结果）
                if tool_name == "generate_skill" and isinstance(tool_result, dict):
                    last_result = tool_result

                result_str = (
                    json.dumps(tool_result, ensure_ascii=False)
                    if not isinstance(tool_result, str)
                    else tool_result
                )

                self.history.append({
                    "role":         "tool",
                    "tool_call_id": tool_call_id,
                    "name":         tool_name,
                    "content":      result_str[:4000],   # 防止过长
                })

        # 达到最大迭代
        logger.warning(f"[Agent] 达到最大迭代 {self.max_iterations}")
        if last_result:
            last_result["iterations"] = self.max_iterations
        return last_result


# ══ 工具注册 ══════════════════════════════════════════════════

def get_all_tools() -> Dict[str, Callable]:
    """返回所有可用工具函数（均来自 tools.py）"""
    from management.interface_mapper.scripts import tools as _tools_module
    return {
        "parse_docx":    _tools_module.parse_docx,
        "match_params":  _tools_module.match_params,
        "map_output":    _tools_module.map_output,
        "detect_units":  _tools_module.detect_units,
        "generate_skill": _tools_module.generate_skill,
    }


def _load_skill_doc() -> str:
    """加载 SKILL.md 作为 Agent 的 System Prompt"""
    return (_MGMT_DIR / "SKILL.md").read_text(encoding="utf-8")


def _load_agent_config() -> Dict:
    cfg_path = _MGMT_DIR / "config" / "agent_config.json"
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


# ══ Function Calling Schema ═══════════════════════════════════

# 工具的手写 OpenAI 格式 Schema
_TOOL_SCHEMAS: List[Dict] = [
    {
        "type": "function",
        "function": {
            "name": "parse_docx",
            "description": "解析 base64 编码的 docx 接口文档，返回结构化接口信息（url/method/headers/input_params/output_params/success_example）",
            "parameters": {
                "type": "object",
                "properties": {
                    "content_b64": {"type": "string", "description": "docx 文件的 base64 内容"}
                },
                "required": ["content_b64"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "match_params",
            "description": (
                "将接口入参列表与主服务占位符进行语义匹配，生成 request_template。"
                "【重要】若 parse_docx 返回了 input_example 字段，必须将其序列化为 JSON 字符串后"
                "通过 input_example_json 参数传入，工具将基于示例的真实嵌套结构递归构建 request_template，"
                "并自动检测 request_body_wrapper（如入参被包裹在 params/body/data 等键中）。"
                "这样可避免嵌套结构错误和重复字段（如 crmpfPubInfo.staffId 变成字面量 key）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "input_params_json":   {"type": "string", "description": "接口入参列表 JSON 字符串"},
                    "service_params_json": {"type": "string", "description": "主服务占位符表 JSON（可省略）"},
                    "input_example_json":  {"type": "string", "description": "入参成功示例 JSON 字符串（parse_docx 返回的 input_example，强烈建议传入以保证结构正确）"},
                },
                "required": ["input_params_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "map_output",
            "description": (
                "接收你分析出参文档后给出的映射结果，进行格式校验和空值过滤（NULL/\"\"过滤，\"0\"保留）。"
                "调用前你需要：1) 阅读出参成功示例，确定实际根节点路径（不一定是bean，可能是data/result/直接平铺等）；"
                "2) 结合出参说明理解每个字段含义，映射到7大标准数据域；"
                "3) 在field_transform中声明平铺/拆分/合并/单位转换等处理规则。"
                "不强制每个域都有结果，按实际出参结构按需映射。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "response_extract_json": {
                        "type": "string",
                        "description": (
                            "你分析后给出的 response_extract JSON 字符串。"
                            "格式：{\"标准域名\": \"实际取数路径\", ...}，路径基于出参示例的真实结构。"
                            "只写整块即可对应标准域的字段；需要分拣的混合对象不要在这里建中间槽，"
                            "交给 field_transform 用真实路径直接取。"
                            "示例：{\"current_package\": \"data.mainOffer\", \"recommended_packages\": \"result.list\"}"
                        ),
                    },
                    "field_transform_json": {
                        "type": "string",
                        "description": (
                            "你分析后给出的 field_transform JSON 字符串。"
                            "支持 passthrough（直接透传）、filter_include（只保留指定key）、filter_exclude（排除指定key）三种类型。"
                            "对需要单位转换的字段在对应规则中加 unit_convert：{\"字段名\": \"mb_to_gb\"|\"fen_to_yuan\"|\"jiao_to_fen\"}。"
                            "from 一律直接写源对象在响应中的真实路径（如 data.userTags），不要引用 raw_xxx 中间槽。"
                            "示例：{\"usage.data_usage\": {\"from\": \"data.userTags\", \"type\": \"filter_include\", \"include_keys\": [\"avgFlow3M\"], \"unit_convert\": {\"avgFlow3M\": \"mb_to_gb\"}}}"
                        ),
                    },
                    "output_params_json": {
                        "type": "string",
                        "description": "出参说明列表 JSON 字符串（可省略，提供后 detect_units 可更准确识别单位）",
                    },
                },
                "required": ["response_extract_json", "field_transform_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_units",
            "description": "扫描 field_transform 中的字段名和出参说明，推断单位转换需求，向 field_transform 注入 unit_convert 规则（MB→GB，分→元）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "field_transform_json": {"type": "string", "description": "当前 field_transform JSON 字符串"},
                    "output_params_json":   {"type": "string", "description": "出参说明列表 JSON 字符串（可省略）"},
                },
                "required": ["field_transform_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_skill",
            "description": "根据上述工具的所有输出，渲染 Jinja2 模板，生成并写入完整 Skill 包目录文件。最后一步必须调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "province":          {"type": "string", "description": "省份代码，如 beijing"},
                    "intent":            {"type": "string", "description": "意图名称，如 套餐升档"},
                    "description":       {"type": "string", "description": "接口描述"},
                    "url":               {"type": "string", "description": "接口 URL"},
                    "method":            {"type": "string", "description": "请求方式"},
                    "headers":           {"type": "object", "description": "请求头 JSON 对象"},
                    "request_template":  {"type": "object", "description": "入参模板 JSON"},
                    "response_extract":  {"type": "object", "description": "响应提取规则 JSON"},
                    "field_transform":   {"type": "object", "description": "字段转换规则 JSON"},
                    "analysis":          {"type": "string", "description": "LLM 分析说明"},
                    "api_name":          {"type": "string", "description": "接口节点名称（可省略，默认按命名规范自动生成）"},
                    "api_display_name":  {"type": "string", "description": "接口中文名称，来自文档接口名称字段（可省略）"},
                    "stage":             {"type": "string", "description": "环节名称（可选，用于接口节点命名前缀，如 upgrade）"},
                    "scene":             {"type": "string", "description": "场景名称（可选，用于接口节点命名前缀，如 inbound）"},
                    "mock_response":          {"type": "object", "description": "Mock 响应数据，从 parse_docx 返回的 success_example 中获取，建议必填"},
                    "request_body_wrapper":   {"type": "string", "description": "入参顶层包装键（如 params/body/data），来自 match_params 返回的 request_body_wrapper 字段，有值时传入"},
                    "field_aliases": {
                        "type": "object",
                        "description": (
                            "套餐字段别名映射，用于告诉 ScriptStep 从 current_package / recommended_packages 中"
                            "取套餐名称、费用、流量、语音、产品ID 时应使用哪些字段名。"
                            "根据出参说明中 current_package/recommended_packages 域下各字段的说明推断，"
                            "说明含「商品名称/套餐名称」→ pkg_name，含「月费/价格/费用」→ pkg_fee，"
                            "含「流量/数据量」→ pkg_flow，含「语音/通话分钟」→ pkg_voice，"
                            "含「商品标识/产品ID/套餐ID」→ product_id。"
                            "格式：{\"pkg_name\": [\"省份字段名\", ...], \"pkg_fee\": [...], ...}，"
                            "省份专属字段名排在列表最前（优先级最高）。"
                            "示例（辽宁）：{\"pkg_name\": [\"curOfferName\"], \"pkg_fee\": [\"curOfferFee\"], "
                            "\"product_id\": [\"curOfferId\"]}"
                        ),
                    },
                    "output_params": {
                        "type": "array",
                        "description": (
                            "出参说明列表，来自 map_output 工具返回的 output_params 字段。"
                            "传入后系统会自动扫描套餐域字段说明，推断 field_aliases（无需手动推断时也可传入）。"
                            "格式：[{\"path\": \"bean.mainoffer.offerName\", \"desc\": \"商品名称\"}, ...]"
                        ),
                    },
                },
                "required": ["province", "intent", "url", "method",
                             "request_template", "response_extract", "field_transform",
                             "mock_response"],
            },
        },
    },
]


def _build_tool_schemas(tools: Dict[str, Callable]) -> List[Dict]:
    """从手写 Schema 列表中筛选已注册的工具"""
    registered = set(tools.keys())
    return [s for s in _TOOL_SCHEMAS if s["function"]["name"] in registered]


async def _execute_tool(
    tools: Dict[str, Callable],
    tool_name: str,
    tool_args: Dict,
) -> Any:
    """执行工具函数（自动处理同步/异步）"""
    fn = tools.get(tool_name)
    if fn is None:
        return {"error": f"未知工具: {tool_name}"}
    try:
        if inspect.iscoroutinefunction(fn):
            return await fn(**tool_args)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: fn(**tool_args))
    except Exception as exc:
        logger.error(f"[Agent] 工具 {tool_name} 执行失败: {exc}")
        return {"error": str(exc), "tool": tool_name}


# ══ 主入口 ════════════════════════════════════════════════════

async def run(
    docx_content_b64: str,
    province: str,
    intent: str,
) -> Dict[str, Any]:
    """外部主入口：启动 Interface Mapper Agent

    Args:
        docx_content_b64: base64 编码的 docx 文件内容
        province:         省份代码（如 beijing）
        intent:           意图名称（如 套餐升档）

    Returns:
        {
          "skill_path": "...",
          "files":      [...],
          "preview":    {"api_nodes": {...}, "biz_config": {...}},
          "analysis":   "...",
          "iterations": N,
        }
    """
    logger.info(f"[Interface Mapper Agent] 启动 province={province} intent={intent}")

    cfg       = _load_agent_config()
    skill_doc = _load_skill_doc()
    tools     = get_all_tools()

    # 使用 qwen-plus（兼容模式，稳定且适合接口映射）
    from utils.config_loader import config_loader
    dash_config = config_loader.config.get("dashscope") or {
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": "qwen-plus",
        "api_key": "sk-d2c2cf7a204043ef95c567a851d258e9",
        "appcode": "sk-d2c2cf7a204043ef95c567a851d258e9",
        "temperature": 0.0,
        "max_tokens": 8192,
        "timeout": 120
    }

    from services.llm_service import LLMService
    mapping_llm = LLMService(config_override=dash_config)

    agent = InterfaceMapperAgent(
        skill_doc      = skill_doc,
        tools          = tools,
        max_iterations = cfg.get("max_iterations", 10),
        temperature    = cfg.get("temperature", 0.1),
        max_tokens     = cfg.get("max_tokens", 4096),
    )
    # 将自定义 LLM 注入到 agent（需更新 InterfaceMapperAgent 支持）
    agent.llm_service = mapping_llm

    task = INTERFACE_AGENT_TASK_TEMPLATE.format(
        province=province,
        intent=intent,
        docx_head=docx_content_b64[:200],
        docx_full=docx_content_b64,
    )

    result = await agent.run(task)
    logger.info(
        f"[Interface Mapper Agent] 完成 skill_path={result.get('skill_path')} "
        f"iterations={result.get('iterations')}"
    )
    return result
