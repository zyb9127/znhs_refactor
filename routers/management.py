"""
运营端管理路由

包含技能包、话术模板、接口节点的 CRUD 操作：
  GET/POST/PUT/DELETE /api/skills/*
  GET/POST/PUT/PATCH/DELETE /api/templates/*
  GET/PUT/DELETE/PATCH /api/interfaces/*
  GET /api/auth/me
  GET /api/user/me
  GET /health
  GET /env
"""
from __future__ import annotations

import csv
import datetime
import io
import os
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from loguru import logger
from pydantic import BaseModel, Field

from utils.auth_utils import check_province_write, get_operator, get_user_province
from utils.placeholder import dig_subfield
from utils.skill_runtime import skill_registry
from utils.var_infer import infer_linked_vars, infer_placeholder_vars, _KEY_ALIAS

router = APIRouter(tags=["运营管理"])

# 保存失败（外部存储不可用）时的统一错误文案
# 收尾修复 F2 后失败即状态完全不变（不再更新本实例内存），可放心重试
_SAVE_FAIL_MSG = "配置保存失败：外部存储不可用（本次更改未生效，可修复后重试），请联系管理员检查 ES/Redis"

def _normalize_direct_extra_info(sample: Any) -> Dict[str, Any]:
    """把直传节点的 mock_response / 粘贴样例归一到 **extra_info 本体**。

    生产/网关常见三种粘贴形态，配置期都要能识别（运行时由
    routers.realtime._unwrap_params_body 已解包，这里是配置期的对应处理）：
      1. 裸 extra_info：``{"uniProdGrade":"58", "recommended_packages":[...]}``
      2. 整请求体：     ``{"phone":..., "extra_info":{...}, "batch_contexts":[...]}``
      3. 网关包裹：     ``{"params": {...同 2...}}``
    归一后返回 extra_info 字典（取不到时返回原 dict / 空 dict），
    供调色板字段提取、样例上下文构建、测试体生成统一使用。
    """
    if not isinstance(sample, dict):
        return {}
    cur = sample
    # ① 解最外层 params（与 realtime._unwrap_params_body 判定一致：
    #    仅当 params 为非空 dict 且顶层没有直接出现业务字段时才解包，避免误伤把
    #    params 当业务字段名的情况）。
    inner = cur.get("params")
    if isinstance(inner, dict) and inner and not (
        {"phone", "intent", "province", "extra_info"} & set(cur.keys())
    ):
        cur = inner
    # ② 整请求体：取 extra_info 本体（非空 dict 才下钻，避免把空壳顶掉真实字段）。
    ei = cur.get("extra_info")
    if isinstance(ei, dict) and ei:
        return ei
    return cur


def _subfield_in_list_sample(sample: Dict[str, Any], path: str) -> bool:
    """列表域下的产品字段路径（``recommended_packages.<字段>``）是否存在于数组元素里。

    逐条产品各取自己那份的字段无法收敛成单个透传值，运行时按「产品字段白名单」处理；
    这里只负责判定该字段在样例产品里真实存在，避免保存时被当作脏项清掉。
    """
    root, _, rest = str(path or "").partition(".")
    if not rest or "." in rest:
        return False
    arr = sample.get(root)
    if not isinstance(arr, list):
        return False
    return any(isinstance(it, dict) and rest in it for it in arr)


def _clean_direct_node_for_save(node: Dict[str, Any]) -> List[str]:
    """保存直传节点前就地归一，保证 ES 里存的是干净数据（生产写路径自愈）：
    - mock_response 若被粘贴成「整请求体」或「{"params":{...}} 网关包裹」，归一为 extra_info 本体；
    - passthrough_fields 去掉指向包裹层/样例中不存在的脏项（如把整包 extra_info 误选为透传字段），
      仅保留归一样例里真实存在的字段或标准域名（标准域值运行时才有，允许保留）。
    就地修改 node，返回改动说明（供日志与保存提示）。非直传节点不处理。
    """
    notes: List[str] = []
    if not isinstance(node, dict) or node.get("source_type") != "direct":
        return notes
    mock = node.get("mock_response")
    sample: Dict[str, Any] = {}
    if isinstance(mock, dict) and mock:
        norm = _normalize_direct_extra_info(mock)
        if isinstance(norm, dict) and norm != mock:
            node["mock_response"] = norm
            notes.append("mock_response 已归一为 extra_info 本体（原为整请求体 / params 包裹写法）")
        sample = norm if isinstance(norm, dict) else {}
    pf = node.get("passthrough_fields")
    if isinstance(pf, list) and pf and sample:
        cleaned = [
            k for k in pf
            if isinstance(k, str) and not k.startswith("_")
            and (
                # 直传透传模式 mock 即入参形态：仅保留样例里真实存在的字段。
                # 标准域名若已被改名 / 删除而不在样例里（如旧 recommended_packages 改成
                # recommended_packages11），属残留脏项，不再因「是标准域名」而保留。
                k in sample
                # 子路径写法（portrait_style.communication_style）：按路径在样例中解析；
                # 列表域下的产品字段（recommended_packages.<字段>）按数组首元素校验
                or ("." in k and bool(dig_subfield(sample, k)[0]))
                or ("." in k and _subfield_in_list_sample(sample, k))
            )
        ]
        if cleaned != pf:
            dropped = [k for k in pf if k not in cleaned]
            node["passthrough_fields"] = cleaned
            notes.append(f"passthrough_fields 已清理无效项 {dropped}（不在样例内且非标准域）")
    return notes


# 7 大标准数据域（与 steps/data_step.py 的 _NODE_TO_CTX_FIELD 保持一致）
# 用于「标准域映射防丢失」守护：见 update_interface
STD_DOMAIN_KEYS = frozenset({
    "current_package", "usage", "tags", "user_info",
    "recommended_packages", "user_profile", "domain_ext",
})


def _lint_template_safe(template: Dict[str, Any], province: str, intent: str) -> Optional[Dict[str, Any]]:
    """对单条模板执行 lint（延迟 import，任何异常吞掉，不影响主流程）。"""
    try:
        from management.config_agent.linter import lint_template
        return lint_template(template, province, intent)
    except Exception as e:
        logger.warning(f"模板 lint 失败（已忽略，不影响保存）: {e}")
        return None


# ── 数据模型 ──────────────────────────────────────────────────

class SkillConfigRequest(BaseModel):
    data: Dict[str, Any]


class TemplateCreateRequest(BaseModel):
    province: str = Field(..., description="省份代码")
    intent: str = Field(..., description="意图（与技能包目录名一致）")
    template_name: str = Field(..., description="模板名称（必填）")
    template_content: str = Field(..., description="模板内容（必填）")
    product_id: str = Field(default="", description="产品 ID，空=兜底模板")
    scene: str = Field(default="", description="应用场景")
    stage: str = Field(default="", description="应用环节")
    linked_vars: List[str] = Field(default_factory=list, description="关联变量键列表")
    script_requirement: str = Field(default="", description="话术要求")
    prompt_template: str = Field(default="", description="LLM Prompt 模板（legacy）")
    linked_apis: List[str] = Field(default_factory=list, description="关联接口名称列表")
    status: str = Field(default="online", description="online / offline")
    created_by: str = Field(default="admin", description="创建人")
    # 优化三：Skill 管理多产品编辑器已移除「接口数据域变量」手选区，
    # 传 True 时后端把「有传参的接口数据域变量」默认全选并入 linked_vars。
    # 全局 TemplateConfig（使用 cur_brief/usage_line 别名）不传此标记，行为不变。
    auto_domain_vars: bool = Field(default=False, description="是否自动全选有传参的接口数据域变量")


class TemplateUpdateRequest(BaseModel):
    template_name: Optional[str] = None
    template_content: Optional[str] = None
    product_id: Optional[str] = None
    scene: Optional[str] = None
    stage: Optional[str] = None
    linked_vars: Optional[List[str]] = None
    script_requirement: Optional[str] = None
    prompt_template: Optional[str] = None
    linked_apis: Optional[List[str]] = None
    status: Optional[str] = None
    auto_domain_vars: Optional[bool] = None  # 优化三：见 TemplateCreateRequest 说明


class TemplateBulkRequest(BaseModel):
    """批量新建/更新话术模板：一次请求写入多条，后端只做一次 ES 写入 + 热重载 + 广播。

    用于 CSV/Excel 批量导入，替代前端「逐条 POST /api/templates」——后者每条都会触发
    一次全量 biz_config 版本化写入 + skill_meta 刷新，导入几百条时版本号疯狂自增、
    日志“一直循环”，且 biz_config 逐条累加造成 O(N²) 写放大。
    """
    province: str = Field(..., description="省份代码")
    intent: str = Field(..., description="意图（与技能包目录名一致）")
    templates: List[Dict[str, Any]] = Field(..., description="待写入的模板对象列表（含 template_id 则原地更新）")
    auto_domain_vars: bool = Field(default=False, description="是否自动全选有传参的接口数据域变量")
    delete_template_ids: List[str] = Field(
        default_factory=list,
        description="本次同时删除的模板 ID（多产品编辑去掉某些产品时用），与写入合并为单次 ES 写入",
    )


class TemplateBatchDeleteRequest(BaseModel):
    template_ids: List[str] = Field(..., description="待删除的模板 ID 列表")
    # 可选：调用方已知的技能包定位（Skill 管理会传）。传了就直接从该技能包删除，
    # 避免同一 template_id 在多个技能包间重复（历史复制）导致按 id 反查定位到错误技能包。
    province: Optional[str] = Field(default=None, description="技能包省份（可选，精确定位）")
    intent: Optional[str] = Field(default=None, description="技能包意图（可选，精确定位）")


# ── 技能包管理 ────────────────────────────────────────────────

@router.get("/api/skills")
async def list_skills():
    """查询已加载技能包列表"""
    return {"code": 200, "data": skill_registry.list_all()}


@router.post("/api/skills/reload")
async def reload_skills(province: Optional[str] = None, intent: Optional[str] = None):
    """热重载技能包（省份配置更新后调用）"""
    skill_registry.reload(province, intent)
    return {"code": 200, "message": "reload 完成", "data": skill_registry.list_all()}


@router.get("/api/skills/{province}/{intent}/biz_config")
async def get_biz_config(province: str, intent: str):
    """获取话术模板配置"""
    pkg = skill_registry.get(province, intent)
    if pkg is None:
        raise HTTPException(404, f"技能包不存在: {province}/{intent}")
    return {"code": 200, "data": pkg.config.get("biz_config", {})}


@router.put("/api/skills/{province}/{intent}/biz_config")
async def save_biz_config(province: str, intent: str, body: SkillConfigRequest, request: Request):
    """保存话术模板配置（写本地文件 + 热重载；需同省份或本部权限）"""
    check_province_write(request, province)
    data = dict(body.data or {})

    # ── 话术模板防丢失守护 ──────────────────────────────────────
    # biz_config 是整字典替换。部分调用方（如"模板匹配设置"保存）只想更新
    # template_match/_domain_fallbacks 等片段，若提交的 biz_config **完全不含**
    # script_templates_v2 键，一次保存就会把已有话术模板静默冲掉。
    # 规则：提交里缺失该键时，自动保留旧模板并告警；确需清空请显式传 []（空列表）。
    preserved_tpls = False
    if "script_templates_v2" not in data:
        pkg = skill_registry.get(province, intent)
        old_biz = pkg.config.get("biz_config", {}) if (pkg and isinstance(pkg.config, dict)) else {}
        old_tpls = old_biz.get("script_templates_v2") if isinstance(old_biz, dict) else None
        if old_tpls:
            data["script_templates_v2"] = old_tpls
            preserved_tpls = True
            logger.warning(
                f"[话术保存守护] {province}/{intent} 提交的 biz_config 缺失 script_templates_v2，"
                f"已自动保留原有 {len(old_tpls)} 条话术模板（如需清空请显式传空列表）"
            )

    ok = skill_registry.save_biz_config(
        province, intent, data, operator=get_operator(request)
    )
    if not ok:
        raise HTTPException(500, _SAVE_FAIL_MSG)
    msg = "保存成功"
    if preserved_tpls:
        msg += "（已自动保留原有话术模板，如需清空请显式传空列表）"
    return {"code": 200, "message": msg}


@router.get("/api/skills/{province}/{intent}/api_nodes")
async def get_api_nodes(province: str, intent: str):
    """获取接口映射配置"""
    pkg = skill_registry.get(province, intent)
    if pkg is None:
        raise HTTPException(404, f"技能包不存在: {province}/{intent}")
    return {"code": 200, "data": pkg.config.get("api_nodes", {})}


def _computed_var_availability(province: str, intent: str) -> Dict[str, bool]:
    """基于技能 mock 样例判断「计算生成变量」在该技能下是否会有值。

    计算变量（pkg_brief/diff_str/pkg_fee/pkg_flow/pkg_voice/table）由 PackageDiff 从推荐条
    与当前套餐实时算出：推荐条缺月费/流量/语音字段时对应派生变量为空；当前套餐缺失时差异不可算。
    用样例预判，供前端隐藏「注定取不到值」的调色板标签（避免拖入后生成 xx/空值）。
    无样例时一律返回可用（保守，不误隐藏运行时真实有值的变量）。
    """
    avail = {k: True for k in ("pkg_brief", "diff_str", "pkg_fee", "pkg_flow", "pkg_voice", "table")}
    try:
        sample = _build_sample_ctx_from_skill(province, intent)
        if not sample:
            return avail
        from plugins.package_diff import PackageDiff
        cur = sample.get("current_package") or {}
        pkg = sample.get("recommended_package") or {}
        diff = PackageDiff(cur, pkg)
        avail["pkg_brief"] = bool(pkg)
        avail["pkg_fee"] = diff.tgt_fee is not None
        avail["pkg_flow"] = diff.tgt_data is not None
        avail["pkg_voice"] = diff.tgt_voice is not None
        avail["diff_str"] = bool(diff.summary_str().strip())
        # 差异表格：只要推荐条有任一可比维度或差异非空即有意义
        avail["table"] = avail["diff_str"] or avail["pkg_fee"] or avail["pkg_flow"] or avail["pkg_voice"]
    except Exception as e:  # noqa: BLE001 - 预判失败不应阻断变量列表
        logger.debug(f"[context_vars] 计算变量可用性预判失败，默认全部可用: {e}")
    return avail


