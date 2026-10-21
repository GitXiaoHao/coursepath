r"""录制 Dify 真实界面演示（webm + 时间轴标记），供 build_video.py 切片。

用法：
    .\.tools\rptvenv\Scripts\python.exe video\record_demo.py

产物：
    video/.build/raw-ui.webm     Playwright 录制的原始画面（1920×1080）
    video/.build/marks.json      关键时间点（相对录制开始，单位秒）

设计：
- 视口 1920×1080，与成片一致，避免二次缩放糊字。
- 每个动作前记录 mark，便于按问题切片与旁白对齐。
- 等答案「文本长度稳定」再继续，避免并发提问被拒（界面会提示「请等待上条信息响应完成」）。
"""

from __future__ import annotations
import os

import json
import shutil
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

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
OUT = Path(__file__).resolve().parent / ".build"
RAW = OUT / "raw-ui.webm"


class Recorder:
    def __init__(self, t0: float | None = None):
        self.t0 = t0 or time.time()
        self.marks: dict[str, float] = {}

    def mark(self, name: str) -> float:
        t = round(time.time() - self.t0, 2)
        self.marks[name] = t
        print(f"  mark {name:18s} {t:7.2f}s")
        return t


def wait_answer(page, label: str, min_wait: float = 3.0, max_wait: float = 60.0) -> None:
    """等回答流式输出结束：文本长度连续 3 次不变。"""
    time.sleep(min_wait)
    last, stable, start = -1, 0, time.time()
    while time.time() - start < max_wait:
        n = len(page.inner_text("body"))
        if n == last:
            stable += 1
            if stable >= 3:
                print(f"    {label} 稳定（{n} 字符）")
                time.sleep(1.2)
                return
        else:
            stable = 0
        last = n
        time.sleep(1.0)
    print(f"    {label} 达到最大等待")


def slow_type(page, box, s: str, per_char: float = 0.045) -> None:
    box.click()
    box.type(s, delay=int(per_char * 1000))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rec = Recorder()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            record_video_dir=str(OUT / "video"),
            record_video_size={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        rec.t0 = time.time()

        # --- 登录
        page.goto(f"{BASE}/signin", wait_until="networkidle", timeout=60_000)
        time.sleep(1.2)
        page.fill('input[name="email"], input[type="email"]', EMAIL)
        page.fill('input[name="password"], input[type="password"]', _require_password())
        rec.mark("login-submit")
        page.click('button[type="submit"]')
        page.wait_for_url(lambda u: "/signin" not in u, timeout=60_000)
        page.wait_for_load_state("networkidle")
        time.sleep(1.5)

        # --- 应用列表 → 打开 Chatflow
        page.goto(f"{BASE}/apps", wait_until="networkidle", timeout=60_000)
        time.sleep(1.8)
        rec.mark("apps-list")
        page.get_by_text(APP_NAME, exact=False).first.click()
        page.wait_for_load_state("networkidle")
        time.sleep(2.2)
        rec.mark("app-open")

        # --- 新对话：填入已修课程
        box = page.locator('input[placeholder*="已修课程代码"]:visible').first
        slow_type(page, box, "EE101,CS101", per_char=0.07)
        time.sleep(0.6)
        rec.mark("new-chat-start")
        page.locator('button:has-text("开始对话")').first.click()
        page.wait_for_load_state("networkidle")
        time.sleep(2.0)
        rec.mark("chat-ready")

        # --- 问答 1：学分
        q1 = "毕业还差多少学分？我已修 EE101 和 CS101"
        box = page.locator("textarea:visible").last
        slow_type(page, box, q1, per_char=0.03)
        time.sleep(0.6)
        rec.mark("q1-send")
        box.press("Enter")
        wait_answer(page, "q1")
        rec.mark("q1-done")

        # --- 问答 2：先修
        q2 = "汽车电子控制能选吗？"
        box = page.locator("textarea:visible").last
        slow_type(page, box, q2, per_char=0.03)
        time.sleep(0.6)
        rec.mark("q2-send")
        box.press("Enter")
        wait_answer(page, "q2")
        rec.mark("q2-done")

        # --- 问答 3：条款
        q3 = "转专业后学分怎么认定？"
        box = page.locator("textarea:visible").last
        slow_type(page, box, q3, per_char=0.03)
        time.sleep(0.6)
        rec.mark("q3-send")
        box.press("Enter")
        wait_answer(page, "q3")
        time.sleep(2.0)
        rec.mark("q3-done")

        video_path = page.video.path()
        ctx.close()
        browser.close()

    shutil.copyfile(video_path, RAW)
    rec.marks["duration"] = round(time.time() - rec.t0, 2)
    (OUT / "marks.json").write_text(json.dumps(rec.marks, ensure_ascii=False, indent=2),
                                    encoding="utf-8", newline="\n")
    print(f"  raw video -> {RAW} ({RAW.stat().st_size // 1024} KB)")
    print(f"  标记 -> {OUT / 'marks.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
