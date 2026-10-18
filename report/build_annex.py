r"""把《开源及第三方资源使用清单》导出为 PDF（作为技术报告的附件提交）。

用法：
    .\.tools\rptvenv\Scripts\python.exe report\build_annex.py
    .\.tools\rptvenv\Scripts\python.exe report\build_annex.py --check

产物：report/开源及第三方资源使用清单.pdf
"""

from __future__ import annotations

import argparse
import sys

from pypdf import PdfReader

from build_pdf import ROOT, build_annex

SRC = ROOT / "开源及第三方资源使用清单.md"
OUT = ROOT / "开源及第三方资源使用清单.pdf"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="导出资源清单附件 PDF")
    ap.add_argument("--check", action="store_true", help="只检查已生成的 PDF")
    args = ap.parse_args(argv)
    if not args.check:
        build_annex(SRC, OUT, with_cover=False)
    if not OUT.exists():
        raise SystemExit("PDF 不存在，先不带 --check 运行一次")
    pages = len(PdfReader(str(OUT)).pages)
    size_mb = OUT.stat().st_size / (1024 * 1024)
    print(f"PDF：{OUT}")
    print(f"页数：{pages}　体积：{size_mb:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
