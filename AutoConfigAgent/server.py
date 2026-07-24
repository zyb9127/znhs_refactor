#!/usr/bin/env python3
"""
server.py — 自动配置智能体 · 后端服务

架构：FastAPI + 静态前端（Vue CDN）

端点：
  GET  /                                           → 前端页面
  GET  /api/health                                 → 健康检查
  GET  /api/provinces                              → 省份+意图列表
  GET  /api/template/example                       → 获取示例导入模板（JSON）
  GET  /api/template/excel                         → 下载 Excel 导入模板（.xlsx）
  GET  /api/template/excel-doc                     → Excel 模板字段说明（JSON）
  POST /api/import/excel                           → 解析 Excel 文件
  POST /api/import/parse                           → 解析 JSON 文件（兼容老接口）
  POST /api/import/parse-text                      → 解析 JSON 文本（兼容老接口）
  POST /api/import/generate                        → 生成 skill 包预览
  POST /api/import/validate                        → 跑通校验（接口/映射/槽位）
  POST /api/import/publish                         → 发布到 skills-runtime
  GET  /api/templates/library                      → 内置模板菜单
  GET  /api/templates/library/{template_id}        → 内置模板详情
  GET  /api/skills/{province}/{intent}/config               → 读取现有 skill 配置
  POST /api/skills/{province}/{intent}/reload               → 触发话术智能体热重载

Skill 管理接口（v2）:
  GET    /api/skills                                        → 列出所有 Skill（支持 province/intent 过滤）
  PUT    /api/skills/{province}/{intent}/config/{type}      → 更新 api_nodes 或 biz_config（自动备份）
  DELETE /api/skills/{province}/{intent}                    → 删除 Skill（默认保留备份）
  GET    /api/skills/{province}/{intent}/versions           → 列出历史备份
  POST   /api/skills/{province}/{intent}/rollback           → 回滚到指定备份
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from code_generator import EXAMPLE_TEMPLATE, SkillPackageGenerator, validate_template
from excel_parser import build_template_workbook, get_template_doc, parse_excel
from template_library import get_template, list_templates

# ── 配置 ─────────────────────────────────────────
ROOT = Path(__file__).parent

# skills-runtime 路径（支持环境变量覆盖）
# 默认：AutoConfigAgent 的上级目录（话术智能体/）下的 skills-runtime
_DEFAULT_SKILLS_RUNTIME = ROOT.parent / "skills-runtime"
SKILLS_RUNTIME = Path(os.environ.get("SKILLS_RUNTIME_PATH", str(_DEFAULT_SKILLS_RUNTIME)))

# 话术智能体-生产 管理 API（可选，用于热重载）
PROD_API_BASE = os.environ.get("PROD_API_BASE", "http://localhost:8000/znhs")

PORT = int(os.environ.get("AUTO_CONFIG_PORT", 8001))

# ── 日志 ─────────────────────────────────────────
log_dir = ROOT / "logs"
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_dir / "server.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ── API 路由（由 main.py 挂载到 {SERVICE_PREFIX}/api/auto-config）────
router = APIRouter(tags=["auto-config"])


def _enforce_province_write(request: Request, target_province: str) -> None:
    """省份级写权限校验（鉴权模式下：仅本部或同省份可写，跨省份抛 403）。

    - 通过 utils.auth_utils.check_province_write 判定，鉴权未启用/本部用户直接放行。
    - 独立启动模式（无 znhs 根环境，import 失败）不做限制。
    """
    try:
        from utils.auth_utils import check_province_write
    except Exception:
        return  # 独立启动模式：无鉴权体系，放行
    check_province_write(request, target_province)

# 独立启动时的 FastAPI 应用（兼容旧用法，推荐 python main.py 统一启动）
app = FastAPI(
    title="自动配置智能体",
    description="话术智能体 Skill 包自动化配置工具",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api")

# 前端由 znhs/frontend Vue 项目提供，不再挂载独立静态文件


# ── Pydantic 模型 ────────────────────────────────

class TextImportRequest(BaseModel):
    text: str


class GenerateRequest(BaseModel):
    template: Dict[str, Any]


class PublishRequest(BaseModel):
    template: Dict[str, Any]
    overwrite: bool = False
    reload: bool = True
    validate_before_publish: bool = True
    validate_run_api_call: bool = False


class ValidateRequest(BaseModel):
    template: Dict[str, Any]
    run_api_call: bool = False
    sample_phone: str = "13800138000"


# ── 工具函数 ─────────────────────────────────────

def _discover_skills() -> Dict[str, Any]:
    """从 skills-runtime 目录自动发现省份和意图。"""
    result: Dict[str, Any] = {}
    if not SKILLS_RUNTIME.exists():
        logger.warning("skills-runtime 路径不存在: %s", SKILLS_RUNTIME)
        return result

    for province_dir in sorted(SKILLS_RUNTIME.iterdir()):
        if not province_dir.is_dir():
            continue
        province = province_dir.name
        # 读取 manifest.json 获取省份元数据
        manifest_path = province_dir / "manifest.json"
        province_info: Dict[str, Any] = {
            "province_code": province,
            "province_name": province,
            "intents": [],
        }
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                province_info["province_name"] = manifest.get("province_name", province)
                province_info["enabled"] = manifest.get("enabled", True)
            except Exception:
                pass

        intents: List[Dict[str, Any]] = []
        for intent_dir in sorted(province_dir.iterdir()):
            if not intent_dir.is_dir() or intent_dir.name == "__pycache__":
                continue
            meta_path = intent_dir / "_meta.json"
            intent_info: Dict[str, Any] = {
                "intent": intent_dir.name,
                "has_api_nodes": (intent_dir / "config" / "api_nodes.json").exists(),
                "has_biz_config": (intent_dir / "config" / "biz_config.json").exists(),
                "has_main_flow": (intent_dir / "scripts" / "main_flow.py").exists(),
            }
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    intent_info["version"] = meta.get("version", "1.0.0")
                    intent_info["description"] = meta.get("description", "")
                    intent_info["created_by"] = meta.get("created_by", "")
                    intent_info["created_at"] = meta.get("created_at", "")
                except Exception:
                    pass
            intents.append(intent_info)

        province_info["intents"] = intents
        result[province] = province_info

    return result


def _write_skill_package(generated: Dict[str, Any], overwrite: bool = False) -> Dict[str, Any]:
    """
    将生成的 skill 包写入 skills-runtime 目录。
    返回写入结果信息。
    """
    province = generated["province"]
    intent = generated["intent"]
    skill_dir = SKILLS_RUNTIME / province / intent

    if skill_dir.exists() and not overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"Skill 已存在：{province}/{intent}，请勾选「覆盖已有配置」后重试。",
        )

    # 备份原有配置（如存在）——best-effort：命名冲突/失败不应阻断发布
    backup_info: Optional[str] = None
    if skill_dir.exists() and overwrite:
        try:
            # 秒级时间戳在同秒内重试会撞名，这里用毫秒 + 唯一后缀兜底，避免 FileExistsError
            stamp = int(datetime.now().timestamp() * 1000)
            backup_dir = skill_dir.parent / f"{intent}.bak.{stamp}"
            _n = 0
            while backup_dir.exists():
                _n += 1
                backup_dir = skill_dir.parent / f"{intent}.bak.{stamp}_{_n}"
            shutil.copytree(str(skill_dir), str(backup_dir))
            backup_info = str(backup_dir.relative_to(SKILLS_RUNTIME))
            logger.info("已备份原配置到: %s", backup_dir)
        except Exception as e:
            # 备份失败不阻断发布（生产真源是 ES，本地文件仅快照）
            logger.warning("备份原配置失败（不阻断发布）: %s", e)

    # 创建目录结构
    (skill_dir / "config").mkdir(parents=True, exist_ok=True)

    # 写入各文件
    files_written: List[str] = []

    def _write_json(path: Path, data: Dict[str, Any]) -> None:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        files_written.append(str(path.relative_to(SKILLS_RUNTIME)))

    # api_nodes / biz_config 走统一发布器（唯一写路径：本地文件 + ES 版本化 + Redis 缓存）；
    # 发布后的热重载仍由上层按 req.reload 统一触发，这里 reload=False。
    try:
        from services.skill_publisher import publish_config  # 延迟 import，独立启动模式可能不可用
    except ImportError:
        publish_config = None
        logger.warning("skill_publisher 不可用（独立启动模式），降级为直接写本地文件")

    published_types: List[str] = []
    for config_type in ("api_nodes", "biz_config"):
        cfg_path = skill_dir / "config" / f"{config_type}.json"
        if publish_config is not None:
            pub = publish_config(
                province, intent, config_type, generated[config_type],
                operator="auto_config_agent", comment="AutoConfig 导入发布", reload=False,
            )
            if not pub.success:
                # 无事务补偿：前面已发布的 config 无法自动撤回，明确告知半发布状态
                if published_types:
                    detail = (
                        f"发布 {config_type} 失败：{pub.message}。"
                        f"注意：{'、'.join(published_types)} 已发布生效，{config_type} 未发布，"
                        f"技能包当前处于部分发布状态。请修复问题后重新发布（覆盖模式），"
                        f"或通过备份回滚恢复到发布前配置。"
                    )
                else:
                    detail = f"发布 {config_type} 失败：{pub.message}（尚无任何 config 生效，可直接重试）"
                raise HTTPException(status_code=500, detail=detail)
            published_types.append(config_type)
            files_written.append(str(cfg_path.relative_to(SKILLS_RUNTIME)))
        else:
            _write_json(cfg_path, generated[config_type])

    # _meta.json 属杂项元数据，仍直接落盘（publisher 只管 api_nodes / biz_config）
    _write_json(skill_dir / "_meta.json", generated["meta"])

    # 更新省份 manifest.json
    _update_province_manifest(province, intent, generated)

    logger.info("Skill 包已发布: %s/%s → %s", province, intent, skill_dir)
    return {
        "province": province,
        "intent": intent,
        "skill_dir": str(skill_dir),
        "files_written": files_written,
        "backup": backup_info,
        "published_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _update_province_manifest(province: str, intent: str, generated: Dict[str, Any]) -> None:
    """更新省份的 manifest.json，添加新意图信息。"""
    manifest_path = SKILLS_RUNTIME / province / "manifest.json"
    meta = generated.get("meta", {})
    tpl_meta = generated.get("meta", {})

    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    else:
        manifest = {
            "province_code": province,
            "province_name": province,
            "enabled": True,
            "version": "1.0.0",
            "intents": {},
        }

    # 兼容 intents 为 list 或 dict 的情况
    intents = manifest.get("intents", {})
    if isinstance(intents, list):
        # 转换为 dict 格式
        intents = {item.get("name", item.get("intent", "")): item for item in intents if isinstance(item, dict)}

    intents[intent] = {
        "name": intent,
        "version": meta.get("version", "1.0.0"),
        "enabled": True,
        "description": meta.get("description", ""),
        "created_by": "auto_config_agent",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    manifest["intents"] = intents

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def _trigger_reload(province: str, intent: str) -> Dict[str, Any]:
    """触发 Skill 热重载：优先同进程 skill_registry，否则 HTTP 回退。"""
    try:
        from utils.skill_runtime import skill_registry

        skill_registry.reload(province, intent)
        return {
            "success": True,
            "method": "in-process",
            "province": province,
            "intent": intent,
            "loaded": len(skill_registry.list_all()),
        }
    except ImportError:
        pass

    url = f"{PROD_API_BASE}/api/skills/reload"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                params={"province": province, "intent": intent},
            )
            if resp.status_code == 200:
                return {"success": True, "method": "http", "response": resp.json()}
            return {"success": False, "status": resp.status_code, "text": resp.text}
    except Exception as e:
        logger.warning("热重载请求失败（不影响发布结果）: %s", e)
        return {"success": False, "error": str(e)}


_PLACEHOLDER_PATTERN = re.compile(r"\{\{([^{}]+)\}\}")
_SLOT_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

_KNOWN_TEMPLATE_SLOTS = {
    "cur_brief",
    "current_package",
    "pkg_brief",
    "pkg_name",
    "cur_name",
    "diff_str",
    "usage_line",
    "usage",
    "tags",
    "user_tags",
    "user_info",
    "user_profile",
    "domain_ext",
    "extra_info",
    "extra_context",
    "table",
    "intent",
    "max_length",
    "template_content",
    "template",
    "template_ref",
    "recommended_packages",
    "reason_line",
    "features_line",
}


def _path_get(data: Any, path: str) -> Any:
    if not path:
        return None
    cur = data
    for part in str(path).split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _replace_placeholders_in_obj(obj: Any, replacements: Dict[str, str]) -> Any:
    if isinstance(obj, dict):
        return {k: _replace_placeholders_in_obj(v, replacements) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_placeholders_in_obj(v, replacements) for v in obj]
    if isinstance(obj, str):
        s = obj
        for k, v in replacements.items():
            s = s.replace(k, v)
        return s
    return obj


def _extract_slots(text: str) -> List[str]:
    return list(dict.fromkeys(_SLOT_PATTERN.findall(text or "")))


def _collect_issue(
    issues: List[Dict[str, str]],
    level: str,
    code: str,
    message: str,
    location: str,
) -> None:
    issues.append(
        {
            "level": level,
            "code": code,
            "message": message,
            "location": location,
        }
    )


def _heuristic_suggestions(issues: List[Dict[str, str]]) -> List[str]:
    suggestions: List[str] = []
    for issue in issues:
        code = issue.get("code", "")
        if code == "API_URL_MISSING":
            suggestions.append("接口 URL 为空：请补齐 `api.url`，或在联调阶段先开启 `mock_mode=true`。")
        elif code == "API_CALL_FAILED":
            suggestions.append("接口调用失败：先用 Postman 验证地址、方法、请求头和请求体，再同步到 `api_nodes.json`。")
        elif code == "RESPONSE_PATH_MISSING":
            suggestions.append("响应字段路径未命中：请对照真实响应修正 `response_extract` 路径，例如 `bean.mainoffer`。")
        elif code == "REQUIRED_EXTRACT_MISSING":
            suggestions.append("缺少关键映射：至少配置 `current_package` 与 `recommended_packages`，保证推荐与话术链路可跑通。")
        elif code == "UNKNOWN_TEMPLATE_SLOT":
            suggestions.append("模板占位符不在标准变量集合内：建议使用 `cur_brief/pkg_brief/diff_str/usage_line` 等规范槽位。")
        elif code == "LINKED_VAR_MISMATCH":
            suggestions.append("模板占位符与 `linked_vars` 不一致：建议补齐 `linked_vars`，避免提示词信息缺失。")
        elif code == "TOP_N_INVALID":
            suggestions.append("`strategy.top_n` 非法：请设为大于 0 的整数，且与业务推荐条数保持一致。")
    if not suggestions:
        suggestions.append("建议先使用 `mock_mode=true` 跑通端到端流程，再逐步切换真实接口并校正字段映射。")
    return list(dict.fromkeys(suggestions))


async def _maybe_llm_suggestions(validation_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    可选：通过外部大模型服务补充优化建议。
    通过环境变量 VALIDATION_ADVISOR_URL 开启，默认仅返回启发式建议。
    """
    advisor_url = os.environ.get("VALIDATION_ADVISOR_URL", "").strip()
    if not advisor_url:
        return {
            "enabled": False,
            "source": "heuristic",
            "items": _heuristic_suggestions(validation_result.get("issues", [])),
        }

    payload = {
        "task": "skill_config_validation_advice",
        "summary": validation_result.get("summary", ""),
        "issues": validation_result.get("issues", []),
        "suggestions_seed": _heuristic_suggestions(validation_result.get("issues", [])),
    }
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(advisor_url, json=payload)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("suggestions") or data.get("items") or []
            items = [str(x).strip() for x in items if str(x).strip()]
            if items:
                return {
                    "enabled": True,
                    "source": "llm",
                    "items": items,
                }
    except Exception as e:
        logger.warning("LLM 优化建议获取失败，降级为启发式建议: %s", e)

    return {
        "enabled": False,
        "source": "heuristic",
        "items": _heuristic_suggestions(validation_result.get("issues", [])),
    }


