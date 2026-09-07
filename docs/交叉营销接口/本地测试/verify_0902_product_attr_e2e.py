#!/usr/bin/env python3
"""河南 product_attr 子字段占位符 端到端本地验证（0902 接口规范）

纯进程内验证，不发网络请求、不连 ES/Redis、不调 LLM。走项目内真实链路：

    灵运 0902 报文（河南，products[].product_attr 含 voiceShareGift 等子键）
      → utils.marketing_assistant.parse / build_extra_info   （剥壳 + 全字段原名透传）
      → FlowContext（DataStep 等效：products 值喂入标准域 recommended_packages）
      → engine.prompt_builder.build_prompt                    （真实模板/占位符解析入口）
      → steps.script_step._apply_slot_facts                   （真实确定性填槽入口）
      → 话术成品（替换前后对照打印）

同时验证 routers.management._flatten_domain_subfields 能把含 product_attr 的样例
递归展开出 product_attr.voiceShareGift 这类调色板子键。

⚠️ 已知边界（本脚本如实报告，不绕过）：复数根占位符
``{recommended_packages[product_attr][voiceShareGift]}`` 在现行 build_prompt 中
**不解析**（_subfield_roots 只注册单数 recommended_package=当前产品 与 extra_info
顶层 dict 字段）。运行态等效可用的写法：
``{recommended_package[product_attr][voiceShareGift]}``（当前产品，多产品时逐条各取自己
那条）或 ``{extra_info[products][product_attr][voiceShareGift]}``（从透传整包取首个
含该子键的产品）。

用法（在 znhs_refactor 目录下）：
    python3 "docs/交叉营销接口/本地测试/verify_0902_product_attr_e2e.py"
退出码：0=全部断言通过；1=有断言失败。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 定位 znhs_refactor 根目录并加入 import 路径（脚本可从任意 cwd 执行）
_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

_PAYLOAD = Path(__file__).resolve().parent / "lingyun_0902_payload.json"

_FAILURES: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "✅" if cond else "❌"
    print(f"  {mark} {name}" + (f" —— {detail}" if detail else ""))
    if not cond:
        _FAILURES.append(name)


def main() -> int:
    from core.context import FlowContext
    from engine.prompt_builder import build_prompt
    from plugins.package_diff import PackageDiff
    from routers.management import _flatten_domain_subfields
    from steps.script_step import _apply_slot_facts
    from utils import marketing_assistant as ma

    # ── 1. 河南场景入参：0902 报文（products[].product_attr 为嵌套 Map）────
    payload = json.loads(_PAYLOAD.read_text(encoding="utf-8"))
    print("▶ 1. 报文解析（marketing_assistant.parse / build_extra_info）")
    req = ma.parse(payload)
    check("0902 报文被识别为营销助手统一接口", req is not None)
    check("servNumber 解析", req.phone == "15012340511")
    extra_info = req.extra_info
    products = extra_info.get("products") or []
    check("products 原名透传", len(products) == 2)
    product = products[0]   # 语音共享优惠包：河南 product_attr
    check(
        "product_attr 嵌套 Map 原样透传",
        product.get("product_attr")
        == {"voiceShareGift": "200分钟", "giftBusiMcdsName": "亲情网", "discountRate": "0.5"},
    )
    for meta in ("servNumber", "touchNumber", "staffId", "staffNo", "sequenceNo"):
        check(f"网关元数据 {meta} 不进 extra_info", meta not in extra_info)

    # ── 2. 构造运行态上下文（等效 DataStep 直传透传 + 营销助手节点）────────
    print("\n▶ 2. 构造 FlowContext（products 值喂入标准域 recommended_packages，键名不变）")
    ctx = FlowContext(phone=req.phone, intent="语音共享", province="河南")
    ctx.recommended_packages = [p for p in products if isinstance(p, dict)]
    ctx.extra_info = extra_info
    pkg = product
    diff = PackageDiff({}, pkg)

    # ── 3. 话术模板：product_attr 嵌套子字段占位符 ─────────────────────────
    # 运行态可解析根：recommended_package（当前产品）；另演示文档直译的复数根写法
    tok_ok = "recommended_package[product_attr][voiceShareGift]"
    tok_plural = "recommended_packages[product_attr][voiceShareGift]"
    template = (
        f"尊敬的客户您好，为您推荐语音共享优惠包，"
        f"可享{{{tok_ok}}}赠送，家庭成员可共享使用。"
    )
    print(f"\n▶ 3. 替换前模板：\n    {template}")

    slot_facts, parts = {}, {}
    prompt = build_prompt(
        "", template, ctx, pkg, diff,
        slot_facts_out=slot_facts, parts_out=parts,
    )
    check(
        "build_prompt 子字段取值进入 slot_facts",
        slot_facts.get(tok_ok) == "200分钟",
        f"slot_facts[{tok_ok!r}]={slot_facts.get(tok_ok)!r}",
    )
    check(
        "【上下文数据】含该子字段事实行",
        f"{{{tok_ok}}}：200分钟" in (parts.get("context_data") or ""),
    )
    check("无缺失槽位", parts.get("missing_slots") == [])

    # ── 4. 确定性填槽（ScriptStep._apply_slot_facts，与运行态后处理同一入口）──
    filled = _apply_slot_facts(template, slot_facts)
    print(f"\n▶ 4. 替换后话术：\n    {filled}")
    check("product_attr 子字段值正确进入话术", "可享200分钟赠送" in filled)
    check("替换后无残留占位符", "{" not in filled)

    # ── 5. 复数根写法的真实表现（已知边界，如实展示不绕过）────────────────
    print(f"\n▶ 5. 边界验证：复数根 {{{tok_plural}}}")
    facts2, parts2 = {}, {}
    build_prompt(
        "", f"可享{{{tok_plural}}}赠送", ctx, pkg, diff,
        slot_facts_out=facts2, parts_out=parts2,
    )
    unresolved = tok_plural not in facts2 and tok_plural in (parts2.get("missing_slots") or [])
    if unresolved:
        print(
            "  ⚠️ 已知缺口（现行行为）：复数根 recommended_packages 未在 build_prompt 的\n"
            "     _subfield_roots 注册（仅注册单数 recommended_package=当前产品与 extra_info\n"
            "     顶层 dict 字段），该写法槽位取不到值。配置模板时请用\n"
            "     {recommended_package[product_attr][voiceShareGift]} 或\n"
            "     {extra_info[products][product_attr][voiceShareGift]}。"
        )
    check(
        "复数根行为与已知边界一致（未解析，落入 missing_slots）",
        unresolved,
        "若此处失败说明代码已支持复数根，请同步更新本脚本与测试的缺口说明",
    )
    # 旁证：取值机制本身支持 list 根，缺的只是根注册
    from engine.prompt_builder import _subfield_walk
    check(
        "取值机制 _subfield_walk 支持 list 根下钻",
        _subfield_walk(products, ["product_attr", "voiceShareGift"]) == "200分钟",
    )

    # ── 6. 调色板递归展开（_flatten_domain_subfields）──────────────────────
    print("\n▶ 6. 调色板子字段递归展开（routers.management._flatten_domain_subfields）")
    subs = _flatten_domain_subfields("recommended_packages", product)
    by_path = {s["path"]: s for s in subs}
    for expect in ("product_attr.voiceShareGift", "product_attr.giftBusiMcdsName",
                   "product_attr.discountRate"):
        check(f"展开出调色板子键 {expect}", expect in by_path)
    check(
        "token 形如 recommended_packages[product_attr][voiceShareGift]",
        by_path.get("product_attr.voiceShareGift", {}).get("token")
        == "recommended_packages[product_attr][voiceShareGift]",
    )
    print("  展开结果（product_attr 部分）:")
    for p, s in by_path.items():
        if p.startswith("product_attr."):
            print(f"    {s['token']}  =  {s['sample']}")

    print("\n" + ("=" * 60))
    if _FAILURES:
        print(f"❌ 共 {len(_FAILURES)} 项断言失败: {_FAILURES}")
        return 1
    print("✅ 河南 product_attr 子字段端到端验证全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
