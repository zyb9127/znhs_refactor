#!/usr/bin/env python3
"""
code_generator.py — Skill 包代码生成器

从标准导入模板（JSON）自动生成完整的话术智能体 skill 包：
  - config/api_nodes.json
  - config/biz_config.json
  - _meta.json

说明：main_flow.py 与 SKILL.md 已不再生成（运行态统一走通用管道，
省份脚本为死产物；历史 skill 包中已存在的文件不受影响）。

导入模板格式（skill_template.json）:
{
  "meta": {
    "province": "shandong",          # 省份代码（英文）
    "province_name": "山东",          # 省份名称（中文，可选）
    "intent": "套餐推荐",              # 业务意图（与 skills-runtime 目录名一致）
    "description": "...",             # 描述
    "version": "1.0.0",
    "author": "ops-team"
  },
  "api": {
    "name": "shandong_package_api",  # 接口节点名称（唯一键）
    "comment": "营销推荐接口",
    "url": "http://...",
    "method": "POST",
    "headers": {"Content-Type": "application/json"},
    "timeout": 30,
    "max_retries": 2,
    "request_template": {...},
    "response_extract": {
      "recommended_packages": "bean.recommend_results",
      "current_package": "bean.mainoffer"
    },
    # 可选，字段转换规则；from 直接写响应路径（如 bean.tags），无需中间槽位
    "field_transform": {"tags": {"from": "bean.tags", "type": "filter_exclude", "exclude_keys": [...]}},
    "mock_response": {...}           # 可选，Mock 数据
  },
  "strategy": {
    "default_strategy": "direct",
    "top_n": 3,
    "max_script_length": 100,
    "max_parallel_scripts": 3
  },
  "field_aliases": {...},            # 可选，套餐字段别名
  "templates": [                     # 话术模板列表
    {
      "template_name": "套餐推荐话术",
      "scene": "",
      "stage": "切入环节",
      "product_id": "",
      "template_content": "...",
      "prompt_template": "...",      # 可选
      "script_requirement": "..."
    }
  ]
}
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from prompt.script_defaults import DEFAULT_PROMPT_TEMPLATE


# ── 默认字段别名（与 话术智能体-生产 保持一致）──
DEFAULT_FIELD_ALIASES = {
    "_comment": "套餐字段别名（按优先级从高到低列出），ScriptStep 按此映射取值，无需硬编码字段名",
    "pkg_name": ["offerName", "package_name", "productName", "name"],
    "pkg_fee": ["initFee", "monthly_fee", "price", "fee"],
    "pkg_flow": ["offerFlow", "data_quota", "dataGB", "flow"],
    "pkg_voice": ["offerVoice", "voice_quota", "voiceMinutes", "voice"],
}

# ── 默认 Prompt 模板 ──
# 定义已外置到 prompt/script_defaults.py（import DEFAULT_PROMPT_TEMPLATE），此处不再内联。


def _gen_template_id(province: str, intent: str) -> str:
    """生成唯一的 template_id（与 话术智能体-生产 生成逻辑一致）"""
    ts = int(time.time() * 1000)
    suffix = format(hash(f"{province}{intent}{ts}") & 0xFFFFFF, "06x")
    return f"tpl_{province}_{ts}_{suffix}"


class SkillPackageGenerator:
    """从导入模板生成 skill 包的所有文件。"""

    def __init__(self, template: Dict[str, Any]):
        self.tpl = template
        self.meta = template.get("meta", {})
        self.api = template.get("api", {})
        self.strategy = template.get("strategy", {})
        self.field_aliases = template.get("field_aliases", DEFAULT_FIELD_ALIASES)
        self.templates = template.get("templates", [])

        self.province = self.meta.get("province", "unknown")
        self.province_name = self.meta.get("province_name", self.province)
        self.intent = self.meta.get("intent", "套餐推荐")
        self.description = self.meta.get("description", f"{self.province_name}{self.intent}")
        self.version = self.meta.get("version", "1.0.0")
        self.author = self.meta.get("author", "auto-config-agent")

    # ─────────────────────────────────────────────
    # 1. api_nodes.json
    # ─────────────────────────────────────────────
    def gen_api_nodes(self) -> Dict[str, Any]:
        """生成 config/api_nodes.json"""
        api = self.api
        # 快速创建：未配置任何接口内容时生成空 api_nodes，稍后在编辑页补配，
        # 避免落一个 url 为空的占位接口节点导致运行期报错。
        _has_content = bool(
            api and (
                api.get("url") or api.get("mock_response")
                or api.get("request_template") or api.get("response_extract")
                or api.get("field_transform")
                or api.get("source_type") == "direct" or api.get("direct_mode")
            )
        )
        if not _has_content:
            return {}
        node_name = api.get("name") or f"{self.province}_package_api"

        node: Dict[str, Any] = {
            "enabled": True,
            "_comment": api.get("comment", f"{self.province_name}{self.intent}接口"),
            "url": api.get("url", ""),
            "method": api.get("method", "POST"),
            "headers": api.get("headers", {"Content-Type": "application/json"}),
            "timeout": api.get("timeout", 30),
            "max_retries": api.get("max_retries", 2),
            "mock_mode": api.get("mock_mode", False),
            "created_by": "auto_config_agent",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

        # 直传/透传模式：保留 source_type / direct_mode / passthrough_fields，
        # 否则生成后会被当作普通接口查询节点（url 为空 → 运行时报错）。
        if api.get("source_type") == "direct" or api.get("direct_mode"):
            node["source_type"] = "direct"
            node["direct_mode"] = api.get("direct_mode", "mapping")
            node["url"] = ""
            node["mock_mode"] = False
            if node["direct_mode"] == "passthrough":
                node["passthrough_fields"] = api.get("passthrough_fields", []) or []

        if api.get("request_body_wrapper"):
            node["request_body_wrapper"] = api["request_body_wrapper"]
        if api.get("request_template"):
            node["request_template"] = api["request_template"]
        if api.get("response_extract"):
            node["response_extract"] = api["response_extract"]
        if api.get("field_transform"):
            node["field_transform"] = api["field_transform"]
        if api.get("mock_response"):
            node["mock_response"] = api["mock_response"]

        return {node_name: node}

    # ─────────────────────────────────────────────
    # 2. biz_config.json
    # ─────────────────────────────────────────────
    def gen_biz_config(self) -> Dict[str, Any]:
        """生成 config/biz_config.json"""
        strategy = {
            "default_strategy": self.strategy.get("default_strategy", "direct"),
            "top_n": self.strategy.get("top_n", 3),
            "intent_strategies": self.strategy.get("intent_strategies", {}),
            "max_script_length": self.strategy.get("max_script_length", 100),
            "max_parallel_scripts": self.strategy.get("max_parallel_scripts", 3),
        }

        # 话术模板 → script_templates_v2 格式
        script_templates = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for t in self.templates:
            item: Dict[str, Any] = {
                "template_id": t.get("template_id") or _gen_template_id(self.province, self.intent),
                "template_name": t.get("template_name", "话术模板"),
                "intent": self.intent,
                "province": self.province,
                "product_id": t.get("product_id", ""),
                "scene": t.get("scene", ""),
                "stage": t.get("stage", ""),
                "template_content": t.get("template_content", ""),
                "prompt_template": t.get("prompt_template") or DEFAULT_PROMPT_TEMPLATE,
                "linked_vars": t.get("linked_vars", ["current_package", "pkg_brief", "diff_str"]),
                "script_requirement": t.get("script_requirement", ""),
                "linked_apis": t.get("linked_apis", []),
                "status": t.get("status", "online"),
                "created_by": "auto_config_agent",
                "created_at": now_str,
            }
            script_templates.append(item)

        field_aliases = dict(DEFAULT_FIELD_ALIASES)
        if self.field_aliases and self.field_aliases != DEFAULT_FIELD_ALIASES:
            user_fa = {k: v for k, v in self.field_aliases.items() if not k.startswith("_")}
            field_aliases.update(user_fa)

        return {
            "strategy": strategy,
            "field_aliases": field_aliases,
            "script_templates_v2": script_templates,
        }

    # ─────────────────────────────────────────────
    # 3. _meta.json
    # ─────────────────────────────────────────────
    def gen_meta(self) -> Dict[str, Any]:
        """生成 _meta.json（包含 skill_id、status、created_at、updated_at 完整字段）"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        skill_id = f"{self.province}-{self.intent.replace(' ', '-')}"
        return {
            # ── 标识字段 ──────────────────────────────
            "skill_id":   skill_id,
            "name":       skill_id,          # 与 skill_id 保持一致
            "description": self.description,
            # ── 版本与状态 ────────────────────────────
            "version":    self.version,
            "status":     "published",       # 发布即 published；手动下线可改为 offline
            # ── 分类 ─────────────────────────────────
            "type":       "scenario",
            "province":   self.province,
            "scenario_id": self.intent,
            # ── 运行时 ───────────────────────────────
            # entry_script 已随 main_flow.py 停止生成而删除（运行态统一走通用管道）
            "config_files":   ["api_nodes.json", "biz_config.json"],
            "required_plugins": ["plugins.package_diff", "plugins.unit_converter"],
            # ── 作者与时间 ───────────────────────────
            "author":     self.author,
            "created_by": "auto_config_agent",
            "created_at": now,
            "updated_at": now,
            # ── 兼容性 ───────────────────────────────
            "openclaw_compatible": True,
        }

    # ─────────────────────────────────────────────
    # 汇总：生成所有文件内容
    # ─────────────────────────────────────────────
    def generate_all(self) -> Dict[str, Any]:
        """
        返回所有待写入的文件内容（字典）：
        {
            "api_nodes": {...},
            "biz_config": {...},
            "meta": {...},
        }
        """
        return {
            "api_nodes": self.gen_api_nodes(),
            "biz_config": self.gen_biz_config(),
            "meta": self.gen_meta(),
            "province": self.province,
            "intent": self.intent,
        }


