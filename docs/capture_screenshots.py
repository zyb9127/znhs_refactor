#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
营销话术智能体 - SkillManager 页面截图脚本
输出：docs/用户手册图片/sandbox/*.png
"""
import asyncio
import os
import re
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright, Page, Locator

BASE_URL = "http://localhost:8000/znhs-gray/SkillManager"
OUTPUT_DIR = Path(__file__).parent / "用户手册图片" / "sandbox"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 截图视口
VIEWPORT = {"width": 1440, "height": 900}


def screenshot_path(name: str) -> Path:
    return OUTPUT_DIR / name


async def wait_loading(page: Page, timeout: int = 15000):
    """等待 element-plus 的 v-loading 消失"""
    try:
        await page.wait_for_selector(".el-loading-mask", state="detached", timeout=timeout)
    except Exception:
        pass


async def safe_click(page: Page, selector: str, timeout: int = 10000):
    """等待并点击元素"""
    await page.wait_for_selector(selector, state="visible", timeout=timeout)
    await page.click(selector)


async def screenshot_region(page: Page, path: Path, selector: str | None = None):
    """截图整个页面或某个区域"""
    await page.wait_for_timeout(500)
    if selector:
        elem = await page.wait_for_selector(selector, state="visible", timeout=10000)
        await elem.screenshot(path=str(path))
    else:
        await page.screenshot(path=str(path), full_page=True)
    print(f"✅ 已保存: {path.name}")


async def capture():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport=VIEWPORT)
        page = await context.new_page()

        print(f"🌐 访问 {BASE_URL}")
        await page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
        await wait_loading(page, 20000)
        await page.wait_for_timeout(1000)

        # 01 首页
        await screenshot_region(page, screenshot_path("01_SkillManager首页.png"))

        # 找到 beijing/套餐推荐行，点击编辑
        row_selector = 'text=beijing >> xpath=../../..'
        # 更稳定：先找到包含 beijing 和 套餐推荐的行
        await page.wait_for_selector(".el-table__row", timeout=10000)
        rows = await page.locator(".el-table__row").all()
        target_row = None
        for row in rows:
            text = await row.inner_text()
            if "beijing" in text and "套餐推荐" in text:
                target_row = row
                break
        if not target_row:
            print("❌ 未找到 beijing/套餐推荐 Skill")
            await browser.close()
            return

        # 点击该行的编辑按钮
        edit_btn = target_row.locator("button:has-text('编辑')").first
        await edit_btn.click()
        await page.wait_for_timeout(1500)

        # 02 接口配置
        await screenshot_region(page, screenshot_path("02_编辑Skill_接口配置.png"),
                                selector=".edit-skill-dialog .el-dialog__body")

        # 切换到标准数据关联
        await page.click(".skill-config-editor .el-tabs__item:has-text('标准数据关联')")
        await page.wait_for_timeout(1000)
        await screenshot_region(page, screenshot_path("08_标准数据关联_概览.png"),
                                selector=".edit-skill-dialog .el-dialog__body")

        # 03 话术模板
        await page.click(".skill-config-editor .el-tabs__item:has-text('话术模板')")
        await page.wait_for_timeout(1000)
        await screenshot_region(page, screenshot_path("03_编辑Skill_话术模板.png"),
                                selector=".edit-skill-dialog .el-dialog__body")

        # 04 数据流映射
        await page.click(".skill-config-editor .el-tabs__item:has-text('数据流映射')")
        await page.wait_for_timeout(1000)
        await screenshot_region(page, screenshot_path("04_编辑Skill_数据流映射.png"),
                                selector=".edit-skill-dialog .el-dialog__body")

        # 回到接口配置，点击第一个接口的编辑
        await page.click(".skill-config-editor .el-tabs__item:has-text('接口配置')")
        await page.wait_for_timeout(500)
        await page.click(".skill-config-editor button:has-text('编辑')")
        await page.wait_for_timeout(1000)

        # 10 编辑接口 - 配置请求
        await screenshot_region(page, screenshot_path("10_编辑接口_配置请求.png"),
                                selector=".el-dialog:has(.ifc-steps)")

        # 11 响应样例
        await page.click(".ifc-step:has-text('响应样例')")
        await page.wait_for_timeout(800)
        await screenshot_region(page, screenshot_path("11_编辑接口_Mock数据.png"),
                                selector=".el-dialog:has(.ifc-steps)")

        # 12 出参映射
        await page.click(".ifc-step:has-text('出参')")
        await page.wait_for_timeout(800)
        await screenshot_region(page, screenshot_path("12_编辑接口_出参映射.png"),
                                selector=".el-dialog:has(.ifc-steps)")

        # 关闭编辑接口弹窗
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)

        # 智能映射弹窗
        await page.click(".skill-config-editor button:has-text('智能映射')")
        await page.wait_for_timeout(1000)
        await screenshot_region(page, screenshot_path("13_智能映射弹窗_空.png"),
                                selector=".el-dialog:has-text('智能映射')")

        # 填入示例文档并解析
        sample_doc = """接口名称：查询用户套餐信息
请求方式：POST
请求URL：/api/user/package
响应示例：
{
  "current_package": {"offerName":"128元套餐","fee":128},
  "usage": {"flow":30,"voice":200}
}"""
        await page.fill(".el-dialog:has-text('智能映射') textarea", sample_doc)
        await page.click(".el-dialog:has-text('智能映射') button:has-text('智能解析')")
        await page.wait_for_timeout(2500)
        await screenshot_region(page, screenshot_path("14_智能映射弹窗_分析结果.png"),
                                selector=".el-dialog:has-text('智能映射')")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)

        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)

        # 测试弹窗
        test_btn = target_row.locator("button:has-text('测试')").first
        await test_btn.click()
        await page.wait_for_timeout(1200)
        await page.fill(".skill-test-dialog input[placeholder='15010470528']", "15010470528")
        await page.fill(".skill-test-dialog input[type='number']", "2")
        await screenshot_region(page, screenshot_path("05_测试弹窗.png"),
                                selector=".skill-test-dialog")

        # 执行推荐（等待时间较长）
        await page.click(".skill-test-dialog button:has-text('执行推荐')")
        # 等待状态从"推荐中"变为成功/失败，最多 180 秒
        try:
            await page.wait_for_selector(
                ".tc-status-bar:has-text('推荐成功'), .tc-status-bar:has-text('推荐失败'), .tc-status-bar:has-text('部分成功')",
                timeout=180000
            )
        except Exception as e:
            print(f"⚠️ 等待测试结果超时: {e}")
        await page.wait_for_timeout(1000)
        await screenshot_region(page, screenshot_path("06_测试结果.png"),
                                selector=".skill-test-dialog")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)

        # 创建 Skill
        await page.click("button:has-text('导入新 Skill')")
        await page.wait_for_timeout(1500)
        await screenshot_region(page, screenshot_path("07_创建Skill.png"))
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)

        # 新建话术模板
        # 重新打开编辑
        await edit_btn.click()
        await page.wait_for_timeout(1200)
        await page.click(".skill-config-editor .el-tabs__item:has-text('话术模板')")
        await page.wait_for_timeout(800)
        await page.click(".skill-config-editor button:has-text('新建')")
        await page.wait_for_timeout(1000)
        await screenshot_region(page, screenshot_path("15_新建话术模板_上半部分.png"),
                                selector=".tpl-edit-dialog")

        # 滚动到关联变量区域
        await page.evaluate("""
            const el = document.querySelector('.tpl-edit-dialog .el-dialog__body');
            if (el) el.scrollTop = el.scrollHeight / 3;
        """)
        await page.wait_for_timeout(600)
        await screenshot_region(page, screenshot_path("16_新建话术模板_关联变量.png"),
                                selector=".tpl-edit-dialog")

        # 输入带占位符的内容，触发变量推荐
        await page.fill(".tpl-edit-dialog textarea", "您好！您当前套餐为{cur_brief}，推荐{pkg_brief}，{diff_str}。")
        await page.wait_for_timeout(800)
        await page.evaluate("""
            const el = document.querySelector('.tpl-edit-dialog .el-dialog__body');
            if (el) el.scrollTop = 0;
        """)
        await page.wait_for_timeout(600)
        await screenshot_region(page, screenshot_path("17_新建话术模板_变量推荐.png"),
                                selector=".tpl-edit-dialog")

        # 点击一键勾选
        suggest_btn = page.locator(".tpl-edit-dialog button:has-text('一键勾选')")
        if await suggest_btn.count() > 0:
            await suggest_btn.click()
            await page.wait_for_timeout(800)
            await page.evaluate("""
                const el = document.querySelector('.tpl-edit-dialog .el-dialog__body');
                if (el) el.scrollTop = el.scrollHeight / 3;
            """)
            await page.wait_for_timeout(600)
            await screenshot_region(page, screenshot_path("18_新建话术模板_已勾选变量.png"),
                                    selector=".tpl-edit-dialog")

        await browser.close()
        print("\n🎉 全部截图完成")


if __name__ == "__main__":
    asyncio.run(capture())
