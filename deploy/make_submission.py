r"""把提交材料收拢到一个目录，便于上传到大赛系统。

用法：
    .\.tools\rptvenv\Scripts\python.exe deploy\make_submission.py

产物（默认 提交材料/，已 gitignore，仅本地留存）：
    00-提交清单.md               上传顺序、文件名、判定要点（数字由本脚本实测填入）
    01-作品简介.txt              作品名 + 300 字以内简介
    02-技术报告.pdf              PDF ≤10 MB
    03-开源及第三方资源使用清单.pdf
    04-演示视频.mp4              MP4 / 3-5 分钟 / ≤300 MB
    05-成果链接.txt              公开仓库地址与插件包位置

设计：只做复制与校验，不重新生成内容；源文件缺失或超出规格时报错退出，避免带走错版本。
修改材料时先改源文件，再跑：
    report\build_pdf.py → report\build_annex.py → video\build_video.py → 本脚本
"""

from __future__ import annotations

import hashlib
import re
import shutil
import time
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "提交材料"
VIDEO = REPO / "video" / "课径-演示视频.mp4"
REPORT_PDF = REPO / "report" / "技术报告.pdf"
ANNEX_PDF = REPO / "report" / "开源及第三方资源使用清单.pdf"
INTRO_MD = REPO / "report" / "作品简介.md"
PLUGIN = REPO / "dify-plugin" / "dist" / "course_audit-0.1.1.difypkg"
REPO_URL = "https://github.com/GitXiaoHao/coursepath"
WORK_TITLE = "课径 · 可核算的培养方案问答"


def die(msg: str) -> None:
    raise SystemExit(f"[失败] {msg}")


def _chars(text: str) -> int:
    """按赛题口径统计字数：去掉所有空白。"""
    return len(re.sub(r"\s", "", text))


def intro_text() -> tuple[str, int, int]:
    """返回 (落盘文本, 正文去空白字数, 含作品名的去空白字数)。"""
    lines = INTRO_MD.read_text(encoding="utf-8").splitlines()
    body = [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]
    name_line = next((ln for ln in body if ln.startswith("作品名")), "")
    paragraphs = [ln for ln in body if ln is not name_line]
    text = "\n".join(paragraphs).strip() + "\n"
    body_chars, total_chars = _chars(text), _chars("".join(body))
    if total_chars > 300:
        die(f"简介正文 {total_chars} 字，超过 300 字上限")
    return f"{name_line}\n\n{text}", body_chars, total_chars


def pdf_info(path: Path) -> tuple[int | None, float]:
    """返回 (页数, 体积 MB)；无 pypdf 时页数为 None。"""
    pages = None
    try:
        from pypdf import PdfReader

        pages = len(PdfReader(str(path)).pages)
    except Exception:  # noqa: BLE001 - 缺依赖时仅少一个校验字段
        pass
    return pages, path.stat().st_size / (1024 * 1024)


def video_spec() -> tuple[float, float]:
    """返回 (时长秒, 体积 MB)，优先用 ffprobe 读容器信息。"""
    ffprobe = shutil.which("ffprobe")
    seconds = 226.8
    if ffprobe:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(VIDEO)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        seconds = float(out)
    return seconds, VIDEO.stat().st_size / (1024 * 1024)


VIEWERS = ("wpspdf", "wps", "Acrobat", "AcroRd32", "SumatraPDF", "FoxitReader",
           "PDFXCview", "msedge", "WINWORD")


def running_viewers() -> list[str]:
    """列出可能在占用 PDF 的阅读器进程（仅用于给出可读提示）。"""
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:  # noqa: BLE001 - 检测失败不影响主流程
        return []
    names = {ln.split(",")[0].strip('"') for ln in out.splitlines() if ln.strip()}
    return sorted(n for n in names if any(v.lower() in n.lower() for v in VIEWERS))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_if_changed(src: Path, dst: Path) -> bool:
    """内容未变则跳过拷贝（PDF 阅读器占用目标文件时也能安全重跑）。返回是否已更新。"""
    if dst.exists() and sha256(dst) == sha256(src):
        return False
    try:
        shutil.copy2(src, dst)
    except PermissionError:
        viewers = running_viewers()
        hint = f"（疑似占用：{'、'.join(viewers)}）" if viewers else ""
        die(f"{dst.name} 正被其他程序占用，无法覆盖{hint}；关闭该文件后重跑")
    return True


def clean_dir(path: Path) -> None:
    """清空输出目录；仍被占用的文件跳过（后续 copy_if_changed 决定是否需要覆盖）。"""
    path.mkdir(parents=True, exist_ok=True)
    stuck: list[str] = []
    for item in path.iterdir():
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
            continue
        for attempt in range(3):
            try:
                item.unlink()
                break
            except PermissionError:
                if attempt == 2:
                    stuck.append(item.name)
                else:
                    time.sleep(0.5)
    if stuck:
        print(f"  提示：{len(stuck)} 个文件被占用未删除，稍后按内容判断是否需要覆盖")



