r"""把插件目录打成 .difypkg（纯标准库实现，不依赖 dify-plugin 开发 CLI）。

.difypkg 就是一个 zip 包，根目录放 manifest.yaml。官方 CLI（`dify plugin package`）
打的包与本脚本打的包内容一致，本脚本用于第三方可复现地重新打包。

用法：
    .\.tools\rptvenv\Scripts\python.exe dify-plugin\pack.py
    .\.tools\rptvenv\Scripts\python.exe dify-plugin\pack.py --dir dify-plugin\course-audit --check

--check 只比对待打包文件清单，不写产物。
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXCLUDE_DIRS = {"__pycache__", ".venv", ".pytest_cache", "dist", ".git", ".github"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".log", ".difypkg"}
EXCLUDE_NAMES = {".env", ".DS_Store", ".gitkeep", ".gitignore"}


def manifest_field(text: str, key: str) -> str:
    match = re.search(rf"^{key}:\s*(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip("\"'") if match else ""


def collect(src: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() in EXCLUDE_SUFFIX or path.name in EXCLUDE_NAMES:
            continue
        files.append(path)
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=str(HERE / "course-audit"))
    parser.add_argument("--out", default=str(HERE / "dist"))
    parser.add_argument("--check", action="store_true", help="只列清单，不写产物")
    args = parser.parse_args()

    src = Path(args.dir).resolve()
    if not src.is_dir():
        print(f"目录不存在：{src}", file=sys.stderr)
        return 1
    manifest = src / "manifest.yaml"
    if not manifest.exists():
        print(f"缺少 manifest.yaml：{manifest}", file=sys.stderr)
        return 1

    text = manifest.read_text(encoding="utf-8")
    author = manifest_field(text, "author")
    name = manifest_field(text, "name")
    version = manifest_field(text, "version")
    for field, value in (("author", author), ("name", name), ("version", version)):
        if not value:
            print(f"manifest.yaml 缺少 {field}", file=sys.stderr)
            return 1

    files = collect(src)
    print(f"待打包 {len(files)} 个文件（{src.name} v{version}）")
    for path in files:
        print(f"  {path.relative_to(src).as_posix()}")
    if args.check:
        return 0

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{name}-{version}.difypkg"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, arcname=path.relative_to(src).as_posix())
    print(f"\n产物：{target}（{target.stat().st_size / 1024:.1f} KB）")
    print("安装：Dify → 集成 → 安装 → 本地集成包")
    return 0


if __name__ == "__main__":
    sys.exit(main())
