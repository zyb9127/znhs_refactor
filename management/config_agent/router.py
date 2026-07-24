"""
配置智能体路由 /api/config-agent/*

响应包装 {"code": 200, ...} 与 routers/management.py 一致；
写操作（proposals/apply）使用 utils.auth_utils.check_province_write 做省份写权限校验。

性能约束（规格 §10）：catalog/audit 为内存读 + 纯计算，循环内的 ES 版本信息
调用逐包 try/except，单包失败不影响整体；audit 默认 skip_versions=true。

事件循环保护：catalog/conflicts/audit 的纯 CPU 计算（冲突检测、lint）与循环内
同步 ES 调用一律经 run_in_executor 放线程池执行（与 agent_tools.execute_tool 的
处理一致），避免阻塞与生产热路径 /marketing/recommend 共享的事件循环；
catalog/audit 只需要 exact_conflicts 组数，跳过 O(n^2) 的 near_duplicates 与
无规模上限的 fuzzy_shadows 扫描。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from utils.auth_utils import check_province_write, get_operator
from utils.skill_runtime import skill_registry

router = APIRouter(tags=["配置智能体"])


# ── 数据模型 ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    messages: List[Dict[str, Any]] = Field(..., description="对话消息列表 [{role, content}]")
    province: str = Field(default="", description="会话上下文省份（可空）")
    intent: str = Field(default="", description="会话上下文意图（可空）")


class LintRequest(BaseModel):
    province: str = Field(default="", description="省份代码")
    intent: str = Field(default="", description="意图名称")
    biz_config: Optional[Dict[str, Any]] = Field(default=None, description="直接校验的 biz_config（可省略）")
    api_nodes: Optional[Dict[str, Any]] = Field(default=None, description="直接校验的 api_nodes（可省略）")


class ProposalBody(BaseModel):
    type: str = Field(..., description="提议类型: template_upsert / template_status")
    payload: Dict[str, Any] = Field(default_factory=dict, description="提议内容")


class ProposalApplyRequest(BaseModel):
    province: str = Field(..., description="省份代码")
    intent: str = Field(..., description="意图名称")
    proposal: ProposalBody
    operator: str = Field(default="", description="操作人（鉴权可用时以鉴权用户为准）")


# ── 内部辅助 ──────────────────────────────────────────────────

def _templates_of_pkg(pkg) -> List[Dict[str, Any]]:
    """取技能包 script_templates_v2（仅 dict 项）。"""
    biz = (pkg.config.get("biz_config") or {}) if pkg is not None else {}
    return [t for t in (biz.get("script_templates_v2") or []) if isinstance(t, dict)]


def _version_info_of(province: str, intent: str) -> Optional[Dict[str, Any]]:
    """读取 ES 版本信息，任何异常/方法缺失一律忽略返回 None（单包失败不影响整体）。"""
    try:
        from services.es_config_store import es_config_store
        fn = getattr(es_config_store, "get_current_version_info", None)
        if not callable(fn):
            return None
        return {
            "biz_config": fn(province, intent, "biz_config"),
            "api_nodes":  fn(province, intent, "api_nodes"),
        }
    except Exception as exc:  # noqa: BLE001 - 版本信息属附加信息，失败忽略
        logger.debug(f"[ConfigAgent] 读取版本信息失败 {province}:{intent}: {exc}")
        return None


def _exact_conflict_group_count(templates: List[Dict[str, Any]], intent: str) -> int:
    """只统计 exact_conflicts 组数（O(n) 分组）。

    catalog/audit 仅展示冲突组计数，不需要完整报告；完整 detect_conflicts 含
    O(n^2) 的 near_duplicates 与无上限的 fuzzy_shadows 扫描（单包数百条模板即
    数秒纯 CPU），逐包执行会拖垮整个实例，故此处直接复用检测器的分组子步骤。
    """
    from management.config_agent.conflict_detector import (
        _detect_exact_conflicts,
        _online_pool,
    )
    return len(_detect_exact_conflicts(_online_pool(templates, intent)))


# ── 基础端点 ──────────────────────────────────────────────────

@router.get("/api/config-agent/health")
async def config_agent_health():
    """健康检查"""
    return {"code": 200, "data": {"status": "ok"}}


@router.get("/api/config-agent/components")
async def list_engine_components():
    """列出运行态引擎已注册的组件（含 config_schema，供前端渲染表单）"""
    try:
        import engine.components  # noqa: F401 - 触发内置组件注册（并行任务提供，缺失时忽略）
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[ConfigAgent] 内置组件包不可用: {exc}")
    from engine.registry import list_components
    return {"code": 200, "data": list_components()}


@router.get("/api/config-agent/schemas")
async def list_config_schemas():
    """列出全部可用的 JSON schema 名称"""
    from schemas import list_schemas
    return {"code": 200, "data": list_schemas()}


@router.get("/api/config-agent/schemas/{name}")
async def get_config_schema(name: str):
    """按名称获取单个 JSON schema，不存在返回 404"""
    from schemas import get_schema
    try:
        return {"code": 200, "data": get_schema(name)}
    except KeyError:
        raise HTTPException(404, f"schema 不存在: {name}")


@router.get("/api/config-agent/standard-domains")
async def get_standard_domains_endpoint():
    """7 大标准域/变量标签/linked_var 候选单一真源（供前端替换硬编码常量）"""
    from schemas import get_standard_domains
    return {"code": 200, "data": get_standard_domains()}


# ── 多意图治理端点 ────────────────────────────────────────────

def _build_catalog_items(skip_versions: bool) -> List[Dict[str, Any]]:
    """catalog 同步构建（在线程池中执行，勿在事件循环线程直接调用）。"""
    items: List[Dict[str, Any]] = []
    for info in skill_registry.list_all():
        province = info.get("province", "")
        intent = info.get("intent", "")
        pkg = skill_registry.get(province, intent)
        biz = (pkg.config.get("biz_config") or {}) if pkg is not None else {}
        templates = _templates_of_pkg(pkg)

        online_count = sum(
            1 for t in templates
            if str(t.get("status") or "online").strip() == "online"
        )
        scenes = {
            str(t.get("scene") or "").strip()
            for t in templates if str(t.get("scene") or "").strip()
        }
        products = {
            str(t.get("product_id") or "").strip()
            for t in templates if str(t.get("product_id") or "").strip()
        }
        try:
            conflict_groups = _exact_conflict_group_count(templates, intent)
        except Exception as exc:  # noqa: BLE001 - 单包检测失败不影响整体
            logger.warning(f"[ConfigAgent] 冲突检测失败 {province}:{intent}: {exc}")
            conflict_groups = 0

        items.append({
            "key":                  info.get("key", f"{province}:{intent}"),
            "province":             province,
            "intent":               intent,
            "version":              info.get("version", ""),
            "enabled":              info.get("enabled", True),
            "template_total":       len(templates),
            "online_count":         online_count,
            "scene_count":          len(scenes),
            "product_count":        len(products),
            "conflict_group_count": conflict_groups,
            "version_info":         None if skip_versions else _version_info_of(province, intent),
            "has_pipeline":         bool(biz.get("pipeline")),
        })
    return items


@router.get("/api/config-agent/catalog")
async def config_catalog(skip_versions: bool = False):
    """多意图管理目录：每个 province:intent 的模板统计/冲突组数/版本信息/是否配置 pipeline"""
    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(None, _build_catalog_items, skip_versions)
    return {"code": 200, "data": items}


@router.post("/api/config-agent/lint")
async def lint_config_endpoint(body: LintRequest):
    """配置 lint：body 只带 province/intent 时校验已保存配置；
    带 biz_config/api_nodes 时直接校验传入的未保存配置。"""
    from management.config_agent.linter import lint_api_nodes, lint_biz_config, lint_package

    if body.biz_config is None and body.api_nodes is None:
        if not body.province or not body.intent:
            raise HTTPException(400, "province 与 intent 必填")
        return {"code": 200, "data": lint_package(body.province, body.intent)}

    # 直接校验模式：路径前缀与 lint_package 保持一致（biz_config. / api_nodes.）
    report: Dict[str, Any] = {"errors": [], "warnings": [], "info": [], "stats": {}}

    def _merge(sub: Dict[str, Any], prefix: str) -> None:
        for level in ("errors", "warnings", "info"):
            for item in sub.get(level, []):
                copied = dict(item)
                copied["path"] = f"{prefix}.{copied['path']}" if copied.get("path") else prefix
                report[level].append(copied)
        report["stats"].update(sub.get("stats", {}))

    if body.biz_config is not None:
        _merge(lint_biz_config(body.biz_config, body.province, body.intent), "biz_config")
    if body.api_nodes is not None:
        _merge(lint_api_nodes(body.api_nodes, body.province, body.intent), "api_nodes")
    return {"code": 200, "data": report}


@router.get("/api/config-agent/conflicts")
async def get_conflicts(province: str, intent: str):
    """模板冲突/重复检测报告"""
    pkg = skill_registry.get(province, intent)
    if pkg is None:
        raise HTTPException(404, f"技能包不存在: {province}/{intent}")
    from management.config_agent.conflict_detector import detect_conflicts
    # 完整检测含 O(n^2) 纯 CPU 比较，放线程池执行（与 agent 工具路径一致）
    templates = _templates_of_pkg(pkg)
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, detect_conflicts, templates, intent)
    return {"code": 200, "data": data}


def _build_audit_report(province: str, skip_versions: bool) -> Dict[str, Any]:
    """audit 同步构建（在线程池中执行，勿在事件循环线程直接调用）。"""
    from management.config_agent.linter import lint_package

    packages: List[Dict[str, Any]] = []
    issue_count: Dict[str, int] = {}
    issue_example: Dict[str, str] = {}
    total_errors = 0
    total_warnings = 0

    for info in skill_registry.list_all():
        pkg_province = info.get("province", "")
        pkg_intent = info.get("intent", "")
        if province and pkg_province != province:
            continue
        try:
            report = lint_package(pkg_province, pkg_intent)
        except Exception as exc:  # noqa: BLE001 - 单包失败不影响整体
            logger.warning(f"[ConfigAgent] lint 失败 {pkg_province}:{pkg_intent}: {exc}")
            report = {"errors": [], "warnings": [], "info": [], "stats": {}}

        pkg = skill_registry.get(pkg_province, pkg_intent)
        try:
            conflict_groups = _exact_conflict_group_count(_templates_of_pkg(pkg), pkg_intent)
        except Exception:  # noqa: BLE001
            conflict_groups = 0

        for level in ("errors", "warnings"):
            for item in report.get(level, []):
                code = item.get("code", "UNKNOWN")
                issue_count[code] = issue_count.get(code, 0) + 1
                issue_example.setdefault(code, item.get("message", ""))

        total_errors += len(report.get("errors", []))
        total_warnings += len(report.get("warnings", []))

        entry: Dict[str, Any] = {
            "province":             pkg_province,
            "intent":               pkg_intent,
            "error_count":          len(report.get("errors", [])),
            "warning_count":        len(report.get("warnings", [])),
            "info_count":           len(report.get("info", [])),
            "conflict_group_count": conflict_groups,
            "stats":                report.get("stats", {}),
        }
        if not skip_versions:
            entry["version_info"] = _version_info_of(pkg_province, pkg_intent)
        packages.append(entry)

    top_issues = [
        {"code": code, "count": count, "example": issue_example.get(code, "")}
        for code, count in sorted(issue_count.items(), key=lambda kv: -kv[1])[:10]
    ]
    return {
        "packages":       packages,
        "top_issues":     top_issues,
        "total_errors":   total_errors,
        "total_warnings": total_warnings,
    }


@router.get("/api/config-agent/audit")
async def audit(province: str = "", skip_versions: bool = True):
    """全量治理报告：遍历技能包跑 lint + 冲突检测，汇总 top 问题。

    lint/冲突检测为纯 CPU、可能伴随同步 ES 调用，放线程池执行；
    版本信息默认跳过（skip_versions=true），需要时显式传 false。
    """
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _build_audit_report, province, skip_versions)
    return {"code": 200, "data": data}


# ── 对话与提议端点 ────────────────────────────────────────────

@router.post("/api/config-agent/chat")
async def config_agent_chat(body: ChatRequest):
    """配置智能体对话（多轮工具调用，只产生 proposal 不落盘）"""
    if not body.messages:
        raise HTTPException(400, "messages 不能为空")
    from management.config_agent.agent_loop import run_config_agent
    result = await run_config_agent(
        body.messages, province=body.province, intent=body.intent
    )
    return {"code": 200, "data": result}


@router.post("/api/config-agent/proposals/apply")
async def apply_proposal(body: ProposalApplyRequest, request: Request):
    """应用一条 agent 提议（人在环确认后调用，需同省份或本部写权限）"""
    check_province_write(request, body.province)
    operator = get_operator(request, fallback=body.operator or "admin")
    ptype = body.proposal.type
    payload = body.proposal.payload or {}

    from management.config_agent.linter import lint_template

    if ptype == "template_upsert":
        # payload 带 template_id（更新语义）时先校验模板存在且归属一致，
        # 与 management.py PUT /api/templates/{id} 行为一致；
        # 否则 upsert_template 会把不存在的 id 当新模板追加（静默新建）。
        template_id = str(payload.get("template_id") or "").strip()
        if template_id:
            tpl = skill_registry.get_template_by_id(template_id)
            if tpl is None:
                raise HTTPException(404, f"模板不存在: {template_id}")
            if tpl.get("province") != body.province or tpl.get("intent") != body.intent:
                raise HTTPException(
                    400,
                    f"模板 {template_id} 归属 {tpl.get('province')}:{tpl.get('intent')}，"
                    f"与请求的 {body.province}:{body.intent} 不一致",
                )
        lint = lint_template(payload, body.province, body.intent)
        if lint.get("errors"):
            raise HTTPException(400, detail={"message": "模板校验未通过", "lint": lint})
        data = dict(payload)
        if data.get("template_id"):
            data["updated_by"] = operator
        else:
            data.setdefault("created_by", operator)
        try:
            saved = skill_registry.upsert_template(body.province, body.intent, data)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"提议应用失败: {exc}")
        return {
            "code": 200,
            "message": "提议已生效",
            "data": saved,
            "warnings": lint.get("warnings", []),
        }

    if ptype == "template_status":
        template_id = str(payload.get("template_id") or "").strip()
        status = str(payload.get("status") or "").strip()
        if not template_id:
            raise HTTPException(400, "payload.template_id 必填")
        if status not in ("online", "offline", "deleted"):
            raise HTTPException(400, "payload.status 必须为 online/offline/deleted")
        tpl = skill_registry.get_template_by_id(template_id)
        if tpl is None:
            raise HTTPException(404, f"模板不存在: {template_id}")
        if tpl.get("province") != body.province or tpl.get("intent") != body.intent:
            raise HTTPException(
                400,
                f"模板 {template_id} 归属 {tpl.get('province')}:{tpl.get('intent')}，"
                f"与请求的 {body.province}:{body.intent} 不一致",
            )
        try:
            saved = skill_registry.upsert_template(
                body.province, body.intent,
                {"template_id": template_id, "status": status, "updated_by": operator},
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"提议应用失败: {exc}")
        return {"code": 200, "message": "状态提议已生效", "data": saved, "warnings": []}

    raise HTTPException(400, f"未知 proposal 类型: {ptype}")


__all__ = ["router"]
