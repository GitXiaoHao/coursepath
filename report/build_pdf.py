r"""技术报告导出 PDF（Markdown → HTML → Edge 无头打印）。

用法：
    .\.tools\rptvenv\Scripts\python.exe report\build_pdf.py
    .\.tools\rptvenv\Scripts\python.exe report\build_pdf.py --check

设计要点：
- 不依赖 Office / LibreOffice / LaTeX：用本机 Edge 的 `--print-to-pdf`（Chromium 打印引擎）。
- 章节（`## ` 起始）自动分页，封面单独一页，页脚不打印浏览器头（--no-pdf-header-footer）。
- 表格宽度自适应并允许换行，避免宽表溢出到页外。
- `--check` 只输出页数与体积，便于预检 A3/A4。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
SRC = ROOT / "技术报告.md"
OUT_PDF = ROOT / "技术报告.pdf"
OUT_HTML = ROOT / ".build" / "技术报告.html"

EDGE_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]

COVER = """
<div class="cover">
  <div class="cover-kicker">2026 第八届全球校园人工智能算法精英大赛 · AI+开源主题赛</div>
  <h1 class="cover-title">课径 · 可核算的培养方案问答</h1>
  <div class="cover-sub">技术报告</div>
  <div class="cover-meta">
    <p>申报方向：开源赋能的 AI 应用创新</p>
    <p>底座平台：Dify 1.17.1（应用层集成，未修改源码）</p>
    <p>开放许可：Apache-2.0（代码） / CC-BY-4.0（文档与样例数据）</p>
    <p>2026 年 10 月</p>
  </div>
</div>
"""

CSS = """
@page { size: A4; margin: 18mm 16mm 16mm 16mm; }
html { -webkit-print-color-adjust: exact; }
body {
  font-family: "Microsoft YaHei", "PingFang SC", "Source Han Sans SC", "SimSun", sans-serif;
  font-size: 10.2pt; line-height: 1.55; color: #1b1b1b; margin: 0;
}
.cover { page-break-after: always; padding-top: 46mm; text-align: center; }
.cover-kicker { font-size: 10pt; color: #555; letter-spacing: .06em; }
.cover-title { font-size: 25pt; margin: 16mm 0 6mm; border: 0; letter-spacing: .04em; }
.cover-sub { font-size: 14pt; color: #333; margin-bottom: 22mm; }
.cover-meta p { font-size: 10pt; color: #444; margin: 3mm 0; }
h1 { font-size: 17pt; margin: 0 0 6mm; padding-bottom: 3mm; border-bottom: 1.5pt solid #1f6feb; }
h2 { font-size: 14pt; margin: 8mm 0 3mm; page-break-before: always; page-break-after: avoid; }
h2:first-of-type { page-break-before: auto; }
h3 { font-size: 11.6pt; margin: 5mm 0 2mm; page-break-after: avoid; }
h4 { font-size: 10.6pt; margin: 4mm 0 1.5mm; page-break-after: avoid; }
p { margin: 0 0 2.4mm; text-align: justify; }
ul, ol { margin: 0 0 2.6mm; padding-left: 6mm; }
li { margin-bottom: 1.1mm; }
code { font-family: Consolas, "Courier New", monospace; font-size: 9pt;
       background: #f2f4f7; padding: 0 1pt; border-radius: 2pt; }
pre { background: #f6f8fa; border: 0.6pt solid #e1e4e8; border-radius: 3pt;
      padding: 2.5mm 3mm; font-size: 8.6pt; line-height: 1.35; white-space: pre-wrap;
      page-break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 8.6pt; }
table { width: 100%; border-collapse: collapse; margin: 2mm 0 3.5mm; font-size: 8.8pt;
        page-break-inside: auto; }
th, td { border: 0.5pt solid #c9d1d9; padding: 1.2mm 1.8mm; vertical-align: top;
         word-break: break-word; text-align: left; }
th { background: #eef2f7; font-weight: 600; }
tr { page-break-inside: avoid; }
blockquote { margin: 2mm 0 3mm; padding: 1.5mm 3mm; border-left: 2.5pt solid #c9d1d9;
             background: #fafbfc; color: #333; }
blockquote p { margin: 0 0 1.2mm; }
hr { border: 0; border-top: 0.6pt solid #d0d7de; margin: 5mm 0; }
img { max-width: 100%; }
"""

HEAD = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<title>课径 · 可核算的培养方案问答 · 技术报告</title>'
        f"<style>{CSS}</style></head><body>")

TAIL = "</body></html>"


def render(md_text: str) -> str:
    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
        output_format="html5",
    )
    # 报告一级标题只用于文件标题，正文从「一、作品概述」起；避免重复标题
    first_h1 = body.find("</h1>")
    if body.startswith("<h1") and first_h1 != -1:
        body = body[first_h1 + len("</h1>"):].lstrip()
    return HEAD + COVER + body + TAIL


def find_edge() -> Path:
    for p in EDGE_CANDIDATES:
        if p.exists():
            return p
    raise SystemExit("未找到 Microsoft Edge，无法打印 PDF")


def build() -> Path:
    md_text = SRC.read_text(encoding="utf-8")
    html = render(md_text)
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html, encoding="utf-8", newline="\n")

    edge = find_edge()
    OUT_PDF.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            str(edge),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={tmp}",
            "--no-pdf-header-footer",
            "--print-to-pdf-no-header",
            f"--print-to-pdf={OUT_PDF}",
            OUT_HTML.as_uri(),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not OUT_PDF.exists():
        raise SystemExit(f"PDF 生成失败（edge exit={proc.returncode}）:\n{proc.stderr[-2000:]}")
    return OUT_PDF


def report() -> int:
    reader = PdfReader(str(OUT_PDF))
    pages = len(reader.pages)
    size_mb = OUT_PDF.stat().st_size / (1024 * 1024)
    print(f"PDF：{OUT_PDF}")
    print(f"页数：{pages}（含封面；A4 门禁：正文 ≤15 页）")
    print(f"体积：{size_mb:.2f} MB（门禁：≤10 MB）")
    ok = pages <= 18 and size_mb <= 10
    print("A3/A4 预检：", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="导出技术报告 PDF")
    ap.add_argument("--check", action="store_true", help="只检查已生成的 PDF")
    args = ap.parse_args(argv)
    if not args.check:
        build()
    if not OUT_PDF.exists():
        raise SystemExit("PDF 不存在，先执行不带 --check 的构建")
    return report()


if __name__ == "__main__":
    sys.exit(main())