async def _run_validation(
    template: Dict[str, Any],
    run_api_call: bool = False,
    sample_phone: str = "13800138000",
) -> Dict[str, Any]:
    """
    校验导入模板是否可跑通，校验点参考话术智能体-生产：
    1) 接口配置完整性（api_nodes）
    2) 接口响应到标准槽位映射（response_extract）
    3) 话术模板槽位与 linked_vars 一致性（script_templates_v2）
    """
    issues: List[Dict[str, str]] = []
    checks: List[Dict[str, Any]] = []

    # Step 0: 基础结构校验
    base_errors = validate_template(template)
    if base_errors:
        for msg in base_errors:
            _collect_issue(issues, "critical", "TEMPLATE_INVALID", msg, "template")
        result = {
            "passed": False,
            "score": 0,
            "summary": "模板基础校验未通过，请先修复必填项。",
            "checks": [],
            "issues": issues,
        }
        result["advisor"] = await _maybe_llm_suggestions(result)
        return result

    generated = SkillPackageGenerator(template).generate_all()
    api_nodes = generated["api_nodes"]
    biz_config = generated["biz_config"]

    # Step 1: 接口配置校验 + 可选连通性测试
    api_check_details: List[str] = []
    api_check_ok = True
    sample_response_by_node: Dict[str, Any] = {}

    for node_name, node in api_nodes.items():
        location = f"api_nodes.{node_name}"
        url = str(node.get("url", "") or "").strip()
        method = str(node.get("method", "POST") or "POST").upper()
        mock_mode = bool(node.get("mock_mode", False))
        response_extract = node.get("response_extract") or {}

        if not url and not mock_mode:
            api_check_ok = False
            _collect_issue(issues, "critical", "API_URL_MISSING", "接口 URL 为空且未开启 mock_mode。", f"{location}.url")

        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            api_check_ok = False
            _collect_issue(issues, "critical", "API_METHOD_INVALID", f"不支持的 HTTP 方法: {method}", f"{location}.method")

        if not response_extract:
            api_check_ok = False
            _collect_issue(issues, "critical", "RESPONSE_EXTRACT_EMPTY", "缺少 response_extract 映射配置。", f"{location}.response_extract")
        else:
            required_extract_keys = {"current_package", "recommended_packages"}
            missing = sorted(required_extract_keys - set(response_extract.keys()))
            if missing:
                api_check_ok = False
                _collect_issue(
                    issues,
                    "critical",
                    "REQUIRED_EXTRACT_MISSING",
                    f"缺少关键抽取字段: {', '.join(missing)}",
                    f"{location}.response_extract",
                )

        # 校验路径在示例响应/Mock响应中的可命中性
        probe_response: Any = None
        if node.get("mock_response"):
            probe_response = node.get("mock_response")
            sample_response_by_node[node_name] = probe_response
        elif mock_mode:
            _collect_issue(
                issues,
                "warning",
                "MOCK_RESPONSE_MISSING",
                "mock_mode=true 但未提供 mock_response，无法做字段路径验证。",
                location,
            )

        # 可选在线连通性测试
        if run_api_call and (not mock_mode) and url:
            req_template = node.get("request_template") or {}
            replacements = {
                "{{PHONE}}": sample_phone,
                "{{INTENT}}": str(template.get("meta", {}).get("intent", "")),
                "{{PROVINCE}}": str(template.get("meta", {}).get("province", "")),
                "{{TOP_N}}": str((template.get("strategy") or {}).get("top_n", 3)),
            }
            req_body = _replace_placeholders_in_obj(req_template, replacements)
            req_timeout = float(node.get("timeout", 10) or 10)
            req_headers = node.get("headers") or {"Content-Type": "application/json"}
            wrapper_key = node.get("request_body_wrapper")
            body_payload: Any = req_body
            if wrapper_key and isinstance(wrapper_key, str):
                body_payload = {wrapper_key: req_body}

            try:
                async with httpx.AsyncClient(timeout=req_timeout) as client:
                    if method == "GET":
                        resp = await client.get(url, headers=req_headers, params=req_body)
                    else:
                        resp = await client.request(method, url, headers=req_headers, json=body_payload)
                if resp.status_code >= 400:
                    api_check_ok = False
                    _collect_issue(
                        issues,
                        "critical",
                        "API_CALL_FAILED",
                        f"接口连通测试失败，HTTP {resp.status_code}",
                        location,
                    )
                else:
                    try:
                        probe_response = resp.json()
                        sample_response_by_node[node_name] = probe_response
                        api_check_details.append(f"{node_name} 连通成功（HTTP {resp.status_code}）")
                    except Exception:
                        _collect_issue(
                            issues,
                            "warning",
                            "API_RESPONSE_NOT_JSON",
                            "接口返回非 JSON，跳过字段路径校验。",
                            location,
                        )
            except Exception as e:
                api_check_ok = False
                _collect_issue(
                    issues,
                    "critical",
                    "API_CALL_FAILED",
                    f"接口连通测试异常: {e}",
                    location,
                )

        # 路径命中验证
        if probe_response and isinstance(response_extract, dict):
            for slot_name, path in response_extract.items():
                if not path:
                    continue
                v = _path_get(probe_response, str(path))
                if v is None:
                    level = "critical" if slot_name in {"current_package", "recommended_packages"} else "warning"
                    if level == "critical":
                        api_check_ok = False
                    _collect_issue(
                        issues,
                        level,
                        "RESPONSE_PATH_MISSING",
                        f"响应路径未命中: {slot_name} -> {path}",
                        f"{location}.response_extract.{slot_name}",
                    )

    checks.append(
        {
            "name": "接口配置与连通性校验",
            "status": "pass" if api_check_ok else "fail",
            "details": api_check_details,
        }
    )

    # Step 2: 模板槽位与变量映射校验
    template_check_ok = True
    template_check_warnings = 0
    script_templates = biz_config.get("script_templates_v2") or []
    top_n = (biz_config.get("strategy") or {}).get("top_n", 0)

    if not isinstance(top_n, int) or top_n <= 0:
        template_check_ok = False
        _collect_issue(
            issues,
            "critical",
            "TOP_N_INVALID",
            "strategy.top_n 必须为大于 0 的整数。",
            "biz_config.strategy.top_n",
        )

    if not script_templates:
        template_check_ok = False
        _collect_issue(
            issues,
            "critical",
            "SCRIPT_TEMPLATES_EMPTY",
            "script_templates_v2 为空，无法生成话术。",
            "biz_config.script_templates_v2",
        )

    for idx, tpl in enumerate(script_templates):
        location = f"biz_config.script_templates_v2[{idx}]"
        content = str(tpl.get("template_content", "") or "")
        prompt_template = str(tpl.get("prompt_template", "") or "")
        linked_vars = tpl.get("linked_vars") or []
        if not isinstance(linked_vars, list):
            linked_vars = []
            _collect_issue(
                issues,
                "warning",
                "LINKED_VARS_TYPE",
                "linked_vars 建议使用数组。",
                f"{location}.linked_vars",
            )
            template_check_warnings += 1

        content_slots = _extract_slots(content)
        prompt_slots = _extract_slots(prompt_template)
        all_slots = sorted(set(content_slots + prompt_slots))

        unknown_slots = [s for s in all_slots if s not in _KNOWN_TEMPLATE_SLOTS]
        for slot in unknown_slots:
            _collect_issue(
                issues,
                "warning",
                "UNKNOWN_TEMPLATE_SLOT",
                f"发现非标准槽位 `{slot}`，请确认是否拼写正确。",
                location,
            )
            template_check_warnings += 1

        # template_content 占位符与 linked_vars 对齐校验
        for slot in content_slots:
            if slot in {"max_length", "template_content", "table"}:
                continue
            if slot not in linked_vars:
                _collect_issue(
                    issues,
                    "warning",
                    "LINKED_VAR_MISMATCH",
                    f"模板使用了 `{slot}`，但 linked_vars 未声明。",
                    f"{location}.linked_vars",
                )
                template_check_warnings += 1

    checks.append(
        {
            "name": "模板槽位与变量映射校验",
            "status": "pass" if template_check_ok else "fail",
            "details": [f"模板数: {len(script_templates)}", f"warning: {template_check_warnings}"],
        }
    )

    critical_count = sum(1 for i in issues if i["level"] == "critical")
    warning_count = sum(1 for i in issues if i["level"] == "warning")
    passed = critical_count == 0
    score = max(0, 100 - critical_count * 25 - warning_count * 5)

    summary = (
        "校验通过，可发布。"
        if passed
        else f"校验未通过：{critical_count} 个阻断问题，{warning_count} 个告警。"
    )

    result = {
        "passed": passed,
        "score": score,
        "summary": summary,
        "checks": checks,
        "issues": issues,
        "stats": {
            "critical": critical_count,
            "warning": warning_count,
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "api_call_enabled": run_api_call,
        },
    }
    result["advisor"] = await _maybe_llm_suggestions(result)
    return result


