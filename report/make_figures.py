r"""抓取报告插图：对「回答卡片」做元素截图（自动滚动、完整高度，不会被视口截断）。

用法：.\.tools\rptvenv\Scripts\python.exe report\make_figures.py
输出：report/img/fig-credits.png、report/img/fig-prereq.png
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = "http://localhost"
EMAIL = "admin@campus-kb.local"
PASSWORD = "CampusKb2026!"
APP_NAME = "课径 · 可核算的培养方案问答"
OUT = Path(__file__).resolve().parent / "img"


def login(page: Page) -> None:
    page.goto(f"{BASE}/signin", wait_until="networkidle", timeout=60_000)
    page.fill('input[name="email"], input[type="email"]', EMAIL)
    page.fill('input[name="password"], input[type="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda u: "/signin" not in u, timeout=60_000)
    page.wait_for_load_state("networkidle")


def wait_answer(page: Page, min_wait: float = 3.0) -> None:
    time.sleep(min_wait)
    last, stable, t0 = -1, 0, time.time()
    while time.time() - t0 < 60:
        n = len(page.inner_text("body"))
        if n == last:
            stable += 1
            if stable >= 3:
                time.sleep(1.0)
                return
        else:
            stable = 0
        last = n
        time.sleep(1.0)


def ask(page: Page, question: str) -> None:
    box = page.locator("textarea:visible").last
    box.click()
    box.fill(question)
    box.press("Enter")
    wait_answer(page)


def shot_answer(page: Page, path: Path) -> None:
    """截取最后一条回答（含其后的引用与脚注）完整高度。"""
    cards = page.locator("div.markdown-body")
    n = cards.count()
    if n == 0:
        raise SystemExit("找不到 .markdown-body，界面结构可能变了")
    el = cards.nth(n - 1)
    el.scroll_into_view_if_needed()
    time.sleep(0.4)
    el.screenshot(path=str(path))
    print(f"  {path.name} {el.bounding_box()}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                                  locale="zh-CN", device_scale_factor=2)
        page = ctx.new_page()
        login(page)
        page.goto(f"{BASE}/apps", wait_until="networkidle")
        page.get_by_text(APP_NAME, exact=False).first.click()
        page.wait_for_load_state("networkidle")
        time.sleep(2.0)

        box = page.locator('input[placeholder*="已修课程代码"]:visible').first
        box.click()
        box.fill("CS101,CS201")
        page.locator('button:has-text("开始对话")').first.click()
        page.wait_for_load_state("networkidle")
        time.sleep(2.0)

        ask(page, "毕业还差多少学分？我已修 CS101 和 CS201")
        shot_answer(page, OUT / "fig-credits.png")

        ask(page, "编译原理的先修要求是什么？我修过 CS101 和 CS201")
        shot_answer(page, OUT / "fig-prereq.png")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
