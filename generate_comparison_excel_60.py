#!/usr/bin/env python3
"""
基于 znhs_refactor 项目配置，生成 北京/广东/山东 营销活动 + 套餐推荐
"原始话术模板 vs 大模型生成话术" 对比样例 Excel（60 条）。

要求：
- 必须使用项目中的原始话术模板
- 槽位入参模拟真实用户数据
- 大模型生成话术调用 DeepSeek API 真实生成
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List

import csv

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.context import FlowContext
from engine.prompt_builder import build_prompt
from plugins.package_diff import PackageDiff


DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"


@dataclass
class Sample:
    province: str
    province_code: str
    intent: str
    scene: str
    stage: str
    product_id: str
    template_content: str
    prompt: str
    generated_text: str = ""


BASE = os.path.dirname(os.path.abspath(__file__))


def load_json(path: str) -> Dict[str, Any]:
    with open(os.path.join(BASE, path), "r", encoding="utf-8") as f:
        return json.load(f)


def load_csv(path: str) -> List[List[str]]:
    with open(os.path.join(BASE, path), "r", encoding="gbk") as f:
        reader = csv.reader(f)
        return list(reader)


# 加载配置
bj_marketing_cfg = load_json("skills-runtime/beijing/营销推荐/config/biz_config.json")
bj_package_cfg = load_json("skills-runtime/beijing/套餐推荐/config/biz_config.json")
gd_marketing_cfg = load_json("skills-runtime/guangdong/营销活动/config/biz_config.json")
sd_package_cfg = load_json("skills-runtime/shandong/套餐推荐/config/biz_config.json")
sd_marketing_rows = load_csv("话术模板.csv")


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════
def pick_templates(templates: List[Dict], scene: str, n: int = 1, by_pid: bool = True) -> List[Dict]:
    """按 scene 取前 n 条在线模板。"""
    out = []
    seen_pid = set()
    for t in templates:
        if t.get("status") != "online":
            continue
        if scene and t.get("scene") != scene:
            continue
        pid = t.get("product_id", "")
        if by_pid and pid in seen_pid:
            continue
        seen_pid.add(pid)
        out.append(t)
        if len(out) >= n:
            break
    # 补充
    if len(out) < n:
        for t in templates:
            if t.get("status") != "online":
                continue
            if scene and t.get("scene") != scene:
                continue
            if t not in out:
                out.append(t)
            if len(out) >= n:
                break
    return out


def make_usage(data_gb: int, saturation: str, voice: int, avg_fee: int) -> Dict[str, Any]:
    return {
        "data_usage": {"近6月平均流量(GB)": data_gb, "近6月平均流量饱和度": saturation},
        "voice_usage": {"近6月平均主叫时长": voice},
        "consumption": {"近6月平均月消费": avg_fee},
    }


def make_flow_context(
    province_code: str,
    intent: str,
    current_package: Dict[str, Any],
    usage: Dict[str, Any],
    tags: Dict[str, Any],
    user_info: Dict[str, Any],
    user_profile: Dict[str, Any],
    extra_info: Dict[str, Any] = None,
) -> FlowContext:
    return FlowContext(
        phone="13800138000",
        intent=intent,
        province=province_code,
        current_package=current_package,
        usage=usage,
        tags=tags,
        user_info=user_info,
        user_profile=user_profile,
        domain_ext={},
        extra_info=extra_info or {},
    )


def build_prompt_for_template(
    province_code: str,
    intent: str,
    template: Dict[str, Any],
    ctx: FlowContext,
    pkg: Dict[str, Any],
    max_length: int = 120,
) -> str:
    """复用项目 engine.prompt_builder 生成运行时 Prompt。"""
    diff = PackageDiff(ctx.current_package, pkg)
    tpl_prompt = template.get("prompt_template") or template.get("template_content", "")
    tpl_content = template.get("template_content", "")
    linked_vars = template.get("linked_vars", []) or []
    script_requirement = template.get("script_requirement", "") or ""

    return build_prompt(
        user_prompt_tpl=tpl_prompt,
        template_text=tpl_content,
        ctx=ctx,
        pkg=pkg,
        diff=diff,
        linked_vars=linked_vars,
        script_requirement=script_requirement,
        max_length=max_length,
    )


def build_simple_prompt(
    province: str,
    intent: str,
    template_content: str,
    current_package: Dict[str, Any],
    recommended: Dict[str, Any],
    usage: Dict[str, Any],
    user_info: Dict[str, Any],
    extra_fill: Dict[str, Any],
    script_requirement: str = "",
    max_length: int = 120,
) -> str:
    """针对无 linked_vars 的旧格式模板（山东 CSV / 部分模板），手动构造 Prompt。"""
    lines = [
        "你是中国移动营销坐席助手。请基于以下话术模板和用户信息，生成自然、口语化、贴合用户痛点的营销话术。",
        f"\n话术模板：{template_content}",
        f"\n用户当前套餐：{json.dumps(current_package, ensure_ascii=False)}",
        f"推荐产品/活动：{json.dumps(recommended, ensure_ascii=False)}",
        f"历史用量：{json.dumps(usage, ensure_ascii=False)}",
        f"用户基础信息：{json.dumps(user_info, ensure_ascii=False)}",
    ]
    if extra_fill:
        lines.append(f"模板槽位填充值：{json.dumps(extra_fill, ensure_ascii=False)}")
    if script_requirement:
        lines.append(f"\n话术要求：{script_requirement}")
    lines.append(f"\n请直接输出完整话术文本（{max_length}字以内），不要加前缀、标题或解释，并将模板中的占位符替换为上述真实数据。")
    return "\n".join(lines)


def call_deepseek(prompt: str, max_tokens: int = 400, temperature: float = 0.5) -> str:
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": "你是中国移动营销坐席助手。请根据提供的话术模板和用户信息，生成自然、口语化、贴合用户痛点的完整营销话术。必须一次性输出完整话术文本，不要省略、不要中断、不要加前缀标题。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        r = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        return f"[生成失败: {exc}]"


# ═══════════════════════════════════════════════════════════════
# 样例构造
# ═══════════════════════════════════════════════════════════════
SAMPLES: List[Sample] = []


def add_sample(s: Sample):
    SAMPLES.append(s)


# ── 北京 营销活动（12 条）───────────────────────────────────────
bj_mkt_tpls = bj_marketing_cfg.get("script_templates_v2", [])

bj_mkt_scenes = [
    ("意图1-营销活动推荐", 3),
    ("意图1-推荐意图", 2),
    ("意图1-消费稳定客户（查流量、查账单）", 2),
    ("意图3-资源较少用户（查流量、嫌流量不够、流量不够用）", 2),
    ("意图1-不需要会员", 1),
]
for scene, n in bj_mkt_scenes:
    for i, tpl in enumerate(pick_templates(bj_mkt_tpls, scene, n)):
        avg_fee = 60 + i * 10 + random.randint(0, 5)
        ctx = make_flow_context(
            province_code="beijing",
            intent="营销推荐",
            current_package={"offerName": f"北京移动4G畅享套餐{avg_fee}元档", "initFee": avg_fee, "offerFlow": 10 + i * 5, "offerVoice": 100 + i * 50},
            usage=make_usage(data_gb=25 + i * 5, saturation=f"{140 + i * 15}%", voice=120 + i * 30, avg_fee=avg_fee),
            tags={"网龄老客户": "是", "近六个月消费稳定": "是", "流量偏紧": "是" if "资源较少" in scene or "营销活动推荐" in scene else "否"},
            user_info={"星级": random.choice(["四星", "五星"]), "网龄": f"{4 + i}年", "归属地": "北京"},
            user_profile={"流量偏好": random.choice(["日常社交与短视频", "办公+短视频", "刷剧达人"])},
            extra_info={
                "活动名称": "网龄回馈倍享礼",
                "承诺消费门槛": f"{avg_fee + 10}元",
                "合约期": "24个月",
                "每月赠送流量": f"{5 + i * 3}GB",
                "灵活会员券": "1张/月",
            },
        )
        pkg = {"offerId": tpl.get("product_id"), "offerName": "网龄回馈倍享礼", "initFee": 9.9, "offerFlow": 5 + i * 3}
        add_sample(Sample(
            province="北京", province_code="beijing", intent="营销活动",
            scene=tpl.get("scene", ""), stage=tpl.get("stage", ""), product_id=tpl.get("product_id", ""),
            template_content=tpl.get("template_content", ""),
            prompt=build_prompt_for_template("beijing", "营销推荐", tpl, ctx, pkg),
        ))

# ── 北京 套餐推荐（8 条）────────────────────────────────────────
bj_pkg_tpls_online = [t for t in bj_package_cfg.get("script_templates_v2", []) if t.get("status") == "online"]
base_bj_pkg_tpl = bj_pkg_tpls_online[0] if bj_pkg_tpls_online else None
if base_bj_pkg_tpl:
    bj_pkg_cases = [
        ("畅享套餐59元档", 59, 20, 200, 35, "175%", 180, 66, "视频类应用为主", "五星", 8),
        ("畅享套餐79元档", 79, 30, 300, 45, "150%", 220, 85, "办公+短视频", "五星", 6),
        ("畅享套餐39元档", 39, 10, 100, 22, "220%", 80, 45, "社交聊天为主", "四星", 4),
        ("4G飞享套餐58元档", 58, 15, 150, 28, "187%", 120, 62, "经常出差", "四星", 7),
        ("5G智享套餐128元档", 128, 30, 500, 55, "183%", 450, 135, "商务通话多", "五星", 10),
        ("校园套餐29元档", 29, 10, 100, 18, "180%", 90, 35, "学生党", "三星", 2),
        ("畅享套餐99元档", 99, 40, 400, 60, "150%", 350, 105, "家庭共享", "五星", 9),
        ("5G优享套餐199元档", 199, 60, 1000, 75, "125%", 900, 210, "高端商务", "五星", 12),
        ("畅享套餐69元档", 69, 25, 250, 40, "160%", 200, 75, "综合使用", "四星", 5),
        ("5G极速套餐159元档", 159, 50, 700, 65, "130%", 650, 170, "重度用户", "五星", 11),
    ]
    for cur_name, cur_fee, cur_flow, cur_voice, data_gb, sat, voice, avg_fee, profile, star, years in bj_pkg_cases:
        rec_fee = cur_fee + (30 if cur_fee < 100 else 50)
        rec_flow = cur_flow + (20 if cur_flow < 50 else 30)
        rec_voice = cur_voice + (100 if cur_voice < 500 else 200)
        ctx = make_flow_context(
            province_code="beijing",
            intent="套餐推荐",
            current_package={"offerName": cur_name, "initFee": cur_fee, "offerFlow": cur_flow, "offerVoice": cur_voice},
            usage=make_usage(data_gb=data_gb, saturation=sat, voice=voice, avg_fee=avg_fee),
            tags={"高频高额超套客户": "是" if int(sat.replace("%", "")) > 150 else "否", "是否老旧套餐": "是" if "4G" in cur_name else "否"},
            user_info={"星级": star, "网龄": f"{years}年"},
            user_profile={"流量偏好": profile},
        )
        pkg = {
            "offerId": "BJ-REC-001",
            "offerName": cur_name.replace("套餐", "升级套餐"),
            "initFee": rec_fee, "offerFlow": rec_flow, "offerVoice": rec_voice,
            "recommendation_reason": "流量更充足，更匹配您的使用习惯",
        }
        add_sample(Sample(
            province="北京", province_code="beijing", intent="套餐推荐",
            scene=base_bj_pkg_tpl.get("scene", ""), stage=base_bj_pkg_tpl.get("stage", ""),
            product_id=base_bj_pkg_tpl.get("product_id", ""),
            template_content=base_bj_pkg_tpl.get("template_content", ""),
            prompt=build_prompt_for_template("beijing", "套餐推荐", base_bj_pkg_tpl, ctx, pkg),
        ))

# ── 广东 营销活动（8 条）────────────────────────────────────────
gd_mkt_tpls = gd_marketing_cfg.get("script_templates_v2", [])
# 广东营销活动只有 6 条模板，扩展为 10 条（复用前 4 条模板，使用不同用户数据）
gd_mkt_extended = list(gd_mkt_tpls) + gd_mkt_tpls[:4]
for idx, tpl in enumerate(gd_mkt_extended):
    product_id = tpl.get("product_id", "")
    variant = idx // len(gd_mkt_tpls)  # 0 或 1，用于区分复用批次
    if product_id == "流量":
        extra = {"优惠期": 12, "月费": 19 + variant * 5, "套内流量": 10 + variant * 5, "赠送流量": 10 + variant * 5, "总流量": 20 + variant * 10}
        cur = {"offerName": "广东移动4G流量王", "initFee": 39 + variant * 10, "offerFlow": 10 + variant * 5, "offerVoice": 100}
    elif product_id == "全家享":
        extra = {"宽带月租": 30, "返还月数": 12, "每月返还": 15 + variant * 5, "最高返还": 180 + variant * 60, "优惠后月租": 15 - variant * 5, "合约期": 24}
        cur = {"offerName": "广东移动全家享套餐", "initFee": 89 + variant * 10, "offerFlow": 30 + variant * 10, "offerVoice": 300}
    elif product_id == "提速":
        extra = {"提速档位": 1000, "月费": 10 + variant * 5, "优惠期": 24, "保底套餐": 89 + variant * 10}
        cur = {"offerName": "广东移动宽带提速包", "initFee": 30, "offerFlow": 0, "offerVoice": 0, "bandwidth": 300 + variant * 200}
    elif product_id == "套餐":
        extra = {"优惠期": 12, "月费": 39 + variant * 10, "套内流量": 20 + variant * 10, "语音分钟": 200 + variant * 50, "赠送流量": 10 + variant * 5, "总流量": 30 + variant * 15}
        cur = {"offerName": "广东移动畅享套餐", "initFee": 59 + variant * 10, "offerFlow": 15 + variant * 5, "offerVoice": 150 + variant * 50}
    elif product_id == "升":
        extra = {"优惠期": 6, "月费": 59 + variant * 10, "套内流量": 30 + variant * 10, "语音分钟": 300 + variant * 50, "赠送流量": 20 + variant * 5, "总流量": 50 + variant * 15}
        cur = {"offerName": "广东移动升档体验包", "initFee": 39 + variant * 10, "offerFlow": 10 + variant * 5, "offerVoice": 100 + variant * 50}
    else:
        extra = {"优惠期": 3, "月费": 9 + variant * 5, "套内流量": 5 + variant * 3, "语音分钟": 50 + variant * 20, "赠送流量": 5 + variant * 3, "总流量": 10 + variant * 6}
        cur = {"offerName": "广东移动流量体验包", "initFee": 19 + variant * 5, "offerFlow": 5 + variant * 3, "offerVoice": 50 + variant * 20}

    ctx = make_flow_context(
        province_code="guangdong",
        intent="营销活动",
        current_package=cur,
        usage=make_usage(data_gb=20 + variant * 10, saturation=f"{150 + variant * 15}%", voice=120 + variant * 40, avg_fee=cur["initFee"]),
        tags={"广东移动客户": "是", "流量偏紧": "是"},
        user_info={"星级": "四星", "网龄": f"{5 + variant}年", "归属地": "广东"},
        user_profile={"流量偏好": "刷视频、玩游戏"},
        extra_info=extra,
    )
    pkg = {"offerId": product_id, "offerName": f"广东-{product_id}优惠", "initFee": extra.get("月费", 0)}
    add_sample(Sample(
        province="广东", province_code="guangdong", intent="营销活动",
        scene=tpl.get("scene", ""), stage=tpl.get("stage", ""), product_id=product_id,
        template_content=tpl.get("template_content", ""),
        prompt=build_prompt_for_template("guangdong", "营销活动", tpl, ctx, pkg),
    ))

# ── 广东 套餐推荐（10 条）────────────────────────────────────────
if base_bj_pkg_tpl:
    gd_pkg_cases = [
        ("广东移动4G畅享套餐58元档", 58, 15, 150, 30, "200%", 120, 62, "追剧达人", "四星", 5),
        ("广东移动5G智享套餐128元档", 128, 30, 500, 55, "183%", 450, 135, "商务办公", "五星", 8),
        ("广东移动全家享套餐89元档", 89, 30, 300, 40, "133%", 280, 95, "家庭共享", "五星", 6),
        ("广东移动流量王套餐39元档", 39, 10, 100, 25, "250%", 80, 45, "游戏娱乐", "三星", 3),
        ("广东移动5G优享套餐199元档", 199, 60, 1000, 75, "125%", 900, 210, "高端用户", "五星", 10),
        ("广东移动校园套餐29元档", 29, 10, 100, 18, "180%", 90, 35, "学生用户", "三星", 2),
        ("广东移动畅享套餐99元档", 99, 40, 400, 50, "125%", 320, 105, "职场白领", "五星", 7),
        ("广东移动4G自由选套餐48元档", 48, 12, 120, 24, "200%", 100, 52, "自由职业", "四星", 4),
        ("广东移动5G尊享套餐299元档", 299, 100, 1500, 90, "90%", 1400, 310, "企业高管", "五星", 15),
        ("广东移动流量卡19元档", 19, 5, 50, 12, "240%", 40, 28, "备用机用户", "二星", 1),
    ]
    for cur_name, cur_fee, cur_flow, cur_voice, data_gb, sat, voice, avg_fee, profile, star, years in gd_pkg_cases:
        rec_fee = cur_fee + 30
        rec_flow = cur_flow + 20
        rec_voice = cur_voice + 100
        ctx = make_flow_context(
            province_code="guangdong",
            intent="套餐推荐",
            current_package={"offerName": cur_name, "initFee": cur_fee, "offerFlow": cur_flow, "offerVoice": cur_voice},
            usage=make_usage(data_gb=data_gb, saturation=sat, voice=voice, avg_fee=avg_fee),
            tags={"高频高额超套客户": "是" if int(sat.replace("%", "")) > 150 else "否", "是否老旧套餐": "是" if "4G" in cur_name else "否"},
            user_info={"星级": star, "网龄": f"{years}年", "归属地": "广东"},
            user_profile={"流量偏好": profile},
        )
        pkg = {
            "offerId": "GD-REC-001",
            "offerName": cur_name.replace("套餐", "升级套餐"),
            "initFee": rec_fee, "offerFlow": rec_flow, "offerVoice": rec_voice,
            "recommendation_reason": "流量语音双升级，更贴合广东本地使用需求",
        }
        add_sample(Sample(
            province="广东", province_code="guangdong", intent="套餐推荐",
            scene=base_bj_pkg_tpl.get("scene", ""), stage=base_bj_pkg_tpl.get("stage", ""),
            product_id=base_bj_pkg_tpl.get("product_id", ""),
            template_content=base_bj_pkg_tpl.get("template_content", ""),
            prompt=build_prompt_for_template("guangdong", "套餐推荐", base_bj_pkg_tpl, ctx, pkg),
        ))

# ── 山东 营销活动（12 条，来自 话术模板.csv）──────────────────────
# 过滤山东营销推荐数据
sd_csv_rows = [r for r in sd_marketing_rows[1:] if r and r[0] == "山东"]
# 按场景分组
from collections import defaultdict
sd_by_scene = defaultdict(list)
for r in sd_csv_rows:
    sd_by_scene[r[4]].append(r)

# 选择代表性场景，每个场景取 1-2 条
sd_mkt_scene_config = [
    ("意图1-近期超套(无费用质疑)", 1),
    ("意图2-近期超套(有费用质疑)", 1),
    ("意图2-其他", 1),
    ("推荐意图", 1),
    ("意图1-铂金标准版1年", 1),
    ("意图2-铂金生活版1年", 1),
    ("意图3-铂金车主版1年", 1),
    ("意图1-明确拒绝不需要", 1),
    ("意图2-不小心才超出", 1),
    ("意图3-有另一张卡在用", 1),
]
for scene, n in sd_mkt_scene_config:
    rows = sd_by_scene.get(scene, [])
    for i, r in enumerate(rows[:n]):
        template_content = r[5]
        # 产品ID可能是多行
        product_id = r[2].replace("\n", ",")[:80] if r[2] else ""
        # 模拟真实槽位数据
        cur_fee = random.choice([39, 59, 79, 99, 129])
        cur_flow = random.choice([10, 20, 30])
        cur_voice = random.choice([100, 200, 300])
        data_gb = cur_flow + random.randint(5, 20)
        sat = f"{min(150 + random.randint(0, 80), 250)}%"
        voice = cur_voice - random.randint(20, 50)
        avg_fee = cur_fee + random.randint(10, 30)
        current_package = {"offerName": f"山东移动4G畅享套餐{cur_fee}元档", "initFee": cur_fee, "offerFlow": cur_flow, "offerVoice": cur_voice}
        usage = make_usage(data_gb=data_gb, saturation=sat, voice=voice, avg_fee=avg_fee)
        user_info = {"星级": random.choice(["三星", "四星", "五星"]), "网龄": f"{random.randint(2, 10)}年", "归属地": "山东", "全球通等级": random.choice(["银卡", "金卡", ""])}
        # 模板中的占位符填充
        extra_fill = {
            "uniProdGrade": cur_fee,
            "discountExpenses": 10,
            "privEndMonth": "2026年12月",
            "flow": cur_flow,
            "giftFlow": 10,
            "totalFlow": cur_flow,
            "shareFlow": 5,
            "year": random.randint(3, 8),
            "level": random.choice(["三星", "四星", "五星"]),
            "flow+giftFlow": cur_flow + 10,
            "flow+giftFlow-tf": 10,
            "discountPrice": 10,
            "origPrice": cur_fee,
            "reducePrice": cur_fee - 10,
            "月流量已用超": random.randint(10, 30),
            "超出后元/G": 5,
            "多年": random.randint(3, 8),
            "客户归属地": "济南",
        }
        recommended = {"活动名称": "山东套餐焕新/权益优惠", "月费": cur_fee, "流量": cur_flow + 10}
        prompt = build_simple_prompt(
            province="山东",
            intent="营销推荐",
            template_content=template_content,
            current_package=current_package,
            recommended=recommended,
            usage=usage,
            user_info=user_info,
            extra_fill=extra_fill,
            script_requirement="直接输出话术文本，不需要前缀，口语化，贴合用户痛点，将模板中的占位符（如 [uniProdGrade]、**、{{...}}）替换为真实数据。",
            max_length=200,
        )
        add_sample(Sample(
            province="山东", province_code="shandong", intent="营销活动",
            scene=scene, stage=r[3], product_id=product_id,
            template_content=template_content,
            prompt=prompt,
        ))

# ── 山东 套餐推荐（12 条）───────────────────────────────────────
sd_pkg_tpls_online = [t for t in sd_package_cfg.get("script_templates_v2", []) if t.get("status") == "online"]
# 选择 12 条不同 scene 的模板
sd_pkg_scenes = [
    ("套餐升档", 1),
    ("", 2),  # 空 scene 的通用套餐推荐
    ("意图1-近期超套(无费用质疑)", 1),
    ("意图2-近期超套(有费用质疑)", 1),
    ("意图1-明确拒绝不需要", 1),
    ("意图2-不小心才超出", 1),
    ("意图3-有另一张卡在用", 1),
    ("意图4-流量/分钟用不完", 1),
    ("意图8-再考虑一下", 1),
    ("意图9-到厅咨询/信任线下", 1),
]
for scene, n in sd_pkg_scenes:
    tpls = pick_templates(sd_pkg_tpls_online, scene, n, by_pid=False)
    for i, tpl in enumerate(tpls):
        cur_fee = random.choice([39, 59, 79, 99, 129])
        cur_flow = random.choice([10, 20, 30])
        cur_voice = random.choice([100, 200, 300])
        data_gb = cur_flow + random.randint(5, 20)
        sat = f"{min(130 + random.randint(0, 80), 220)}%"
        voice = max(cur_voice - random.randint(20, 50), 50)
        avg_fee = cur_fee + random.randint(5, 25)
        ctx = make_flow_context(
            province_code="shandong",
            intent="套餐推荐",
            current_package={"offerName": f"山东移动4G畅享套餐{cur_fee}元档", "initFee": cur_fee, "offerFlow": cur_flow, "offerVoice": cur_voice},
            usage=make_usage(data_gb=data_gb, saturation=sat, voice=voice, avg_fee=avg_fee),
            tags={"近期超套": "是" if int(sat.replace("%", "")) > 150 else "否", "是否老旧套餐": "是"},
            user_info={"星级": random.choice(["三星", "四星", "五星"]), "网龄": f"{random.randint(2, 10)}年", "归属地": "山东"},
            user_profile={"流量偏好": random.choice(["刷短视频", "日常办公", "游戏娱乐"])},
            extra_info={"flow_sat": sat},
        )
        rec_fee = cur_fee + 20
        rec_flow = cur_flow + 15
        rec_voice = cur_voice + 100
        pkg = {
            "offerId": tpl.get("product_id", "SD-REC"),
            "offerName": f"山东移动5G畅享套餐{rec_fee}元档",
            "initFee": rec_fee,
            "offerFlow": rec_flow,
            "offerVoice": rec_voice,
            "recommendation_reason": "流量更充足，5G极速体验",
        }
        add_sample(Sample(
            province="山东", province_code="shandong", intent="套餐推荐",
            scene=tpl.get("scene", ""), stage=tpl.get("stage", ""), product_id=tpl.get("product_id", ""),
            template_content=tpl.get("template_content", ""),
            prompt=build_prompt_for_template("shandong", "套餐推荐", tpl, ctx, pkg, max_length=150),
        ))




# 限制总数为 60 条
SAMPLES = SAMPLES[:60]

print(f"样例总数: {len(SAMPLES)}")
from collections import Counter
dist = Counter((s.province, s.intent) for s in SAMPLES)
for (prov, intent), cnt in sorted(dist.items()):
    print(f"  {prov} {intent}: {cnt}")


# ═══════════════════════════════════════════════════════════════
# 调用大模型生成话术（并行）
# ═══════════════════════════════════════════════════════════════
def generate_one(args):
    idx, sample = args
    text = call_deepseek(sample.prompt)
    time.sleep(0.15)
    return idx, text


print(f"开始为 {len(SAMPLES)} 条样例生成大模型话术...")
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = {executor.submit(generate_one, (i, s)): i for i, s in enumerate(SAMPLES)}
    for future in as_completed(futures):
        idx, text = future.result()
        SAMPLES[idx].generated_text = text
        print(f"  完成 {idx + 1}/{len(SAMPLES)}")


# ═══════════════════════════════════════════════════════════════
# 导出 Excel
# ═══════════════════════════════════════════════════════════════
rows = []
for i, s in enumerate(SAMPLES, start=1):
    rows.append({
        "序号": i,
        "省份": s.province,
        "意图": s.intent,
        "场景": s.scene,
        "环节": s.stage,
        "产品ID": s.product_id,
        "原始话术模板": s.template_content,
        "大模型生成话术": s.generated_text,
        "Prompt": s.prompt,
    })

df = pd.DataFrame(rows)
output_path = os.path.join(BASE, "话术对比样例_北京广东山东_60条.xlsx")
df.to_excel(output_path, index=False, engine="openpyxl")
print(f"\n已生成 Excel: {output_path}")
print(f"共 {len(df)} 条样例")
print("\n省份意图分布:")
print(df.groupby(["省份", "意图"]).size())
