#!/usr/bin/env python3
"""
基于 znhs_refactor 项目配置，生成 北京/广东 营销活动 + 套餐推荐
"原始话术模板 vs 大模型生成话术" 对比样例 Excel（30 条）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List

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
    province: str          # 展示用中文省份
    province_code: str     # beijing / guangdong
    intent: str            # 营销活动 / 套餐推荐
    scene: str             # 场景/环节
    product_id: str
    template_content: str  # 项目中的原始话术模板
    prompt: str            # 实际发给大模型的 prompt
    generated_text: str = ""


# ═══════════════════════════════════════════════════════════════
# 1. 加载项目配置
# ═══════════════════════════════════════════════════════════════
BASE = os.path.dirname(os.path.abspath(__file__))


def load_json(path: str) -> Dict[str, Any]:
    with open(os.path.join(BASE, path), "r", encoding="utf-8") as f:
        return json.load(f)


bj_marketing_cfg = load_json("skills-runtime/beijing/营销推荐/config/biz_config.json")
bj_package_cfg = load_json("skills-runtime/beijing/套餐推荐/config/biz_config.json")
gd_marketing_cfg = load_json("skills-runtime/guangdong/营销活动/config/biz_config.json")


# ═══════════════════════════════════════════════════════════════
# 2. 工具函数
# ═══════════════════════════════════════════════════════════════
def pick_templates(templates: List[Dict], scene: str, n: int = 1) -> List[Dict]:
    """按 scene 取前 n 条在线模板，并尽量覆盖不同 product_id。"""
    seen_pid = set()
    out = []
    for t in templates:
        if t.get("status") != "online":
            continue
        if t.get("scene") != scene:
            continue
        pid = t.get("product_id", "")
        if pid in seen_pid:
            continue
        seen_pid.add(pid)
        out.append(t)
        if len(out) >= n:
            break
    # 若去重后不足，再补充同 scene 模板
    if len(out) < n:
        for t in templates:
            if t.get("status") != "online":
                continue
            if t.get("scene") != scene:
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
) -> str:
    """复用项目 engine.prompt_builder 生成运行时 Prompt。"""
    diff = PackageDiff(ctx.current_package, pkg)
    tpl_prompt = template.get("prompt_template") or template.get("template_content", "")
    tpl_content = template.get("template_content", "")
    linked_vars = template.get("linked_vars", []) or []
    script_requirement = template.get("script_requirement", "") or ""

    # 北京营销活动模板中 prompt_template 多为空，走新格式自动构造
    return build_prompt(
        user_prompt_tpl=tpl_prompt,
        template_text=tpl_content,
        ctx=ctx,
        pkg=pkg,
        diff=diff,
        linked_vars=linked_vars,
        script_requirement=script_requirement,
        max_length=120,
    )


def call_deepseek(prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": "你是中国移动营销坐席助手，请根据提供的话术模板和用户信息，生成自然、口语化、贴合用户痛点的营销话术。直接输出话术文本，不要加前缀、标题或解释。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 300,
    }
    try:
        r = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        return f"[生成失败: {exc}]"


# ═══════════════════════════════════════════════════════════════
# 3. 构造 30 条样例数据
# ═══════════════════════════════════════════════════════════════
SAMPLES: List[Sample] = []

# ── 3.1 北京 营销活动（10 条）────────────────────────────────────
bj_mkt_tpls = bj_marketing_cfg.get("script_templates_v2", [])

# 3.1.1 网龄回馈-营销活动推荐
mkt_rec_tpls = pick_templates(bj_mkt_tpls, "意图1-营销活动推荐", 3)
for i, tpl in enumerate(mkt_rec_tpls):
    avg_fee = 68 + i * 10
    ctx = make_flow_context(
        province_code="beijing",
        intent="营销推荐",
        current_package={"offerName": f"北京移动4G畅享套餐{avg_fee}元档", "initFee": avg_fee, "offerFlow": 10 + i * 5, "offerVoice": 100 + i * 50},
        usage=make_usage(data_gb=28 + i * 4, saturation=f"{150 + i * 20}%", voice=120 + i * 30, avg_fee=avg_fee),
        tags={"网龄老客户": "是", "近六个月消费稳定": "是"},
        user_info={"星级": "四星", "网龄": f"{5 + i}年", "归属地": "北京"},
        user_profile={"流量偏好": "日常社交与短视频"},
        extra_info={"活动名称": "网龄回馈倍享礼", "承诺消费门槛": f"{avg_fee + 10}元", "合约期": "24个月", "每月赠送流量": f"{5 + i * 3}GB", "灵活会员券": "1张/月"},
    )
    pkg = {"offerId": tpl.get("product_id"), "offerName": "网龄回馈倍享礼", "initFee": 9.9, "offerFlow": 5 + i * 3, "offerVoice": 0}
    SAMPLES.append(Sample(
        province="北京",
        province_code="beijing",
        intent="营销活动",
        scene=tpl.get("scene", ""),
        product_id=tpl.get("product_id", ""),
        template_content=tpl.get("template_content", ""),
        prompt=build_prompt_for_template("beijing", "营销推荐", tpl, ctx, pkg),
    ))

# 3.1.2 推荐意图
for i, tpl in enumerate(pick_templates(bj_mkt_tpls, "意图1-推荐意图", 2)):
    ctx = make_flow_context(
        province_code="beijing",
        intent="营销推荐",
        current_package={"offerName": "北京移动畅享套餐", "initFee": 78, "offerFlow": 20, "offerVoice": 200},
        usage=make_usage(data_gb=30, saturation="160%", voice=150, avg_fee=78),
        tags={"北京移动老客户": "是"},
        user_info={"星级": "五星", "网龄": f"{8 + i}年"},
        user_profile={"流量偏好": "办公+视频"},
        extra_info={"活动名称": "网龄回馈倍享礼", "承诺消费门槛": "88元", "合约期": "24个月"},
    )
    pkg = {"offerId": tpl.get("product_id"), "offerName": "网龄回馈倍享礼", "initFee": 9.9}
    SAMPLES.append(Sample(
        province="北京", province_code="beijing", intent="营销活动", scene=tpl.get("scene", ""),
        product_id=tpl.get("product_id", ""), template_content=tpl.get("template_content", ""),
        prompt=build_prompt_for_template("beijing", "营销推荐", tpl, ctx, pkg),
    ))

# 3.1.3 消费稳定 / 资源较少 / 拒绝环节
extra_scenes = [
    ("意图1-消费稳定客户（查流量、查账单）", 2),
    ("意图3-资源较少用户（查流量、嫌流量不够、流量不够用）", 2),
    ("意图1-不需要会员", 1),
]
for scene, n in extra_scenes:
    for i, tpl in enumerate(pick_templates(bj_mkt_tpls, scene, n)):
        ctx = make_flow_context(
            province_code="beijing",
            intent="营销推荐",
            current_package={"offerName": "北京移动4G套餐", "initFee": 58 + i * 10, "offerFlow": 15 + i * 5, "offerVoice": 150},
            usage=make_usage(data_gb=25 + i * 10, saturation=f"{140 + i * 30}%", voice=120, avg_fee=60 + i * 10),
            tags={"流量偏紧": "是" if "资源较少" in scene else "否", "消费稳定": "是"},
            user_info={"星级": "四星", "网龄": f"{4 + i}年"},
            user_profile={"流量偏好": "刷短视频、追剧"},
            extra_info={"活动名称": "网龄回馈倍享礼", "承诺消费门槛": f"{70 + i * 10}元", "合约期": "24个月"},
        )
        pkg = {"offerId": tpl.get("product_id"), "offerName": "网龄回馈倍享礼", "initFee": 9.9}
        SAMPLES.append(Sample(
            province="北京", province_code="beijing", intent="营销活动", scene=tpl.get("scene", ""),
            product_id=tpl.get("product_id", ""), template_content=tpl.get("template_content", ""),
            prompt=build_prompt_for_template("beijing", "营销推荐", tpl, ctx, pkg),
        ))

# ── 3.2 北京 套餐推荐（8 条）─────────────────────────────────────
bj_pkg_tpls = [t for t in bj_package_cfg.get("script_templates_v2", []) if t.get("status") == "online"]
# 只有 1 条在线，复用它构造不同用户画像
base_bj_pkg_tpl = bj_pkg_tpls[0] if bj_pkg_tpls else None
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
    ]
    for cur_name, cur_fee, cur_flow, cur_voice, data_gb, sat, voice, avg_fee, profile, star, years in bj_pkg_cases:
        rec_fee = cur_fee + 30 if cur_fee < 100 else cur_fee + 50
        rec_flow = cur_flow + 20 if cur_flow < 50 else cur_flow + 30
        rec_voice = cur_voice + 100 if cur_voice < 500 else cur_voice + 200
        ctx = make_flow_context(
            province_code="beijing",
            intent="套餐推荐",
            current_package={"offerName": cur_name, "initFee": cur_fee, "offerFlow": cur_flow, "offerVoice": cur_voice},
            usage=make_usage(data_gb=data_gb, saturation=sat, voice=voice, avg_fee=avg_fee),
            tags={"高频高额超套客户": "是" if "超套" in profile or int(sat.replace("%", "")) > 150 else "否", "是否老旧套餐": "是" if "4G" in cur_name else "否"},
            user_info={"星级": star, "网龄": f"{years}年"},
            user_profile={"流量偏好": profile},
        )
        pkg = {
            "offerId": "BJ-REC-001",
            "offerName": f"{cur_name.replace('套餐', '升级套餐')}",
            "initFee": rec_fee,
            "offerFlow": rec_flow,
            "offerVoice": rec_voice,
            "recommendation_reason": "流量更充足，更匹配您的使用习惯",
        }
        SAMPLES.append(Sample(
            province="北京", province_code="beijing", intent="套餐推荐",
            scene=base_bj_pkg_tpl.get("scene", ""), product_id=base_bj_pkg_tpl.get("product_id", ""),
            template_content=base_bj_pkg_tpl.get("template_content", ""),
            prompt=build_prompt_for_template("beijing", "套餐推荐", base_bj_pkg_tpl, ctx, pkg),
        ))

# ── 3.3 广东 营销活动（6 条）─────────────────────────────────────
gd_mkt_tpls = gd_marketing_cfg.get("script_templates_v2", [])
for tpl in gd_mkt_tpls:
    product_id = tpl.get("product_id", "")
    # 根据 product_id 构造业务示例
    if product_id == "流量":
        extra = {"优惠期": 12, "月费": 19, "套内流量": 10, "赠送流量": 10, "总流量": 20}
        cur = {"offerName": "广东移动4G流量王", "initFee": 39, "offerFlow": 10, "offerVoice": 100}
    elif product_id == "全家享":
        extra = {"宽带月租": 30, "返还月数": 12, "每月返还": 15, "最高返还": 180, "优惠后月租": 15, "合约期": 24}
        cur = {"offerName": "广东移动全家享套餐", "initFee": 89, "offerFlow": 30, "offerVoice": 300}
    elif product_id == "提速":
        extra = {"提速档位": 1000, "月费": 10, "优惠期": 24, "保底套餐": 89}
        cur = {"offerName": "广东移动宽带提速包", "initFee": 30, "offerFlow": 0, "offerVoice": 0, "bandwidth": 300}
    elif product_id == "套餐":
        extra = {"优惠期": 12, "月费": 39, "套内流量": 20, "语音分钟": 200, "赠送流量": 10, "总流量": 30}
        cur = {"offerName": "广东移动畅享套餐", "initFee": 59, "offerFlow": 15, "offerVoice": 150}
    elif product_id == "升":
        extra = {"优惠期": 6, "月费": 59, "套内流量": 30, "语音分钟": 300, "赠送流量": 20, "总流量": 50}
        cur = {"offerName": "广东移动升档体验包", "initFee": 39, "offerFlow": 10, "offerVoice": 100}
    else:  # 体验
        extra = {"优惠期": 3, "月费": 9, "套内流量": 5, "语音分钟": 50, "赠送流量": 5, "总流量": 10}
        cur = {"offerName": "广东移动流量体验包", "initFee": 19, "offerFlow": 5, "offerVoice": 50}

    ctx = make_flow_context(
        province_code="guangdong",
        intent="营销活动",
        current_package=cur,
        usage=make_usage(data_gb=25, saturation="160%", voice=150, avg_fee=cur["initFee"]),
        tags={"广东移动客户": "是", "流量偏紧": "是"},
        user_info={"星级": "四星", "网龄": "5年", "归属地": "广东"},
        user_profile={"流量偏好": "刷视频、玩游戏"},
        extra_info=extra,
    )
    pkg = {"offerId": product_id, "offerName": f"广东-{product_id}优惠", "initFee": extra.get("月费", 0)}
    SAMPLES.append(Sample(
        province="广东", province_code="guangdong", intent="营销活动",
        scene=tpl.get("scene", ""), product_id=product_id,
        template_content=tpl.get("template_content", ""),
        prompt=build_prompt_for_template("guangdong", "营销活动", tpl, ctx, pkg),
    ))

# ── 3.4 广东 套餐推荐（6 条）─────────────────────────────────────
# 广东套餐推荐无独立在线模板，复用北京套餐推荐模板逻辑，构造广东本地化数据
if base_bj_pkg_tpl:
    gd_pkg_cases = [
        ("广东移动4G畅享套餐58元档", 58, 15, 150, 30, "200%", 120, 62, "追剧达人", "四星", 5),
        ("广东移动5G智享套餐128元档", 128, 30, 500, 55, "183%", 450, 135, "商务办公", "五星", 8),
        ("广东移动全家享套餐89元档", 89, 30, 300, 40, "133%", 280, 95, "家庭共享", "五星", 6),
        ("广东移动流量王套餐39元档", 39, 10, 100, 25, "250%", 80, 45, "游戏娱乐", "三星", 3),
        ("广东移动5G优享套餐199元档", 199, 60, 1000, 75, "125%", 900, 210, "高端用户", "五星", 10),
        ("广东移动校园套餐29元档", 29, 10, 100, 18, "180%", 90, 35, "学生用户", "三星", 2),
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
            "initFee": rec_fee,
            "offerFlow": rec_flow,
            "offerVoice": rec_voice,
            "recommendation_reason": "流量语音双升级，更贴合广东本地使用需求",
        }
        SAMPLES.append(Sample(
            province="广东", province_code="guangdong", intent="套餐推荐",
            scene=base_bj_pkg_tpl.get("scene", ""), product_id=base_bj_pkg_tpl.get("product_id", ""),
            template_content=base_bj_pkg_tpl.get("template_content", ""),
            prompt=build_prompt_for_template("guangdong", "套餐推荐", base_bj_pkg_tpl, ctx, pkg),
        ))


# ═══════════════════════════════════════════════════════════════
# 4. 调用大模型生成话术（并行）
# ═══════════════════════════════════════════════════════════════
def generate_one(idx_sample):
    idx, sample = idx_sample
    text = call_deepseek(sample.prompt)
    time.sleep(0.2)
    return idx, text


print(f"开始为 {len(SAMPLES)} 条样例生成大模型话术...")
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = {executor.submit(generate_one, (i, s)): i for i, s in enumerate(SAMPLES)}
    for future in as_completed(futures):
        idx, text = future.result()
        SAMPLES[idx].generated_text = text
        print(f"  完成 {idx + 1}/{len(SAMPLES)}")

# ═══════════════════════════════════════════════════════════════
# 5. 整理并导出 Excel
# ═══════════════════════════════════════════════════════════════
rows = []
for i, s in enumerate(SAMPLES, start=1):
    rows.append({
        "序号": i,
        "省份": s.province,
        "意图": s.intent,
        "场景/环节": s.scene,
        "产品ID": s.product_id,
        "原始话术模板": s.template_content,
        "大模型生成话术": s.generated_text,
        "Prompt(供参考)": s.prompt,
    })

df = pd.DataFrame(rows)
output_path = os.path.join(BASE, "话术对比样例_北京广东_30条.xlsx")
df.to_excel(output_path, index=False, engine="openpyxl")
print(f"\n已生成 Excel: {output_path}")
print(f"共 {len(df)} 条样例")