# ── API 端点 ─────────────────────────────────────

@app.get("/")
async def index():
    """根路径健康提示（前端由 Vue/Vite 开发服务器或 Nginx 独立提供）。"""
    return {"service": "AutoConfigAgent API", "status": "ok", "docs": "/docs"}


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "skills_runtime": str(SKILLS_RUNTIME),
        "skills_runtime_exists": SKILLS_RUNTIME.exists(),
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/provinces")
async def get_provinces():
    """获取省份和意图列表（从 skills-runtime 自动发现）。"""
    skills = _discover_skills()
    return {
        "provinces": list(skills.values()),
        "total_provinces": len(skills),
        "skills_runtime_path": str(SKILLS_RUNTIME),
    }


@router.get("/template/example")
async def get_example_template():
    """获取标准导入模板示例。"""
    return {"template": EXAMPLE_TEMPLATE}


# ── Excel 导入相关 ────────────────────────────────

@router.get("/template/excel")
async def download_excel_template():
    """下载 Excel 导入模板（.xlsx）。"""
    content = build_template_workbook()
    headers = {
        "Content-Disposition": 'attachment; filename="skill_import_template.xlsx"'
    }
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers=headers,
    )


@router.get("/template/excel-doc")
async def get_excel_template_doc():
    """获取 Excel 模板的字段说明（用于前端帮助区展示）。"""
    return get_template_doc()


@router.post("/import/excel")
async def parse_import_excel(file: UploadFile = File(...)):
    """解析上传的 Excel 模板文件（.xlsx）。"""
    if not file:
        raise HTTPException(status_code=400, detail="请上传 Excel 文件")
    name = (file.filename or "").lower()
    if not (name.endswith(".xlsx") or name.endswith(".xlsm")):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx / .xlsm 格式")

    try:
        content = await file.read()
        template = parse_excel(content)
    except Exception as e:
        logger.exception("Excel 解析失败")
        raise HTTPException(status_code=422, detail=f"Excel 解析失败：{e}")

    errors = validate_template(template)
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "template": template,
        "province": template.get("meta", {}).get("province", ""),
        "intent": template.get("meta", {}).get("intent", ""),
    }


# ── 内置模板库 ────────────────────────────────────

@router.get("/templates/library")
async def get_template_library():
    """返回内置模板菜单（不含完整内容，仅元信息）。"""
    return {"templates": list_templates()}


@router.get("/templates/library/{template_id}")
async def get_template_library_item(template_id: str):
    """根据 ID 获取完整模板。"""
    tpl = get_template(template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    return {
        "template_id": template_id,
        "template": tpl,
    }


@router.post("/import/parse")
async def parse_import(file: Optional[UploadFile] = File(None)):
    """
    解析上传的模板文件（JSON）。
    支持 multipart 文件上传。
    """
    if not file:
        raise HTTPException(status_code=400, detail="请上传文件")
    try:
        content = await file.read()
        template = json.loads(content.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"JSON 格式错误：{e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件读取失败：{e}")

    errors = validate_template(template)
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "template": template,
        "province": template.get("meta", {}).get("province", ""),
        "intent": template.get("meta", {}).get("intent", ""),
    }


@router.post("/import/parse-text")
async def parse_import_text(req: TextImportRequest):
    """
    解析粘贴的模板文本（JSON 字符串）。
    """
    try:
        template = json.loads(req.text)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"JSON 格式错误：{e}")

    errors = validate_template(template)
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "template": template,
        "province": template.get("meta", {}).get("province", ""),
        "intent": template.get("meta", {}).get("intent", ""),
    }


@router.post("/import/generate")
async def generate_preview(req: GenerateRequest):
    """
    根据导入模板生成 skill 包预览（不写入磁盘）。
    返回所有待生成文件的内容。
    """
    errors = validate_template(req.template)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "模板验证失败", "errors": errors},
        )

    try:
        gen = SkillPackageGenerator(req.template)
        result = gen.generate_all()
    except Exception as e:
        logger.exception("代码生成失败")
        raise HTTPException(status_code=500, detail=f"代码生成失败：{e}")

    # 检查是否已存在
    province = result["province"]
    intent = result["intent"]
    skill_dir = SKILLS_RUNTIME / province / intent
    already_exists = skill_dir.exists()

    return {
        "province": province,
        "intent": intent,
        "already_exists": already_exists,
        "skill_dir": str(SKILLS_RUNTIME / province / intent),
        "files": {
            "api_nodes": {
                "path": f"{province}/{intent}/config/api_nodes.json",
                "content": json.dumps(result["api_nodes"], ensure_ascii=False, indent=2),
            },
            "biz_config": {
                "path": f"{province}/{intent}/config/biz_config.json",
                "content": json.dumps(result["biz_config"], ensure_ascii=False, indent=2),
            },
            "meta": {
                "path": f"{province}/{intent}/_meta.json",
                "content": json.dumps(result["meta"], ensure_ascii=False, indent=2),
            },
        },
    }


@router.post("/import/validate")
async def validate_import(req: ValidateRequest):
    """
    对导入模板执行跑通校验（接口、映射、模板槽位）。
    """
    try:
        validation = await _run_validation(
            template=req.template,
            run_api_call=req.run_api_call,
            sample_phone=req.sample_phone,
        )
        return validation
    except Exception as e:
        logger.exception("配置校验失败")
        raise HTTPException(status_code=500, detail=f"配置校验失败：{e}")


