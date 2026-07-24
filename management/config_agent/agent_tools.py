"""
配置智能体工具集（设计态）

提供：
- TOOLS: OpenAI Function Calling 格式的工具 schema 列表（供 llm_service.chat_with_tools）
- TOOL_EXECUTORS: 工具名 -> Python 执行函数
- execute_tool: 统一执行入口（同步/异步自适应、异常转 error dict、提议工具注入会话级 proposals 列表）

约束（对齐规格书 §9）：
- 全部工具只读或只登记 proposal，绝不落盘；真正写入由 router 的 /proposals/apply 在人确认后执行
- 结果均为可 JSON 序列化的 dict，大结果做截断/摘要，防止上下文爆炸
- 服务集成一律方法内延迟 import（参照 utils/skill_runtime.py 惯例）
"""
from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

# 提议类工具（执行时由 execute_tool 注入会话级 proposals 列表）
PROPOSAL_TOOL_NAMES = {"propose_template_upsert", "propose_template_status"}

# 合法的提议状态值（与 template.schema.json 的 status enum 一致）
_VALID_PROPOSAL_STATUS = ("online", "offline", "deleted")

# 模板摘要正文截断长度
_CONTENT_BRIEF_LEN = 60


# ── 内部辅助 ──────────────────────────────────────────────────

def _get_pkg(province: str, intent: str):
    """按 (province, intent) 取技能包，不存在返回 None。延迟 import registry。"""
    from utils.skill_runtime import skill_registry
    return skill_registry.get(province, intent)


def _templates_of(pkg) -> List[Dict[str, Any]]:
    """取技能包的 script_templates_v2（仅 dict 项）。"""
    biz = (pkg.config.get("biz_config") or {}) if pkg is not None else {}
    return [t for t in (biz.get("script_templates_v2") or []) if isinstance(t, dict)]


def _template_brief(tpl: Dict[str, Any]) -> Dict[str, Any]:
    """单条模板摘要（防止全量正文塞进上下文）。"""
    content = str(tpl.get("template_content") or "")
    return {
        "template_id":   tpl.get("template_id", ""),
        "template_name": tpl.get("template_name", ""),
        "product_id":    tpl.get("product_id", ""),
        "stage":         tpl.get("stage", ""),
        "scene":         tpl.get("scene", ""),
        "status":        tpl.get("status", "online"),
        "priority":      tpl.get("priority", 0),
        "content_brief": content[:_CONTENT_BRIEF_LEN] + ("..." if len(content) > _CONTENT_BRIEF_LEN else ""),
    }


# ── 工具执行函数 ──────────────────────────────────────────────

def _tool_list_skills(province: str = "") -> Dict[str, Any]:
    """列出已加载技能包 + 每包模板数统计。"""
    from utils.skill_runtime import skill_registry
    items: List[Dict[str, Any]] = []
    for info in skill_registry.list_all():
        if province and info.get("province") != province:
            continue
        pkg = skill_registry.get(info.get("province", ""), info.get("intent", ""))
        items.append({
            "key":            info.get("key", ""),
            "province":       info.get("province", ""),
            "intent":         info.get("intent", ""),
            "version":        info.get("version", ""),
            "enabled":        info.get("enabled", True),
            "template_count": len(_templates_of(pkg)),
        })
    return {"skills": items, "total": len(items)}


def _tool_get_biz_config_summary(province: str, intent: str) -> Dict[str, Any]:
    """技能包 biz_config 摘要：strategy + 统计 + 前 5 条模板摘要（不返回全量）。"""
    pkg = _get_pkg(province, intent)
    if pkg is None:
        return {"error": f"技能包不存在: {province}:{intent}"}
    biz = pkg.config.get("biz_config") or {}
    templates = _templates_of(pkg)
    online_count = sum(
        1 for t in templates
        if str(t.get("status") or "online").strip() == "online"
    )
    scenes = sorted({
        str(t.get("scene") or "").strip()
        for t in templates if str(t.get("scene") or "").strip()
    })
    products = sorted({
        str(t.get("product_id") or "").strip()
        for t in templates if str(t.get("product_id") or "").strip()
    })
    return {
        "province":          province,
        "intent":            intent,
        "strategy":          biz.get("strategy") or {},
        "template_total":    len(templates),
        "online_count":      online_count,
        "scenes":            scenes,
        "products":          products,
        "templates_preview": [_template_brief(t) for t in templates[:5]],
        "has_pipeline":      bool(biz.get("pipeline")),
    }