# 标准域变量 key → 该域在「示例上下文」(_build_sample_ctx_from_skill) 中对应的原始字典 key。
# 用于把域的下一级子字段推导成可精确引用的占位符（{域[子键]}），供调色板展开选择。
_SUBFIELD_ROOT_TO_SAMPLE: Dict[str, str] = {
    "current_package": "current_package",
    "usage": "usage",
    "tags": "tags",
    "user_info": "user_info",
    "user_profile": "user_profile",
    "domain_ext": "domain_ext",
    "pkg_brief": "recommended_package",
    "extra_info": "extra_info",
}


def _flatten_domain_subfields(
    root_key: str, obj: Any, prefix: Optional[List[str]] = None,
    out: Optional[List[Dict[str, Any]]] = None, depth: int = 0,
    include_empty: bool = False,
) -> List[Dict[str, Any]]:
    """把某个标准域字典递归拍平成「可精确引用的子字段占位符」列表。

    返回每项：{"token": "usage[data_usage][近6月平均流量(GB)]", "label": "近6月平均流量(GB)",
              "path": "data_usage.近6月平均流量(GB)", "sample": 35}
    - 标量叶子 → 生成一条占位符（token 用方括号包裹每级子键，与 build_prompt 解析一致）
    - 跳过 _ 开头键、列表（下标不稳定），最多下钻 3 层
    - include_empty=False（默认，映射模式标准域）：跳过空值叶子，避免展示「样例下注定取不到值」的子字段；
      include_empty=True（透传/extra_info 骨架样例）：空值叶子仍按「键结构」产出占位符——
      因为透传样例常是全空骨架（仅声明字段存在，值运行时才有），此时结构才是关键。
    """
    out = [] if out is None else out
    prefix = prefix or []
    if not isinstance(obj, dict) or depth > 3:
        return out
    for k, v in obj.items():
        if not isinstance(k, str) or k.startswith("_"):
            continue
        keys = prefix + [k]
        if isinstance(v, dict):
            _flatten_domain_subfields(root_key, v, keys, out, depth + 1, include_empty)
        elif isinstance(v, list):
            continue
        else:
            if v in (None, "") and not include_empty:
                continue
            token = root_key + "".join(f"[{kk}]" for kk in keys)
            out.append({
                "token": token,
                "label": keys[-1],
                "path": ".".join(keys),
                "sample": v,
            })
    return out


def _compute_domain_subfields(province: str, intent: str) -> Dict[str, List[Dict[str, Any]]]:
    """按该技能的接口 mock 映射样例，推导各标准域可选子字段（供调色板展开）。

    复用 _build_sample_ctx_from_skill：其产出已过 response_extract/field_transform（含单位换算、
    字段重命名），故子字段键名 = 运行态映射后名，保证「调色板可选项 == 运行时可取值」精准一致。
    无技能 / 无 mock 时返回空 dict，前端仅展示整块域（向后兼容）。
    """
    sample = _build_sample_ctx_from_skill(province, intent)
    if not isinstance(sample, dict) or not sample:
        return {}
    result: Dict[str, List[Dict[str, Any]]] = {}
    for var_key, sample_key in _SUBFIELD_ROOT_TO_SAMPLE.items():
        dom = sample.get(sample_key)
        if isinstance(dom, dict) and dom:
            # extra_info（主服务补充信息/直传骨架）常为全空样例，按键结构展开；
            # 其余标准域（映射产出）保持跳过空值，避免展示注定取不到值的子字段。
            # 推荐产品样例同理：直传省份的产品样例常是全空骨架（只声明字段名，值运行时才有），
            # 此时若跳过空值，调色板里一个产品字段都看不到。
            _skeleton = (
                var_key == "pkg_brief"
                and not any(str(v).strip() for v in dom.values()
                            if not isinstance(v, (dict, list)))
            )
            subs = _flatten_domain_subfields(
                var_key, dom, include_empty=(var_key == "extra_info" or _skeleton))
            if subs:
                result[var_key] = subs
    return result


@router.get("/api/skills/{province}/{intent}/context_vars")
async def get_context_vars(province: str, intent: str):
    """从 api_nodes.json 推导该意图下所有可用的 context 变量列表。

    推导规则：
    - response_extract 的每个 key → 对应一个 context 变量（raw 层，如 current_package）
    - field_transform 的每个顶级 key（忽略 _unit_conversions 等下划线开头的）→ 对应一个 context 变量
      （key 含 "." 时取第一段，如 "usage.data_usage" → "usage"）
    - 固定变量（user_info / user_profile / domain_ext / extra_info / extra_context）始终包含
    - 特殊变量 pkg_brief / diff_str / table 也始终包含（话术生成层）

    返回：[{"key": "current_package", "label": "当前套餐信息", "source": "response_extract", "desc": "{context.current_package}"}, ...]
    """
    pkg = skill_registry.get(province, intent)
    if pkg is None:
        raise HTTPException(404, f"技能包不存在: {province}/{intent}")

    api_nodes = pkg.config.get("api_nodes", {})

    # 收集来自接口配置的变量
    api_derived: Dict[str, str] = {}  # key → source 说明
    api_producers: Dict[str, List[str]] = {}  # key → 产出该变量的接口名列表（供前端联动展示/过滤）
    # 直传透传字段（source_type=direct 且 direct_mode=passthrough）：入参字段直接作为 context
    passthrough_keys: List[str] = []
    passthrough_samples: Dict[str, Any] = {}  # key → 样例值（取自 mock_response，供预览）
    passthrough_producers: Dict[str, List[str]] = {}  # key → 产出该透传字段的直传节点名列表
    has_passthrough_node = False              # 是否存在直传透传节点（决定产品字段呈现口径）
    product_allow: List[str] = []             # 运营精确勾选的产品字段（recommended_packages.<字段>）
    # 字典型透传大变量（portrait_style / current_package…）被精确勾选的子字段：
    # parent → [leaf,...]。收进父级大变量的 subfields，让调色板按「大变量→展开子字段」
    # 一一对应，而非把 leaf 平铺成一堆顶层 chip。
    pt_subfields: Dict[str, List[str]] = {}
    pt_subfield_samples: Dict[tuple, Any] = {}  # (parent, leaf) → 样例值

    _STD_DOMAINS = {
        "current_package", "usage", "tags", "user_info",
        "recommended_packages", "user_profile", "domain_ext",
    }

    def _add_producer(producers: Dict[str, List[str]], key: str, api_name: str) -> None:
        lst = producers.setdefault(key, [])
        if api_name not in lst:
            lst.append(api_name)

    for api_name, cfg in api_nodes.items():
        if api_name.startswith("_") or not isinstance(cfg, dict):
            continue
        # response_extract 的 key（非 raw_ 开头的才是真正写入 context 的字段）
        for key in cfg.get("response_extract", {}).keys():
            if not key.startswith("raw_"):
                api_derived[key] = "response_extract"
                _add_producer(api_producers, key, api_name)
        # field_transform 的顶级 key（取 "." 前第一段；忽略 _ 开头的）
        for key in cfg.get("field_transform", {}).keys():
            if key.startswith("_"):
                continue
            top_key = key.split(".")[0]
            if top_key not in api_derived:
                api_derived[top_key] = "field_transform"
            _add_producer(api_producers, top_key, api_name)
        # 直传透传模式：把入参字段名作为可用 context 变量（供话术模板直接引用 {字段名}）
        if cfg.get("source_type") == "direct" and cfg.get("direct_mode") == "passthrough":
            # 归一样例：兼容 mock 粘贴成「整请求体」或「网关 params 包裹」的写法，
            # 否则透传字段会误取到 params / extra_info / batch_contexts 顶层键。
            sample = _normalize_direct_extra_info(cfg.get("mock_response"))
            fields = cfg.get("passthrough_fields")
            if not fields:
                fields = list(sample.keys())
            has_passthrough_node = True
            for key in fields:
                if not isinstance(key, str) or key.startswith("_"):
                    continue
                # 子路径写法：列表域（recommended_packages.<字段>）是逐条产品各取自己那份的
                # 产品字段，收进 product_allow 由下方「推荐产品字段」分组按勾选暴露；
                # 字典域（portrait_style.communication_style）运行时按叶子名注入，
                # 调色板同样暴露叶子名占位符 {communication_style}。
                if "." in key:
                    _root = key.split(".", 1)[0]
                    if isinstance(sample.get(_root), list):
                        _leaf = key.rsplit(".", 1)[-1]
                        if _leaf and _leaf not in product_allow:
                            product_allow.append(_leaf)
                        # 确保父级大变量（recommended_packages）进调色板作直传占位符，
                        # 即便运营只勾了子字段没单独勾 recommended_packages。
                        if _root not in api_derived and _root not in passthrough_keys:
                            passthrough_keys.append(_root)
                        _add_producer(passthrough_producers, _root, api_name)
                        if _root in sample and _root not in passthrough_samples:
                            passthrough_samples[_root] = sample[_root]
                        continue
                    # 字典域子字段：不平铺成顶层 leaf chip，而是收进父级大变量的 subfields，
                    # 调色板按「大变量 {portrait_style} → 展开选子字段」一一对应（与
                    # recommended_packages 分组一致），避免十几个 leaf 平铺顶层。
                    _leaf, _val = dig_subfield(sample, key)
                    if not _leaf:
                        continue
                    _lvs = pt_subfields.setdefault(_root, [])
                    if _leaf not in _lvs:
                        _lvs.append(_leaf)
                    if _val not in (None, "", [], {}):
                        pt_subfield_samples[(_root, _leaf)] = _val
                    # 确保父级大变量本身进调色板（运营即便只勾子字段没勾父级也要有大变量）；
                    # 仅当父级在归一样例里真实存在时才补，避免改名后的残留父级键复活成幽灵占位符。
                    if _root in sample:
                        if _root not in api_derived and _root not in passthrough_keys:
                            passthrough_keys.append(_root)
                        _add_producer(passthrough_producers, _root, api_name)
                        if _root in sample and _root not in passthrough_samples:
                            passthrough_samples[_root] = sample[_root]
                    continue
                # 过滤历史脏透传字段：mock 被粘贴成「整请求体/params 包裹」时，
                # passthrough_fields 可能残留指向包裹层的键（如把整包 extra_info 选成透传字段）。
                # 直传透传模式下 mock 即入参形态，仅保留归一样例里真实存在的字段；
                # 标准域名（如 recommended_packages）若已被改名 / 删除而不在样例里，属残留脏项，
                # 不再因「是标准域名」而保留，避免与最新字段名（如 recommended_packages11）重复。
                if key not in sample:
                    continue
                # 注意：dict 型标准域（current_package / usage / tags / user_profile 等）
                # 也在此暴露为可展开子字段的占位符——否则纯直传节点没有 response_extract，
                # 这些域不会进 api_derived，调色板里就完全看不到（current_package 消失的根因）。
                if key not in api_derived and key not in passthrough_keys:
                    passthrough_keys.append(key)
                _add_producer(passthrough_producers, key, api_name)
                if key in sample and key not in passthrough_samples:
                    passthrough_samples[key] = sample[key]

    # 变量元信息：key → (label, desc)
    _META: Dict[str, tuple] = {
        "current_package": ("当前套餐信息",    "{context.current_package}"),
        "recommended_packages": ("推荐产品列表（原始）", "{context.recommended_packages}"),
        "usage":           ("历史用量",        "{context.usage}"),
        "tags":            ("用户标签",        "{context.tags}"),
        "user_info":       ("用户基础信息",    "{context.user_info}"),
        "user_profile":    ("用户画像",        "{context.user_profile}"),
        "domain_ext":      ("扩展信息",        "{context.domain_ext}"),
        "extra_info":      ("主服务补充信息",   "{context.extra_info}"),
        "extra_context":   ("模板匹配上下文",   "{context.extra_context}"),
        "pkg_brief":       ("推荐产品信息（单条格式化）", "{context.pkg_brief}"),
        "diff_str":        ("套餐差异",        "{context.diff_str}"),
        "pkg_fee":         ("推荐套餐月费(元)",   "由推荐条计算，单位已归一为元"),
        "pkg_flow":        ("推荐套餐流量(GB)",   "由推荐条计算，单位已归一为GB"),
        "pkg_voice":       ("推荐套餐语音(分钟)", "由推荐条计算，单位为分钟"),
        "table":           ("差异表格",        "（仅话术展示，不打入LLM）"),
    }

    # 各标准域可选子字段（{域[子键]}），按接口 mock 映射样例推导，供调色板展开精确匹配
    subfields_map = _compute_domain_subfields(province, intent)

    # 推荐产品字段是否来自「直传透传节点」：决定 recommended_packages 的呈现口径——
    #   透传节点产出（无接口映射产出 recommended_packages）→ 按「直传大变量」呈现：
    #     recommended_packages 作 source=passthrough 的直传 chip（展开=运营勾选的产品字段），
    #     且不再另出计算生成的 {pkg_brief}（二者都指向推荐产品，属重复）；
    #   接口/映射节点产出（混合配置）→ 保持映射模式：单独「推荐产品字段」分组自动摊开 + {pkg_brief}。
    try:
        from engine.prompt_builder import PKG_FIELD_EXCLUDED_KEYS as _PKG_SKIP
    except Exception:
        _PKG_SKIP = frozenset()
    _prod_from_passthrough = has_passthrough_node and "recommended_packages" not in api_derived
    # 产品字段子占位符（裸字段名 {recommend_actual_price}，多产品逐条取值）。
    # 透传口径：只暴露运营在「透传字段」页按 recommended_packages.<字段> 精确勾选过的那几个；
    # 映射口径：按产品样例自动摊开全部非 ID/排序字段。
    _prod_subs: List[Dict[str, Any]] = []
    _prod_seen: set = set()
    for _sf in (subfields_map.get("pkg_brief") or []):
        _path = str(_sf.get("path") or "")
        if not _path or "." in _path or _path in _prod_seen:
            continue
        if _path in _PKG_SKIP:
            # ID/排序类字段不作为话术槽位（念串号无意义且有风险）；与标准域同名的产品字段也排除。
            continue
        if _prod_from_passthrough and _path not in product_allow:
            continue
        _prod_seen.add(_path)
        _prod_subs.append({
            "token": _path, "label": _path, "path": _path, "sample": _sf.get("sample"),
        })

    result = []
    seen = set()

    # 先输出 api_nodes 推导出来的变量（带产出接口列表，供前端做「接口 ↔ 占位符」联动）
    for key, source in api_derived.items():
        if key in seen:
            continue
        seen.add(key)
        label, desc = _META.get(key, (key, f"{{context.{key}}}"))
        item = {
            "key": key, "label": label, "source": source, "desc": desc,
            "api_names": api_producers.get(key, []),
        }
        if subfields_map.get(key):
            item["subfields"] = subfields_map[key]
        result.append(item)

    # 直传透传字段（入参字段直接作为 context，供话术模板引用 {字段名}）
    for key in passthrough_keys:
        if key in seen:
            continue
        # 混合配置（接口/映射节点也产出 recommended_packages）时，交给下方「推荐产品字段」
        # 映射分组自动摊开；纯透传节点则在此按「直传大变量」呈现（下方 subfields 分支处理）。
        if key == "recommended_packages" and not _prod_from_passthrough:
            continue
        seen.add(key)
        label, desc = _META.get(key, (key, "直传入参字段"))
        item = {
            "key": key, "label": label, "source": "passthrough", "desc": desc,
            "sample": passthrough_samples.get(key),
            "api_names": passthrough_producers.get(key, []),
        }
        # 透传大变量按「大变量 → 展开子字段」一一对应。子字段占位符 token 用运行态可解析格式：
        #   标准域（current_package…）→ {域[子键]}，走 resource_context 子字段解析；
        #   非标准域字典（portrait_style）→ 裸叶子 {子键}，走 passthrough_context 叶子注入。
        _is_std = key in _STD_DOMAINS
        _selected = pt_subfields.get(key)
        _sample = passthrough_samples.get(key)
        _subs: List[Dict[str, Any]] = []
        if isinstance(_sample, list) and _sample:
            # 直传列表型大变量（产品/明细列表，不写死字段名）：子字段 = 数组元素字段并集，
            # 按透传参数里的实际字段自动适配；裸占位符 {字段名}，多条时每条话术各取自己那条的值。
            # 透传口径：只暴露运营在「透传字段」页按 <列表>.<字段> 勾选过的（product_allow），
            # 一个没勾就只给整块 {列表名}——与「不需要摊开显示字段」一致。ID/排序/标准域同名字段排除。
            _seen_lf: set = set()
            for _el in _sample:
                if not isinstance(_el, dict):
                    continue
                for _lf, _lv in _el.items():
                    if (not isinstance(_lf, str) or _lf.startswith("_")
                            or _lf in _seen_lf or isinstance(_lv, (dict, list))
                            or _lf in _PKG_SKIP or _lf not in product_allow):
                        continue
                    _seen_lf.add(_lf)
                    _subs.append({"token": _lf, "label": _lf, "path": _lf, "sample": _lv})
        elif _selected:
            # 运营在「透传字段」页精确勾了子字段 → 只暴露这几个
            for _lf in _selected:
                _subs.append({
                    "token": f"{key}[{_lf}]" if _is_std else _lf,
                    "label": _lf, "path": _lf,
                    "sample": pt_subfield_samples.get((key, _lf)),
                })
        elif isinstance(_sample, dict) and _sample:
            # 只勾了大变量没勾子字段 → 展开全部（一层）子字段
            if _is_std:
                # 标准域样例常为全空骨架（仅声明结构），按键结构展开（include_empty）
                _subs = _flatten_domain_subfields(key, _sample, include_empty=True)
            else:
                for _lf, _lv in _sample.items():
                    if not isinstance(_lf, str) or _lf.startswith("_"):
                        continue
                    if isinstance(_lv, (dict, list)):
                        continue
                    _subs.append({"token": _lf, "label": _lf, "path": _lf, "sample": _lv})
        if _subs:
            item["subfields"] = _subs
        result.append(item)

    # pkg_brief / diff_str / pkg_fee 等是话术生成层计算产出；附 available 标志，
    # 供前端隐藏「该技能样例下注定取不到值」的计算变量（避免拖入后生成 xx/空值）
    computed_avail = _computed_var_availability(province, intent)
    for key in ("pkg_brief", "diff_str", "pkg_fee", "pkg_flow", "pkg_voice", "table"):
        # 直传口径：产品信息只用透传的 {recommended_packages}（展开选字段）呈现，不再另出
        # 计算生成的 {pkg_brief}——二者都指向推荐产品，同列会让运营误以为重复/需映射。
        if key == "pkg_brief" and _prod_from_passthrough:
            seen.add(key)
            continue
        if key not in seen:
            seen.add(key)
            label, desc = _META[key]
            item = {
                "key": key, "label": label, "source": "script_step", "desc": desc,
                "available": computed_avail.get(key, True),
            }
            # pkg_brief 支持展开推荐条子字段（{pkg_brief[offerName]} 等）
            if subfields_map.get(key):
                item["subfields"] = subfields_map[key]
            result.append(item)

    # 推荐产品字段：
    #   透传口径（_prod_from_passthrough）→ recommended_packages 已在上方以 source=passthrough
    #     的「直传大变量」形式发射（展开=运营勾选的产品字段），此处不再另起「推荐产品」映射分组；
    #   映射/接口口径 → 产品字段只在数组元素内，既不是标准域也不是透传顶层字段，需单独暴露为
    #     「推荐产品字段」分组（自动摊开全部非 ID/排序字段），与运行时产品字段注入同名对齐。
    if not _prod_from_passthrough and _prod_subs and "recommended_packages" not in seen:
        seen.add("recommended_packages")
        result.append({
            "key": "recommended_packages",
            "label": "推荐产品字段",
            "source": "recommended_product",
            "desc": "推荐产品字段（展开选具体字段作占位符；多产品时每条话术各取自己那条产品的值）",
            "subfields": _prod_subs,
        })

    # 固定变量（主服务传入 / 通用）
    for key in ("user_info", "user_profile", "domain_ext", "extra_info", "extra_context"):
        if key not in seen:
            seen.add(key)
            label, desc = _META[key]
            item = {"key": key, "label": label, "source": "fixed", "desc": desc}
            if subfields_map.get(key):
                item["subfields"] = subfields_map[key]
            result.append(item)

    return {"code": 200, "data": result}