@router.post("/import/publish")
async def publish_skill(req: PublishRequest, request: Request):
    """
    将生成的 skill 包发布到 skills-runtime 目录。
    可选：触发话术智能体-生产的热重载。需同省份或本部权限（不可为其他省份创建配置）。
    """
    errors = validate_template(req.template)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "模板验证失败", "errors": errors},
        )

    try:
        gen = SkillPackageGenerator(req.template)
        generated = gen.generate_all()
    except Exception as e:
        logger.exception("代码生成失败")
        raise HTTPException(status_code=500, detail=f"代码生成失败：{e}")

    # 省份级写权限校验：非本部用户只能为本省创建/覆盖配置
    _enforce_province_write(request, generated.get("province", ""))

    validation: Optional[Dict[str, Any]] = None
    if req.validate_before_publish:
        validation = await _run_validation(
            template=req.template,
            run_api_call=req.validate_run_api_call,
        )
        if not validation.get("passed", False):
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "发布前校验未通过，请先修复阻断问题。",
                    "validation": validation,
                },
            )

    # 写入文件
    write_result = _write_skill_package(generated, overwrite=req.overwrite)

    # 触发热重载（可选）
    reload_result: Optional[Dict[str, Any]] = None
    if req.reload:
        reload_result = await _trigger_reload(generated["province"], generated["intent"])

    return {
        "success": True,
        "message": f"Skill 包已发布：{generated['province']}/{generated['intent']}",
        **write_result,
        "reload": reload_result,
        "validation": validation,
    }


@router.get("/skills/{province}/{intent}/config")
async def get_skill_config(province: str, intent: str):
    """读取已有 skill 的配置文件（先把最新生效配置落盘，保证读到的不是旧文件）。

    仅存于 ES 的技能包（容器内无本地目录）也支持：落盘生效配置后正常展示。
    """
    skill_dir = SKILLS_RUNTIME / province / intent
    if not skill_dir.exists():
        # 本地无目录时，若运行时 registry 已从 ES 加载到该技能包，则先落盘再展示
        _pkg = None
        try:
            from utils.skill_runtime import skill_registry as _reg
            _pkg = _reg.get(province, intent)
        except ImportError:
            pass
        if _pkg is None:
            raise HTTPException(status_code=404, detail=f"Skill 不存在：{province}/{intent}")
        logger.info("[get_skill_config] %s/%s 本地无目录，从 ES 生效配置落盘展示", province, intent)

    _sync_live_config_to_disk(province, intent)

    # 记录本次详情展示的配置来源，排查「页面显示的到底是 ES 还是本地」时直接看日志
    config_source = "local"
    try:
        from utils.skill_runtime import skill_registry
        pkg = skill_registry.get(province, intent)
        if pkg is not None:
            config_source = (pkg.meta or {}).get("config_source", "local")
    except ImportError:
        pass
    logger.info("[get_skill_config] %s/%s 详情返回（生效配置来源: %s）", province, intent, config_source)

    result: Dict[str, Any] = {"province": province, "intent": intent, "files": {},
                              "config_source": config_source}

    for filename, rel_path in [
        ("api_nodes", "config/api_nodes.json"),
        ("biz_config", "config/biz_config.json"),
        ("meta", "_meta.json"),
    ]:
        path = skill_dir / rel_path
        if path.exists():
            try:
                result["files"][filename] = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                result["files"][filename] = None

    for filename, rel_path in [
        ("main_flow", "scripts/main_flow.py"),
        ("skill_md", "SKILL.md"),
    ]:
        path = skill_dir / rel_path
        if path.exists():
            result["files"][filename] = path.read_text(encoding="utf-8")

    # 本地无 _meta.json 时，用 ES 中的 skill_meta 兜底（仅存于 ES 的技能包）
    if not result["files"].get("meta"):
        try:
            from utils.skill_runtime import skill_registry as _reg2
            _pkg2 = _reg2.get(province, intent)
            sm = ((_pkg2.meta if _pkg2 else None) or {}).get("skill_meta")
            if sm:
                result["files"]["meta"] = sm
                result["meta_source"] = "es"
        except ImportError:
            pass

    return result


@router.post("/skills/{province}/{intent}/reload")
async def reload_skill(province: str, intent: str):
    """触发话术智能体-生产的 Skill 热重载，并将状态置为 published。

    状态与列表页保持同源：生产模式写入 ES skill_meta（+Redis/广播），
    本地 _meta.json 仅作快照；仅存于 ES 的技能包（无本地目录）同样支持。
    """
    skill_dir = SKILLS_RUNTIME / province / intent
    _pkg = None
    try:
        from utils.skill_runtime import skill_registry as _reg
        _pkg = _reg.get(province, intent)
    except ImportError:
        pass
    if not skill_dir.exists() and _pkg is None:
        raise HTTPException(status_code=404, detail=f"Skill 不存在：{province}/{intent}")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    overrides = {"status": "published", "published_at": now}

    # 本地 _meta.json 快照（目录存在才写）
    meta_path = skill_dir / "_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta.update(overrides)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # 生产模式：状态写入 ES skill_meta（列表页真源）+ 本实例内存
    try:
        from utils.skill_runtime import IS_DEV
        if not IS_DEV:
            from services.skill_meta_service import upsert_skill_meta
            if upsert_skill_meta(province, intent, operator="status_change",
                                 overrides=overrides) and _pkg is not None:
                sm = dict(((_pkg.meta or {}).get("skill_meta")) or {})
                sm.update(overrides)
                _pkg.meta["skill_meta"] = sm
    except ImportError:
        pass

    result = await _trigger_reload(province, intent)
    return result


# ── Skill 管理接口 ────────────────────────────────

class SkillUpdateRequest(BaseModel):
    config_type: str          # "api_nodes" | "biz_config"
    content: Dict[str, Any]   # 更新后的 JSON 内容


class SkillRollbackRequest(BaseModel):
    backup_name: str          # 备份目录名（来自 GET versions 接口）


class SkillStatusRequest(BaseModel):
    status: str               # "published" | "draft" | "offline"


