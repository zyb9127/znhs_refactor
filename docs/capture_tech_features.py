#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
营销话术智能体 - 比赛演讲文稿「五、技术特点」章节配图截图脚本
输出：docs/用户手册图片/比赛技术特点/*.png

只读约束：全程不点「保存/发布上线/下线/删除/修复」，弹窗一律 Escape/取消 关闭。
仅「执行推荐」（测试）会真实调用大模型，属只读操作。
"""
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright, Page

BASE = "http://localhost:8000/znhs-gray"
SKILL_URL = f"{BASE}/SkillManager"
TPL_URL = f"{BASE}/TemplateConfig"
OUTPUT_DIR = Path(__file__).parent / "用户手册图片" / "比赛技术特点"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

VIEWPORT = {"width": 1440, "height": 900}
HL_STYLE = "3px solid #f56c6c"

report = {}  # name -> note


def out(name: str) -> Path:
    return OUTPUT_DIR / name


async def wait_loading(page: Page, timeout: int = 15000):
    try:
        await page.wait_for_selector(".el-loading-mask", state="detached", timeout=timeout)
    except Exception:
        pass


async def highlight(page: Page, selector: str, all_match: bool = False, label: str = ""):
    """给目标元素加红色高亮框，返回命中数量"""
    js = """
    ([sel, all, style]) => {
      const els = all ? [...document.querySelectorAll(sel)]
                      : [document.querySelector(sel)].filter(Boolean);
      const vis = els.filter(e => e && e.offsetParent !== null);
      vis.forEach(e => {
        e.dataset.hlShot = '1';
        e.style.outline = style;
        e.style.outlineOffset = '2px';
        e.style.borderRadius = '4px';
      });
      return vis.length;
    }
    """
    n = await page.evaluate(js, [selector, all_match, HL_STYLE])
    print(f"   🔴 高亮 {label or selector}: {n} 个元素")
    return n


async def clear_highlight(page: Page):
    await page.evaluate("""
      document.querySelectorAll('[data-hl-shot]').forEach(e => {
        e.style.outline = ''; e.style.outlineOffset = ''; e.style.borderRadius = '';
        delete e.dataset.hlShot;
      });
    """)


async def shot(page: Page, name: str, selector: str | None = None, full_page: bool = False):
    await page.wait_for_timeout(400)
    p = out(name)
    if selector:
        el = await page.wait_for_selector(selector, state="visible", timeout=10000)
        await el.screenshot(path=str(p))
    else:
        await page.screenshot(path=str(p), full_page=full_page)
    print(f"✅ 已保存: {name}")
    report[name] = "ok"


async def find_row(page: Page, *must_contain: str):
    """在首页技能列表中找包含全部关键字的行"""
    await page.wait_for_selector(".el-table__row", timeout=15000)
    rows = await page.locator(".el-table__row").all()
    for row in rows:
        text = await row.inner_text()
        if all(k in text for k in must_contain):
            return row
    return None


async def close_dialog(page: Page, dialog_sel: str):
    await page.keyboard.press("Escape")
    try:
        await page.wait_for_selector(dialog_sel, state="hidden", timeout=4000)
    except Exception:
        # 有些弹窗关闭动画较慢，或 Escape 被吞，再点一次右上角关闭
        try:
            await page.click(f"{dialog_sel} .el-dialog__headerbtn", timeout=2000)
        except Exception:
            pass
        try:
            await page.wait_for_selector(dialog_sel, state="hidden", timeout=4000)
        except Exception:
            print(f"   ⚠️ 弹窗未能确认关闭: {dialog_sel}")
    await page.wait_for_timeout(400)


async def run_test_and_wait(page: Page, timeout_ms: int = 180000) -> str:
    """点执行推荐并等待状态栏出现终态，返回状态文本"""
    # 等待请求体已自动生成（textarea 非空）
    await page.wait_for_function("""
      () => [...document.querySelectorAll('.skill-test-dialog textarea')]
              .some(t => t.value.trim().length > 20)
    """, timeout=30000)
    await page.click(".skill-test-dialog .tc-btn-primary")
    await page.wait_for_selector(
        ".tc-status-bar:has-text('推荐成功'), "
        ".tc-status-bar:has-text('推荐失败'), "
        ".tc-status-bar:has-text('部分成功')",
        timeout=timeout_ms,
    )
    status = (await page.locator(".skill-test-dialog .tc-status-bar").inner_text()).strip()
    print(f"   📊 测试状态: {status}")
    return status


async def expand_step3(page: Page):
    """展开 Step3 分步卡片（若未展开），不滚动"""
    await page.evaluate("""
      () => {
        const cards = [...document.querySelectorAll('.skill-test-dialog .tc-step-card')];
        const c3 = cards.find(c => {
          const t = c.querySelector(':scope > .tc-step-header')?.innerText || '';
          return t.includes('Step3');
        }) || cards[cards.length - 1];
        if (c3) {
          const body = c3.querySelector(':scope > .tc-step-body');
          if (body && !body.classList.contains('open')) {
            c3.querySelector(':scope > .tc-step-header')?.click();
          }
        }
      }
    """)
    await page.wait_for_timeout(800)


async def scroll_result_pane(page: Page, to_top: bool = True):
    """把测试弹窗内所有滚动容器复位到顶部（让 Step1/2/3 标题都尽量入画）"""
    await page.evaluate("""
      (toTop) => {
        document.querySelectorAll('.skill-test-dialog *').forEach(el => {
          if (el.scrollHeight > el.clientHeight + 10) el.scrollTop = toTop ? 0 : el.scrollHeight;
        });
      }
    """, to_top)
    await page.wait_for_timeout(400)


async def count_fallback_scripts(page: Page) -> tuple[int, int]:
    """统计 Step3 中话术条数与兜底话术条数（兜底特征：短文本含「回复1立即办理」）"""
    return await page.evaluate("""
      () => {
        const cards = [...document.querySelectorAll('.skill-test-dialog .tc-step-card')];
        const c3 = cards.find(c => (c.querySelector(':scope > .tc-step-header')?.innerText || '').includes('Step3'));
        if (!c3) return [0, 0];
        const body = c3.querySelector(':scope > .tc-step-body');
        const text = body ? body.innerText : c3.innerText;
        const total = (text.match(/第\\s*\\d+\\s*(推荐|条)/g) || []).length;
        const fb = (text.match(/回复1立即办理/g) || []).length;
        return [total, fb];
      }
    """)


async def step3_is_fallback(page: Page) -> bool:
    total, fb = await count_fallback_scripts(page)
    return fb > 0


async def run_test_until_real(page: Page, max_attempts: int = 12) -> str:
    """执行推荐；若 Step3 含兜底话术则清空结果重跑，最多 max_attempts 次"""
    status = ""
    for attempt in range(1, max_attempts + 1):
        status = await run_test_and_wait(page)
        if "失败" in status:
            return status
        await expand_step3(page)
        total, fb = await count_fallback_scripts(page)
        if fb == 0:
            if attempt > 1:
                print(f"   ✅ 第 {attempt} 次执行拿到全部真实话术（{total} 条）")
            return status
        print(f"   ⚠️ 第 {attempt} 次：{total} 条中 {fb} 条兜底话术，清空重跑")
        if attempt < max_attempts:
            await page.click(".skill-test-dialog button:has-text('清空结果')")
            await page.wait_for_timeout(800)
    report_note = "仍含兜底话术（已达重跑上限）"
    print(f"   ❌ {report_note}")
    return status + "｜" + report_note


async def prepare_full_dialog(page: Page):
    """折叠非 Step 卡片（提示词/接口调用等），解除结果区 72vh 高度限制，
    使 Step1/2/3 三张卡片能在同一张弹窗截图里完整呈现"""
    await page.evaluate("""
      () => {
        const dlg = document.querySelector('.skill-test-dialog');
        if (!dlg) return;
        [...dlg.querySelectorAll('.tc-step-card')].forEach(c => {
          const t = c.querySelector(':scope > .tc-step-header')?.innerText || '';
          const isStep = /Step\\s*[123]/.test(t);
          const body = c.querySelector(':scope > .tc-step-body');
          if (!isStep && body && body.classList.contains('open')) {
            c.querySelector(':scope > .tc-step-header').click();
          }
        });
      }
    """)
    await page.wait_for_timeout(500)
    await page.add_style_tag(content="""
      .skill-test-dialog .tc-result-wrap { max-height: none !important; overflow: visible !important; }
      .skill-test-dialog .tc-layout { max-height: none !important; }
      .skill-test-dialog { max-height: none !important; }
      .skill-test-dialog .el-dialog__body { max-height: none !important; overflow: visible !important; }
    """)
    await page.wait_for_timeout(400)


async def highlight_step_cards(page: Page) -> int:
    """只高亮 Step1/2/3 三张主分步卡片"""
    return await page.evaluate("""
      () => {
        const cards = [...document.querySelectorAll('.skill-test-dialog .tc-step-card')].filter(c => {
          const t = c.querySelector(':scope > .tc-step-header .tc-step-title')?.innerText
                 || c.querySelector(':scope > .tc-step-header')?.innerText || '';
          return /Step\\s*[123]/.test(t);
        });
        cards.forEach(e => {
          e.dataset.hlShot = '1';
          e.style.outline = '3px solid #f56c6c';
          e.style.outlineOffset = '2px';
          e.style.borderRadius = '4px';
        });
        return cards.length;
      }
    """)


async def open_test_dialog(page: Page, *row_keys: str):
    row = await find_row(page, *row_keys)
    if row is None:
        raise RuntimeError(f"未找到技能行: {row_keys}")
    await row.locator("button.btn-link:has-text('测试')").first.click()
    await page.wait_for_selector(".skill-test-dialog", state="visible", timeout=15000)
    await page.wait_for_timeout(1200)


async def open_edit_dialog(page: Page, *row_keys: str):
    row = await find_row(page, *row_keys)
    if row is None:
        raise RuntimeError(f"未找到技能行: {row_keys}")
    await row.locator("button.btn-link:has-text('编辑')").first.click()
    await page.wait_for_selector(".edit-skill-dialog", state="visible", timeout=15000)
    await wait_loading(page)
    await page.wait_for_timeout(1200)


# ───────────────────────── 各特点截图 ─────────────────────────

async def cap6_home(page: Page):
    """特点6_配置即上线首页：整页 + 高亮 .sync-status-bar"""
    await highlight(page, ".sync-status-bar", label="配置来源统计条")
    await shot(page, "特点6_配置即上线首页.png", full_page=True)
    await clear_highlight(page)


async def cap1_pipeline(page: Page):
    """特点1_三步管道：北京/套餐推荐 测试弹窗执行后的三张分步卡片"""
    # 行内省份列可能显示中文名或编码，两个都试
    row = await find_row(page, "beijing", "套餐推荐") or await find_row(page, "北京", "套餐推荐")
    if row is None:
        raise RuntimeError("未找到 beijing/套餐推荐 行")
    await row.locator("button.btn-link:has-text('测试')").first.click()
    await page.wait_for_selector(".skill-test-dialog", state="visible", timeout=15000)
    await page.wait_for_timeout(1200)
    status = await run_test_until_real(page)
    await expand_step3(page)
    await prepare_full_dialog(page)
    n = await highlight_step_cards(page)
    print(f"   🔴 高亮 Step1/2/3 主卡片: {n} 个")
    await shot(page, "特点1_三步管道测试结果.png", selector=".skill-test-dialog")
    await clear_highlight(page)
    report["特点1_status"] = status
    report["特点1_cards"] = n
    await close_dialog(page, ".skill-test-dialog")


async def cap5_multi_product(page: Page) -> bool:
    """特点5_多产品并发：山东/套餐推荐「全流程」用例，Step3 多条话术。失败返回 False 走 fallback"""
    row = await find_row(page, "shandong", "套餐推荐") or await find_row(page, "山东", "套餐推荐")
    if row is None:
        print("   ⚠️ 未找到 shandong/套餐推荐 行")
        return False
    await row.locator("button.btn-link:has-text('测试')").first.click()
    await page.wait_for_selector(".skill-test-dialog", state="visible", timeout=15000)
    await page.wait_for_timeout(1200)
    # 选择「全流程」用例（切入→推荐→挽留，一次返回 3 条话术）
    try:
        opts = await page.locator("select.tc-case-select option").all_inner_texts()
        idx = next((i for i, t in enumerate(opts) if "全流程" in t or "多" in t), None)
        if idx is not None:
            val = await page.locator("select.tc-case-select option").nth(idx).get_attribute("value")
            await page.select_option("select.tc-case-select", value=val)
            print(f"   📋 已选用例: {opts[idx]}")
            await page.wait_for_timeout(600)
    except Exception as e:
        print(f"   ⚠️ 用例选择失败（沿用默认用例）: {e}")
    try:
        status = await run_test_until_real(page)
    except Exception as e:
        print(f"   ⚠️ 山东测试超时/失败: {e}")
        await close_dialog(page, ".skill-test-dialog")
        return False
    if "失败" in status:
        await close_dialog(page, ".skill-test-dialog")
        return False
    await expand_step3(page)
    # 兜底话术已由 run_test_until_real 重跑规避；这里仍记录 Step3 概况
    body_text = await page.evaluate("""
      () => {
        const cards = [...document.querySelectorAll('.skill-test-dialog .tc-step-card')];
        const c3 = cards.find(c => (c.querySelector(':scope > .tc-step-header')?.innerText || '').includes('Step3')) || cards[cards.length-1];
        return c3 ? c3.innerText : '';
      }
    """)
    print(f"   📝 Step3 文本长度 {len(body_text)}")
    await prepare_full_dialog(page)
    # 高亮 Step3 卡片本身
    n = await page.evaluate("""
      () => {
        const cards = [...document.querySelectorAll('.skill-test-dialog .tc-step-card')];
        const c3 = cards.find(c => (c.querySelector(':scope > .tc-step-header')?.innerText || '').includes('Step3'));
        if (!c3) return 0;
        c3.dataset.hlShot = '1';
        c3.style.outline = '3px solid #f56c6c';
        c3.style.outlineOffset = '2px';
        c3.style.borderRadius = '4px';
        return 1;
      }
    """)
    print(f"   🔴 高亮 Step3 多条话术结果: {n} 个")
    await shot(page, "特点5_多产品并发.png", selector=".skill-test-dialog")
    await clear_highlight(page)
    report["特点5_status"] = status
    await close_dialog(page, ".skill-test-dialog")
    return True


async def cap5_fallback(page: Page):
    """特点5 fallback(b)：添加模板弹窗多产品模式「话术匹配 ID」多值配置区"""
    row = await find_row(page, "beijing", "套餐推荐") or await find_row(page, "北京", "套餐推荐")
    if row is None:
        raise RuntimeError("fallback 未找到 beijing/套餐推荐 行")
    await row.locator("button.btn-link:has-text('编辑')").first.click()
    await page.wait_for_selector(".edit-skill-dialog", state="visible", timeout=15000)
    await wait_loading(page)
    await page.wait_for_timeout(1000)
    await page.click(".edit-skill-dialog .el-tabs__item:has-text('话术模板')")
    await page.wait_for_timeout(1000)
    await page.click(".edit-skill-dialog button:has-text('添加模板')")
    await page.wait_for_selector(".tpl-edit-dialog", state="visible", timeout=15000)
    await page.wait_for_timeout(800)
    await highlight(page, ".tpl-edit-dialog .el-form-item:has(textarea[placeholder*='prod']), "
                          ".tpl-edit-dialog .el-form-item", label="话术匹配 ID 区")
    await shot(page, "特点5_多产品并发.png", selector=".tpl-edit-dialog")
    await clear_highlight(page)
    report["特点5_status"] = "fallback(b) 话术匹配ID多值区"
    await close_dialog(page, ".tpl-edit-dialog")
    await close_dialog(page, ".edit-skill-dialog")


async def cap2_template_chain(page: Page):
    """特点2_模板优先级链：山东/套餐推荐 编辑弹窗-话术模板 tab 模板列表"""
    # 弹窗比默认视口高（约 1764px），元素截图拼接会留白；先加高视口让弹窗完整落在视口内
    await page.set_viewport_size({"width": 1440, "height": 1950})
    row = await find_row(page, "shandong", "套餐推荐") or await find_row(page, "山东", "套餐推荐")
    if row is None:
        raise RuntimeError("未找到 shandong/套餐推荐 行")
    await row.locator("button.btn-link:has-text('编辑')").first.click()
    await page.wait_for_selector(".edit-skill-dialog", state="visible", timeout=15000)
    await wait_loading(page)
    await page.wait_for_timeout(1000)
    await page.click(".edit-skill-dialog .el-tabs__item:has-text('话术模板')")
    await page.wait_for_timeout(1200)
    # 收起「话术生成参数」「模板匹配规则」两个折叠区，让模板列表占满画面
    await page.evaluate("""
      () => document.querySelectorAll('.edit-skill-dialog details[open]')
        .forEach(d => d.removeAttribute('open'))
    """)
    await page.wait_for_timeout(500)
    # 高亮模板表格（取弹窗内可见的 el-table）
    n = await page.evaluate("""
      () => {
        const dlg = document.querySelector('.edit-skill-dialog');
        const tables = [...dlg.querySelectorAll('.el-table')].filter(t => t.offsetParent !== null);
        const t = tables[tables.length - 1];
        if (t) {
          t.dataset.hlShot='1'; t.style.outline='3px solid #f56c6c'; t.style.outlineOffset='2px';
          t.scrollIntoView({ block: 'start' });
        }
        return tables.length;
      }
    """)
    print(f"   🔴 高亮模板表格（可见表格 {n} 个）")
    # 弹窗内容区是独立滚动容器，元素截图时视口外区域会留白；
    # 展开为完整高度再截，并移开鼠标避免 hover tooltip 入镜
    await page.evaluate("""
      () => {
        const dlg = document.querySelector('.edit-skill-dialog');
        const body = dlg && (dlg.querySelector('.edit-dialog-body') || dlg.querySelector('.el-dialog__body'));
        if (body) {
          body.scrollTop = 0;
          body.style.maxHeight = 'none';
          body.style.height = 'auto';
          body.style.overflow = 'visible';
        }
        if (dlg) { dlg.style.maxHeight = 'none'; }
      }
    """)
    await page.mouse.move(5, 5)
    await page.wait_for_timeout(600)
    await shot(page, "特点2_模板优先级链.png", selector=".edit-skill-dialog")
    await clear_highlight(page)
    await close_dialog(page, ".edit-skill-dialog")


async def cap3_derived_fields(page: Page):
    """特点3_派生字段引擎：天津/营销活动 接口编辑弹窗的派生字段区块"""
    # 加高视口，让「字段计算 + 逻辑判断」两组规则同框
    await page.set_viewport_size({"width": 1440, "height": 1650})
    row = await find_row(page, "tianjin", "营销活动") or await find_row(page, "天津", "营销活动")
    if row is None:
        raise RuntimeError("未找到 tianjin/营销活动 行")
    await row.locator("button.btn-link:has-text('编辑')").first.click()
    await page.wait_for_selector(".edit-skill-dialog", state="visible", timeout=15000)
    await wait_loading(page)
    await page.wait_for_timeout(1000)
    # 接口配置 tab（默认），在接口表找到 cc 行点「编辑」
    await page.wait_for_selector(".edit-skill-dialog .el-table__row", timeout=15000)
    ifc_rows = await page.locator(".edit-skill-dialog .el-table__row").all()
    target = None
    for r in ifc_rows:
        t = await r.inner_text()
        if "cc" in t.split()[0:3] or "营销助手" in t or "cc" in t:
            target = r
            break
    if target is None:
        target = ifc_rows[0]
        print("   ⚠️ 未精确定位 cc 接口行，使用第一行")
    await target.locator("button.ifc-btn-link:has-text('编辑')").first.click()
    await page.wait_for_selector(".el-dialog:has-text('编辑接口')", state="visible", timeout=15000)
    await page.wait_for_timeout(800)
    # 切到 ② 透传字段 tab（直传模式只有两步）
    steps = await page.locator(".el-dialog:has-text('编辑接口') .ifc-step").all()
    await steps[-1].click()
    await page.wait_for_timeout(800)
    # 滚动到派生字段区块并高亮其所在容器（向上找同时包含「字段计算」「逻辑判断」的祖先）
    n = await page.evaluate("""
      () => {
        const dlg = [...document.querySelectorAll('.el-dialog')]
          .find(d => d.offsetParent && d.innerText.includes('编辑接口'));
        if (!dlg) return 0;
        const title = [...dlg.querySelectorAll('.om2-title')]
          .find(t => t.innerText.includes('派生字段'));
        if (!title) return 0;
        let sec = title.parentElement;
        for (let i = 0; i < 8 && sec && sec !== dlg; i++) {
          if (sec.innerText.includes('字段计算') && sec.innerText.includes('逻辑判断')) break;
          sec = sec.parentElement;
        }
        if (!sec || sec === dlg) sec = title.parentElement;
        title.scrollIntoView({ block: 'start' });
        sec.dataset.hlShot = '1';
        sec.style.outline = '3px solid #f56c6c';
        sec.style.outlineOffset = '2px';
        sec.style.borderRadius = '4px';
        return 1;
      }
    """)
    if n == 0:
        print("   ⚠️ 未找到派生字段区块")
    else:
        print("   🔴 已高亮派生字段区块")
    await page.wait_for_timeout(400)
    # 弹窗高于视口，元素截图会截不全，改用视口截图
    await shot(page, "特点3_派生字段引擎.png")
    await clear_highlight(page)
    await close_dialog(page, ".el-dialog:has(.el-dialog__title:has-text('编辑接口'))")
    await close_dialog(page, ".edit-skill-dialog")


async def cap4_slot_filling(page: Page) -> bool:
    """特点4_槽位填槽：TemplateConfig 新建模板弹窗（占位符检测 + 一键勾选 + 关联变量）"""
    await page.goto(TPL_URL, wait_until="networkidle", timeout=60000)
    await wait_loading(page, 20000)
    await page.wait_for_timeout(1000)
    try:
        await page.click("button.btn-primary:has-text('新建')", timeout=10000)
        await page.wait_for_selector(".modal-mask.show .modal-box.modal-lg", timeout=8000)
    except Exception as e:
        print(f"   ⚠️ TemplateConfig 新建弹窗打不开: {e}")
        return False
    dlg = ".modal-mask.show .modal-box.modal-lg"
    # 选省份 beijing → 意图 套餐推荐
    selects = page.locator(f"{dlg} select.form-control")
    await selects.nth(0).select_option(value="beijing")
    await page.wait_for_timeout(1500)
    try:
        await selects.nth(1).select_option(label="套餐推荐", timeout=8000)
    except Exception:
        opts = await selects.nth(1).locator("option").all_inner_texts()
        print(f"   ⚠️ 意图下拉选项: {opts}")
        return False
    await page.wait_for_timeout(800)
    # 填带占位符的模板内容，触发占位符检测
    await page.fill(f"{dlg} textarea.form-control.tall",
                    "您好！您当前套餐为{cur_brief}，推荐您办理{pkg_brief}，{diff_str}。")
    await page.wait_for_timeout(1200)
    try:
        await page.wait_for_selector(f"{dlg} .var-suggest-bar", state="visible", timeout=8000)
    except Exception:
        print("   ⚠️ 未出现占位符检测提示条")
        return False
    await page.click(f"{dlg} .var-suggest-bar button:has-text('一键勾选')")
    await page.wait_for_timeout(800)
    # 滚动关联变量区到可见位置
    await page.evaluate("""
      (dlg) => {
        const d = document.querySelector(dlg);
        const bar = d.querySelector('.var-suggest-bar');
        if (bar) bar.scrollIntoView({ block: 'center' });
      }
    """, dlg)
    await page.wait_for_timeout(400)
    await highlight(page, f"{dlg} .var-suggest-bar", label="占位符检测提示条")
    await highlight(page, f"{dlg} .var-grid", label="已勾选变量区")
    await shot(page, "特点4_槽位填槽.png", selector=dlg)
    await clear_highlight(page)
    # 取消关闭（不保存）
    await page.click(f"{dlg} .modal-footer button:has-text('取消')")
    await page.wait_for_timeout(600)
    return True


async def cap4_fallback(page: Page):
    """特点4 fallback：SkillManager 添加模板弹窗的占位符检测提示（contentVarHint）"""
    await page.goto(SKILL_URL, wait_until="networkidle", timeout=60000)
    await wait_loading(page, 20000)
    await page.wait_for_timeout(1000)
    row = await find_row(page, "beijing", "套餐推荐") or await find_row(page, "北京", "套餐推荐")
    await row.locator("button.btn-link:has-text('编辑')").first.click()
    await page.wait_for_selector(".edit-skill-dialog", state="visible", timeout=15000)
    await wait_loading(page)
    await page.wait_for_timeout(1000)
    await page.click(".edit-skill-dialog .el-tabs__item:has-text('话术模板')")
    await page.wait_for_timeout(1000)
    await page.click(".edit-skill-dialog button:has-text('添加模板')")
    await page.wait_for_selector(".tpl-edit-dialog", state="visible", timeout=15000)
    await page.wait_for_timeout(800)
    await page.fill(".tpl-edit-dialog textarea.content-area",
                    "您好！您当前套餐为{cur_brief}，推荐您办理{pkg_brief}，{diff_str}。")
    await page.wait_for_timeout(1200)
    await highlight(page, ".tpl-edit-dialog .content-var-hint", label="占位符检测提示条")
    await highlight(page, ".tpl-edit-dialog .dragvar-palette", label="可映射固定域调色板")
    await page.evaluate("""
      const el = document.querySelector('.tpl-edit-dialog .el-dialog__body');
      if (el) el.scrollTop = 0;
    """)
    await page.wait_for_timeout(400)
    await shot(page, "特点4_槽位填槽.png", selector=".tpl-edit-dialog")
    await clear_highlight(page)
    await close_dialog(page, ".tpl-edit-dialog")
    await close_dialog(page, ".edit-skill-dialog")
    report["特点4_note"] = "fallback：SkillManager 添加模板弹窗占位符检测（无③关联变量区）"


async def _open_ifc_edit(page: Page, *row_keys: str):
    """打开指定技能行的编辑弹窗，并进入第一个接口的「编辑接口」弹窗"""
    await page.set_viewport_size({"width": 1440, "height": 1500})
    await open_edit_dialog(page, *row_keys)
    await page.wait_for_selector(".edit-skill-dialog .el-table__row", timeout=15000)
    ifc_row = page.locator(".edit-skill-dialog .el-table__row").first
    await ifc_row.locator("button.ifc-btn-link:has-text('编辑')").first.click()
    await page.wait_for_selector(".el-dialog:has-text('编辑接口')", state="visible", timeout=15000)
    await page.wait_for_timeout(1200)


async def _hl_ifc_form_items(page: Page, *label_keywords: str):
    """高亮「编辑接口」弹窗中 label 含指定关键字的 el-form-item"""
    return await page.evaluate("""
      ([kws, style]) => {
        const dlg = [...document.querySelectorAll('.el-dialog')]
          .find(d => d.offsetParent
                  && (d.querySelector('.el-dialog__title')?.innerText || '').includes('编辑接口'));
        if (!dlg) return 0;
        let n = 0;
        for (const fi of dlg.querySelectorAll('.el-form-item')) {
          const lb = fi.querySelector('.el-form-item__label');
          if (lb && kws.some(k => lb.innerText.includes(k))) {
            fi.dataset.hlShot = '1';
            fi.style.outline = style; fi.style.outlineOffset = '2px'; fi.style.borderRadius = '4px';
            n++;
          }
        }
        return n;
      }
    """, [list(label_keywords), HL_STYLE])


async def shot_ifc_dialog(page: Page, name: str):
    """按「编辑接口」弹窗的包围盒裁剪视口截图（去掉周围遮罩背景）"""
    box = await page.evaluate("""
      () => {
        // 编辑接口弹窗嵌套在外层 edit-skill-dialog 的 DOM 里，须按标题栏精确锁定内层弹窗
        const dlg = [...document.querySelectorAll('.el-dialog')]
          .find(d => d.offsetParent
                  && (d.querySelector('.el-dialog__title')?.innerText || '').includes('编辑接口'));
        if (!dlg) return null;
        const r = dlg.getBoundingClientRect();
        const pad = 6;
        return { x: Math.max(0, r.x - pad), y: Math.max(0, r.y - pad),
                 width: r.width + pad * 2, height: r.height + pad * 2 };
      }
    """)
    await page.wait_for_timeout(300)
    p = out(name)
    if box:
        await page.screenshot(path=str(p), clip=box)
    else:
        await page.screenshot(path=str(p))
    print(f"✅ 已保存: {name}")
    report[name] = "ok"


async def cap7_api_query(page: Page):
    """接口1_查询模式_北京：接口查询模式的「配置请求」步（URL/方法/报文模板配置化）"""
    await _open_ifc_edit(page, "beijing", "套餐推荐")
    n = await _hl_ifc_form_items(page, "数据来源", "接口 URL", "请求方法")
    print(f"   🔴 高亮表单项 {n} 个（数据来源/接口 URL/请求方法）")
    await page.mouse.move(5, 5)
    await page.wait_for_timeout(400)
    await shot_ifc_dialog(page, "接口1_查询模式_北京.png")
    await clear_highlight(page)
    await close_dialog(page, ".el-dialog:has(.el-dialog__title:has-text('编辑接口'))")
    await close_dialog(page, ".edit-skill-dialog")


async def cap8_direct_standard(page: Page):
    """接口2_直传通用_广东：透传模式 + 接口规范「通用模式」"""
    await _open_ifc_edit(page, "guangdong", "营销活动")
    n = await _hl_ifc_form_items(page, "数据来源", "接口规范")
    print(f"   🔴 高亮表单项 {n} 个（数据来源/接口规范）")
    await page.mouse.move(5, 5)
    await page.wait_for_timeout(400)
    await shot_ifc_dialog(page, "接口2_直传通用_广东.png")
    await clear_highlight(page)
    await close_dialog(page, ".el-dialog:has(.el-dialog__title:has-text('编辑接口'))")
    await close_dialog(page, ".edit-skill-dialog")


async def cap9_marketing_assistant(page: Page):
    """接口3_营销助手_天津：接口规范「营销助手统一接口」+ 灵运报文说明"""
    await _open_ifc_edit(page, "tianjin", "营销活动")
    n = await _hl_ifc_form_items(page, "数据来源", "接口规范")
    m = await highlight(page, ".el-dialog .ifc-hint-ma", label="营销助手说明")
    print(f"   🔴 高亮表单项 {n} 个 + 营销助手说明 {m} 处")
    await page.mouse.move(5, 5)
    await page.wait_for_timeout(400)
    await shot_ifc_dialog(page, "接口3_营销助手_天津.png")
    await clear_highlight(page)
    await close_dialog(page, ".el-dialog:has(.el-dialog__title:has-text('编辑接口'))")
    await close_dialog(page, ".edit-skill-dialog")


async def capture(only: set[str] | None = None):
    def want(key: str) -> bool:
        return only is None or key in only

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
        page = await context.new_page()

        print(f"🌐 访问 {SKILL_URL}")
        await page.goto(SKILL_URL, wait_until="networkidle", timeout=60000)
        await wait_loading(page, 20000)
        await page.wait_for_timeout(1500)

        # 特点6 首页（最先截，页面最干净）
        if want("6"):
            await cap6_home(page)

        # 特点1 三步管道（北京，含大模型调用）
        if want("1"):
            await cap1_pipeline(page)

        # 特点5 多产品并发（山东全流程用例；失败走 fallback）
        if want("5"):
            ok5 = await cap5_multi_product(page)
            if not ok5:
                print("🔁 特点5 走 fallback(b)：话术匹配 ID 多值配置区")
                await cap5_fallback(page)

        # 特点2 模板优先级链（山东 18 条模板）
        if want("2"):
            await cap2_template_chain(page)

        # 特点3 派生字段（天津）
        if want("3"):
            await cap3_derived_fields(page)

        # 特点4 槽位填槽（TemplateConfig 新建模板弹窗；失败走 fallback）
        if want("4"):
            ok4 = await cap4_slot_filling(page)
            if not ok4:
                print("🔁 特点4 走 fallback：SkillManager 添加模板弹窗")
                await cap4_fallback(page)

        # 接口配置三件套（举措3 配图）
        if want("7"):
            await cap7_api_query(page)
        if want("8"):
            await cap8_direct_standard(page)
        if want("9"):
            await cap9_marketing_assistant(page)

        await browser.close()
        print("\n🎉 全部截图完成")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    # 可传编号只重跑部分截图，如：python3 capture_tech_features.py 1 2 3 5
    only = set(sys.argv[1:]) or None
    asyncio.run(capture(only))