def _tool_search_templates(
    province: str,
    intent: str,
    name: str = "",
    scene: str = "",
    stage: str = "",
    status: str = "",
    page: int = 1,
) -> Dict[str, Any]:
    """分页查询话术模板（page_size 固定 10，防上下文爆炸）。"""
    from utils.skill_runtime import skill_registry
    try:
        page_num = max(1, int(page))
    except (TypeError, ValueError):
        page_num = 1
    result = skill_registry.get_all_templates(
        province=province or None,
        intent=intent or None,
        name=name or None,
        scene=scene or None,
        stage=stage or None,
        status=status or None,
        page=page_num,
        page_size=10,
    )
    items = result.get("items") if isinstance(result, dict) else None
    if isinstance(items, list):
        result = dict(result)
        result["items"] = [
            _template_brief(t) if isinstance(t, dict) else t for t in items
        ]
    return result


def _tool_get_template(template_id: str) -> Dict[str, Any]:
    """按 template_id 查询单条模板全量内容。"""
    from utils.skill_runtime import skill_registry
    tpl = skill_registry.get_template_by_id(str(template_id or ""))
    if tpl is None:
        return {"error": f"模板不存在: {template_id}"}
    return tpl


def _tool_lint_config(province: str, intent: str) -> Dict[str, Any]:
    """对指定技能包跑全量确定性 lint。"""
    from management.config_agent.linter import lint_package
    return lint_package(province, intent)


def _tool_detect_conflicts(province: str, intent: str) -> Dict[str, Any]:
    """对指定技能包的模板跑冲突/重复检测。"""
    pkg = _get_pkg(province, intent)
    if pkg is None:
        return {"error": f"技能包不存在: {province}:{intent}"}
    from management.config_agent.conflict_detector import detect_conflicts
    return detect_conflicts(_templates_of(pkg), intent)


def _tool_infer_linked_vars(template_content: str) -> Dict[str, Any]:
    """根据话术正文推断需要关联的 context 变量。"""
    content = str(template_content or "").strip()
    if not content:
        return {"linked_vars": [], "reason": "模板内容为空"}
    from utils.var_infer import infer_linked_vars
    linked = infer_linked_vars(content)
    return {"linked_vars": linked}