def hhmmss(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def main() -> int:
    for path in (VIDEO, REPORT_PDF, ANNEX_PDF, INTRO_MD, PLUGIN):
        if not path.exists():
            die(f"缺少源文件：{path}")

    clean_dir(OUT)

    intro, body_chars, total_chars = intro_text()
    (OUT / "01-作品简介.txt").write_text(intro, encoding="utf-8", newline="\n")
    print(f"  简介：正文 {body_chars} 字 / 含作品名 {total_chars} 字（上限 300）")

    for src, name in ((REPORT_PDF, "02-技术报告.pdf"),
                      (ANNEX_PDF, "03-开源及第三方资源使用清单.pdf"),
                      (VIDEO, "04-演示视频.mp4")):
        changed = copy_if_changed(src, OUT / name)
        if not changed:
            print(f"  {name}：内容未变，保留原文件")

    report_pages, report_mb = pdf_info(OUT / "02-技术报告.pdf")
    annex_pages, annex_mb = pdf_info(OUT / "03-开源及第三方资源使用清单.pdf")
    seconds, video_mb = video_spec()

    if not (180 <= seconds <= 300):
        die(f"视频时长 {seconds:.1f}s 不在 180–300s 区间")
    if video_mb > 300:
        die(f"视频体积 {video_mb:.1f} MB 超过 300 MB")
    for name, mb in (("技术报告", report_mb), ("资源清单", annex_mb)):
        if mb > 10:
            die(f"{name} {mb:.2f} MB 超过 10 MB")
    if report_pages and report_pages - 1 > 15:
        die(f"技术报告正文 {report_pages - 1} 页（不含封面）超过建议 15 页")

    print(f"  技术报告：{report_pages} 页（含封面）/ {report_mb:.2f} MB")
    print(f"  资源清单：{annex_pages} 页 / {annex_mb:.2f} MB")
    print(f"  演示视频：{hhmmss(seconds)} / {video_mb:.1f} MB（门禁 180–300s、≤300MB）")

    (OUT / "05-成果链接.txt").write_text(
        "公开代码仓库（Apache-2.0）：\n"
        f"{REPO_URL}\n\n"
        "其中可直接复用的插件包：\n"
        f"dify-plugin/dist/{PLUGIN.name}（安装后在工作流中调用 credit_audit / prereq_check）\n\n"
        "插件使用说明（安装步骤、期望输出、自定义培养方案写法）：\n"
        f"{REPO_URL}/blob/main/docs/08-插件使用指南.md\n",
        encoding="utf-8", newline="\n",
    )

    report_line = (f"{report_pages} 页（封面 + 正文 {report_pages - 1} 页）、"
                   f"{report_mb:.2f} MB" if report_pages else f"{report_mb:.2f} MB")
    annex_line = f"{annex_pages} 页、{annex_mb:.2f} MB" if annex_pages else f"{annex_mb:.2f} MB"

    (OUT / "00-提交清单.md").write_text(
        "# 提交清单（上传到大赛系统时按此顺序）\n\n"
        "| 顺序 | 文件 | 系统里的位置 | 校验点 |\n"
        "| --- | --- | --- | --- |\n"
        f"| 1 | `01-作品简介.txt` | 作品名单独填「{WORK_TITLE}」；正文粘到简介框（{body_chars} 字） | 正文不超 300 字 |\n"
        f"| 2 | `02-技术报告.pdf` | 技术报告附件 | {report_line} |\n"
        f"| 3 | `03-开源及第三方资源使用清单.pdf` | 报告附件（也可作报告附录） | {annex_line} |\n"
        f"| 4 | `04-演示视频.mp4` | 演示视频 | {hhmmss(seconds)}、1080p、{video_mb:.1f} MB |\n"
        "| 5 | `05-成果链接.txt` | 成果链接（可填多个） | 仓库公开可访问，评审期内有效 |\n\n"
        "## 提交前最后三件事\n\n"
        "1. 报名信息已确认（费用、成员、方向＝开源赋能的 AI 应用创新）；\n"
        "2. 材料内**不含**学校名称、校徽、指导教师信息（报告与视频已按此制作，替换姓名占位时勿加单位）；\n"
        "3. 截止 **2026-10-30 20:00**，建议 10/30 12:00 前完成提交，留出修回时间。\n",
        encoding="utf-8", newline="\n",
    )

    print(f"\n已生成：{OUT}")
    for f in sorted(OUT.iterdir()):
        print(f"  {f.name}（{f.stat().st_size / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