@router.get("/skills")
async def list_skills(province: str = "", intent: str = ""):
    """
    列出所有已配置的 Skill，支持按省份/意图过滤。
    返回每个 Skill 的完整元数据：skill_id、skill_name、version、status、
    created_at、updated_at 等，_meta.json 缺失的字段自动用文件系统时间兜底。

    生产模式下统计字段（接口数/模板数等）优先取运行时生效配置
    （skill_registry，来源 ES/Redis），并附带 config_source 标识
    （es / redis / local），保证列表反映的是真正生效的配置而非本地旧文件。
    """
    result: List[Dict[str, Any]] = []
    if not SKILLS_RUNTIME.exists():
        logger.warning("skills-runtime 路径不存在: %s", SKILLS_RUNTIME)
        return {"skills": result, "total": 0, "skills_runtime": str(SKILLS_RUNTIME)}

    # 运行时 registry（启动时已按 Redis → ES → 本地 优先级加载的生效配置）
    _registry = None
    try:
        from utils.skill_runtime import skill_registry as _registry  # noqa: F811
    except ImportError:
        logger.warning("[list_skills] skill_registry 不可用（独立启动模式），仅展示本地文件信息")

    def _fs_time(path: Path) -> str:
        """取文件/目录的 mtime，格式化为 YYYY-MM-DD HH:MM。"""
        try:
            return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    for province_dir in sorted(SKILLS_RUNTIME.iterdir()):
        if not province_dir.is_dir():
            continue
        pcode = province_dir.name
        if province and pcode != province:
            continue

        # 省份名称（来自 manifest.json）
        manifest_path = province_dir / "manifest.json"
        province_name = pcode
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                province_name = manifest.get("province_name", pcode)
            except Exception:
                pass

        for intent_dir in sorted(province_dir.iterdir()):
            # 跳过备份目录（含 .bak.）和非目录
            if not intent_dir.is_dir():
                continue
            if ".bak." in intent_dir.name or intent_dir.name.startswith("__"):
                continue
            iname = intent_dir.name
            if intent and iname != intent:
                continue

            # ── 基础信息 ───────────────────────
            skill: Dict[str, Any] = {
                "province":        pcode,
                "province_name":   province_name,
                "intent":          iname,
                # 规范化的 skill_id：{province}-{intent}
                "skill_id":        f"{pcode}-{iname}",
                "skill_name":      f"{province_name} · {iname}",
                # 默认值（_meta.json 解析后覆盖）
                "version":         "1.0.0",
                "status":          "published",
                "description":     "",
                "author":          "",
                "created_by":      "",
                "created_at":      _fs_time(intent_dir),
                "updated_at":      _fs_time(intent_dir),
                # 文件完整性
                "has_api_nodes":   (intent_dir / "config" / "api_nodes.json").exists(),
                "has_biz_config":  (intent_dir / "config" / "biz_config.json").exists(),
                "has_main_flow":   (intent_dir / "scripts" / "main_flow.py").exists(),
            }

            # ── _meta.json（覆盖默认值）──────────
            meta_path = intent_dir / "_meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    # skill_id / skill_name 优先取 meta 中的 name
                    if meta.get("name"):
                        skill["skill_name"] = meta["name"]
                    if meta.get("skill_id"):
                        skill["skill_id"] = meta["skill_id"]
                    skill.update({
                        "version":     meta.get("version",    skill["version"]),
                        "status":      meta.get("status",     skill["status"]),
                        "description": meta.get("description", skill["description"]),
                        "author":      meta.get("author",     skill["author"]),
                        "created_by":  meta.get("created_by", skill["created_by"]),
                        # 若 meta 里有时间则覆盖文件系统时间
                        "created_at":  meta.get("created_at", skill["created_at"]),
                        "updated_at":  meta.get("updated_at", _fs_time(meta_path)),
                    })
                    # rollback 信息
                    if meta.get("rollback_at"):
                        skill["rollback_at"]   = meta["rollback_at"]
                        skill["rollback_from"] = meta.get("rollback_from", "")
                except Exception:
                    pass
            else:
                # 无 _meta.json → updated_at 用最新配置文件的 mtime
                cfg_times = []
                for p in [
                    intent_dir / "config" / "api_nodes.json",
                    intent_dir / "config" / "biz_config.json",
                ]:
                    if p.exists():
                        cfg_times.append(_fs_time(p))
                if cfg_times:
                    skill["updated_at"] = max(cfg_times)

            # ── 接口节点统计 ──────────────────────
            api_path = intent_dir / "config" / "api_nodes.json"
            if api_path.exists():
                try:
                    api_nodes = json.loads(api_path.read_text(encoding="utf-8"))
                    skill["api_node_count"] = sum(
                        1 for c in api_nodes.values()
                        if isinstance(c, dict) and c.get("enabled", True)
                    )
                    skill["mock_mode"] = any(
                        c.get("mock_mode") for c in api_nodes.values() if isinstance(c, dict)
                    )
                except Exception:
                    skill["api_node_count"] = 0
                    skill["mock_mode"]      = False
            else:
                skill["api_node_count"] = 0
                skill["mock_mode"]      = False

            # ── 话术模板统计 ──────────────────────
            biz_path = intent_dir / "config" / "biz_config.json"
            if biz_path.exists():
                try:
                    biz = json.loads(biz_path.read_text(encoding="utf-8"))
                    skill["template_count"] = len(biz.get("script_templates_v2") or [])
                    skill["top_n"]          = (biz.get("strategy") or {}).get("top_n", 0)
                except Exception:
                    skill["template_count"] = 0
            else:
                skill["template_count"] = 0

            # ── 运行时生效配置覆盖（ES/Redis 优先）───────────────
            # 统计字段以 skill_registry 中真正生效的配置为准，
            # 并标注 config_source（es/redis/local），本地文件仅作兜底。
            skill["config_source"] = "local"
            if _registry is not None:
                try:
                    pkg = _registry.get(pcode, iname)
                except Exception:
                    pkg = None
                if pkg is not None:
                    skill["config_source"] = (pkg.meta or {}).get("config_source", "local")
                    skill["config_loaded_at"] = (pkg.meta or {}).get("loaded_at", "")
                    # ES 中的结构化 skill 信息（skill_meta）优先于本地 _meta.json
                    sm = (pkg.meta or {}).get("skill_meta") or {}
                    if sm:
                        skill["meta_source"] = "es"
                        for k in ("skill_id", "skill_name", "version", "status",
                                  "description", "author", "created_by",
                                  "created_at", "updated_at"):
                            if sm.get(k):
                                skill[k] = sm[k]
                    else:
                        skill["meta_source"] = "local"
                    live_api = (pkg.config or {}).get("api_nodes") or {}
                    live_biz = (pkg.config or {}).get("biz_config") or {}
                    if live_api:
                        skill["api_node_count"] = sum(
                            1 for c in live_api.values()
                            if isinstance(c, dict) and c.get("enabled", True)
                        )
                        skill["mock_mode"] = any(
                            c.get("mock_mode") for c in live_api.values() if isinstance(c, dict)
                        )
                    if live_biz:
                        skill["template_count"] = len(live_biz.get("script_templates_v2") or [])
                        skill["top_n"] = (live_biz.get("strategy") or {}).get("top_n", 0)

            # ── 历史备份数 ────────────────────────
            skill["backup_count"] = sum(
                1 for d in province_dir.iterdir()
                if d.is_dir() and d.name.startswith(f"{iname}.bak.")
            )

            result.append(skill)

    # ── 并入仅存于 ES/Redis 的技能包（容器内无本地目录）────────
    # skill_registry.load_all 已把 ES 中已发布但本地缺目录的技能包也加载进内存，
    # 这里补充展示，保证 SkillManager 列表与 ES 真源一致。
    if _registry is not None:
        seen_keys = {f"{s['province']}:{s['intent']}" for s in result}
        try:
            all_pkgs = _registry.list_all()
        except Exception:
            all_pkgs = []
        for info in all_pkgs or []:
            key = info.get("key", "")
            if key in seen_keys or ":" not in key:
                continue
            pcode, iname = key.split(":", 1)
            if (province and pcode != province) or (intent and iname != intent):
                continue
            pkg = _registry.get(pcode, iname)
            if pkg is None:
                continue
            sm = (pkg.meta or {}).get("skill_meta") or {}
            live_api = (pkg.config or {}).get("api_nodes") or {}
            live_biz = (pkg.config or {}).get("biz_config") or {}
            result.append({
                "province":        pcode,
                "province_name":   sm.get("province_name", pcode),
                "intent":          iname,
                "skill_id":        sm.get("skill_id", f"{pcode}-{iname}"),
                "skill_name":      sm.get("skill_name", f"{pcode} · {iname}"),
                "version":         sm.get("version", pkg.version),
                "status":          sm.get("status", "published"),
                "description":     sm.get("description", ""),
                "author":          sm.get("author", ""),
                "created_by":      sm.get("created_by", ""),
                "created_at":      sm.get("created_at", ""),
                "updated_at":      sm.get("updated_at", ""),
                "has_api_nodes":   bool(live_api),
                "has_biz_config":  bool(live_biz),
                "has_main_flow":   False,
                "api_node_count":  sum(
                    1 for c in live_api.values()
                    if isinstance(c, dict) and c.get("enabled", True)
                ),
                "mock_mode":       any(
                    c.get("mock_mode") for c in live_api.values() if isinstance(c, dict)
                ),
                "template_count":  len(live_biz.get("script_templates_v2") or []),
                "top_n":           (live_biz.get("strategy") or {}).get("top_n", 0),
                "backup_count":    0,
                "config_source":   (pkg.meta or {}).get("config_source", "es"),
                "config_loaded_at": (pkg.meta or {}).get("loaded_at", ""),
                "meta_source":     "es" if sm else "runtime",
                "local_missing":   True,
            })
            logger.info("[list_skills] 并入仅存于 ES 的技能: %s", key)

    # 来源汇总（写日志 + 返回，前端/排查都能直接看到配置从哪来）
    by_source: Dict[str, int] = {}
    for s in result:
        src = s.get("config_source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1
    logger.info(
        "[list_skills] 返回 %d 个技能（过滤 province=%r intent=%r），配置来源: %s",
        len(result), province or "全部", intent or "全部",
        ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())) or "无",
    )

    return {
        "skills": result,
        "total": len(result),
        "skills_runtime": str(SKILLS_RUNTIME),
        "source_summary": by_source,
    }


# ── Context 工程只读审计（验收 S2-1~S2-5，不写 ES/本地）────────────────

_STANDARD_DOMAINS: List[Dict[str, str]] = [
    {"key": "current_package",      "label": "当前套餐"},
    {"key": "usage",                "label": "历史用量"},
    {"key": "tags",                 "label": "用户标签"},
    {"key": "user_info",            "label": "用户基础信息"},
    {"key": "recommended_packages", "label": "推荐产品"},
    {"key": "user_profile",         "label": "用户画像"},
    {"key": "domain_ext",           "label": "扩展域"},
]
_DOMAIN_KEYS = {d["key"] for d in _STANDARD_DOMAINS}
_VAR_KEY_TO_SLOT: Dict[str, str] = {
    "cur_brief": "current_package",
    "pkg_brief": "recommended_packages",
    "usage_line": "usage",
    "user_tags": "tags",
    "user_info": "user_info",
    "user_profile": "user_profile",
    "domain_ext": "domain_ext",
}


def _domains_from_api_node(node: Dict[str, Any]) -> List[str]:
    """从单个 api_nodes 配置推断产出的标准域（只读分析，不改配置）。"""
    domains: set = set()
    if not isinstance(node, dict) or not node.get("enabled", True):
        return []
    if node.get("source_type") == "direct":
        mock = node.get("mock_response") or {}
        if isinstance(mock, dict):
            domains.update(k for k in mock if k in _DOMAIN_KEYS)
        domains.add("domain_ext")  # 直传默认走 extra_info 域
    re = node.get("response_extract") or {}
    if isinstance(re, dict):
        domains.update(k for k in re if k in _DOMAIN_KEYS)
    ft = node.get("field_transform") or {}
    if isinstance(ft, dict):
        for k in ft:
            top = str(k).split(".")[0]
            if top in _DOMAIN_KEYS:
                domains.add(top)
    return sorted(domains)


def _audit_skill_context(
    api_nodes: Dict[str, Any],
    biz_config: Dict[str, Any],
) -> Dict[str, Any]:
    """只读审计：接口出参 → 7 大固定域 → 话术模板 linked_vars 闭环。"""
    issues: List[Dict[str, str]] = []
    api_supplies: Dict[str, List[str]] = {}
    global_supplied: set = set()

    for name, node in (api_nodes or {}).items():
        if not isinstance(node, dict):
            continue
        supplied = _domains_from_api_node(node)
        api_supplies[name] = supplied
        global_supplied.update(supplied)
        if not node.get("enabled", True):
            continue
        if node.get("source_type") != "direct" and not node.get("response_extract"):
            _collect_issue(issues, "warning", "API_NO_EXTRACT",
                           f"接口节点 {name} 未配置 response_extract", f"api_nodes.{name}")

    domain_status = [
        {
            "key": d["key"], "label": d["label"],
            "supplied": d["key"] in global_supplied,
            "providers": [n for n, slots in api_supplies.items() if d["key"] in slots],
        }
        for d in _STANDARD_DOMAINS
    ]

    templates = biz_config.get("script_templates_v2") or []
    tpl_results: List[Dict[str, Any]] = []
    unmet_total = 0
    for idx, tpl in enumerate(templates):
        if not isinstance(tpl, dict):
            continue
        if tpl.get("status") == "offline" or tpl.get("deleted"):
            continue
        linked_apis = tpl.get("linked_apis") or []
        linked_vars = tpl.get("linked_vars") or []
        need_slots: set = set()
        for v in linked_vars:
            slot = _VAR_KEY_TO_SLOT.get(v)
            if slot:
                need_slots.add(slot)
        # 限定 linked_apis 时只统计对应接口产出的域
        if linked_apis:
            api_slots: set = set()
            for an in linked_apis:
                api_slots.update(api_supplies.get(an, []))
            unmet = sorted(s for s in need_slots if s not in api_slots)
        else:
            unmet = sorted(s for s in need_slots if s not in global_supplied)
        if unmet:
            unmet_total += 1
            _collect_issue(issues, "warning", "TPL_SLOT_UNMET",
                           f"模板「{tpl.get('template_name', idx)}」缺少域: {', '.join(unmet)}",
                           f"biz_config.script_templates_v2[{idx}]")
        tpl_results.append({
            "template_id":   tpl.get("template_id", ""),
            "template_name": tpl.get("template_name", ""),
            "linked_vars":   linked_vars,
            "linked_apis":   linked_apis,
            "need_slots":    sorted(need_slots),
            "unmet_slots":   unmet,
            "closed":        len(unmet) == 0,
        })

    critical = sum(1 for i in issues if i["level"] == "critical")
    passed = critical == 0 and unmet_total == 0
    return {
        "passed": passed,
        "summary": (
            "数据流闭环正常" if passed
            else f"发现 {len(issues)} 项问题（{unmet_total} 个模板变量未闭环）"
        ),
        "domain_status":    domain_status,
        "api_supplies":     api_supplies,
        "template_audit":   tpl_results,
        "issues":           issues,
        "domains_covered":  len(global_supplied),
        "domains_total":    len(_STANDARD_DOMAINS),
        "templates_closed": sum(1 for t in tpl_results if t["closed"]),
        "templates_total":  len(tpl_results),
    }