def _derive_api_domain_var_keys(province: str, intent: str) -> List[str]:
    """推导某意图下「有传参的接口数据域变量」key 列表（用于话术模板 linked_vars 默认全选）。

    规则与 get_context_vars 的 api_derived 一致：
    - response_extract 的非 raw_ 开头 key
    - field_transform 的非 _ 开头顶级 key（含 "." 时取第一段）
    仅统计接口配置真实产出的数据域（即「有传参的数据配置」），不含始终存在的固定/生成变量。
    """
    pkg = skill_registry.get(province, intent)
    if pkg is None:
        return []
    api_nodes = pkg.config.get("api_nodes", {})
    keys: List[str] = []
    seen = set()
    for api_name, cfg in api_nodes.items():
        if api_name.startswith("_") or not isinstance(cfg, dict):
            continue
        for key in cfg.get("response_extract", {}).keys():
            if key.startswith("raw_") or key in seen:
                continue
            seen.add(key)
            keys.append(key)
        for key in cfg.get("field_transform", {}).keys():
            if key.startswith("_"):
                continue
            top_key = key.split(".")[0]
            if top_key in seen:
                continue
            seen.add(top_key)
            keys.append(top_key)
    return keys


def _merge_auto_domain_vars(province: str, intent: str, linked_vars) -> List[str]:
    """把「有传参的接口数据域变量」并入 linked_vars（默认全选，前端已移除该手选区）。"""
    merged = list(linked_vars or [])
    for k in _derive_api_domain_var_keys(province, intent):
        if k not in merged:
            merged.append(k)
    return merged


def _fill_placeholder_vars(content: Any, linked_vars: Any) -> tuple:
    """保存即补齐：按当前模板内容重新计算占位符变量，同时清理已不再引用的变量。

    子字段占位符 ``{usage[consumption][近6月平均月消费]}`` 只写了根名 ``usage`` 的子键，
    历史推断（infer_linked_vars 的 ``\\{(\\w+)\\}`` 精确层）匹配不到，模板若又没手动勾选
    usage，配置页就看不出这条模板依赖该域，运营排查"话术没带上月均消费"时会找错方向。
    这里按占位符零猜测地补齐，保证存下来的模板自带完整依赖声明。

    Returns:
        (补齐后的 linked_vars, 新增的变量列表)
    """
    merged = list(linked_vars or [])
    current = set(infer_placeholder_vars(str(content or "")))
    # 占位符体系能产出的所有变量值（_KEY_ALIAS 的值集合）
    # 用于区分"占位符来源"与"API域变量来源"：后者不应被清理
    placeholder_values = set(_KEY_ALIAS.values())
    # 保留：非占位符来源的变量（API域变量等）+ 当前模板仍在引用的占位符变量
    kept = [v for v in merged if v not in placeholder_values or v in current]
    removed = [v for v in merged if v in placeholder_values and v not in current]
    if removed:
        logger.info(f"[_fill_placeholder_vars] 清理已不再引用的占位符变量: {removed}")
    # 补充新增的占位符变量
    added = [v for v in current if v not in kept]
    kept.extend(added)
    return kept, added


@router.put("/api/skills/{province}/{intent}/api_nodes")
async def save_api_nodes(province: str, intent: str, body: SkillConfigRequest, request: Request):
    """保存整份接口映射配置（写本地文件 + 热重载；需同省份或本部权限）"""
    check_province_write(request, province)
    pkg = skill_registry.get(province, intent)
    old_nodes = pkg.config.get("api_nodes", {}) if (pkg and isinstance(pkg.config, dict)) else {}
    data, preserved = _guard_api_nodes_package(old_nodes, body.data or {})
    if preserved:
        logger.warning(
            f"[接口保存守护] {province}/{intent} 整份保存缺失标准域/被引用中间槽位，"
            f"已自动保留：{preserved}（如需删除请将该 key 显式置为空串）"
        )
    # 保存即补齐：旧配置本就残缺时守护无从保留，这里按自证线索补回（同「修复」按钮逻辑）
    removed = {
        name: _explicit_removed_slots(cfg.get("response_extract"))
        for name, cfg in (body.data or {}).items()
        if isinstance(cfg, dict)
    }
    data, filled_notes, unfixed = _autofill_api_nodes(data, province, intent, removed)
    if filled_notes:
        logger.warning(f"[接口保存补齐] {province}/{intent} 配置已自动修正: {filled_notes}")
    if unfixed:
        logger.warning(f"[接口保存补齐] {province}/{intent} 仍需人工处理: {unfixed}")

    ok = skill_registry.save_api_nodes(
        province, intent, data, operator=get_operator(request)
    )
    if not ok:
        raise HTTPException(500, _SAVE_FAIL_MSG)
    msg = "保存成功"
    if preserved:
        msg += f"（已自动保留映射：{'；'.join(preserved)}）"
    if filled_notes:
        msg += f"（配置已自动修正：{'；'.join(filled_notes)}）"
    return {"code": 200, "message": msg, "autofilled": filled_notes, "unfixed": unfixed}


@router.post("/api/skills/{province}/{intent}/repair_config")
async def repair_skill_config(province: str, intent: str, request: Request):
    """基于 ES 当前配置自愈修复：重新校验 → 发现问题 → 自动生成修正配置 → 发布回 ES 生效。

    不依赖本地文件/历史版本：修复依据是坏配置里的自证线索——field_transform 声明的
    from 槽位（如 raw_tags）+ 节点自带 mock_response 里的同名字段（bean.tags），
    按名探测补回 response_extract；同时补回可识别的标准域映射、规范化畸形重命名。
    只增不删；无可修复项时不发布（幂等安全）。需同省份或本部权限。
    """
    check_province_write(request, province)
    pkg = skill_registry.get(province, intent)
    if pkg is None:
        raise HTTPException(404, f"技能包不存在: {province}/{intent}")
    from management.config_agent.repairer import repair_api_nodes

    api_nodes = pkg.config.get("api_nodes") or {}
    rep = repair_api_nodes(api_nodes, province, intent)
    errors_before = [i.get("message") for i in rep["lint_before"].get("errors", [])]
    errors_after = [i.get("message") for i in rep["lint_after"].get("errors", [])]
    data = {
        "fixes": rep["fixes"],
        "unfixed": rep["unfixed"],
        "errors_before": errors_before,
        "errors_after": errors_after,
        "published": False,
    }

    if not rep["fixes"]:
        msg = ("校验通过，配置健康，无需修复" if not errors_before and not rep["unfixed"]
               else "校验发现问题，但均无法自动修复（详见 unfixed），未发布")
        return {"code": 200, "message": msg, "data": data}

    operator = get_operator(request)
    # 修复产物本身不带 updated_by，版本记录里的操作人只能靠这里传：不传会记成 system
    ok = skill_registry.save_api_nodes(province, intent, rep["config"], operator=operator)
    if not ok:
        raise HTTPException(500, f"修复配置已生成但发布失败：{_SAVE_FAIL_MSG}")
    data["published"] = True
    logger.warning(
        f"[repair_config] {province}/{intent} by={operator} "
        f"fixes={rep['fixes']} unfixed={rep['unfixed']}"
    )
    msg = f"已自动修复 {len(rep['fixes'])} 项并发布生效"
    if rep["unfixed"]:
        msg += f"；另有 {len(rep['unfixed'])} 项需人工处置"
    return {"code": 200, "message": msg, "data": data}


class RepublishLocalRequest(BaseModel):
    """从本地标准配置重新发布到 ES 的请求体（config_types 缺省仅 api_nodes）。"""
    config_types: List[str] = Field(default_factory=lambda: ["api_nodes"])