# ── 模板格式验证 ─────────────────────────────────
def validate_template(tpl: Dict[str, Any]) -> List[str]:
    """
    验证导入模板格式，返回错误列表（空列表 = 通过）。

    只强制要求 meta.province + meta.intent —— 支持「快速创建」：先建骨架，
    接口与话术模板可留空、稍后在「智能话术配置管理 → 编辑」中补配。
    api / templates 均为可选；仅当填了内容时才做基础格式校验。
    """
    errors: List[str] = []

    # meta —— 唯一强制项：省份 + 意图
    meta = tpl.get("meta", {})
    if not meta:
        errors.append("缺少 meta 字段")
    else:
        if not meta.get("province"):
            errors.append("meta.province 不能为空（省份代码，如 shandong）")
        if not meta.get("intent"):
            errors.append("meta.intent 不能为空（业务意图，如 套餐推荐）")

    # api —— 可选；若填了 url/mock 之外的接口查询模式才提示补 url（不阻断快速创建）
    api = tpl.get("api", {})
    if api:
        is_direct = (
            api.get("source_type") == "direct"
            or bool(api.get("direct_mode"))
        )
        has_any_api_content = bool(
            api.get("url") or api.get("mock_response")
            or api.get("request_template") or api.get("response_extract")
        )
        # 仅当明确按「接口查询模式」填了内容却漏了 url 时才报错；
        # 完全空的 api（快速创建）与直传模式都放行。
        if has_any_api_content and not is_direct \
                and not api.get("url") and not api.get("mock_mode"):
            errors.append("api.url 为空，请填写接口地址或设置 mock_mode=true")

    # templates —— 可选；若填了某条则要求内容非空
    templates = tpl.get("templates", [])
    for i, t in enumerate(templates):
        if not t.get("template_content"):
            errors.append(f"templates[{i}].template_content 不能为空")

    return errors