@router.get("/skills/{province}/{intent}/context-audit")
async def skill_context_audit(province: str, intent: str):
    """只读审计单个 Skill 的 Context 工程链路（接口→固定域→模板槽位）。

    数据来自运行时生效配置（skill_registry / ES / Redis），不修改任何配置。
    供 SkillManager「数据流概览」Tab 展示，对应验收标准 S2-1~S2-5。
    """
    api_nodes: Dict[str, Any] = {}
    biz_config: Dict[str, Any] = {}
    config_source = "local"
    try:
        from utils.skill_runtime import skill_registry
        pkg = skill_registry.get(province, intent)
        if pkg is not None:
            api_nodes  = (pkg.config or {}).get("api_nodes")  or {}
            biz_config = (pkg.config or {}).get("biz_config") or {}
            config_source = (pkg.meta or {}).get("config_source", "local")
    except ImportError:
        pass
    if not api_nodes and not biz_config:
        skill_dir = SKILLS_RUNTIME / province / intent
        if skill_dir.exists():
            for name in ("api_nodes", "biz_config"):
                p = skill_dir / "config" / f"{name}.json"
                if p.exists():
                    try:
                        data = json.loads(p.read_text(encoding="utf-8"))
                        if name == "api_nodes":
                            api_nodes = data
                        else:
                            biz_config = data
                    except Exception:
                        pass
        else:
            raise HTTPException(status_code=404, detail=f"Skill 不存在：{province}/{intent}")

    audit = _audit_skill_context(api_nodes, biz_config)
    audit["province"] = province
    audit["intent"] = intent
    audit["config_source"] = config_source
    return audit


@router.get("/skills/export")
async def export_all_skills(province: str = "", intent: str = "", download: bool = True):
    """一键导出全部技能包配置（接口 + 话术模板 + 结构化信息）。

    数据以「运行时生效配置」为准（skill_registry，来源 ES/Redis，
    与 SkillManager 同步后的内容一致）；registry 不可用时回退本地文件。
    可选 province/intent 过滤。download=true 时返回带下载头的附件。
    """
    now = datetime.now()
    export: Dict[str, Any] = {
        "export_meta": {
            "exported_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "unknown",
            "filter": {"province": province or None, "intent": intent or None},
            "total": 0,
        },
        "skills": [],
    }

    # 运行时 registry（ES/Redis 同步后的生效配置）
    _registry = None
    try:
        from utils.skill_runtime import skill_registry as _registry  # noqa: F811
    except ImportError:
        logger.warning("[export] skill_registry 不可用，改用本地文件导出")

    by_source: Dict[str, int] = {}

    def _local_cfg(pcode: str, iname: str, name: str) -> Dict[str, Any]:
        p = SKILLS_RUNTIME / pcode / iname / "config" / f"{name}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    # 枚举技能包：registry 优先（含仅存于 ES 的），否则扫描本地目录
    keys: List[tuple] = []
    if _registry is not None:
        try:
            for info in _registry.list_all() or []:
                k = info.get("key", "")
                if ":" in k:
                    keys.append(tuple(k.split(":", 1)))
        except Exception as e:
            logger.warning("[export] 枚举 registry 失败: %s", e)
    if not keys and SKILLS_RUNTIME.exists():
        for pdir in sorted(SKILLS_RUNTIME.iterdir()):
            if not pdir.is_dir():
                continue
            for idir in sorted(pdir.iterdir()):
                if idir.is_dir() and ".bak." not in idir.name and not idir.name.startswith("__"):
                    keys.append((pdir.name, idir.name))

    for pcode, iname in keys:
        if province and pcode != province:
            continue
        if intent and iname != intent:
            continue

        api_nodes: Dict[str, Any] = {}
        biz_config: Dict[str, Any] = {}
        skill_meta: Dict[str, Any] = {}
        src = "local"
        if _registry is not None:
            try:
                pkg = _registry.get(pcode, iname)
            except Exception:
                pkg = None
            if pkg is not None:
                api_nodes  = (pkg.config or {}).get("api_nodes")  or {}
                biz_config = (pkg.config or {}).get("biz_config") or {}
                skill_meta = (pkg.meta or {}).get("skill_meta") or {}
                src = (pkg.meta or {}).get("config_source", "local")

        # 兜底本地文件
        if not api_nodes:
            api_nodes = _local_cfg(pcode, iname, "api_nodes")
        if not biz_config:
            biz_config = _local_cfg(pcode, iname, "biz_config")

        by_source[src] = by_source.get(src, 0) + 1
        export["skills"].append({
            "province": pcode,
            "intent": iname,
            "config_source": src,
            "skill_meta": skill_meta,
            "api_nodes": api_nodes,
            "biz_config": biz_config,
        })

    export["export_meta"]["total"] = len(export["skills"])
    export["export_meta"]["source_summary"] = by_source
    export["export_meta"]["source"] = (
        "es/redis" if any(s in ("es", "redis") for s in by_source) else "local"
    )
    logger.info(
        "[export] 导出 %d 个技能包（来源: %s）",
        len(export["skills"]),
        ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())) or "无",
    )

    body = json.dumps(export, ensure_ascii=False, indent=2)
    if download:
        fname = f"skills_export_{now.strftime('%Y%m%d_%H%M%S')}.json"
        return Response(
            content=body,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    return Response(content=body, media_type="application/json; charset=utf-8")


@router.get("/skills/{province}/{intent}/as-template")
async def skill_as_template(province: str, intent: str):
    """
    将已有 Skill 的配置文件（api_nodes.json + biz_config.json + _meta.json）
    反向转换为标准导入模板格式，供前端「克隆已有Skill」模式使用。
    """
    skill_dir = SKILLS_RUNTIME / province / intent
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"Skill 不存在：{province}/{intent}")

    # ── 读取配置文件 ─────────────────────────────────
    def _read_json(rel: str) -> dict:
        p = skill_dir / rel
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    meta       = _read_json("_meta.json")
    api_nodes  = _read_json("config/api_nodes.json")
    biz_config = _read_json("config/biz_config.json")

    # ── 提取第一个 API 节点 ──────────────────────────
    first_node_name = next(
        (k for k, v in api_nodes.items() if isinstance(v, dict)), None
    )
    api_block: Dict[str, Any] = {}
    if first_node_name:
        node = api_nodes[first_node_name]
        api_block = {
            "name":              first_node_name,
            "comment":           node.get("comment", ""),
            "url":               node.get("url", ""),
            "method":            node.get("method", "POST"),
            "headers":           node.get("headers", {"Content-Type": "application/json"}),
            "timeout":           node.get("timeout", 30),
            "max_retries":       node.get("max_retries", 2),
            "mock_mode":         node.get("mock_mode", False),
            "request_template":  node.get("request_template", {}),
            "response_extract":  node.get("response_extract", {}),
            "field_transform":   node.get("field_transform", {}),
            "mock_response":     node.get("mock_response", {}),
            "request_body_wrapper": node.get("request_body_wrapper", ""),
        }

    # ── 提取策略和字段别名 ───────────────────────────
    strategy     = biz_config.get("strategy", {})
    field_aliases = biz_config.get("field_aliases", {})

    # ── 提取话术模板 ─────────────────────────────────
    raw_templates = biz_config.get("script_templates_v2", [])
    templates = [
        {
            "template_id":       t.get("template_id", ""),
            "template_name":     t.get("template_name", ""),
            "stage":             t.get("stage", ""),
            "scene":             t.get("scene", ""),
            "product_id":        t.get("product_id", ""),
            "template_content":  t.get("content", t.get("template_content", "")),
            "prompt_template":   t.get("prompt_template", ""),
            "script_requirement": t.get("script_requirement", ""),
            "linked_vars":       t.get("linked_vars", []),
            "linked_apis":       t.get("linked_apis", []),
            "status":            t.get("status", "online"),
        }
        for t in raw_templates
    ]

    template: Dict[str, Any] = {
        "meta": {
            "province":      meta.get("province", province),
            "province_name": meta.get("province_name", province),
            "intent":        meta.get("scenario_id", intent),
            "description":   meta.get("description", ""),
            "version":       meta.get("version", "1.0.0"),
            "author":        meta.get("author", ""),
        },
        "api":           api_block,
        "strategy":      strategy,
        "field_aliases": field_aliases,
        "templates":     templates,
        # 标记来源，前端克隆时会修改 meta.province / meta.intent
        "_cloned_from": f"{province}/{intent}",
    }

    return {
        "template":     template,
        "source":       f"{province}/{intent}",
        "node_count":   len([v for v in api_nodes.values() if isinstance(v, dict)]),
        "template_count": len(templates),
    }