def _tool_propose_template_upsert(
    province: str,
    intent: str,
    template: Dict[str, Any],
    _proposals: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """登记"新建/更新模板"提议：先 lint，错误则回传让模型修正；通过则登记 proposal。"""
    proposals = _proposals if _proposals is not None else []
    if not isinstance(template, dict):
        return {"ok": False, "error": "template 必须是 JSON 对象"}
    if _get_pkg(province, intent) is None:
        return {"ok": False, "error": f"技能包不存在: {province}:{intent}"}

    from management.config_agent.linter import lint_template
    lint = lint_template(template, province, intent)
    if lint.get("errors"):
        return {
            "ok": False,
            "error": "模板校验未通过，请根据 lint.errors 修正后重新提议",
            "lint": lint,
        }

    proposal_id = f"p{len(proposals) + 1}"
    proposals.append({
        "proposal_id": proposal_id,
        "type":        "template_upsert",
        "province":    province,
        "intent":      intent,
        "payload":     template,
        "lint":        lint,
    })
    return {
        "ok":          True,
        "proposal_id": proposal_id,
        "warnings":    lint.get("warnings", []),
        "message":     "提议已登记（未生效），需运营在界面确认后才会写入配置",
    }


def _tool_propose_template_status(
    province: str,
    intent: str,
    template_id: str,
    status: str,
    _proposals: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """登记"模板状态变更"提议：校验模板存在性与状态合法性后登记。"""
    proposals = _proposals if _proposals is not None else []
    status_str = str(status or "").strip()
    if status_str not in _VALID_PROPOSAL_STATUS:
        return {"ok": False, "error": f"status 非法值 {status_str!r}，合法值: online/offline/deleted"}

    from utils.skill_runtime import skill_registry
    tpl = skill_registry.get_template_by_id(str(template_id or ""))
    if tpl is None:
        return {"ok": False, "error": f"模板不存在: {template_id}"}
    if tpl.get("province") != province or tpl.get("intent") != intent:
        return {
            "ok": False,
            "error": (
                f"模板 {template_id} 归属 {tpl.get('province')}:{tpl.get('intent')}，"
                f"与请求的 {province}:{intent} 不一致"
            ),
        }

    proposal_id = f"p{len(proposals) + 1}"
    proposals.append({
        "proposal_id": proposal_id,
        "type":        "template_status",
        "province":    province,
        "intent":      intent,
        "payload":     {"template_id": str(template_id), "status": status_str},
        "lint":        {"errors": [], "warnings": [], "info": [], "stats": {}},
    })
    return {
        "ok":          True,
        "proposal_id": proposal_id,
        "message":     "提议已登记（未生效），需运营在界面确认后才会写入配置",
    }


def _tool_get_standard_domains() -> Dict[str, Any]:
    """返回 7 大标准域/变量标签等单一真源。"""
    from schemas import get_standard_domains
    return get_standard_domains()


def _tool_preview_prompt(
    template: Dict[str, Any],
    province: str = "",
    intent: str = "",
) -> Dict[str, Any]:
    """用示例数据预览模板最终生成的 LLM prompt（走运行态同一 build_prompt 逻辑）。"""
    if not isinstance(template, dict):
        return {"error": "template 必须是 JSON 对象"}
    try:
        # engine/prompt_builder.py 由并行任务提供，延迟 import 且失败降级
        from engine.prompt_builder import preview_prompt
    except Exception as exc:  # noqa: BLE001 - 并行模块未就绪时降级
        return {"error": f"prompt 预览功能暂不可用: {exc}"}
    prompt = preview_prompt(template, sample_ctx_data=None, province=province, intent=intent)
    return {"prompt": prompt}


# ── 工具注册表 ────────────────────────────────────────────────

TOOL_EXECUTORS: Dict[str, Callable] = {
    "list_skills":             _tool_list_skills,
    "get_biz_config_summary":  _tool_get_biz_config_summary,
    "search_templates":        _tool_search_templates,
    "get_template":            _tool_get_template,
    "lint_config":             _tool_lint_config,
    "detect_conflicts":        _tool_detect_conflicts,
    "infer_linked_vars":       _tool_infer_linked_vars,
    "propose_template_upsert": _tool_propose_template_upsert,
    "propose_template_status": _tool_propose_template_status,
    "get_standard_domains":    _tool_get_standard_domains,
    "preview_prompt":          _tool_preview_prompt,
}


# ── OpenAI Function Calling Schema（与 interface_mapper/agent_runner.py 惯例一致）──

TOOLS: List[Dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "列出已加载的技能包（省份+意图），含每个包的模板数统计。",
            "parameters": {
                "type": "object",
                "properties": {
                    "province": {"type": "string", "description": "省份代码过滤（可省略，省略时列全部）"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_biz_config_summary",
            "description": "获取指定技能包的 biz_config 摘要：strategy、模板数/scene 列表/product 列表以及前 5 条模板摘要（不返回全量模板）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "province": {"type": "string", "description": "省份代码，如 shandong"},
                    "intent":   {"type": "string", "description": "意图名称（与技能包目录名一致）"},
                },
                "required": ["province", "intent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_templates",
            "description": "分页查询话术模板（每页固定 10 条），支持按名称/场景/环节/状态过滤，返回模板摘要列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "province": {"type": "string", "description": "省份代码"},
                    "intent":   {"type": "string", "description": "意图名称"},
                    "name":     {"type": "string", "description": "模板名称关键词（可省略）"},
                    "scene":    {"type": "string", "description": "应用场景（可省略）"},
                    "stage":    {"type": "string", "description": "应用环节（可省略）"},
                    "status":   {"type": "string", "description": "状态过滤 online/offline（可省略）"},
                    "page":     {"type": "integer", "description": "页码，从 1 开始（可省略，默认 1）"},
                },
                "required": ["province", "intent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_template",
            "description": "按 template_id 查询单条话术模板的全量内容（含正文/linked_vars/prompt_template 等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "template_id": {"type": "string", "description": "模板 ID"},
                },
                "required": ["template_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lint_config",
            "description": "对指定技能包的 biz_config 与 api_nodes 跑全量确定性检查（缺字段/非法状态/未知占位符/维度冲突等），返回 errors/warnings/info/stats。",
            "parameters": {
                "type": "object",
                "properties": {
                    "province": {"type": "string", "description": "省份代码"},
                    "intent":   {"type": "string", "description": "意图名称"},
                },
                "required": ["province", "intent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_conflicts",
            "description": "检测指定技能包的模板冲突：同维度多条 online 模板（exact_conflicts）、模糊关键词遮蔽（fuzzy_shadows）、近似重复正文（near_duplicates）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "province": {"type": "string", "description": "省份代码"},
                    "intent":   {"type": "string", "description": "意图名称"},
                },
                "required": ["province", "intent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "infer_linked_vars",
            "description": "根据话术模板正文推断需要关联的 context 变量列表（linked_vars 候选）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "template_content": {"type": "string", "description": "话术模板正文"},
                },
                "required": ["template_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_template_upsert",
            "description": "提议新建或更新一条话术模板（template 含 template_id 为更新，否则为新建）。先做确定性校验，未通过会返回错误让你修正；通过后登记为待确认提议并返回 proposal_id，不会直接写入配置。",
            "parameters": {
                "type": "object",
                "properties": {
                    "province": {"type": "string", "description": "省份代码"},
                    "intent":   {"type": "string", "description": "意图名称"},
                    "template": {
                        "type": "object",
                        "description": "完整模板对象，至少含 template_name 与 template_content；可选 product_id/stage/scene/linked_vars/priority/status 等",
                    },
                },
                "required": ["province", "intent", "template"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_template_status",
            "description": "提议变更某条模板的状态（online/offline/deleted）。校验模板存在后登记为待确认提议并返回 proposal_id，不会直接写入配置。",
            "parameters": {
                "type": "object",
                "properties": {
                    "province":    {"type": "string", "description": "省份代码"},
                    "intent":      {"type": "string", "description": "意图名称"},
                    "template_id": {"type": "string", "description": "模板 ID"},
                    "status":      {"type": "string", "description": "目标状态", "enum": ["online", "offline", "deleted"]},
                },
                "required": ["province", "intent", "template_id", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_standard_domains",
            "description": "获取 7 大标准数据域、变量标签、linked_var 候选、生成变量与固定占位符的单一真源定义。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_prompt",
            "description": "用内置示例数据预览一条模板最终会生成的 LLM prompt（与运行态完全同一套拼接逻辑），用于检查占位符渲染效果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "template": {"type": "object", "description": "完整模板对象（至少含 template_content）"},
                    "province": {"type": "string", "description": "省份代码（可省略）"},
                    "intent":   {"type": "string", "description": "意图名称（可省略）"},
                },
                "required": ["template"],
            },
        },
    },
]


# ── 统一执行入口 ──────────────────────────────────────────────

async def execute_tool(
    tool_name: str,
    tool_args: Dict[str, Any],
    proposals: List[Dict[str, Any]],
) -> Any:
    """执行单个工具调用（供 agent_loop 使用）。

    - 未知工具/参数不合法/执行异常均转为 {"error": ...}（回传给模型修正，不中断循环）
    - 提议类工具注入会话级 proposals 列表
    - 同步函数放线程池执行，避免阻塞事件循环（与 interface_mapper/agent_runner.py 一致）
    """
    fn = TOOL_EXECUTORS.get(tool_name)
    if fn is None:
        return {"error": f"未知工具: {tool_name}"}
    if not isinstance(tool_args, dict):
        tool_args = {}
    if tool_name in PROPOSAL_TOOL_NAMES:
        tool_args = {**tool_args, "_proposals": proposals}
    try:
        if inspect.iscoroutinefunction(fn):
            return await fn(**tool_args)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(**tool_args))
    except TypeError as exc:
        # 模型传了未定义/缺失的参数
        return {"error": f"工具 {tool_name} 参数不合法: {exc}", "tool": tool_name}
    except Exception as exc:  # noqa: BLE001 - 工具异常回传模型，不中断 agent 循环
        logger.error(f"[ConfigAgent] 工具 {tool_name} 执行失败: {exc}")
        return {"error": str(exc), "tool": tool_name}


__all__ = ["TOOLS", "TOOL_EXECUTORS", "PROPOSAL_TOOL_NAMES", "execute_tool"]