# ── 示例模板 ─────────────────────────────────────
EXAMPLE_TEMPLATE: Dict[str, Any] = {
    "meta": {
        "province": "shandong",
        "province_name": "山东",
        "intent": "套餐推荐",
        "description": "山东套餐推荐 Skill",
        "version": "1.0.0",
        "author": "ops-team",
    },
    "api": {
        "name": "shandong_package_api",
        "comment": "山东营销推荐接口",
        "url": "http://your-api-host/recommend",
        "method": "POST",
        "headers": {"Content-Type": "application/json"},
        "timeout": 30,
        "max_retries": 2,
        "mock_mode": True,
        "request_template": {
            "phone": "{{PHONE}}",
            "intent": "{{INTENT}}",
        },
        "response_extract": {
            "recommended_packages": "bean.recommend_results",
            "current_package": "bean.mainoffer",
        },
        # 直连映射：from 直接写响应路径，不再建 raw_xxx 中间槽位再引用
        "field_transform": {
            "usage.consumption": {
                "from": "bean.tags", "type": "filter_include",
                "include_keys": ["近3月平均月消费"],
            },
            "tags": {
                "from": "bean.tags", "type": "filter_exclude",
                "exclude_keys": ["近3月平均月消费"],
            },
        },
        "mock_response": {
            "rtnCode": "0",
            "rtnMsg": "成功",
            "bean": {
                "mainoffer": {"offerName": "99元畅享套餐", "initFee": 99, "offerFlow": "20", "offerVoice": "200"},
                "recommend_results": [
                    {"rank": 1, "offerName": "129元5G套餐", "initFee": 129, "offerFlow": "50", "offerVoice": "300"},
                ],
                "tags": {"近3月流量饱和度": "0.85", "近3月平均月消费": "99.00"},
            },
        },
    },
    "strategy": {
        "default_strategy": "direct",
        "top_n": 3,
        "max_script_length": 100,
        "max_parallel_scripts": 3,
    },
    "templates": [
        {
            "template_name": "套餐推荐话术",
            "scene": "",
            "stage": "",
            "product_id": "",
            "template_content": "您好，您当前套餐为{cur_brief}，根据您近期使用情况{usage_line}，为您推荐{pkg_brief}套餐，{diff_str}，即可享受更多权益。",
            "script_requirement": "直接输出话术文本，不需要前缀，口语化，亲切；营销推荐话术贴合用户痛点；字数80字以内。",
            "linked_vars": ["cur_brief", "pkg_brief", "diff_str", "usage_line"],
        },
        {
            "template_name": "切入环节话术",
            "scene": "",
            "stage": "切入环节",
            "product_id": "",
            "template_content": "您好，看到您近期流量/语音使用较多，我们现在有{pkg_brief}套餐，很适合您，给您简单介绍下。",
            "script_requirement": "直接输出话术文本，不需要前缀，口语化；字数50字以内。",
            "linked_vars": ["pkg_brief", "usage_line"],
        },
    ],
}
