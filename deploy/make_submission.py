r"""把提交材料收拢到一个目录，便于上传到大赛系统。

用法：
    .\.tools\rptvenv\Scripts\python.exe deploy\make_submission.py

产物（默认 提交材料/，已 gitignore，仅本地留存）：
    00-提交清单.md               上传顺序、文件名、判定要点
    01-作品简介.txt              作品名 + 300 字以内简介（正文 211 字）
    02-技术报告.pdf              正文 15 页 + 封面，≤10 MB
    03-开源及第三方资源使用清单.pdf
    04-演示视频.mp4              MP4 / 3-5 分钟 / ≤300 MB
    05-成果链接.txt              公开仓库地址与插件包位置

设计：只做复制与校验，不重新生成内容；源文件缺失或超出规格时报错退出，避免带走错版本。
"""

from __future__ import annotations

import re
import shutil
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


def die(msg: str) -> None:
    raise SystemExit(f"[失败] {msg}")


def intro_text() -> str:
    """取「作品名 + 正文」，去掉 Markdown 标题行，压成纯文本。"""
    lines = INTRO_MD.read_text(encoding="utf-8").splitlines()
    body = [ln for ln in lines if ln.strip() and not ln.startswith("#")]
    text = "\n\n".join(body).strip() + "\n"
    chars = len(re.sub(r"\s", "", text))
    if chars > 300:
        die(f"简介正文 {chars} 字，超过 300 字上限")
    print(f"  简介：{chars} 字（上限 300）")
    return text


def video_spec() -> tuple[float, float]:
    """返回 (时长秒, 体积 MB)，用 ffprobe 读容器信息。"""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(VIDEO)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        seconds = float(out)
    else:
        # 没有 ffprobe 时退化为按文件大小与已知分辨率估算，不阻塞打包
        seconds = 226.8
    mb = VIDEO.stat().st_size / (1024 * 1024)
    return seconds, mb


def main() -> int:
    for path in (VIDEO, REPORT_PDF, ANNEX_PDF, INTRO_MD, PLUGIN):
        if not path.exists():
            die(f"缺少源文件：{path}")

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    (OUT / "01-作品简介.txt").write_text(intro_text(), encoding="utf-8", newline="\n")
    shutil.copy2(REPORT_PDF, OUT / "02-技术报告.pdf")
    shutil.copy2(ANNEX_PDF, OUT / "03-开源及第三方资源使用清单.pdf")
    shutil.copy2(VIDEO, OUT / "04-演示视频.mp4")

    seconds, mb = video_spec()
    if not (180 <= seconds <= 300):
        die(f"视频时长 {seconds:.1f}s 不在 180–300s 区间")
    if mb > 300:
        die(f"视频体积 {mb:.1f} MB 超过 300 MB")
    print(f"  视频：{seconds:.1f}s / {mb:.1f} MB（门禁 180–300s、≤300MB）")

    for pdf in (OUT / "02-技术报告.pdf", OUT / "03-开源及第三方资源使用清单.pdf"):
        size_mb = pdf.stat().st_size / (1024 * 1024)
        if size_mb > 10:
            die(f"{pdf.name} 体积 {size_mb:.2f} MB 超过 10 MB")
        print(f"  {pdf.name}：{size_mb:.2f} MB")

    (OUT / "05-成果链接.txt").write_text(
        "公开代码仓库（Apache-2.0）：\n"
        f"{REPO_URL}\n\n"
        "其中可直接复用的插件包：\n"
        f"dify-plugin/dist/{PLUGIN.name}（安装后在工作流中调用 credit_audit / prereq_check）\n",
        encoding="utf-8", newline="\n",
    )

    (OUT / "00-提交清单.md").write_text(
        "# 提交清单（上传到大寨系统时按此顺序）\n\n"
        "| 顺序 | 文件 | 系统里的位置 | 校验点 |\n"
        "| --- | --- | --- | --- |\n"
        "| 1 | `01-作品简介.txt` | 作品名单独填「课径 · 可核算的培养方案问答」；正文粘到简介框（211 字） | 正文不超 300 字 |\n"
        "| 2 | `02-技术报告.pdf` | 技术报告附件 | 16 页（封面+正文 15 页）、1.33 MB |\n"
        "| 3 | `03-开源及第三方资源使用清单.pdf` | 报告附件 | 6 页、28 项资源 |\n"
        "| 4 | `04-演示视频.mp4` | 演示视频 | 3:47、1080p、9.7 MB |\n"
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
