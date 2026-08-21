#!/usr/bin/env python3
"""
template_library.py — 内置 Skill 配置模板库

提供若干常用场景的导入模板，前端可直接选择菜单加载，免去手填。
每个模板的结构与 code_generator.SkillPackageGenerator 接受的字典一致。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

# ── 通用 Mock 响应 ──────────────────────────────────────
_DEFAULT_MOCK = {
    "rtnCode": "0",
    "rtnMsg": "成功",
    "bean": {
        "mainoffer": {
            "offerName": "99元畅享套餐",
            "initFee": 99,
            "offerFlow": "20",
            "offerVoice": "200",
        },
        "recommend_results": [
            {"rank": 1, "offerName": "129元5G套餐", "initFee": 129,
             "offerFlow": "50", "offerVoice": "300"},
            {"rank": 2, "offerName": "159元尊享套餐", "initFee": 159,
             "offerFlow": "80", "offerVoice": "500"},
        ],
        "tags": {"近3月流量饱和度": "0.85", "近3月平均月消费": "99.00"},
    },
}


# ── 模板 1: 套餐推荐（默认通用模板）────────────────────
TPL_PACKAGE_RECOMMEND: Dict[str, Any] = {
    "meta": {
        "province": "shandong",
        "province_name": "山东",
        "intent": "套餐推荐",
        "description": "山东 · 套餐推荐 Skill",
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
        "request_template": {"phone": "{{PHONE}}", "intent": "{{INTENT}}"},
        "response_extract": {
            "recommended_packages": "bean.recommend_results",
            "current_package": "bean.mainoffer",
        },
        # 直连映射：from 直接写响应路径，不再建 raw_xxx 中间槽位再引用
        # （中间集要求 response_extract 与 from 两处同名才成立，任一边丢失即静默失效）
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
        "mock_response": _DEFAULT_MOCK,
    },
    "strategy": {
        "default_strategy": "direct",
        "top_n": 3,
        "max_script_length": 100,
        "max_parallel_scripts": 3,
    },
    "templates": [
        {
            "template_name": "套餐推荐主话术",
            "stage": "推荐环节",
            "template_content": (
                "您好，您当前套餐为{cur_brief}，根据您近期使用情况{usage_line}，"
                "为您推荐{pkg_brief}套餐，{diff_str}，即可享受更多权益。"
            ),
            "script_requirement": "口语自然、像真人客户经理，别用官话套话；结合用户用量/痛点，用推荐套餐真实值说清好处并做前后对比；只讲有数据支撑的卖点，不夸大；80字以内，结尾一句自然的办理引导。",
            "linked_vars": ["cur_brief", "pkg_brief", "diff_str", "usage_line"],
        },
        {
            "template_name": "切入环节话术",
            "stage": "切入环节",
            "template_content": "您好，看到您近期流量/语音使用较多，为您简单介绍下{pkg_brief}套餐。",
            "script_requirement": "像真人坐席自然开口，一句话结合近期用量切入、点出关注点并过渡到产品；口语化、50字以内，不喊口号。",
            "linked_vars": ["pkg_brief", "usage_line"],
        },
    ],
}

# ── 模板 2: 流量包推荐 ─────────────────────────────────
TPL_FLOW_RECOMMEND: Dict[str, Any] = {
    "meta": {
        "province": "beijing",
        "province_name": "北京",
        "intent": "流量包推荐",
        "description": "北京 · 流量包推荐 Skill",
        "version": "1.0.0",
        "author": "ops-team",
    },
    "api": {
        "name": "beijing_flow_api",
        "comment": "北京流量包推荐接口",
        "url": "",
        "method": "POST",
        "headers": {"Content-Type": "application/json"},
        "timeout": 20,
        "mock_mode": True,
        "request_template": {"phone": "{{PHONE}}", "intent": "{{INTENT}}"},
        "response_extract": {
            "recommended_packages": "bean.flow_pkgs",
            "current_package": "bean.cur_flow_pkg",
        },
        "mock_response": {
            "rtnCode": "0",
            "bean": {
                "cur_flow_pkg": {"offerName": "10元1G日包", "initFee": 10, "offerFlow": "1"},
                "flow_pkgs": [
                    {"rank": 1, "offerName": "30元10G月包", "initFee": 30, "offerFlow": "10"},
                ],
            },
        },
    },
    "strategy": {"default_strategy": "direct", "top_n": 2, "max_script_length": 80},
    "templates": [
        {
            "template_name": "流量告急提醒",
            "stage": "切入环节",
            "template_content": "您本月流量使用较多，建议加办{pkg_brief}流量包。",
            "script_requirement": "口语自然、有紧迫感但不夸大，用真实用量点出流量吃紧、自然引出流量包；50字以内。",
            "linked_vars": ["pkg_brief", "usage_line"],
        },
        {
            "template_name": "流量包推荐主话术",
            "stage": "推荐环节",
            "template_content": (
                "为您推荐{pkg_brief}，{diff_str}，办理后流量更够用。"
            ),
            "script_requirement": "口语化，用真实差异说清办理后流量更够用、更划算；只讲有数据支撑的点；60字以内，结尾一句办理引导。",
            "linked_vars": ["pkg_brief", "diff_str"],
        },
    ],
}

# ── 模板 3: 续约挽留 ───────────────────────────────────
TPL_RENEW_RETAIN: Dict[str, Any] = {
    "meta": {
        "province": "guangdong",
        "province_name": "广东",
        "intent": "续约挽留",
        "description": "广东 · 续约挽留 Skill",
        "version": "1.0.0",
        "author": "ops-team",
    },
    "api": {
        "name": "guangdong_retain_api",
        "comment": "广东续约挽留接口",
        "url": "",
        "method": "POST",
        "headers": {"Content-Type": "application/json"},
        "timeout": 30,
        "mock_mode": True,
        "request_template": {"phone": "{{PHONE}}", "intent": "{{INTENT}}"},
        "response_extract": {
            "recommended_packages": "bean.retain_offers",
            "current_package": "bean.mainoffer",
        },
        "mock_response": _DEFAULT_MOCK,
    },
    "strategy": {"default_strategy": "direct", "top_n": 2, "max_script_length": 100},
    "templates": [
        {
            "template_name": "挽留切入话术",
            "stage": "切入环节",
            "template_content": "您好，注意到您即将到期，我们为您准备了专属续约权益{pkg_brief}。",
            "script_requirement": "礼貌热情、像真人客户经理，点出到期并自然带出专属续约权益、营造被重视感；60字以内，不夸大。",
            "linked_vars": ["pkg_brief"],
        },
        {
            "template_name": "续约推荐话术",
            "stage": "推荐环节",
            "template_content": (
                "续约{pkg_brief}套餐可享{diff_str}，更划算。"
            ),
            "script_requirement": "突出续约真实优惠、做前后对比放大划算感，不编造不夸大；口语化、70字以内，结尾一句办理引导。",
            "linked_vars": ["pkg_brief", "diff_str"],
        },
    ],
}


# ── 注册表 ─────────────────────────────────────────────
TEMPLATE_REGISTRY: List[Dict[str, Any]] = [
    {
        "id": "package_recommend",
        "name": "套餐推荐（通用）",
        "category": "套餐推荐",
        "icon": "📦",
        "description": "标准套餐推荐场景，含两条话术（切入 + 推荐）",
        "tags": ["推荐", "通用"],
        "template": TPL_PACKAGE_RECOMMEND,
    },
    {
        "id": "flow_recommend",
        "name": "流量包推荐",
        "category": "流量推荐",
        "icon": "📶",
        "description": "面向流量超量用户，推荐加办流量包",
        "tags": ["流量", "推荐"],
        "template": TPL_FLOW_RECOMMEND,
    },
    {
        "id": "renew_retain",
        "name": "续约挽留",
        "category": "挽留",
        "icon": "🤝",
        "description": "面向到期/异网倾向用户的续约挽留场景",
        "tags": ["挽留", "续约"],
        "template": TPL_RENEW_RETAIN,
    },
]


def list_templates() -> List[Dict[str, Any]]:
    """返回模板菜单（不含完整 template，节省传输）。"""
    return [
        {k: v for k, v in t.items() if k != "template"}
        for t in TEMPLATE_REGISTRY
    ]


def get_template(template_id: str) -> Dict[str, Any] | None:
    """根据 ID 取完整模板（深拷贝，防止前端修改影响内置模板）。"""
    for t in TEMPLATE_REGISTRY:
        if t["id"] == template_id:
            return deepcopy(t["template"])
    return None
