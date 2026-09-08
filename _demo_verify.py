"""本地演示用例验证（离线，不联网 LLM）。

对 skills-runtime 四省 test_cases 逐条跑真实 pipeline：
DataStep（dev 透传 + 派生字段）→ RecommendStep → ScriptStep（模板匹配 + 填槽 + Prompt 组装），
只把 llm_service.generate 打桩为回声，避免联网。

判定每条用例：是否推荐出产品、话术是否走正常模板（非兜底）、Prompt 是否含真实数值锚点
（证明入参已进槽）。用途：确认「接口 + 话术模板」配置能让演示参数正常跑出结果，不改任何配置。
"""
import asyncio
import json
import re
import sys
from pathlib import Path

from loguru import logger

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "skills-runtime"

logger.remove()  # 静默流程日志，只看校验结论
logger.add(sys.stderr, level="ERROR")

TARGETS = [
    ("beijing", "套餐推荐"),
    ("beijing", "营销推荐"),
    ("shandong", "套餐推荐"),
    ("tianjin", "营销活动"),
    ("guangdong", "营销活动"),
]

# 抽检：该用例的 Prompt 里应出现的真实数值锚点（证明入参进了槽/上下文）
ANCHORS = {
    "【比赛】山东·算式填槽·近期超套用户（单产品）": ["98", "128", "2026年12月"],
    "【比赛】山东·一通电话全流程（切入→推荐→挽留）": ["98", "128"],
    "【比赛】天津·营销助手·场景一（小额超套→1元安心包）": ["2028009"],
    "【比赛】天津·营销助手·场景二（流量语音双超→3元安心包）": ["2028009"],
    "【比赛】广东·细致型·精算白领（千人千面）": ["细致型"],
    "【比赛】广东·豪爽型·大流量重度用户（千人千面）": ["豪爽型"],
    "【比赛】广东·长辈耐心型·稳健长辈（千人千面）": ["长辈耐心型"],
}

_FALLBACK_MARK = "回复1立即办理"
_prompts: list[str] = []


async def _fake_generate(prompt, *a, **kw):
    _prompts.append(prompt or "")
    return "为您推荐更合适的套餐，本月办理更划算，性价比更高。"


def _load_cases(province, intent):
    f = RUNTIME / province / intent / "config" / "test_cases.json"
    if not f.exists():
        return []
    return json.loads(f.read_text(encoding="utf-8")).get("cases", [])


def _script_text(s: dict) -> str:
    return s.get("marketing_text") or s.get("words") or s.get("script") or ""


async def main():
    from services.llm_service import llm_service as _inst
    _inst.generate = _fake_generate

    from utils.skill_runtime import skill_registry
    skill_registry.initialize(RUNTIME)

    total, passed = 0, 0
    for province, intent in TARGETS:
        cases = [c for c in _load_cases(province, intent) if c.get("payload")]
        print(f"\n{'='*72}\n▍{province} / {intent}　（{len(cases)} 条用例）")
        executor = skill_registry.get_executor(province, intent)
        if executor is None:
            print(f"  ✗ 技能包未加载"); continue

        for c in cases:
            name = c.get("name", "?")
            total += 1
            _prompts.clear()
            try:
                res = await executor.execute(c["payload"])
            except Exception as e:
                print(f"  ✗ {name}：执行异常 {type(e).__name__}: {e}")
                continue

            scripts = res.get("marketing_scripts") or []
            texts = [_script_text(s) for s in scripts]
            n = len(scripts)
            n_fallback = sum(1 for t in texts if _FALLBACK_MARK in t)
            joined_prompt = "\n".join(_prompts)
            miss = [a for a in ANCHORS.get(name, []) if a not in joined_prompt]
            # 最终话术不应残留花括号占位符（桩输出本身无占位符，仅防模板漏填透传）
            residual = sorted({m for t in texts for m in re.findall(r"\{[^{}]+\}", t)})

            problems = []
            if n == 0:
                problems.append("无话术产出")
            if n_fallback:
                problems.append(f"{n_fallback}/{n} 条走兜底话术")
            if miss:
                problems.append(f"Prompt 缺锚点 {miss}")
            if residual:
                problems.append(f"话术残留占位符 {residual[:5]}")

            if not problems:
                passed += 1
                print(f"  ✓ {name}　→ {n} 条话术（模板命中·非兜底·锚点入槽）")
            else:
                print(f"  ✗ {name}　→ {n} 条话术；" + "；".join(problems))

    print(f"\n{'='*72}\n汇总：{passed}/{total} 条演示用例本地跑通（接口 mock + 模板匹配 + 填槽）")
    print("注：话术措辞由现场真实 LLM 生成；本脚本用回声桩离线验证配置链路正确性。")


if __name__ == "__main__":
    asyncio.run(main())