@router.post("/api/skills/{province}/{intent}/republish_local")
async def republish_local_config(
    province: str, intent: str, request: Request,
    body: Optional[RepublishLocalRequest] = None,
):
    """从本地标准配置整包重新发布到 ES（生产事故恢复）。

    读取部署包里的 skills-runtime/{province}/{intent}/config/{type}.json（正确标准配置），
    整包 publish_config 覆盖写 ES —— 用于修复 ES 里被误改/漏映射的配置（如北京 raw_tags
    丢失导致 usage/tags 静默为空）。一键、幂等，无需人工写 ES。需同省份或本部权限。
    """
    check_province_write(request, province)
    from services.skill_publisher import republish_local, ALLOWED_CONFIG_TYPES

    req_types = (body.config_types if body and body.config_types else ["api_nodes"])
    config_types = tuple(t for t in req_types if t in ALLOWED_CONFIG_TYPES)
    if not config_types:
        raise HTTPException(400, f"config_types 非法，仅支持: {list(ALLOWED_CONFIG_TYPES)}")

    operator = get_operator(request)
    results = republish_local(province, intent, config_types=config_types, operator=operator)

    data = {
        ct: {
            "success": r.success,
            "message": r.message,
            "version": r.version,
            "es_written": r.es_written,
            "file_written": r.file_written,
            "warnings": r.warnings,
        }
        for ct, r in results.items()
    }
    all_ok = all(r.success for r in results.values()) if results else False
    ok_types = [ct for ct, r in results.items() if r.success]
    fail_types = [ct for ct, r in results.items() if not r.success]
    if all_ok:
        msg = f"已从本地标准配置重新发布到 ES：{', '.join(ok_types)}"
    elif ok_types:
        msg = f"部分成功：{', '.join(ok_types)}；失败：{', '.join(fail_types)}"
    else:
        msg = f"重新发布失败：{', '.join(fail_types) or '无可发布配置'}"
    logger.warning(
        f"[republish_local] {province}/{intent} by={operator} types={list(config_types)} "
        f"ok={ok_types} fail={fail_types}"
    )
    return {"code": 200 if all_ok else 500, "message": msg, "data": data}


# ── Skill 测试用例 + 完整入参生成 ────────────────────────────────

class TestCasesSaveRequest(BaseModel):
    cases: List[Dict[str, Any]] = Field(default_factory=list, description="测试用例列表")


@router.get("/api/skills/{province}/{intent}/test_cases")
async def list_skill_test_cases(province: str, intent: str):
    """读取该技能包已保存的测试用例（与 skill 配置关联，生产存 ES）。"""
    from services.test_case_store import get_test_cases
    return {"code": 200, "data": get_test_cases(province, intent)}


@router.put("/api/skills/{province}/{intent}/test_cases")
async def save_skill_test_cases(
    province: str, intent: str, body: TestCasesSaveRequest, request: Request
):
    """保存该技能包的测试用例列表（需同省份或本部权限）。"""
    check_province_write(request, province)
    from services.test_case_store import save_test_cases
    ok = save_test_cases(
        province, intent, body.cases,
        operator=get_operator(request, fallback="tester"),
    )
    if not ok:
        raise HTTPException(500, "测试用例保存失败：外部存储不可用，请稍后重试")
    return {"code": 200, "message": "已保存", "data": {"count": len(body.cases)}}


def _tc_set_deep(obj: Dict[str, Any], path: str, val: Any) -> None:
    parts = path.split(".")
    cur = obj
    for k in parts[:-1]:
        if not isinstance(cur.get(k), dict):
            cur[k] = {}
        cur = cur[k]
    cur.setdefault(parts[-1], val)


def _tc_deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _tc_deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@router.post("/api/skills/{province}/{intent}/gen_test_payload")
async def gen_test_payload(province: str, intent: str):
    """依据该技能包的接口类型，生成一份**完整的**推荐请求体（可编辑后直接执行）。

    - 直传（source_type=direct）：extra_info 取各直传节点 mock_response 合并；
      无 mock 时按 passthrough_fields 生成骨架。
    - 接口查询（source_type=api）：扫描 request_template 中 {{extra_data.xxx}} 占位符生成 extra_data 骨架。
    - batch_contexts：从话术模板去重取 (product_id, stage, scene) 组合（最多 3 条），无模板则给一条空上下文。
    返回 {payload, skill_type, notes}。
    """
    pkg = skill_registry.get(province, intent)
    if pkg is None:
        raise HTTPException(404, f"技能包不存在: {province}/{intent}")

    api_nodes = pkg.config.get("api_nodes", {}) if isinstance(pkg.config, dict) else {}
    biz = pkg.config.get("biz_config", {}) if isinstance(pkg.config, dict) else {}
    templates = biz.get("script_templates_v2", []) if isinstance(biz, dict) else []

    extra_info: Dict[str, Any] = {}
    extra_data: Dict[str, Any] = {}
    notes: List[str] = []
    types: set = set()

    for name, cfg in (api_nodes or {}).items():
        if not isinstance(cfg, dict) or str(name).startswith("_") or not cfg.get("enabled", True):
            continue
        st = cfg.get("source_type", "api")
        if st == "direct":
            types.add("direct")
            mock = _normalize_direct_extra_info(cfg.get("mock_response"))
            if isinstance(mock, dict) and mock:
                extra_info = _tc_deep_merge(extra_info, mock)
                notes.append(f"直传节点「{name}」：extra_info 取自配置样例 mock_response")
            else:
                for f in (cfg.get("passthrough_fields") or []):
                    if not isinstance(f, str) or f.startswith("_"):
                        continue
                    # 子路径写法生成嵌套骨架（portrait_style.communication_style）
                    if "." in f:
                        _tc_set_deep(extra_info, f, "")
                    else:
                        extra_info.setdefault(f, "")
                if cfg.get("passthrough_fields"):
                    notes.append(f"直传节点「{name}」：按 passthrough_fields 生成 extra_info 骨架，请补值")
        else:
            types.add("api")
            tpl = cfg.get("request_template")
            tpl_str = tpl if isinstance(tpl, str) else _json_dumps_safe(tpl or {})
            found = 0
            for m in re.finditer(r"\{\{\s*extra_data\.([\w.]+)\s*\}\}", tpl_str):
                _tc_set_deep(extra_data, m.group(1), "<请填写>")
                found += 1
            if found:
                notes.append(f"接口节点「{name}」：从 request_template 补全 {found} 个 extra_data 占位字段")

    # batch_contexts：从模板去重 (product_id, stage, scene)
    seen: set = set()
    bcs: List[Dict[str, Any]] = []
    for t in templates:
        if not isinstance(t, dict) or t.get("status") == "offline":
            continue
        pid_raw = str(t.get("product_id") or "")
        pid = re.split(r"[,，\n]+", pid_raw)[0].strip() if pid_raw.strip() else ""
        stage = str(t.get("stage") or "")
        scene = str(t.get("scene") or "")
        key = (pid, stage, scene)
        if key in seen:
            continue
        seen.add(key)
        bcs.append({"product_id": pid, "stage": stage, "scence": scene})
        if len(bcs) >= 3:
            break
    if not bcs:
        bcs = [{"product_id": "", "stage": "", "scence": ""}]

    payload = {
        "callId": "test_" + datetime.datetime.now().strftime("%Y%m%d%H%M%S"),
        "intent": intent,
        "province": province,
        "phone": "15010470528",
        "topN": 2,
        "extra_data": extra_data,
        "extra_info": extra_info,
        "batch_contexts": bcs,
    }
    skill_type = "mixed" if len(types) > 1 else (next(iter(types)) if types else "none")
    if skill_type == "none":
        notes.append("该技能未配置接口节点：extra_data/extra_info 均为空，将仅按 batch_contexts 匹配话术模板")
    return {"code": 200, "data": {"payload": payload, "skill_type": skill_type, "notes": notes}}


def _json_dumps_safe(obj: Any) -> str:
    import json as _json
    try:
        return _json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(obj)


@router.post("/api/skills/test_mapping")
async def test_mapping(body: Dict[str, Any]):
    """测试接口映射：模拟 DataStep 调用，返回 resource_context 映射结果

    body: {"api_nodes": {...}, "phone": "...", "extra_vars": {...},
           "extra_data": {...}, "extra_info": {...}}
    extra_info 用于测试直传（source_type=direct）节点的映射。
    """
    from core.context import FlowContext
    from steps.data_step import DataStep

    api_nodes = body.get("api_nodes", {})
    ctx = FlowContext(
        phone=body.get("phone", "13800000000"),
        intent=body.get("intent", "test"),
        province=body.get("province", "test"),
        extra_vars=body.get("extra_vars", {}),
        extra_data=body.get("extra_data", {}) or {},
        extra_info=body.get("extra_info", {}) or {},
    )
    step = DataStep("test")
    await step.run(ctx, api_nodes)
    return {
        "code": 200,
        "data": {
            "resource_context": ctx.resource_context,
            "raw_responses": ctx.raw_responses,
            "errors": ctx.errors,
        },
    }


# ── 话术模板 CRUD ─────────────────────────────────────────────

