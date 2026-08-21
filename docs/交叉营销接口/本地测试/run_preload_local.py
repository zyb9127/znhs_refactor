#!/usr/bin/env python3
"""营销助手统一接口·直传模式 本地效果验证脚本

跑通整条链路（报文解析 → 省份/活动路由 → 营销标志过滤 → 逐产品并发生成
推荐/切入/挽留话术 → 归并成回调 value），并把最终「回调网关的 value」直接打印出来，
**不依赖外部交叉营销网关**（把 push_cache 替换成本地捕获）。

用法（在 znhs_refactor 目录下，用与服务相同的环境跑）：
    python3 "docs/交叉营销接口/本地测试/run_preload_local.py"
    python3 "docs/交叉营销接口/本地测试/run_preload_local.py" 自定义报文.json

前提：话术生成会真实调用 LLM（与线上服务同一套配置）。LLM 不可达时会走降级话术，
链路和回调结构仍可验证。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# 定位 znhs_refactor 根目录并加入 import 路径（脚本可从任意 cwd 执行）
_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

_DEFAULT_PAYLOAD = Path(__file__).resolve().parent / "preload_payload.json"


async def _main(payload_path: Path) -> int:
    from routers import cross_sell

    # 加载技能包注册表（服务在启动时做，脚本里手动触发一次；开发模式读本地目录）
    cross_sell.skill_registry.initialize()

    with open(payload_path, encoding="utf-8") as f:
        payload = json.load(f)

    captured: dict = {}

    async def _capture_push(*, call_id, phone, value, identifier="hs", trace_id="", **_):
        captured["call_id"] = call_id
        captured["phone"] = phone
        captured["identifier"] = identifier
        captured["value"] = value
        return True

    # 把回调网关替换成本地捕获，避免依赖外网
    cross_sell.push_cache = _capture_push

    print(f"▶ 提交报文: {payload_path}")
    resp = await cross_sell.handle_marketing_assistant_payload(payload)
    ack = json.loads(resp.body.decode("utf-8"))
    print(f"  同步 ack: {ack}")

    # 等后台生成任务跑完（主接口是 ack 即返回的异步语义）
    for _ in range(120):
        if not cross_sell._PENDING:
            break
        await asyncio.gather(*list(cross_sell._PENDING), return_exceptions=True)
    else:
        print("⚠️ 后台任务未在预期内结束")

    if not captured:
        print("❌ 没有捕获到回调（可能被拒收 / 无可路由技能包，见上方日志）")
        return 1

    print("\n===== 回调网关 value（下游用结果获取接口取到的就是这个）=====")
    print(json.dumps(captured["value"], ensure_ascii=False, indent=2))

    print("\n===== 逐产品话术概览 =====")
    for item in captured["value"].get("result", []):
        print(f"\n· productId={item['productId']}  activityType={item['activityType']}  rank={item['rank']}")
        print(f"  words(推荐话术)          : {item['words']}")
        print(f"  aiPitchMarketingDesc(切入): {item['aiPitchMarketingDesc'] or '（空·未配切入模板）'}")
        print(f"  aiRetentionMarketingDesc(挽留): {item['aiRetentionMarketingDesc'] or '（空·未配挽留模板）'}")
    return 0


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_PAYLOAD
    raise SystemExit(asyncio.run(_main(path)))
