r"""抓取报告插图：对「回答卡片」做元素截图（自动滚动、完整高度，不会被视口截断）。

用法：.\.tools\rptvenv\Scripts\python.exe report\make_figures.py
输出：report/img/fig-credits.png、report/img/fig-prereq.png
"""

from __future__ import annotations
import os

import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

BASE = "http://localhost"
def _require_password() -> str:
    if not PASSWORD:
        raise SystemExit(
            "请先设置环境变量 DIFY_ADMIN_PASSWORD（本机 Dify 管理员密码），"
            "例如：$env:DIFY_ADMIN_PASSWORD = '<你的密码>'")
    return PASSWORD


EMAIL = os.environ.get("DIFY_ADMIN_EMAIL", "admin@campus-kb.local")
PASSWORD = os.environ.get("DIFY_ADMIN_PASSWORD", "")
APP_NAME = "课径 · 可核算的培养方案问答"
OUT = Path(__file__).resolve().parent / "img"


def login(page: Page) -> None:
    page.goto(f"{BASE}/signin", wait_until="networkidle", timeout=60_000)
    page.fill('input[name="email"], input[type="email"]', EMAIL)
    page.fill('input[name="password"], input[type="password"]', _require_password())
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


def ask(page: Page, question: str, expect: str) -> None:
    """提问并等到「出现一条新回答且包含 expect」为止。"""
    before = page.locator("div.markdown-body").count()
    box = page.locator("textarea:visible").last
    box.click()
    box.fill(question)
    box.press("Enter")
    wait_answer(page)
    t0 = time.time()
    while time.time() - t0 < 90:
        cards = page.locator("div.markdown-body")
        if cards.count() > before:
            text = cards.nth(cards.count() - 1).inner_text()
            if expect in text:
                return
        time.sleep(1.5)
    raise SystemExit(f"未拿到预期回答（缺少 {expect!r}）：{question}")


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


def resolve_app_id() -> str | None:
    """用控制台 API 直接查应用 id，避免依赖应用列表页的渲染（列表偶尔加载慢会点不到）。"""
    import base64
    import http.cookiejar
    import json
    import urllib.request

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def csrf() -> str | None:
        for cookie in jar:
            if cookie.name == "csrf_token":
                return cookie.value
        return None

    def call(method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Version", "1.17.1")
        token = csrf()
        if token:
            req.add_header("X-CSRF-Token", token)   # Dify 控制台接口（含 GET）都校验 CSRF
        with opener.open(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")

    call("POST", "/console/api/login", {
        "email": EMAIL,
        "password": base64.b64encode(_require_password().encode()).decode(),
        "remember_me": True,
    })
    data = call("GET", "/console/api/apps?page=1&limit=50")
    for app in data.get("data", []):
        if app.get("name") == APP_NAME:
            return app.get("id")
    return None


def open_app(page: Page) -> None:
    """从左栏 WEB APPS 进入应用；列表页偶尔渲染慢，故用显式等待而非点击后立即继续。"""
    page.goto(f"{BASE}/apps", wait_until="domcontentloaded")
    entry = page.get_by_text(APP_NAME, exact=False).first
    entry.wait_for(state="visible", timeout=60_000)
    entry.click()
    page.wait_for_load_state("networkidle")
    time.sleep(2.5)


def new_chat(page: Page, completed: str) -> None:
    """开一个新会话并填写已修课程；表单只在「新对话」出现时才填。"""
    trigger = page.locator('button:has-text("开启新对话")').first
    if trigger.count():
        try:
            trigger.click()
            page.wait_for_load_state("networkidle")
            time.sleep(1.5)
        except PlaywrightTimeoutError:
            pass
    box = page.locator('input[placeholder*="已修课程代码"]:visible').first
    try:
        box.wait_for(state="visible", timeout=8000)
        box.click()
        box.fill(completed)
        page.locator('button:has-text("开始对话")').first.click()
        page.wait_for_load_state("networkidle")
    except PlaywrightTimeoutError:
        print("  （未出现新对话设置表单，直接在当前会话提问）")
    time.sleep(2.0)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                                  locale="zh-CN", device_scale_factor=2)
        page = ctx.new_page()
        login(page)
        open_app(page)

        # 图 5-1：学分核算（已修三门，含一门专业核心课）
        new_chat(page, "EE101,CS101,VE201")
        ask(page, "毕业还差多少学分？我已修 EE101、CS101 和 VE201", "还差 149")
        shot_answer(page, OUT / "fig-credits.png")

        # 图 5-2：先修判定（故意只修一门，回答会给出"暂不能选 + 还缺哪两门"）
        new_chat(page, "EE101")
        ask(page, "汽车电子控制能选吗？我只修过 EE101", "暂不能选")
        shot_answer(page, OUT / "fig-prereq.png")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