@router.get("/api/templates")
async def list_templates(
    province: Optional[str] = None,
    intent: Optional[str] = None,
    name: Optional[str] = None,
    scene: Optional[str] = None,
    stage: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """跨技能包聚合查询话术模板列表（支持多条件过滤 + 分页）"""
    result = skill_registry.get_all_templates(
        province=province, intent=intent, name=name,
        scene=scene, stage=stage, status=status,
        page=page, page_size=page_size,
    )
    return {"code": 200, "data": result}


@router.get("/api/templates/apis")
async def list_template_apis(province: Optional[str] = None, intent: Optional[str] = None):
    """获取可关联接口列表（供前端下拉选择）"""
    if not province or not intent:
        all_apis: List[str] = []
        for s in skill_registry.list_all():
            apis = skill_registry.get_available_apis(s["province"], s["intent"])
            all_apis.extend(a for a in apis if a not in all_apis)
        return {"code": 200, "data": all_apis}
    return {"code": 200, "data": skill_registry.get_available_apis(province, intent)}


@router.post("/api/templates")
async def create_template(body: TemplateCreateRequest, request: Request):
    """新建话术模板（需同省份或本部权限）"""
    check_province_write(request, body.province)
    try:
        data = body.model_dump()
        data["created_by"] = get_operator(request, fallback=body.created_by or "admin")
        # 优化三：接口数据域变量「有传参默认全选」——仅 Skill 管理多产品编辑器传标记，
        # 全局 TemplateConfig 不受影响。标记本身不入库。
        auto_flag = data.pop("auto_domain_vars", False)
        if auto_flag:
            data["linked_vars"] = _merge_auto_domain_vars(body.province, body.intent, data.get("linked_vars"))
        data["linked_vars"], _added = _fill_placeholder_vars(
            data.get("template_content"), data.get("linked_vars"))
        saved = skill_registry.upsert_template(body.province, body.intent, data)
        from services.kafka_service import send_op_log
        send_op_log(request, "add", "新建话术模板",
                    f"新建话术模板「{body.template_name}」({body.province}/{body.intent})")
        return {"code": 200, "message": "创建成功", "data": saved,
                "lint": _lint_template_safe(saved, body.province, body.intent)}
    except ValueError as e:
        raise HTTPException(404, str(e))
    except RuntimeError:
        # save_biz_config 真实失败（ES/Redis 不可用）
        raise HTTPException(500, _SAVE_FAIL_MSG)
    except Exception as e:
        raise HTTPException(500, f"创建失败: {e}")


@router.post("/api/templates/bulk")
async def bulk_create_templates(body: TemplateBulkRequest, request: Request):
    """批量新建/更新话术模板（需同省份或本部权限）。

    一次请求写入多条，后端合并进 biz_config 后**只做一次** ES 版本化写入 + skill_meta
    刷新 + 热重载 + 广播，彻底避免逐条 POST 导致的版本号疯狂自增 / 日志“一直循环”。
    """
    check_province_write(request, body.province)
    if skill_registry.get(body.province, body.intent) is None:
        raise HTTPException(404, f"技能包不存在: {body.province}/{body.intent}")
    delete_ids = [t for t in (body.delete_template_ids or []) if t]
    if not body.templates and not delete_ids:
        return {"code": 200, "message": "无可导入数据", "data": {"imported": 0, "templates": []}}

    operator = get_operator(request, fallback="import")
    # auto_domain_vars 的派生键对同一技能包只计算一次，避免每条重复推断
    domain_vars: List[str] = (
        _merge_auto_domain_vars(body.province, body.intent, [])
        if body.auto_domain_vars else []
    )
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    data_list: List[Dict[str, Any]] = []
    for t in body.templates:
        if not isinstance(t, dict):
            continue
        content = str(t.get("template_content", "") or "").strip()
        if not content:
            continue
        linked = list(t.get("linked_vars") or [])
        if domain_vars:
            for k in domain_vars:
                if k not in linked:
                    linked.append(k)
        linked, _ = _fill_placeholder_vars(content, linked)
        item = dict(
            province=body.province, intent=body.intent,
            template_name=str(t.get("template_name") or body.intent),
            template_content=content,
            product_id=str(t.get("product_id", "") or ""),
            scene=str(t.get("scene", "") or ""),
            stage=str(t.get("stage", "") or ""),
            linked_vars=linked,
            script_requirement=str(t.get("script_requirement", "") or ""),
            prompt_template=str(t.get("prompt_template", "") or ""),
            linked_apis=list(t.get("linked_apis") or []),
            status="offline" if str(t.get("status", "online")) == "offline" else "online",
        )
        # 传了 template_id 视为原地更新：保留原 created_at/created_by（由 bulk 合并保留），
        # 仅新建项写入 created_by/created_at，避免编辑时把创建信息覆盖成本次操作人/时间。
        tid = str(t.get("template_id", "") or "")
        if tid:
            item["template_id"] = tid
        else:
            item["created_by"] = operator
            item["created_at"] = now
        data_list.append(item)

    if not data_list and not delete_ids:
        return {"code": 200, "message": "无有效模板（话术内容均为空）",
                "data": {"imported": 0, "templates": []}}

    try:
        saved = skill_registry.bulk_upsert_templates(
            body.province, body.intent, data_list, skip_reload=False,
            delete_ids=delete_ids,
        )
        from services.kafka_service import send_op_log
        send_op_log(request, "import", "批量保存话术模板",
                    f"批量保存 {len(saved)} 条话术模板"
                    f"{f'、删除 {len(delete_ids)} 条' if delete_ids else ''}"
                    f"({body.province}/{body.intent})")
        return {"code": 200, "message": f"保存完成：写入 {len(saved)} 条"
                + (f"，删除 {len(delete_ids)} 条" if delete_ids else ""),
                "data": {"imported": len(saved), "deleted": len(delete_ids), "templates": saved}}
    except ValueError as e:
        raise HTTPException(404, str(e))
    except RuntimeError:
        raise HTTPException(500, _SAVE_FAIL_MSG)
    except Exception as e:
        raise HTTPException(500, f"批量导入失败: {e}")


@router.get("/api/templates/{template_id}")
async def get_template(template_id: str):
    """查询单条话术模板详情"""
    tpl = skill_registry.get_template_by_id(template_id)
    if tpl is None:
        raise HTTPException(404, f"模板不存在: {template_id}")
    return {"code": 200, "data": tpl}


@router.put("/api/templates/{template_id}")
async def update_template(template_id: str, body: TemplateUpdateRequest, request: Request):
    """编辑话术模板（需同省份或本部权限）"""
    tpl = skill_registry.get_template_by_id(template_id)
    if tpl is None:
        raise HTTPException(404, f"模板不存在: {template_id}")

    province = tpl["province"]
    intent = tpl["intent"]
    check_province_write(request, province)

    update_data = {"template_id": template_id}
    update_data.update({k: v for k, v in body.model_dump().items() if v is not None})
    update_data["updated_by"] = get_operator(request)
    # 优化三：接口数据域变量「有传参默认全选」——仅当携带 auto_domain_vars 标记且提交了 linked_vars 时并入。
    # 标记本身不入库。
    auto_flag = update_data.pop("auto_domain_vars", None)
    if auto_flag and "linked_vars" in update_data:
        update_data["linked_vars"] = _merge_auto_domain_vars(province, intent, update_data.get("linked_vars"))
    # 保存即补齐：正文改了就按新正文里的占位符补全数据域声明（含子字段占位符的根名）
    if "template_content" in update_data:
        base_vars = update_data["linked_vars"] if "linked_vars" in update_data else tpl.get("linked_vars")
        update_data["linked_vars"], _added = _fill_placeholder_vars(
            update_data["template_content"], base_vars)

    try:
        saved = skill_registry.upsert_template(province, intent, update_data)
        from services.kafka_service import send_op_log
        send_op_log(request, "update", "编辑话术模板",
                    f"编辑话术模板 {template_id}（{province}/{intent}）")
        return {"code": 200, "message": "更新成功", "data": saved,
                "lint": _lint_template_safe(saved, province, intent)}
    except RuntimeError:
        # save_biz_config 真实失败（ES/Redis 不可用）
        raise HTTPException(500, _SAVE_FAIL_MSG)
    except Exception as e:
        raise HTTPException(500, f"更新失败: {e}")


@router.patch("/api/templates/{template_id}/status")
async def patch_template_status(template_id: str, body: Dict[str, Any], request: Request):
    """内联切换模板上线/下线状态（需同省份或本部权限）"""
    status = body.get("status")
    if status not in ("online", "offline"):
        raise HTTPException(400, "status 必须为 online 或 offline")
    tpl = skill_registry.get_template_by_id(template_id)
    if tpl is None:
        raise HTTPException(404, f"模板不存在: {template_id}")
    province = tpl["province"]
    intent = tpl["intent"]
    check_province_write(request, province)
    try:
        saved = skill_registry.upsert_template(
            province, intent, {"template_id": template_id, "status": status}
        )
        return {"code": 200, "message": "状态更新成功", "data": saved}
    except RuntimeError:
        # save_biz_config 真实失败（ES/Redis 不可用）
        raise HTTPException(500, _SAVE_FAIL_MSG)
    except Exception as e:
        raise HTTPException(500, f"状态更新失败: {e}")


class TemplateAutoFillRequest(BaseModel):
    """「智能填充占位符」请求：基于原话术语义，LLM 改写为 {key} 占位符版本。"""
    province: str = Field(..., description="省份代码")
    intent: str = Field(..., description="意图（技能包目录名）")
    template_content: str = Field(..., description="待改写的话术模板内容")
    linked_apis: List[str] = Field(default_factory=list,
                                   description="已勾选的关联接口，用于收窄可用占位符范围；空=全部启用接口")


@router.post("/api/templates/auto_fill_placeholders")
async def auto_fill_placeholders(body: TemplateAutoFillRequest):
    """基于原话术模板语义，调用大模型自动把可参数化内容改写为 {占位符}。

    - 可用占位符与编辑页「可映射固定域」调色板同源（context_vars），并按 linked_apis 收窄；
    - LLM 走 get_dashscope_config()（环境感知：development=公网 DeepSeek，
      gray/production/production_noauth=内网代理 qwen-plus），与接口智能分析同一路由；
    - 返回改写后模板 + 实际使用的占位符；LLM 自造的非法占位符会被还原并在 unknown_vars 中提示。
    """
    content = (body.template_content or "").strip()
    if not content:
        raise HTTPException(400, "template_content 不能为空")

    # 1) 收集该技能可用占位符（与调色板同源），按 linked_apis 收窄
    ctx_res = await get_context_vars(body.province, body.intent)
    all_vars: List[Dict[str, Any]] = ctx_res.get("data", [])
    linked = set(body.linked_apis or [])

    def _var_usable(v: Dict[str, Any]) -> bool:
        if v.get("key") == "table":          # 差异表格仅前端展示，不进 LLM
            return False
        if v.get("available") is False:      # 该技能样例下注定取不到值的计算变量
            return False
        if linked and v.get("source") in ("response_extract", "field_transform", "passthrough"):
            producers = v.get("api_names") or []
            if producers and not (set(producers) & linked):
                return False
        return True

    usable = [v for v in all_vars if _var_usable(v)]
    if not usable:
        raise HTTPException(400, "该技能下没有可用占位符，请先在「接口配置」完成出参映射或透传配置")

    _SRC_LABEL = {
        "response_extract": "接口映射域", "field_transform": "接口映射域",
        "passthrough": "直传入参字段", "script_step": "计算生成", "fixed": "固定上下文",
    }
    var_lines = []
    for v in usable:
        line = f"- {{{v['key']}}}：{v.get('label', v['key'])}（{_SRC_LABEL.get(v.get('source'), v.get('source', ''))}）"
        if v.get("sample") not in (None, ""):
            sample_str = str(v["sample"])
            if len(sample_str) > 60:
                sample_str = sample_str[:60] + "…"
            line += f"，示例值：{sample_str}"
        var_lines.append(line)

    # 2) 组装提示词并调用环境感知 LLM
    from prompt.placeholder_fill import AUTO_FILL_PLACEHOLDER_PROMPT
    prompt = (AUTO_FILL_PLACEHOLDER_PROMPT
              .replace("<<VAR_LIST>>", "\n".join(var_lines))
              .replace("<<TEMPLATE_CONTENT>>", content))

    try:
        from services.llm_service import LLMService
        from utils.config_loader import config_loader
        llm = LLMService(config_override=config_loader.get_dashscope_config())
        raw = await llm.generate(
            prompt, temperature=0.1, max_tokens=2000,
            stage="template.auto_fill_placeholders", provider="auto_fill",
            province=body.province,
        )
    except Exception as e:
        logger.error(f"[auto_fill_placeholders] LLM 调用失败: {e}")
        raise HTTPException(500, f"大模型调用失败: {e}")

    # 3) 解析 LLM 返回
    import json as _json
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if not m:
        raise HTTPException(500, f"大模型返回无法解析，原始内容: {(raw or '')[:300]}")
    try:
        parsed = _json.loads(m.group())
    except Exception:
        raise HTTPException(500, f"大模型返回 JSON 格式错误: {(raw or '')[:300]}")

    filled = str(parsed.get("filled_template") or "").strip()
    notes = str(parsed.get("notes") or "")
    if not filled:
        raise HTTPException(500, "大模型未返回改写后的模板内容")

    # 4) 占位符合法性校验：以改写文本为准提取 token；非法 token 记录并提示
    allowed = {v["key"] for v in usable}
    tokens = re.findall(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}", filled)
    used_vars = [t for t in dict.fromkeys(tokens) if t in allowed]
    unknown_vars = [t for t in dict.fromkeys(tokens) if t not in allowed]

    logger.info(
        f"[auto_fill_placeholders] {body.province}/{body.intent} 完成："
        f"使用 {len(used_vars)} 个占位符{('，含非法 ' + '、'.join(unknown_vars)) if unknown_vars else ''}"
    )
    return {
        "code": 200,
        "data": {
            "filled_template": filled,
            "used_vars": used_vars,
            "unknown_vars": unknown_vars,
            "notes": notes,
            "available_vars": sorted(allowed),
        },
    }


@router.post("/api/templates/infer_vars")
async def infer_template_vars(body: Dict[str, Any]):
    """根据话术模板内容推断需要关联的 context 变量（双层：精确key + 语义关键词）。

    请求体：{"template_content": "..."}
    返回：{"code": 200, "data": {"linked_vars": ["usage", "pkg_brief", ...], "reason": {...}}}
    """
    content = (body.get("template_content") or "").strip()
    if not content:
        return {"code": 200, "data": {"linked_vars": [], "reason": "模板内容为空"}}
    linked = infer_linked_vars(content)
    return {
        "code": 200,
        "data": {
            "linked_vars": linked,
            "reason": f"推断出 {len(linked)} 个关联变量" if linked else "未检测到需要关联的变量（话术为纯固定文案）",
        },
    }


# 预览示例清洗：接口文档 mock 常把待填字段写成 {{XXX}} 占位标记（如 {{CURRENT_OFFER_NAME}}），
# 直接进预览会显示成一串未填的花括号 token，误导配置人员；此处按 token 语义替换为可读示例值。
_MUSTACHE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
# 流量字段（与 plugins.package_diff._get_flow 对齐）：mock 里常存 MB（如 20480），预览需换算成 GB
_FLOW_KEYS = ("offerFlow", "data_quota", "flow", "data", "flowGb")


def _humanize_mock_token(token: str) -> str:
    """把 {{XXX}} 占位 token 映射为可读示例值（按字段语义猜测，纯展示用）。"""
    t = (token or "").strip().upper()
    if "NAME" in t:
        return "示例套餐"
    if any(x in t for x in ("FEE", "PRICE", "AMOUNT", "MONEY", "COST")):
        return "99"
    if "ID" in t:
        return "P0000000001"
    if any(x in t for x in ("FLOW", "DATA")):
        return "20"
    if any(x in t for x in ("VOICE", "MINUTE", "CALL")):
        return "200"
    return "示例值"


def _normalize_flow_mb_to_gb(pkg: Dict[str, Any]) -> None:
    """就地把套餐字典里的流量字段从 MB 归一到 GB（>200 视为 MB，与差异计算口径一致）。"""
    for k in list(pkg.keys()):
        if k not in _FLOW_KEYS:
            continue
        v = pkg[k]
        try:
            f = float(re.sub(r"(?i)(gb|mb|g|m)", "", str(v)).strip())
        except (TypeError, ValueError):
            continue
        if f > 200:  # 经验阈值：真实 GB 档位极少 >200，>200 视为 MB
            gb = f / 1024
            pkg[k] = int(gb) if abs(gb - round(gb)) < 1e-6 else round(gb, 2)


def _clean_preview_sample(sample: Dict[str, Any]) -> Dict[str, Any]:
    """清洗预览示例：① 递归替换 {{占位}} 标记为可读示例；② 套餐流量 MB→GB 归一。"""
    def _walk(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: _walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_walk(x) for x in o]
        if isinstance(o, str) and "{{" in o:
            return _MUSTACHE_RE.sub(lambda m: _humanize_mock_token(m.group(1)), o)
        return o

    cleaned = _walk(sample)
    for key in ("current_package", "recommended_package"):
        if isinstance(cleaned.get(key), dict):
            _normalize_flow_mb_to_gb(cleaned[key])
    recs = cleaned.get("recommended_packages")
    if isinstance(recs, list):
        for item in recs:
            if isinstance(item, dict):
                _normalize_flow_mb_to_gb(item)
    return cleaned


def _build_sample_ctx_from_skill(province: str, intent: str) -> Optional[Dict[str, Any]]:
    """用技能包 api_nodes 的 mock_response 跑一遍真实映射（response_extract / field_transform），
    产出贴合该技能实际配置的 resource_context 示例，供 Prompt 预览动态填充。

    这样预览里的【上下文数据】取值来自该技能「接口映射规则 + 出参示例」，而非写死的通用样例，
    与运行态 build_prompt 的字段结构一致；无技能/无 mock 时返回 None，回退内置默认样例。
    """
    if not province or not intent:
        return None
    try:
        pkg = skill_registry.get(province, intent)
    except Exception:
        pkg = None
    if pkg is None:
        return None
    api_nodes = pkg.config.get("api_nodes", {}) if isinstance(pkg.config, dict) else {}
    if not isinstance(api_nodes, dict) or not api_nodes:
        return None

    from steps.data_step import DataStep, _NODE_TO_CTX_FIELD
    ds = DataStep(province)
    merged: Dict[str, Any] = {}
    extra_info_sample: Dict[str, Any] = {}

    for name, cfg in api_nodes.items():
        if not isinstance(cfg, dict) or str(name).startswith("_") or not cfg.get("enabled", True):
            continue
        mock = cfg.get("mock_response")
        if not isinstance(mock, dict) or not mock:
            continue
        try:
            # 直传节点：mock 即 extra_info 样例（供透传字段展示）。归一兼容 mock 被粘贴成
            # 「整请求体」或「网关 params 包裹」的写法，取到 extra_info 本体再展示/映射。
            eff_mock = mock
            if cfg.get("source_type") == "direct":
                eff_mock = _normalize_direct_extra_info(mock)
                extra_info_sample.update(eff_mock)
            if cfg.get("response_extract") or cfg.get("field_transform"):
                extracted = ds._extract_fields(eff_mock, cfg)
                resources = ds._transform_fields(extracted, cfg, eff_mock)
            else:
                resources = {k: v for k, v in eff_mock.items() if k in _NODE_TO_CTX_FIELD}
            if resources:
                merged = ds._deep_merge(merged, resources)
        except Exception as e:
            logger.debug(f"[preview] 节点 {name} mock 映射失败，跳过: {e}")
            continue

    sample: Dict[str, Any] = {}
    for k in ("current_package", "usage", "tags", "user_info", "user_profile", "domain_ext"):
        if merged.get(k):
            sample[k] = merged[k]
    recs = merged.get("recommended_packages")
    if not (isinstance(recs, list) and recs):
        # 直传模式：推荐产品由主服务放在 extra_info.recommended_packages 数组里，不经接口
        # 映射，故不在 merged 中。直传节点的 mock 可能是整个请求体（产品数组嵌在 extra_info
        # 下一层），也可能就是 extra_info 本身，两种形态都找一遍；否则调色板看不到产品字段。
        # 兼容多种声明形态：新版多产品数组 recommended_packages、旧版单产品
        # final_recommendations（可为单个字典），以及 mock 为整请求体时嵌套的 extra_info。
        _nested = extra_info_sample.get("extra_info")
        _srcs = [extra_info_sample]
        if isinstance(_nested, dict):
            _srcs.append(_nested)
        for _src in _srcs:
            for _name in ("recommended_packages", "final_recommendations"):
                _cand = _src.get(_name)
                if isinstance(_cand, dict) and _cand:
                    _cand = [_cand]
                if isinstance(_cand, list) and _cand:
                    recs = _cand
                    break
            if isinstance(recs, list) and recs:
                break
    if isinstance(recs, list) and recs:
        first = recs[0]
        if isinstance(first, dict):
            sample["recommended_package"] = {
                k: v for k, v in first.items()
                if not (isinstance(k, str) and k.startswith("_"))
            }
    if extra_info_sample:
        sample["extra_info"] = extra_info_sample
    if not sample:
        return None
    # 清洗接口 mock 里未填的 {{占位}} 标记 + 流量单位归一，避免预览出现花括号 token / “20480GB”
    return _clean_preview_sample(sample)


def _build_field_source_view(province: str, intent: str, template: Dict[str, Any]) -> str:
    """字段来源视图（配置视图）：说明【上下文数据】里每个变量由哪些接口出参字段 / 映射规则得到。

    与“示例数据填充”视图互补——后者展示运行时真实取值的样子，本视图展示数据从哪来，
    帮助配置人员理解“具体值 → 透传接口字段 / 映射域”的对应关系。
    """
    try:
        from engine.prompt_builder import VAR_LABELS
    except Exception:
        VAR_LABELS = {}

    pkg = None
    try:
        pkg = skill_registry.get(province, intent)
    except Exception:
        pkg = None
    api_nodes = pkg.config.get("api_nodes", {}) if (pkg and isinstance(pkg.config, dict)) else {}

    def _lbl(key: str) -> str:
        return VAR_LABELS.get(key, key)

    lines: List[str] = [
        "【字段来源说明】（配置视图：展示每个上下文变量由哪些接口出参字段 / 映射规则得到；"
        "运行时发送给大模型的是这些字段在该用户下的真实取值，而非此处示例）"
    ]
    has_any = False
    if isinstance(api_nodes, dict):
        for name, cfg in api_nodes.items():
            if not isinstance(cfg, dict) or str(name).startswith("_") or not cfg.get("enabled", True):
                continue
            node_label = cfg.get("description") or name
            src_type = cfg.get("source_type", "api")
            if src_type == "direct" and cfg.get("direct_mode") == "passthrough":
                _sample = _normalize_direct_extra_info(cfg.get("mock_response"))
                fields = cfg.get("passthrough_fields") or list(_sample.keys())
                # 按顶层大字段分组（与「透传字段」勾选结构、图三一致）：直传直接引用入参原值，不映射标准域。
                # 子字段占位符 token 与调色板/运行态一致：标准域字典 → {域[子键]}；其余（含产品列表）→ 裸叶子 {子键}
                _groups: Dict[str, List[str]] = {}
                _order: List[str] = []
                for f in fields:
                    if not isinstance(f, str) or f.startswith("_"):
                        continue
                    top = f.split(".", 1)[0]
                    if top not in _groups:
                        _groups[top] = []
                        _order.append(top)
                    if "." in f:
                        leaf = f.rsplit(".", 1)[-1]
                        if leaf and leaf not in _groups[top]:
                            _groups[top].append(leaf)
                for top in _order:
                    lines.append(
                        f"• {_lbl(top)} {{{top}}} ← 直传入参「{node_label}」透传大字段"
                        f"（直接引用入参原值，不映射标准域）"
                    )
                    _top_is_dict = isinstance(_sample.get(top), dict)
                    for leaf in _groups[top]:
                        _tok = f"{top}[{leaf}]" if (top in STD_DOMAIN_KEYS and _top_is_dict) else leaf
                        lines.append(f"    └ {leaf} {{{_tok}}} ← 「{top}」下一级字段")
                    has_any = True
                continue
            # response_extract：接口出参路径 → 标准域 / 中间变量
            for domain, path in (cfg.get("response_extract") or {}).items():
                lines.append(f"• {_lbl(domain)} {{{domain}}} ← 接口「{node_label}」出参路径 {path}")
                has_any = True
            # field_transform：中间变量/出参 → 映射域（含筛选规则简述）
            for dst, rule in (cfg.get("field_transform") or {}).items():
                if str(dst).startswith("_"):
                    continue
                if isinstance(rule, dict):
                    frm = rule.get("from", "")
                    rtype = rule.get("type", "")
                    detail = f"（{rtype}）" if rtype else ""
                    lines.append(f"• 映射域 {dst} ← {frm}{detail}")
                else:
                    lines.append(f"• 映射域 {dst} ← {rule}")
                has_any = True

    lines.append("• 差异 {diff_str} ← 由「当前套餐」与「推荐产品」的月费/流量/语音自动计算（双侧齐全才展示）")
    if not has_any:
        lines.append("• （该技能未配置接口映射规则，或为纯本地/默认样例）")

    tpl_text = (template.get("template_content") or template.get("prompt_template") or "").strip()
    if tpl_text:
        lines.append("【话术模板】")
        lines.append(tpl_text)
    return "\n".join(lines)


@router.post("/api/templates/preview_prompt")
async def preview_template_prompt(body: Dict[str, Any]):
    """预览单条模板最终发给 LLM 的 Prompt（与运行态 build_prompt 同一条路径）。

    请求体：{"template": {...}, "province": "可选", "intent": "可选",
             "sample_data": {...可选}, "mode": "sample|schema"}
    - mode=sample（默认）：示例数据填充，展示运行时真正发送给大模型的完整提示词。
    - mode=schema：字段来源视图，展示各上下文变量由哪些接口出参字段 / 映射域得到。
    样例数据来源优先级：显式 sample_data > 技能包 mock_response 经真实映射（已清洗占位/单位）> 内置默认样例。
    返回：{"code": 200, "data": {"prompt": "..."}}
    """
    template = body.get("template")
    if not isinstance(template, dict) or not template:
        raise HTTPException(400, "template 字段必填且必须为非空对象")
    province = body.get("province", "") or ""
    intent = body.get("intent", "") or ""
    mode = (body.get("mode") or "sample").lower()

    if mode == "schema":
        try:
            prompt = _build_field_source_view(province, intent, template)
        except Exception as e:
            raise HTTPException(500, f"字段来源视图生成失败: {e}")
        return {"code": 200, "data": {"prompt": prompt, "mode": "schema"}}

    from engine.prompt_builder import preview_prompt
    try:
        pt_fields = body.get("passthrough_fields")
        # 前端未显式传样例时，用技能实际 mock_response + 映射规则动态生成，避免写死通用样例
        sample_data = body.get("sample_data")
        if sample_data is None:
            sample_data = _build_sample_ctx_from_skill(province, intent)
        prompt = preview_prompt(
            template,
            sample_ctx_data=sample_data,
            province=province,
            intent=intent,
            passthrough_fields=pt_fields if isinstance(pt_fields, list) else None,
        )
    except Exception as e:
        raise HTTPException(500, f"Prompt 预览失败: {e}")
    return {"code": 200, "data": {"prompt": prompt, "mode": "sample"}}


@router.post("/api/templates/import")
async def import_templates(file: UploadFile = File(...), request: Request = None):
    """批量导入话术模板（支持 CSV 和 Excel）。

    列：省份,意图,产品,环节,应用场景,话术内容
    - 产品列支持多产品，用换行符或英文逗号分隔，每个产品 ID 单独创建一条模板
    - 省份/意图/产品/环节/场景为空时沿用上一行的值（跨行合并）
    """
    import re as _re
    content = await file.read()
    filename = (file.filename or "").lower()

    # ── 解析文件为行列表 ──────────────────────────────────────
    rows: List[Dict[str, str]] = []
    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        try:
            import openpyxl as _openpyxl
        except ImportError:
            raise HTTPException(500, "服务端缺少 openpyxl，请执行 pip install openpyxl")
        wb = _openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        headers: List[str] = []
        for ridx, row in enumerate(ws.iter_rows(values_only=True)):
            cells = [str(c).strip() if c is not None else "" for c in row]
            if ridx == 0:
                headers = cells
            else:
                rows.append(dict(zip(headers, cells)))
        wb.close()
    else:
        # 编码自动识别：优先 UTF-8(带/不带 BOM)，失败回退 GB18030（Excel 中文另存默认），
        # 均失败时用 UTF-8 替换非法字节兜底，避免整批导入因编码直接 500。
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = content.decode("gb18030")
            except UnicodeDecodeError:
                text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            rows.append({k: (v or "").strip() for k, v in row.items()})

    # ── 构建省份名称 → province_code 映射 ────────────────────
    province_map: Dict[str, str] = {}
    for s in skill_registry.list_all():
        prov = s["province"]
        province_map[prov] = prov
        meta_name = (s.get("meta") or {}).get("province_name", "")
        if meta_name:
            province_map[meta_name] = prov
    _BUILTIN = {"北京": "beijing", "上海": "shanghai", "广东": "guangdong",
                "浙江": "zhejiang", "江苏": "jiangsu", "山东": "shandong"}
    for cn, code in _BUILTIN.items():
        if cn not in province_map:
            province_map[cn] = code

    imported, errors = [], []
    affected_keys: set = set()
    # 按 province:intent 归集待写模板，导入完成后每个技能包只做**一次** ES 写入，
    # 避免逐行 upsert 造成的版本号疯狂自增 / 日志“一直循环” / O(N²) 写放大。
    pending: Dict[str, List[Dict[str, Any]]] = {}
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    operator = get_operator(request, fallback="import")

    def _clean(s: str) -> str:
        return "" if s.lstrip("-").strip() == "" else s.strip()

    # 上一行有效值（跨行向下填充）
    prev: Dict[str, str] = {"province": "", "intent": "", "product": "", "stage": "", "scene": "", "content": ""}

    for i, row in enumerate(rows, start=1):
        try:
            province_raw = _clean(row.get("省份", "").strip() or prev["province"])
            intent_raw   = _clean(row.get("意图", "").strip()   or prev["intent"])
            product_raw  = _clean(row.get("产品", "").strip()   or prev["product"])
            stage_raw    = _clean(row.get("环节", "").strip()   or prev["stage"])
            scene_raw    = _clean(row.get("应用场景", "").strip() or prev["scene"])
            content_text = _clean(row.get("话术内容", "").strip() or prev["content"])

            if province_raw: prev["province"] = province_raw
            if intent_raw:   prev["intent"]   = intent_raw
            if product_raw:  prev["product"]  = product_raw
            if stage_raw:    prev["stage"]    = stage_raw
            if scene_raw:    prev["scene"]    = scene_raw
            if content_text: prev["content"]  = content_text

            if not intent_raw:
                errors.append(f"第{i}行: 意图为空，跳过")
                continue
            if not content_text:
                continue

            province = province_map.get(province_raw) or province_raw.lower()
            intent   = intent_raw

            if skill_registry.get(province, intent) is None:
                errors.append(f"第{i}行: 未找到技能包 '{province_raw}/{intent_raw}'，跳过")
                continue

            # 产品列多值拆分（换行或英文逗号）
            product_ids = [
                p.strip() for p in _re.split(r"[\n,]+", product_raw) if p.strip()
            ] if product_raw else [""]

            # ── 双层推断：精确 key 匹配 + 语义关键词扫描 ──────────────
            auto_linked_vars = infer_linked_vars(content_text)

            for pid in product_ids:
                data = dict(
                    province=province, intent=intent,
                    template_name=intent,
                    scene=scene_raw, stage=stage_raw, product_id=pid,
                    template_content=content_text,
                    prompt_template="",
                    linked_vars=auto_linked_vars,
                    linked_apis=[], status="online",
                    created_by=operator,
                    created_at=now,
                )
                key = f"{province}:{intent}"
                pending.setdefault(key, []).append(data)
                affected_keys.add(key)

        except Exception as e:
            errors.append(f"第{i}行: {e}")

    # ── 每个技能包一次性批量写入（单次 ES 版本化 + skill_meta 刷新）──────────
    for key, tpls in pending.items():
        p, i_ = key.split(":", 1)
        try:
            saved_list = skill_registry.bulk_upsert_templates(
                p, i_, tpls, skip_reload=True
            )
            imported.extend(saved_list)
        except Exception as e:
            affected_keys.discard(key)
            errors.append(f"{p}/{i_}: 批量写入失败，该技能包 {len(tpls)} 条未导入: {e}")

    # 批量导入完成后，按影响到的省份+意图统一 reload 一次并广播一次变更。
    # bulk_upsert_templates(skip_reload=True) 已跳过热重载与变更广播
    # （publish_config broadcast=False），避免逐条广播风暴；此处统一补发。
    for key in affected_keys:
        p, i_ = key.split(":", 1)
        skill_registry.reload(p, i_)
        try:
            from utils.skill_runtime import IS_DEV  # 延迟 import
            if not IS_DEV:
                from services.redis_config_bus import redis_config_bus  # 延迟 import
                if redis_config_bus.enabled:
                    redis_config_bus.publish_change(p, i_)
        except Exception as e:
            logger.warning(f"批量导入后广播变更失败（其他实例可能延迟感知）{p}/{i_}: {e}")

    return {
        "code": 200,
        "message": f"导入完成：成功 {len(imported)} 条，失败 {len(errors)} 条",
        "data": {"imported": len(imported), "errors": errors, "templates": imported},
    }


@router.post("/api/templates/batch_delete")
async def batch_delete_templates(body: TemplateBatchDeleteRequest, request: Request):
    """批量删除话术模板（需同省份或本部权限）。

    按 province/intent 分组，每组**一次性删除 + 单次 ES 写入**，相比逐条删除
    大幅减少外部存储写入次数，降低「外部存储不可用」失败概率，并支持一次删多条。
    """
    ids = [t for t in (body.template_ids or []) if t]
    if not ids:
        raise HTTPException(400, "template_ids 不能为空")

    groups: Dict[tuple, List[str]] = {}
    not_found: List[str] = []

    if body.province and body.intent:
        # 精确定位：直接从指定技能包删除（Skill 管理场景），不按 id 反查，
        # 规避同 id 跨技能包重复 & 模板内脏 province 字段导致的误定位。
        if skill_registry.get(body.province, body.intent) is None:
            raise HTTPException(404, f"技能包不存在: {body.province}/{body.intent}")
        groups[(body.province, body.intent)] = ids
    else:
        # 全局场景：按 id 反查归属技能包（province/intent 以所属技能包为准）
        for tid in ids:
            tpl = skill_registry.get_template_by_id(tid)
            if tpl is None:
                not_found.append(tid)
                continue
            key = (tpl["province"], tpl["intent"])
            groups.setdefault(key, []).append(tid)

        if not groups:
            raise HTTPException(404, "未找到任何可删除的模板")

    # 2) 权限校验（逐省份），任一无权直接拒绝（不做部分删除）
    for (province, _intent) in groups:
        check_province_write(request, province)

    # 3) 逐组批量删除（每组单次 ES 写）
    deleted = 0
    failed_groups: List[str] = []
    for (province, intent), tids in groups.items():
        try:
            deleted += skill_registry.delete_templates(province, intent, tids)
        except RuntimeError:
            failed_groups.append(f"{province}/{intent}")
        except Exception as e:
            logger.error(f"批量删除模板失败({province}/{intent}): {e}")
            failed_groups.append(f"{province}/{intent}")

    if failed_groups and deleted == 0:
        # 全部失败：外部存储不可用
        raise HTTPException(500, _SAVE_FAIL_MSG)

    from services.kafka_service import send_op_log
    send_op_log(request, "delete", "批量删除话术模板",
                f"批量删除话术模板 {deleted} 条（覆盖 {len(groups)} 个技能包）")

    msg = f"删除成功，共 {deleted} 条"
    if failed_groups:
        msg += f"；{len(failed_groups)} 个技能包保存失败（{'、'.join(failed_groups)}），请稍后重试"
    return {
        "code": 200,
        "message": msg,
        "data": {"deleted": deleted, "not_found": not_found, "failed_groups": failed_groups},
    }


@router.delete("/api/templates/{template_id}")
async def delete_template_route(template_id: str, request: Request):
    """删除话术模板（需同省份或本部权限）"""
    tpl = skill_registry.get_template_by_id(template_id)
    if tpl is None:
        raise HTTPException(404, f"模板不存在: {template_id}")

    province = tpl["province"]
    intent = tpl["intent"]
    tpl_name = tpl.get("template_name", template_id)
    ok = skill_registry.delete_template(province, intent, template_id)
    if not ok:
        # 模板存在性已在前面校验，此处 False 意味着底层保存失败
        raise HTTPException(500, _SAVE_FAIL_MSG)

    from services.kafka_service import send_op_log
    send_op_log(request, "delete", "删除话术模板",
                f"删除话术模板「{tpl_name}」({province}/{intent})")
    return {"code": 200, "message": "删除成功"}


# ── 接口节点 CRUD ─────────────────────────────────────────────

@router.get("/api/interfaces")
async def list_interfaces(
    province: Optional[str] = None,
    enabled: Optional[str] = None,
):
    """列出所有技能包的接口节点（扁平行）"""
    items = []
    for s in skill_registry.list_all():
        p = s["province"]
        i = s["intent"]
        if province and p != province:
            continue
        province_name = (s.get("meta") or {}).get("province_name", p)
        pkg = skill_registry.get(p, i)
        if pkg is None:
            continue
        api_nodes_cfg = pkg.config.get("api_nodes", {})
        for api_name, cfg in api_nodes_cfg.items():
            if api_name.startswith("_") or not isinstance(cfg, dict):
                continue
            is_enabled = cfg.get("enabled", True)
            if enabled == "true" and not is_enabled:
                continue
            if enabled == "false" and is_enabled:
                continue
            items.append({
                "province": p,
                "province_name": province_name,
                "intent": i,
                "api_name": api_name,
                "description": cfg.get("_comment", cfg.get("description", "")),
                "url": cfg.get("url", ""),
                "method": cfg.get("method", "POST"),
                "enabled": is_enabled,
                "source_type": cfg.get("source_type", "api"),
                "mock_mode": cfg.get("mock_mode", False),
                "created_by": cfg.get("created_by", "系统"),
                "created_at": cfg.get("created_at", "—"),
                "has_extract": bool(cfg.get("response_extract")),
                "has_transform": bool(cfg.get("field_transform")),
            })
    return {"code": 200, "data": items}


@router.get("/api/interfaces/{province}/{intent}/{api_name}")
async def get_interface(province: str, intent: str, api_name: str):
    """获取单个接口节点完整配置"""
    pkg = skill_registry.get(province, intent)
    if pkg is None:
        raise HTTPException(404, f"技能包不存在: {province}/{intent}")
    cfg = pkg.config.get("api_nodes", {}).get(api_name)
    if cfg is None:
        raise HTTPException(404, f"接口不存在: {api_name}")
    return {"code": 200, "data": {**cfg, "api_name": api_name,
                                   "province": province, "intent": intent}}


# 重命名字段名规范化（公共实现见 utils/field_naming.py；publish_config 写入 choke point
# 已统一防护，此处保留在 update_interface 中提前清洗，便于把修正结果回显给保存者）
from utils.field_naming import (  # noqa: E402
    clean_rename_field as _clean_rename_field,
    normalize_field_transform_renames as _normalize_field_transform_renames,
)


def _guard_response_extract(
    old_ext: Dict[str, Any],
    body_ext: Dict[str, Any],
    field_transform: Any,
) -> tuple:
    """response_extract 保存防丢失守护（纯函数，供 update_interface 调用）。

    两类槽位在新配置中缺失时自动保留旧映射（显式置空串/None = 有意删除，予以尊重）：
    1. 标准域（current_package / recommended_packages 等 STD_DOMAIN_KEYS）
       —— 北京事故第一形态：推荐产品映射被智能分析保存冲掉；
    2. 被 field_transform 规则引用（from=xxx）的中间槽位（如 raw_tags）
       —— 北京事故第二形态（2026-07-23）：raw_tags 被冲掉后 usage/tags 的
          filter 规则静默产出为空，话术缺历史用量/用户标签。

    Returns:
        (合并后的 response_extract, 被保留的 key 列表)
    """
    new_ext = dict(body_ext or {})
    removed = {k for k, v in new_ext.items() if v in ("", None)}
    new_ext = {k: v for k, v in new_ext.items() if k not in removed}
    preserved: List[str] = []
    referenced: set = set()
    if isinstance(field_transform, dict):
        for tgt, rule in field_transform.items():
            if str(tgt).startswith("_"):
                continue
            if isinstance(rule, dict):
                referenced.add(str(rule.get("from") or tgt))
    for key in list(STD_DOMAIN_KEYS) + sorted(referenced):
        if key in old_ext and key not in new_ext and key not in removed:
            new_ext[key] = old_ext[key]
            preserved.append(key)
    return new_ext, preserved


def _explicit_removed_slots(body_ext: Any) -> set:
    """本次提交里被显式置空（""/None）的 response_extract 槽位 = 有意删除。"""
    if not isinstance(body_ext, dict):
        return set()
    return {k for k, v in body_ext.items() if v in ("", None)}


def _added_slot_notes(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    """对比补齐前后的 api_nodes，列出新增的 response_extract 槽位（回显给保存者）。"""
    notes: List[str] = []
    for name, node in (after or {}).items():
        if str(name).startswith("_") or not isinstance(node, dict):
            continue
        old_node = (before or {}).get(name)
        old_ext = old_node.get("response_extract") or {} if isinstance(old_node, dict) else {}
        for key, path in (node.get("response_extract") or {}).items():
            if key not in old_ext:
                notes.append(f"{name}: 补回映射 {key} → {path}")
    return notes


def _autofill_api_nodes(
    api_nodes: Dict[str, Any],
    province: str,
    intent: str,
    removed_by_node: Optional[Dict[str, set]] = None,
) -> tuple:
    """保存即补齐：按配置自证线索补回缺失的映射槽位/标准域，并规范化字段名。

    与 :func:`_guard_response_extract` 的分工：守护只能保住「旧配置里还有、这次提交漏了」
    的槽位；如果 ES 上那份配置本身早就缺了（如 raw_tags 被历史保存冲掉），守护无从补起，
    运营重新编辑保存一次也修不好，只能另去点「修复」。这里在保存路径上复用「修复」的同一套
    逻辑（management.config_agent.repairer），让编辑保存本身就把配置补到健全状态。

    只增不删；``removed_by_node`` 里本次被显式置空的槽位视为有意删除，补齐后再摘掉，
    保证运营仍能真正删除某条映射。

    保存的同时把存量 ``from: raw_xxx`` 中间集写法就地转成直连写法（等价变换，
    见 inline_intermediate_slots），让"两处同名才成立"的脆弱契约随编辑逐步消失。

    Returns:
        (补齐后的 api_nodes, 变更说明列表, 无法自动补齐的问题列表)
    """
    from management.config_agent.repairer import inline_intermediate_slots, repair_api_nodes

    try:
        rep = repair_api_nodes(api_nodes, province, intent)
    except Exception as exc:  # noqa: BLE001 - 补齐失败不能阻断保存
        logger.warning(f"[接口保存补齐] {province}/{intent} 补齐跳过: {exc}")
        return api_nodes, [], []

    cfg = rep["config"]
    for node_name, removed in (removed_by_node or {}).items():
        node = cfg.get(node_name)
        if not isinstance(node, dict) or not isinstance(node.get("response_extract"), dict):
            continue
        for key in removed:
            node["response_extract"].pop(key, None)
    notes = _added_slot_notes(api_nodes, cfg)
    try:
        notes.extend(inline_intermediate_slots(cfg))
    except Exception as exc:  # noqa: BLE001 - 简化失败同样不阻断保存
        logger.warning(f"[接口保存补齐] {province}/{intent} 中间集简化跳过: {exc}")
    # usage.* 带「实际」前缀的 include_keys 自动补 field_rename → 去前缀规范名，
    # 让产出键对齐话术模板占位符（根治「补了 include 漏写 rename」的漂移）。
    try:
        from utils.field_naming import autofill_usage_renames

        _ur = autofill_usage_renames(cfg)
        if _ur:
            notes.append("已为用量字段自动补齐「实际→规范名」映射：" + "；".join(_ur))
    except Exception as exc:  # noqa: BLE001 - 补齐失败不阻断保存
        logger.warning(f"[接口保存补齐] {province}/{intent} usage 重命名补齐跳过: {exc}")
    return cfg, notes, list(rep["unfixed"])


def _guard_api_nodes_package(
    old_nodes: Dict[str, Any],
    new_nodes: Dict[str, Any],
) -> tuple:
    """整份 api_nodes 保存的防丢失守护（逐节点复用 :func:`_guard_response_extract`）。

    单节点保存（PUT /api/interfaces/...）早已有守护，但整份保存
    （PUT /api/skills/{province}/{intent}/api_nodes，技能管理页与「模板匹配与填槽设置」
    的保存都走这里）是整字典替换：前端表单只要没完整回显某个中间槽位
    （raw_tags 之类非标准域），一次保存就把它冲掉，usage/tags 随即静默变空。
    北京「用户消费信息未生效」正是这条路径漏防的结果，故在 choke point 补齐。

    同时保留旧配置里以 ``_`` 开头的顶层元数据键（如 ``_domain_fallbacks``），
    避免只提交接口节点的调用方把空域兜底配置一并抹掉。

    Returns:
        (合并后的 api_nodes, 保留项说明列表)
    """
    merged = dict(new_nodes or {})
    notes: List[str] = []
    for key, val in (old_nodes or {}).items():
        if str(key).startswith("_") and key not in merged:
            merged[key] = val
            notes.append(f"顶层元数据[{key}]")
    for name, cfg in list(merged.items()):
        if str(name).startswith("_") or not isinstance(cfg, dict):
            continue
        old_cfg = (old_nodes or {}).get(name)
        if not isinstance(old_cfg, dict) or "response_extract" not in cfg:
            continue
        if (cfg.get("source_type") or old_cfg.get("source_type") or "api") == "direct":
            continue
        ft = cfg.get("field_transform") or old_cfg.get("field_transform") or {}
        new_ext, preserved = _guard_response_extract(
            old_cfg.get("response_extract") or {},
            cfg.get("response_extract") or {},
            ft,
        )
        if preserved:
            merged[name] = {**cfg, "response_extract": new_ext}
            notes.append(f"{name}: {', '.join(preserved)}")
    return merged, notes


@router.put("/api/interfaces/{province}/{intent}/{api_name}")
async def update_interface(
    province: str, intent: str, api_name: str,
    body: Dict[str, Any], request: Request,
):
    """新建或更新单个接口节点（需同省份或本部权限）"""
    check_province_write(request, province)
    pkg = skill_registry.get(province, intent)
    if pkg is None:
        raise HTTPException(404, f"技能包不存在: {province}/{intent}")
    api_nodes_cfg = dict(pkg.config.get("api_nodes", {}))
    existing = api_nodes_cfg.get(api_name) or {}
    merged = {**existing, **body}

    # ── 直传样例归一守护（生产 ES 写路径自愈）─────────────────────
    # 运营常把「整请求体」或网关 {"params":{...}} 包裹体直接粘进直传样例，导致
    # ES 里存成带包裹层的 mock 与错位的 passthrough_fields，调色板/预览取不到内层字段。
    # 保存时归一为 extra_info 本体并清理脏透传字段，保证入库即干净。
    _direct_notes = _clean_direct_node_for_save(merged)
    if _direct_notes:
        logger.warning(
            f"[接口保存守护] {province}/{intent}/{api_name} 直传样例已归一: {_direct_notes}"
        )

    # ── 重命名字段名规范化守护 ──────────────────────────────────
    # field_rename / _unit_conversions.new_field 的目标名若含畸形括号（双括号、全半角混用），
    # 运行时数据键将与模板子字段占位符无法同名对齐 → 槽位取不到值。保存时统一规范化。
    if isinstance(merged.get("field_transform"), dict):
        _renames_fixed = _normalize_field_transform_renames(merged["field_transform"])
        if _renames_fixed:
            logger.warning(
                f"[接口保存守护] {province}/{intent}/{api_name} 重命名目标字段名已规范化: "
                f"{_renames_fixed}"
            )

    # ── 标准域映射防丢失守护 ────────────────────────────────────
    # 背景：response_extract 是整字典替换（非增量合并）。前端「智能分析/自动映射」
    # 会用 LLM 重新生成映射并回填表单，若 LLM 输出漏掉某个标准域（如
    # recommended_packages），一次保存就会把 ES 里原有映射静默冲掉——这正是
    # 北京「套餐推荐」推荐产品丢失、只回 1 条兜底话术的事故根因（2026-07）。
    # 规则：接口查询模式下，旧配置已映射的标准域 key 若在新配置中缺失，自动保留
    # 旧映射并告警；确需删除时，将该 key 显式置为 ""（空串）即可真正移除。
    preserved_domains: List[str] = []
    if "response_extract" in body and (merged.get("source_type") or "api") != "direct":
        ft_after = merged.get("field_transform") or existing.get("field_transform") or {}
        new_ext, preserved_domains = _guard_response_extract(
            existing.get("response_extract") or {},
            body.get("response_extract") or {},
            ft_after,
        )
        merged["response_extract"] = new_ext
        if preserved_domains:
            logger.warning(
                f"[接口保存守护] {province}/{intent}/{api_name} 新映射缺失标准域/被引用中间槽位 "
                f"{preserved_domains}，已自动保留旧映射（如需删除请将该 key 置为空串）"
            )

    if api_name not in api_nodes_cfg:
        merged["created_by"] = get_operator(request, fallback=body.get("created_by", "admin"))
        merged.setdefault("created_at", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    else:
        merged["updated_by"] = get_operator(request)
        merged["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    api_nodes_cfg[api_name] = merged

    # ── 保存即补齐 ──────────────────────────────────────────────
    # 守护只能保住"旧配置里还有、这次提交漏了"的槽位；若线上那份本就残缺（历史保存
    # 已把 raw_tags 冲掉），重新编辑保存也修不好。这里对本节点复用「修复」逻辑，
    # 按 field_transform 引用关系 + mock_response 自证补回缺失槽位。
    filled, filled_notes, unfixed = _autofill_api_nodes(
        {api_name: merged}, province, intent,
        {api_name: _explicit_removed_slots(body.get("response_extract"))},
    )
    api_nodes_cfg[api_name] = filled.get(api_name, merged)
    if filled_notes:
        logger.warning(f"[接口保存补齐] {province}/{intent}/{api_name} 配置已自动修正: {filled_notes}")
    if unfixed:
        logger.warning(f"[接口保存补齐] {province}/{intent}/{api_name} 仍需人工处理: {unfixed}")

    ok = skill_registry.save_api_nodes(
        province, intent, api_nodes_cfg, operator=get_operator(request)
    )
    if not ok:
        raise HTTPException(500, "保存失败")
    msg = "保存成功"
    if _direct_notes:
        msg += f"（直传样例已归一：{'；'.join(_direct_notes)}）"
    if preserved_domains:
        msg += f"（已自动保留标准域映射：{', '.join(preserved_domains)}，如需删除请将其显式置为空串）"
    if filled_notes:
        msg += f"（配置已自动修正：{'；'.join(filled_notes)}）"
    return {"code": 200, "message": msg, "autofilled": filled_notes, "unfixed": unfixed}


@router.delete("/api/interfaces/{province}/{intent}/{api_name}")
async def delete_interface(province: str, intent: str, api_name: str, request: Request):
    """删除单个接口节点（需同省份或本部权限）"""
    check_province_write(request, province)
    pkg = skill_registry.get(province, intent)
    if pkg is None:
        raise HTTPException(404, f"技能包不存在: {province}/{intent}")
    api_nodes_cfg = dict(pkg.config.get("api_nodes", {}))
    if api_name not in api_nodes_cfg:
        raise HTTPException(404, f"接口不存在: {api_name}")
    del api_nodes_cfg[api_name]
    ok = skill_registry.save_api_nodes(
        province, intent, api_nodes_cfg, operator=get_operator(request)
    )
    if not ok:
        raise HTTPException(500, "删除失败")
    return {"code": 200, "message": "删除成功"}


@router.patch("/api/interfaces/{province}/{intent}/{api_name}/status")
async def patch_interface_status(
    province: str, intent: str, api_name: str,
    body: Dict[str, Any], request: Request,
):
    """切换接口启用状态（需同省份或本部权限）"""
    check_province_write(request, province)
    enabled_val = body.get("enabled")
    if enabled_val is None:
        raise HTTPException(400, "enabled 字段必填")
    pkg = skill_registry.get(province, intent)
    if pkg is None:
        raise HTTPException(404, f"技能包不存在: {province}/{intent}")
    api_nodes_cfg = dict(pkg.config.get("api_nodes", {}))
    if api_name not in api_nodes_cfg:
        raise HTTPException(404, f"接口不存在: {api_name}")
    api_nodes_cfg[api_name]["enabled"] = bool(enabled_val)
    ok = skill_registry.save_api_nodes(
        province, intent, api_nodes_cfg, operator=get_operator(request)
    )
    if not ok:
        raise HTTPException(500, "保存失败")
    return {"code": 200, "message": "状态已更新"}


# ── 用户与鉴权 ────────────────────────────────────────────────

@router.get("/api/auth/me")
async def auth_me(request: Request):
    """鉴权探针：经灵运 satoken 校验后返回当前用户信息。"""
    user = getattr(request.state, "lingyun_user", None)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="未鉴权或鉴权中间件未注入用户信息（请携带 satoken 请求头）",
        )
    ui = user.user_info
    return {
        "code": 200,
        "data": {
            "id": ui.id,
            "username": ui.username,
            "phone": ui.phone,
            "deptId": ui.dept_id,
            "deptName": ui.dept_name,
            "roles": [{"roleId": r.role_id, "roleName": r.role_name} for r in user.role_list],
        },
    }


@router.get("/api/user/me")
async def get_user_me(request: Request):
    """返回当前登录用户的运营权限信息（前端用于权限控制和操作人填充）。"""
    from utils.auth_utils import _HQ_DEPT_NAME
    user = getattr(request.state, "lingyun_user", None)
    if user is None:
        return {
            "code": 200,
            "data": {
                "id": "", "username": "admin", "phone": "",
                "deptName": "", "province": "", "isHQ": True, "roles": [],
            },
        }
    ui = user.user_info
    dept_name = (ui.dept_name or "").strip()
    is_hq = dept_name == _HQ_DEPT_NAME
    province = None if is_hq else get_user_province(request)
    return {
        "code": 200,
        "data": {
            "id": ui.id,
            "username": ui.username,
            "phone": ui.phone,
            "deptName": dept_name,
            "province": province or "",
            "isHQ": is_hq,
            "roles": [{"roleId": r.role_id, "roleName": r.role_name} for r in user.role_list],
        },
    }


# ── 配置版本管理（回滚 / 历史版本）─────────────────────────────

class RollbackRequest(BaseModel):
    config_type: str = Field(..., description="biz_config 或 api_nodes")
    version: int = Field(..., description="目标版本号")


@router.post("/api/skills/{province}/{intent}/rollback")
async def rollback_config(province: str, intent: str, body: RollbackRequest, request: Request):
    """回滚到指定历史版本（回滚即生效，自动广播所有实例）"""
    check_province_write(request, province)
    operator = get_operator(request, fallback="admin")
    ok, msg = skill_registry.rollback_config(
        province, intent, body.config_type,
        target_version=body.version,
        operator=operator,
    )
    if not ok:
        raise HTTPException(400, msg)
    return {"code": 200, "message": msg}


@router.get("/api/skills/{province}/{intent}/versions")
async def get_config_versions(province: str, intent: str, config_type: str = "biz_config"):
    """获取配置历史版本列表（published + archived）"""
    from services.es_config_store import es_config_store
    versions = es_config_store.get_versions(province, intent, config_type)
    return {"code": 200, "data": versions}


@router.get("/api/skills/{province}/{intent}/version_info")
async def get_version_info(province: str, intent: str):
    """获取当前 Skill 的 biz_config 和 api_nodes 最新发布版本信息"""
    from services.es_config_store import es_config_store
    biz_info = es_config_store.get_current_version_info(province, intent, "biz_config")
    api_info = es_config_store.get_current_version_info(province, intent, "api_nodes")
    return {
        "code": 200,
        "data": {
            "biz_config": biz_info,
            "api_nodes":  api_info,
        }
    }


@router.post("/api/migrate")
async def migrate_to_es(request: Request):
    """一次性将本地配置文件迁移到 ES（首次部署时调用，幂等）"""
    operator = get_operator(request, fallback="admin")
    result = skill_registry.migrate_local_to_es(operator=operator)
    return {"code": 200, "data": result}


# ── 健康检查 & 环境信息 ───────────────────────────────────────

@router.get("/health")
async def health():
    return {
        "status": "ok",
        "skills": len(skill_registry.list_all()),
        "ts": int(time.time()),
    }


@router.get("/api/config-source")
async def get_config_source():
    """配置来源诊断：每个技能包实际从哪里加载（redis/es/local）+ ES/Redis 可用性。

    生产排查用：浏览器直接访问 /znhs-gray/api/config-source 即可确认
    「为什么还在读本地配置」——ES 未发布过、index 缺失、连接失败等一目了然。
    """
    from services.es_config_store import es_config_store, INDEX_CONFIGS, INDEX_META
    from utils.skill_runtime import IS_DEV

    skills = [
        {
            "key":           p["key"],
            "version":       p["version"],
            "config_source": (p.get("meta") or {}).get("config_source", "unknown"),
            "loaded_at":     (p.get("meta") or {}).get("loaded_at"),
        }
        for p in skill_registry.list_all()
    ]

    # ES 状态与已发布配置数
    es_info: Dict[str, Any] = {"enabled": es_config_store.enabled}
    if es_config_store.enabled:
        published = es_config_store.get_all_published_versions()
        # 直接按字段搜 configs 索引，绕过 meta 指针，确认「ES 里到底有没有数据」
        try:
            raw = es_config_store.load_all_published()
            raw_summary = {k: sorted(v.keys()) for k, v in raw.items()}
        except Exception as e:
            raw_summary = {"error": str(e)}
        es_info.update({
            "index_configs":            INDEX_CONFIGS,
            "index_meta":               INDEX_META,
            "doc_type":                 getattr(es_config_store, "_doc_type", None),
            "meta_published_count":     len(published),
            "meta_published_versions":  published,
            "configs_search_result":    raw_summary,
        })

    # Redis 状态
    try:
        from services.redis_config_bus import redis_config_bus
        redis_info = {"enabled": redis_config_bus.enabled}
    except Exception as e:
        redis_info = {"enabled": False, "error": str(e)}

    by_source: Dict[str, int] = {}
    for s in skills:
        by_source[s["config_source"]] = by_source.get(s["config_source"], 0) + 1

    return {
        "code": 200,
        "data": {
            "is_dev_mode": IS_DEV,
            "summary_by_source": by_source,
            "skills": skills,
            "elasticsearch": es_info,
            "redis": redis_info,
            "hint": (
                "① meta_published_count>0 但 configs_search_result 有数据、技能仍 local → "
                "meta 指针与 configs 不一致，已加字段搜索兜底，重启即可从 ES 读。"
                "② meta_published_count=0 且 configs_search_result 也为空 → ES 确实无数据，"
                "需在 SkillManager 保存/发布一次。"
                "③ configs_search_result 有数据但 doc_type 与索引真实 type 不符 → 看启动日志"
                "[ESConfigStore] 的『校准 doc_type』行是否生效。"
            ),
        },
    }


@router.post("/api/skill-meta/sync")
async def sync_skill_meta(force: bool = False):
    """手动触发 skill 结构化信息（skill_meta）存量迁移。

    扫描 ES 中已有 api_nodes/biz_config 的技能包，为缺少 skill_meta 的
    自动生成结构化信息并写入 ES（config_type=skill_meta）。
    force=true 时对已存在的 skill_meta 也重建（字段结构升级时用）。
    完成后重载 skill_registry，使列表立即反映新的 skill_meta。
    """
    from services.skill_meta_service import sync_skill_meta_to_es

    summary = sync_skill_meta_to_es(force=force)
    if summary.get("created"):
        try:
            skill_registry.initialize()
        except Exception as e:
            summary.setdefault("warnings", []).append(f"registry 重载失败: {e}")
    return {"code": 200, "data": summary}


@router.get("/api/skill-meta/{province}/{intent}")
async def get_skill_meta_api(province: str, intent: str):
    """查看单个技能包的 skill_meta（Redis → ES），排查列表展示问题用。"""
    from services.skill_meta_service import get_skill_meta

    meta = get_skill_meta(province, intent)
    return {
        "code": 200,
        "data": {
            "province": province,
            "intent": intent,
            "found": meta is not None,
            "skill_meta": meta,
        },
    }


@router.get("/env")
async def get_environment_info():
    """获取当前运行环境信息"""
    from utils.env_config import (
        get_environment, get_service_prefix, get_gray_ui_prefix,
        is_gray_spa_ingress_restore, is_auth_enabled, get_auth_client_env,
    )
    import os
    from pathlib import Path

    env = get_environment()
    svc = get_service_prefix()
    dist_dir = str(Path(__file__).resolve().parents[1] / "frontend" / "dist")
    return {
        "environment": env,
        "environment_display": env.upper(),
        "service_prefix": svc,
        "gray_ui_prefix": get_gray_ui_prefix() or None,
        "gray_spa_ingress_restore": is_gray_spa_ingress_restore(),
        "auth_enabled": is_auth_enabled(),
        "auth_client_env": get_auth_client_env(env),
        "auth_env": env,
        "frontend_dist_dir": "dist",
        "frontend_path": dist_dir,
        "dist_exists": os.path.exists(dist_dir),
        "port": int(os.getenv("PORT", "8000")),
        "timestamp": int(time.time()),
        "version": "2.0.0",
    }