def _sync_live_config_to_disk(province: str, intent: str) -> None:
    """把当前生效配置落盘到本地 config/*.json。

    编辑页的接口/话术保存（skill_registry.save_api_nodes / save_biz_config）
    写入的是 ES/Redis + 进程内存，不写本地文件；
    因此任何备份（copytree 本地目录）前必须先把最新生效配置同步到磁盘，
    否则备份到的是旧的初始配置。
    """
    try:
        from utils.skill_runtime import skill_registry

        pkg = skill_registry.get(province, intent)
        if not pkg:
            return
        cfg_dir = SKILLS_RUNTIME / province / intent / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        for name in ("api_nodes", "biz_config"):
            data = (pkg.config or {}).get(name)
            if isinstance(data, dict) and data:
                (cfg_dir / f"{name}.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        logger.info("已同步生效配置到本地: %s/%s", province, intent)
    except Exception as e:
        logger.warning("生效配置落盘失败 %s/%s: %s", province, intent, e)


def _push_local_config_to_registry(
    province: str, intent: str, operator: str = "rollback"
) -> Tuple[bool, str]:
    """把本地 config/*.json 经统一发布器发布为生效配置（本地文件 + ES/Redis + 进程内存）。

    回滚只恢复本地文件是不够的：运行时配置优先从 ES/Redis 读取，
    必须把恢复后的配置发布回去，回滚才真正生效。

    返回 (ok, message)：发布失败必须如实上报（ok=False），由调用方决定
    是否向用户暴露错误——静默吞掉会导致"回滚显示成功但生产未生效"。
    独立启动模式（skill_publisher 不可用）视为降级成功，与 PUT config 端点语义一致。
    """
    try:
        from services.skill_publisher import publish_package  # 延迟 import
    except ImportError:
        # 独立启动模式（无 znhs 根环境）：本地文件即唯一配置源，降级为成功
        logger.warning("skill_publisher 不可用（独立启动模式），回滚仅恢复本地文件")
        return True, "独立启动模式：仅恢复本地文件"

    try:
        cfg_dir = SKILLS_RUNTIME / province / intent / "config"
        payload: Dict[str, Any] = {}
        for name in ("api_nodes", "biz_config"):
            path = cfg_dir / f"{name}.json"
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict) and data:
                payload[name] = data
        if not payload:
            return True, "本地无可发布的配置文件"
        results = publish_package(
            province, intent,
            api_nodes=payload.get("api_nodes"),
            biz_config=payload.get("biz_config"),
            operator=operator, comment="文件备份回滚同步",
        )
        failed = {k: r.message for k, r in results.items() if not r.success}
        if failed:
            detail = "; ".join(f"{k}: {v}" for k, v in failed.items())
            logger.warning("回滚配置发布未完全成功 %s/%s: %s", province, intent, detail)
            return False, detail
        logger.info("已将本地配置发布为生效配置: %s/%s", province, intent)
        return True, "已发布为生效配置"
    except Exception as e:
        logger.warning("本地配置推送生效失败 %s/%s: %s", province, intent, e)
        return False, str(e)


def _bump_version(v) -> str:
    """语义化版本号末位 +1：1.0.0 → 1.0.1；无法解析时返回 1.0.1。"""
    parts = str(v or "1.0.0").strip().split(".")
    try:
        nums = [int(p) for p in parts]
    except (ValueError, TypeError):
        return "1.0.1"
    while len(nums) < 3:
        nums.append(0)
    nums[-1] += 1
    return ".".join(str(n) for n in nums)


@router.put("/skills/{province}/{intent}/config/{config_type}")
async def update_skill_config(province: str, intent: str, config_type: str, req: SkillUpdateRequest, request: Request):
    """
    更新 Skill 的配置文件（api_nodes 或 biz_config）。
    自动备份原文件。需同省份或本部权限。
    """
    _enforce_province_write(request, province)
    if config_type not in ("api_nodes", "biz_config"):
        raise HTTPException(status_code=400, detail="config_type 只支持 api_nodes 或 biz_config")

    skill_dir = SKILLS_RUNTIME / province / intent
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"Skill 不存在：{province}/{intent}")

    # 整 Skill 目录备份（格式 {intent}.bak.{ts}/，与「回滚」列表一致）。
    # 一次保存会连续调用 api_nodes + biz_config 两次 PUT，60 秒内只备份一次。
    bak_prefix = f"{intent}.bak."
    latest_bak_ts = 0
    for d in skill_dir.parent.iterdir():
        if d.is_dir() and d.name.startswith(bak_prefix):
            try:
                latest_bak_ts = max(latest_bak_ts, int(d.name[len(bak_prefix):]))
            except ValueError:
                continue
    now_ts = int(datetime.now().timestamp())
    did_backup = False
    if now_ts - latest_bak_ts > 60:
        _sync_live_config_to_disk(province, intent)
        backup_dir = skill_dir.parent / f"{intent}.bak.{now_ts}"
        shutil.copytree(str(skill_dir), str(backup_dir))
        did_backup = True
        logger.info("已备份 Skill 到: %s", backup_dir)

    # 写入走统一发布器（本地文件 + ES 版本化 + Redis 缓存 + 本实例内存热更新）
    try:
        from services.skill_publisher import publish_config  # 延迟 import，独立启动模式可能不可用
        pub = publish_config(
            province, intent, config_type, req.content,
            operator="auto_config_agent", comment="AutoConfig 配置编辑",
        )
        if not pub.success:
            raise HTTPException(status_code=500, detail=f"配置发布失败：{pub.message}")
    except ImportError:
        # 独立启动模式（无 znhs 根环境）：降级为直接写本地文件（旧行为）
        logger.warning("skill_publisher 不可用（独立启动模式），降级为直接写本地文件")
        (skill_dir / "config" / f"{config_type}.json").write_text(
            json.dumps(req.content, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # 更新 _meta.json：修改时间 + 状态置为 draft（编辑中）+ 版本号自动递增
    meta_path = skill_dir / "_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            # 已下线的不自动变为 draft，其余均进入"编辑中"
            if meta.get("status") != "offline":
                meta["status"] = "draft"
            # 版本号自动递增：与备份去重窗口一致，一次保存（两次 PUT）只递增一次
            if did_backup:
                meta["version"] = _bump_version(meta.get("version"))
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    logger.info("Skill 配置已更新: %s/%s/%s", province, intent, config_type)
    return {
        "success": True,
        "message": f"已更新 {config_type}",
        "province": province,
        "intent": intent,
        "config_type": config_type,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.delete("/skills/{province}/{intent}")
async def delete_skill(province: str, intent: str, request: Request, backup: bool = True):
    """
    删除 Skill（默认保留备份，可通过 ?backup=false 彻底删除）。需同省份或本部权限。

    生产模式下同时删除 ES 中的全部配置文档（api_nodes/biz_config/skill_meta 所有版本）
    与 Redis 缓存，并从内存注册表移除 + 广播——否则下次 reload/重启时技能包会从 ES 复活。
    仅存于 ES 的技能包（容器内无本地目录）同样支持删除。
    """
    _enforce_province_write(request, province)
    skill_dir = SKILLS_RUNTIME / province / intent
    _pkg = None
    try:
        from utils.skill_runtime import skill_registry as _reg
        _pkg = _reg.get(province, intent)
    except ImportError:
        pass
    if not skill_dir.exists() and _pkg is None:
        raise HTTPException(status_code=404, detail=f"Skill 不存在：{province}/{intent}")

    # 安全机制：已发布的 Skill 不允许直接删除，须先下线。
    # 状态判定与列表页一致：ES skill_meta 优先，本地 _meta.json 兜底。
    status_check = (
        (((_pkg.meta if _pkg else None) or {}).get("skill_meta") or {}).get("status")
    )
    meta_path = skill_dir / "_meta.json"
    if not status_check and meta_path.exists():
        try:
            status_check = json.loads(meta_path.read_text(encoding="utf-8")).get("status")
        except Exception:
            status_check = None
    if status_check == "published":
        raise HTTPException(
            status_code=409,
            detail=(
                "已发布的 Skill 不可直接删除。"
                "请先点击【下线】将状态变更为「已下线」后再执行删除操作。"
            ),
        )

    backup_name: Optional[str] = None
    if backup and skill_dir.exists():
        _sync_live_config_to_disk(province, intent)
        backup_dir = skill_dir.parent / f"{intent}.bak.{int(datetime.now().timestamp())}"
        shutil.copytree(str(skill_dir), str(backup_dir))
        backup_name = str(backup_dir.relative_to(SKILLS_RUNTIME))
        logger.info("Skill 已备份到: %s", backup_dir)

    if skill_dir.exists():
        shutil.rmtree(str(skill_dir))

    # 从 manifest 中移除
    manifest_path = SKILLS_RUNTIME / province / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            intents = manifest.get("intents", {})
            if isinstance(intents, dict):
                intents.pop(intent, None)
                manifest["intents"] = intents
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # 生产模式：删除 ES 真源配置 + Redis 缓存，移除内存包并广播其他实例
    es_message = ""
    try:
        from utils.skill_runtime import IS_DEV, skill_registry as _reg2
        if not IS_DEV:
            from services.es_config_store import es_config_store
            ok, n_docs, msg = es_config_store.delete_skill_configs(province, intent)
            es_message = msg
            if not ok:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"本地目录已删除，但 ES 配置删除失败：{msg}。"
                        f"生产读取以 ES 为准，该技能包可能在 reload 后重新出现，"
                        f"请检查 ES 可用性后重试删除。"
                    ),
                )
            try:
                from services.redis_config_bus import redis_config_bus
                if redis_config_bus.enabled:
                    for ct in ("api_nodes", "biz_config", "skill_meta"):
                        redis_config_bus.delete_config(province, intent, ct)
                    redis_config_bus.publish_change(province, intent)
            except Exception as e:
                logger.warning("删除后清理 Redis/广播失败（其他实例可能延迟感知）: %s", e)
        _reg2.remove(province, intent)
    except HTTPException:
        raise
    except ImportError:
        logger.warning("skill_registry 不可用（独立启动模式），仅删除本地文件")

    logger.info("Skill 已删除: %s/%s（%s）", province, intent, es_message or "仅本地")
    return {
        "success": True,
        "message": f"Skill 已删除：{province}/{intent}" + (f"（{es_message}）" if es_message else ""),
        "backup": backup_name,
        "deleted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/skills/{province}/{intent}/versions")
async def list_skill_versions(province: str, intent: str):
    """
    列出该 Skill 的历史备份（可用于回滚）。
    备份格式：{intent}.bak.{timestamp}/
    """
    province_dir = SKILLS_RUNTIME / province
    if not province_dir.exists():
        raise HTTPException(status_code=404, detail=f"省份不存在：{province}")

    versions: List[Dict[str, Any]] = []
    prefix = f"{intent}.bak."

    for d in sorted(province_dir.iterdir(), reverse=True):
        if not d.is_dir() or not d.name.startswith(prefix):
            continue
        ts_str = d.name[len(prefix):]
        try:
            ts = int(ts_str)
            created_at = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            created_at = ts_str

        # 读取备份 meta
        meta: Dict[str, Any] = {}
        meta_path = d / "_meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        versions.append({
            "backup_name": d.name,
            "backup_dir": str(d.relative_to(SKILLS_RUNTIME)),
            "created_at": created_at,
            "version": meta.get("version", "—"),
            "author": meta.get("author", "—"),
            "created_by": meta.get("created_by", "—"),
        })

    return {
        "province": province,
        "intent": intent,
        "versions": versions,
        "total": len(versions),
    }


