r"""生成演示视频的静态画面（1920×1080 PNG）。

用法：.\.tools\rptvenv\Scripts\python.exe video\make_slides.py
产物：video/.build/slide-*.png
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
OUT = Path(__file__).resolve().parent / ".build"

FONT_DIR = Path(r"C:\Windows\Fonts")
F_BOLD = FONT_DIR / "msyhbd.ttc"   # 微软雅黑 粗体
F_REG = FONT_DIR / "msyh.ttc"      # 微软雅黑

BG = (248, 249, 251)
INK = (24, 28, 33)
MUTED = (96, 105, 115)
ACCENT = (31, 111, 235)
ACCENT_SOFT = (232, 240, 254)
LINE = (214, 220, 228)
WHITE = (255, 255, 255)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = F_BOLD if bold else F_REG
    if not path.exists():
        path = F_REG
    return ImageFont.truetype(str(path), size)


def canvas(bg=BG) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), bg)
    return img, ImageDraw.Draw(img)


def text(d: ImageDraw.ImageDraw, xy, s: str, size=40, bold=False, fill=INK,
         spacing=1.3, anchor="la") -> int:
    """支持换行的文本绘制，返回下一行的 y。"""
    f = font(size, bold)
    x, y = xy
    line_h = int(size * spacing)
    for line in s.split("\n"):
        d.text((x, y), line, font=f, fill=fill, anchor=anchor)
        y += line_h
    return y


def footer(d: ImageDraw.ImageDraw, page: str = "") -> None:
    d.line([(100, H - 96), (W - 100, H - 96)], fill=LINE, width=2)
    text(d, (100, H - 70), "课径 · 可核算的培养方案问答", 24, fill=MUTED)
    if page:
        d.text((W - 100, H - 70), page, font=font(24), fill=MUTED, anchor="ra")


def rounded(d, box, radius=18, fill=WHITE, outline=LINE, width=2):
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def arrow(d, x1, y1, x2, y2, color=ACCENT, width=4):
    d.line([(x1, y1), (x2, y2)], fill=color, width=width)
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    size = 14
    for sign in (1, -1):
        d.line([(x2, y2), (x2 - size * math.cos(ang - sign * 0.44),
                          y2 - size * math.sin(ang - sign * 0.44))], fill=color, width=width)


# ---------------------------------------------------------------- slides

def slide_title() -> Image.Image:
    img, d = canvas()
    d.rectangle([(0, 0), (W, 12)], fill=ACCENT)
    text(d, (W // 2, 300), "课径", 132, bold=True, anchor="ma")
    text(d, (W // 2, 470), "可核算的培养方案问答", 72, bold=True, anchor="ma")
    text(d, (W // 2, 600), "答案有出处 · 数字能复算 · 边界可核查", 40, fill=MUTED, anchor="ma")
    text(d, (W // 2, 760), "2026 第八届全球校园人工智能算法精英大赛 · AI+开源主题赛", 32,
         fill=MUTED, anchor="ma")
    text(d, (W // 2, 830), "基于 Dify 1.17.1 应用层集成 · Apache-2.0", 32, fill=ACCENT, anchor="ma")
    return img


def slide_pain() -> Image.Image:
    img, d = canvas()
    text(d, (100, 90), "现状：三个硬伤", 64, bold=True)
    text(d, (100, 190), "学生问「还差多少学分」「这门课能不能选」，得到的答案往往不能用", 32, fill=MUTED)

    items = [
        ("机械检索", "文档切片后按相似度返回片段，跨页表格被切碎，找不到「专业核心课 46 学分」这一行"),
        ("模型算术", "把学分交给大模型求和，减法出错、超额冲抵、不区分「方案内课程」"),
        ("无据可依", "答案没有页码与行号，学生无法逐条核对；出错也说不清错在哪一步"),
    ]
    y = 270
    for i, (head, body) in enumerate(items, 1):
        rounded(d, [(100, y), (W - 100, y + 200)])
        d.ellipse([(140, y + 62), (216, y + 138)], fill=ACCENT_SOFT)
        d.text((178, y + 100), str(i), font=font(48, True), fill=ACCENT, anchor="mm")
        text(d, (250, y + 52), head, 44, bold=True)
        text(d, (250, y + 116), body, 30, fill=MUTED)
        y += 232
    footer(d, "1 / 6")
    return img


def slide_arch() -> Image.Image:
    img, d = canvas()
    text(d, (100, 90), "我们的做法：把「算」和「说」分开", 60, bold=True)
    text(d, (100, 185), "检索负责找依据，确定性代码负责算结论，模型只负责组织语言", 32, fill=MUTED)

    # Dify 应用层
    rounded(d, [(100, 260), (W - 100, 470)], fill=ACCENT_SOFT, outline=(198, 216, 250))
    text(d, (130, 285), "Dify 应用层（未修改任何源码）", 32, bold=True, fill=ACCENT)
    boxes = ["Chatflow\n意图分流", "外部知识库\n混合检索", "HTTP 节点\n调用核算", "回答节点\n引用 + 结论"]
    bw, gap = 360, 44
    x = 150
    for b in boxes:
        rounded(d, [(x, 340), (x + bw, 440)], fill=WHITE, outline=(198, 216, 250))
        d.multiline_text((x + bw // 2, 390), b, font=font(28, True), fill=INK,
                         anchor="mm", align="center", spacing=6)
        x += bw + gap

    # 自研服务
    rounded(d, [(100, 540), (W - 100, 830)])
    text(d, (130, 565), "自研服务 campus-kb（FastAPI，Docker 部署）", 32, bold=True)
    items = [
        ("表格解析器", "Markdown / PDF 双来源\n表头别名 + 剪枝规则"),
        ("混合检索", "BM25Plus + 意图加权\n过滤先行 + 证据门槛"),
        ("核算引擎", "先修表达式树（且/或/学分）\nR1–R5 规则，非模型算术"),
        ("可复现评测", "检索 12 / 核算 7 / 先修 10\n脚本一键出数"),
    ]
    bw, gap = 400, 40
    x = 150
    for head, body in items:
        rounded(d, [(x, 620), (x + bw, 790)], fill=BG, outline=LINE)
        text(d, (x + 24, 640), head, 30, bold=True)
        d.multiline_text((x + 24, 690), body, font=font(24), fill=MUTED, spacing=8)
        x += bw + gap

    arrow(d, 480, 480, 480, 530)
    text(d, (500, 492), "SSRF 白名单放行后直连", 22, fill=MUTED)
    footer(d, "2 / 6")
    return img


def slide_metrics() -> Image.Image:
    img, d = canvas()
    text(d, (100, 90), "技术亮点：每一项都能复跑", 60, bold=True)
    text(d, (100, 185), "162 项测试 · 评测脚本退出码 0 · 指标与报告数字一一对应", 32, fill=MUTED)

    cards = [
        ("Recall@1", "0.833 → 1.000", "查询意图加权（1.15×）修正排序"),
        ("MRR", "0.896 → 1.000", "同名课程在两张表里不再混淆"),
        ("学分核算", "7 / 7", "含超额不冲抵、方案外课程不认定"),
        ("先修判定", "10 / 10", "且/或/学分三类表达式全覆盖"),
    ]
    bw, bh, gap = 830, 250, 60
    for i, (head, big, note) in enumerate(cards):
        x = 100 + (i % 2) * (bw + gap)
        y = 270 + (i // 2) * (bh + gap)
        rounded(d, [(x, y), (x + bw, y + bh)])
        text(d, (x + 40, y + 30), head, 30, fill=MUTED)
        text(d, (x + 40, y + 80), big, 68, bold=True, fill=ACCENT)
        text(d, (x + 40, y + 178), note, 26, fill=MUTED)
    footer(d, "3 / 6")
    return img


def slide_open() -> Image.Image:
    img, d = canvas()
    text(d, (100, 90), "开放成果与合规边界", 60, bold=True)

    left = [
        ("Apache-2.0 代码开源", "服务、插件、DSL、部署脚本全部公开，含快速开始与 162 项测试"),
        ("CC-BY-4.0 数据与文档", "合成培养方案样例、评测用例、集成踩坑文档"),
        ("可复用插件", "course-audit.difypkg：两个工具，内核随包分发，装即用"),
    ]
    y = 210
    for head, body in left:
        rounded(d, [(100, y), (900, y + 170)])
        text(d, (130, y + 30), head, 32, bold=True)
        text(d, (130, y + 90), body, 26, fill=MUTED)
        y += 200

    text(d, (980, 190), "合规核查", 40, bold=True)
    lines = [
        "Dify 修改版 Apache-2.0：",
        "  · 单租户自用，未触发商业许可",
        "  · 未改 web/ 与 LOGO",
        "",
        "未引入 AGPL 组件：",
        "  · PDF 解析用 MIT 的 pdfplumber",
        "  · 规避 PyMuPDF / Ghostscript",
        "",
        "数据边界：",
        "  · 仓库无真实培养方案与学生数据",
        "  · 真实文档仅本地演示，已 git 忽略",
    ]
    d.multiline_text((980, 260), "\n".join(lines), font=font(28), fill=INK, spacing=12)
    footer(d, "4 / 6")
    return img


def slide_end() -> Image.Image:
    img, d = canvas()
    d.rectangle([(0, 0), (W, 12)], fill=ACCENT)
    text(d, (W // 2, 340), "课径 · 可核算的培养方案问答", 76, bold=True, anchor="ma")
    text(d, (W // 2, 470), "把培养方案从「能搜到」做到「能算清、能核对」", 40, fill=MUTED, anchor="ma")
    rounded(d, [(560, 580), (W - 560, 700)], fill=ACCENT_SOFT, outline=(198, 216, 250))
    d.multiline_text((W // 2, 640), "开源仓库 · Chatflow DSL · .difypkg 插件", font=font(34, True),
                     fill=ACCENT, anchor="mm", align="center")
    text(d, (W // 2, 800), "Apache-2.0 / CC-BY-4.0", 28, fill=MUTED, anchor="ma")
    return img


SLIDES = {
    "slide-1-title": slide_title,
    "slide-2-pain": slide_pain,
    "slide-3-arch": slide_arch,
    "slide-4-metrics": slide_metrics,
    "slide-5-open": slide_open,
    "slide-6-end": slide_end,
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in SLIDES.items():
        path = OUT / f"{name}.png"
        fn().save(path)
        print(f"  {path.name} ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
