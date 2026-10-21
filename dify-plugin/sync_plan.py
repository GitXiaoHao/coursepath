"""同步可计算培养方案到插件默认资产，避免服务端与 `.difypkg` 规则漂移。

当前仓库只分发合成的车辆工程示范数据；将来换成获授权的真实培养方案时，先更新
``campus-kb/data/samples/plan.json``，再执行本脚本并重新打包插件。
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "campus-kb" / "data" / "samples" / "plan.json"
TARGET = ROOT / "dify-plugin" / "course-audit" / "_assets" / "plan-demo.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not SOURCE.is_file():
        print(f"缺少源培养方案：{SOURCE}")
        return 1
    same = TARGET.is_file() and digest(SOURCE) == digest(TARGET)
    if args.check:
        print("培养方案一致" if same else "培养方案不同步，请运行 python dify-plugin/sync_plan.py")
        return 0 if same else 1
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, TARGET)
    print(f"已同步 {SOURCE.relative_to(ROOT)} → {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
