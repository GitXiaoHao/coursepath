r"""Dify UI 自动化探针：登录 → 应用列表 → 打开 Chatflow → 提问，逐步截图。

用途：视频录制脚本 `record_demo.py` 的前置勘察。产物为 `.build/` 下的 PNG 截图。
用法：.\.tools\rptvenv\Scripts\python.exe video\probe_ui.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost"
EMAIL = "admin@campus-kb.local"
PASSWORD = "CampusKb2026!"
OUT = Path(__file__).resolve().parent / ".build"
APP_NAME = "课径 · 可核算的培养方案问答"


def shot(page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path))
    print(f"  shot -> {path.name} ({path.stat().st_size // 1024} KB)")


def login(page) -> None:
    page.goto(f"{BASE}/signin", wait_until="networkidle", timeout=60_000)
    time.sleep(1.0)
    shot(page, "01-signin")
    page.fill('input[type="email"], input[name="email"]', EMAIL)
    page.fill('input[type="password"], input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda u: "/signin" not in u, timeout=60_000)
    page.wait_for_load_state("networkidle")
    time.sleep(1.5)
    shot(page, "02-after-login")
    print("  login ok ->", page.url)


def open_app(page) -> None:
    page.goto(f"{BASE}/apps", wait_until="networkidle", timeout=60_000)
    time.sleep(1.5)
    shot(page, "03-apps")
    card = page.get_by_text(APP_NAME, exact=False).first
    card.click()
    page.wait_for_load_state("networkidle")
    time.sleep(2.0)
    shot(page, "04-app-open")
    print("  app url ->", page.url)


def start_chat(page, completed: str = "CS101,CS201") -> None:
    """新对话设置：填已修课程 → 开始对话。"""
    box = page.locator('input[placeholder*="已修课程代码"]:visible').first
    box.click()
    box.fill(completed)
    time.sleep(0.4)
    shot(page, "05a-new-chat-filled")
    page.locator('button:has-text("开始对话")').first.click()
    page.wait_for_load_state("networkidle")
    time.sleep(2.0)
    shot(page, "05b-chat-ready")


def ask(page, question: str, tag: str) -> None:
    box = page.locator('textarea:visible').last
    box.click()
    box.fill(question)
    time.sleep(0.4)
    box.press("Enter")
    # 等答案渲染完成：出现「###」结构或超时
    deadline = time.time() + 45
    while time.time() < deadline:
        text = page.inner_text("body")
        if "### " in text or "学分" in text:
            time.sleep(2.0)
            break
        time.sleep(1.0)
    shot(page, tag)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 900},
                                  locale="zh-CN", device_scale_factor=1)
        page = ctx.new_page()
        login(page)
        open_app(page)
        start_chat(page)
        ask(page, "毕业还差多少学分？我已修 CS101 和 CS201", "06-ask-credits")
        ask(page, "编译原理的先修要求是什么？", "07-ask-prereq")
        ask(page, "转专业后学分怎么认定？", "08-ask-clause")
        browser.close()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