@router.post("/skills/{province}/{intent}/backup")
async def backup_skill(province: str, intent: str, request: Request):
    """手动备份当前 Skill 配置（格式 {intent}.bak.{ts}/，与历史版本列表一致）。需同省份或本部权限。

    流程：
      1. 把最新生效配置（编辑页保存在 ES/Redis/内存中）落盘到本地文件；
      2. _meta.json 版本号自动 +1，并记录备份时间；
      3. 整目录 copytree 生成备份快照（快照携带新版本号）。
    """
    _enforce_province_write(request, province)
    skill_dir = SKILLS_RUNTIME / province / intent
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"Skill 不存在：{province}/{intent}")

    now_ts = int(datetime.now().timestamp())
    backup_dir = skill_dir.parent / f"{intent}.bak.{now_ts}"
    if backup_dir.exists():
        raise HTTPException(status_code=409, detail="操作过于频繁，请稍后再试")

    # 1. 关键：先把最新生效配置同步到磁盘，否则备份到的是旧的初始配置
    _sync_live_config_to_disk(province, intent)

    # 2. 版本号 +1（备份快照与当前 Skill 保持同一新版本号）
    old_version, new_version = "—", "—"
    meta: Dict[str, Any] = {}
    meta_path = skill_dir / "_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            old_version = str(meta.get("version", "—"))
            new_version = _bump_version(meta.get("version"))
            meta["version"] = new_version
            meta["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            meta["backed_up_at"] = datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S")
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("备份时更新 _meta.json 失败: %s", e)

    # 3. 整目录快照
    shutil.copytree(str(skill_dir), str(backup_dir))
    logger.info("Skill 已手动备份: %s（v%s → v%s）", backup_dir, old_version, new_version)

    return {
        "success": True,
        "message": f"已备份最新配置，版本号 v{old_version} → v{new_version}",
        "backup_name": backup_dir.name,
        "version": new_version,
        "old_version": old_version,
        "created_at": datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _resolve_backup_dir(province: str, intent: str, backup_name: str) -> Path:
    """校验并返回备份目录（防路径穿越：名称必须形如 {intent}.bak.{ts}）。"""
    if not re.fullmatch(rf"{re.escape(intent)}\.bak\.\d+", backup_name):
        raise HTTPException(status_code=400, detail=f"非法备份名称：{backup_name}")
    backup_dir = SKILLS_RUNTIME / province / backup_name
    if not backup_dir.exists() or not backup_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"备份不存在：{backup_name}")
    return backup_dir


@router.get("/skills/{province}/{intent}/versions/{backup_name}")
async def get_skill_version_detail(province: str, intent: str, backup_name: str):
    """查询某个备份的详情：meta + api_nodes + biz_config。"""
    backup_dir = _resolve_backup_dir(province, intent, backup_name)

    def _read_json(p: Path):
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    ts_str = backup_name.rsplit(".", 1)[-1]
    try:
        created_at = datetime.fromtimestamp(int(ts_str)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        created_at = ts_str

    return {
        "backup_name": backup_name,
        "created_at": created_at,
        "meta":       _read_json(backup_dir / "_meta.json"),
        "api_nodes":  _read_json(backup_dir / "config" / "api_nodes.json"),
        "biz_config": _read_json(backup_dir / "config" / "biz_config.json"),
    }


@router.delete("/skills/{province}/{intent}/versions/{backup_name}")
async def delete_skill_version(province: str, intent: str, backup_name: str, request: Request):
    """删除某个历史备份。需同省份或本部权限。"""
    _enforce_province_write(request, province)
    backup_dir = _resolve_backup_dir(province, intent, backup_name)
    shutil.rmtree(str(backup_dir))
    logger.info("Skill 备份已删除: %s/%s", province, backup_name)
    return {
        "success": True,
        "message": f"已删除备份：{backup_name}",
        "backup_name": backup_name,
    }


@router.patch("/skills/{province}/{intent}/status")
async def update_skill_status(province: str, intent: str, req: SkillStatusRequest, request: Request):
    """
    手动切换 Skill 状态：published / draft / offline。
    下线（offline）后方可删除。需同省份或本部权限。

    生产模式下状态是「结构化信息」的一部分，真源在 ES skill_meta（列表页优先读它）：
    本函数会把状态写入 ES skill_meta + Redis 缓存并广播，本地 _meta.json 仅作快照。
    仅存于 ES 的技能包（容器内无本地目录）同样支持。
    """
    _enforce_province_write(request, province)
    valid = {"published", "draft", "offline"}
    if req.status not in valid:
        raise HTTPException(status_code=400, detail=f"无效状态，可选值：{sorted(valid)}")

    # 定位技能包：本地目录 或 运行时 registry（仅存于 ES/Redis 的技能包无本地目录）
    skill_dir = SKILLS_RUNTIME / province / intent
    _pkg = None
    try:
        from utils.skill_runtime import skill_registry as _reg
        _pkg = _reg.get(province, intent)
    except ImportError:
        pass
    if not skill_dir.exists() and _pkg is None:
        raise HTTPException(status_code=404, detail=f"Skill 不存在：{province}/{intent}")

    # 旧状态：本地 _meta.json → ES skill_meta → unknown
    meta_path = skill_dir / "_meta.json"
    meta: Dict[str, Any] = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    old_status = meta.get("status") or (
        ((_pkg.meta if _pkg else None) or {}).get("skill_meta") or {}
    ).get("status") or "unknown"

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    overrides: Dict[str, Any] = {"status": req.status}
    if req.status == "published":
        overrides["published_at"] = now
    elif req.status == "offline":
        overrides["offline_at"] = now

    # 本地 _meta.json 快照（目录存在才写，best-effort）
    if skill_dir.exists():
        try:
            meta.update(overrides)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("写本地 _meta.json 失败（不影响状态变更）: %s", e)

    # 生产模式：状态写入 ES skill_meta（列表页真源）+ Redis 缓存 + 广播其他实例
    try:
        from utils.skill_runtime import IS_DEV
        if not IS_DEV:
            from services.skill_meta_service import upsert_skill_meta
            if not upsert_skill_meta(province, intent, operator="status_change",
                                     overrides=overrides):
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"状态写入 ES skill_meta 失败（生产列表以 ES 为准，"
                        f"页面状态将仍显示「{old_status}」）。请检查 ES 可用性后重试。"
                    ),
                )
            # 本实例内存同步（upsert 已写 ES + Redis，其他实例经广播 reload 感知）
            if _pkg is not None:
                sm = dict(((_pkg.meta or {}).get("skill_meta")) or {})
                sm.update(overrides)
                _pkg.meta["skill_meta"] = sm
            try:
                from services.redis_config_bus import redis_config_bus
                if redis_config_bus.enabled:
                    redis_config_bus.publish_change(province, intent)
            except Exception as e:
                logger.warning("状态变更广播失败（其他实例可能延迟感知）: %s", e)
    except HTTPException:
        raise
    except ImportError:
        logger.warning("skill_registry/skill_meta_service 不可用（独立启动模式），状态仅写本地文件")

    logger.info("Skill 状态变更: %s/%s  %s → %s", province, intent, old_status, req.status)

    return {
        "success": True,
        "province": province,
        "intent": intent,
        "old_status": old_status,
        "new_status": req.status,
        "updated_at": now,
    }


@router.post("/skills/{province}/{intent}/rollback")
async def rollback_skill(province: str, intent: str, req: SkillRollbackRequest, request: Request):
    """
    将 Skill 回滚到指定备份版本。
    当前版本会先备份，再用指定备份内容覆盖。需同省份或本部权限。
    """
    _enforce_province_write(request, province)
    province_dir = SKILLS_RUNTIME / province
    backup_dir = province_dir / req.backup_name

    if not backup_dir.exists():
        raise HTTPException(status_code=404, detail=f"备份不存在：{req.backup_name}")

    skill_dir = SKILLS_RUNTIME / province / intent

    # 备份当前版本（以防误操作）：先把最新生效配置落盘再快照
    current_backup: Optional[str] = None
    if skill_dir.exists():
        _sync_live_config_to_disk(province, intent)
        cb = skill_dir.parent / f"{intent}.bak.{int(datetime.now().timestamp())}"
        shutil.copytree(str(skill_dir), str(cb))
        current_backup = cb.name
        shutil.rmtree(str(skill_dir))

    # 从备份恢复
    shutil.copytree(str(backup_dir), str(skill_dir))

    # 更新 _meta 中的 rollback 记录
    meta_path = skill_dir / "_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["rollback_from"] = req.backup_name
            meta["rollback_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # 关键：把恢复后的本地配置推回 ES/Redis/内存，回滚才真正生效。
    # 发布失败必须如实报错（与 PUT config、/import/publish 的失败语义一致），
    # 否则生产实例（ES/Redis 优先读取）仍在使用回滚前配置，前端却显示回滚成功。
    push_ok, push_msg = _push_local_config_to_registry(province, intent, operator="rollback")
    if not push_ok:
        raise HTTPException(
            status_code=500,
            detail=(
                f"本地文件已回滚到 {req.backup_name}，但配置发布到 ES/Redis 失败：{push_msg}。"
                f"生产实例可能仍在使用回滚前配置，请检查 ES/Redis 后重试回滚。"
            ),
        )

    logger.info("Skill 已回滚: %s/%s ← %s", province, intent, req.backup_name)
    return {
        "success": True,
        "message": f"已回滚到：{req.backup_name}",
        "province": province,
        "intent": intent,
        "restored_from": req.backup_name,
        "current_backup": current_backup,
        "rollback_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── 启动（兼容独立运行；推荐在 znhs 根目录 python main.py 统一启动）──
if __name__ == "__main__":
    import uvicorn

    logger.info("⚠️  建议改用 znhs 根目录: python main.py（AutoConfig 已合并到主服务）")
    logger.info("启动自动配置智能体（独立模式），端口: %d", PORT)
    logger.info("skills-runtime 路径: %s", SKILLS_RUNTIME)
    logger.info("话术智能体 API: %s", PROD_API_BASE)

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
        log_level="info",
    )
