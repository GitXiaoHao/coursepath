"""把 campus-kb 的确定性内核同步进插件包（单一源码，避免两份实现漂移）。

为什么要这一步：插件要能在**没有任何外部服务**的情况下独立完成核算，因此必须自带
`schema / graph / scoring` 三个纯 Python 模块；但这三个模块同时是 campus-kb 服务的
内核。手抄两份必然漂移，所以用脚本从 `campus-kb/core/` 复制，并在文件头写入生成标记。

用法：
    python dify-plugin/sync_core.py          # 复制
    python dify-plugin/sync_core.py --check  # 只校验是否一致（CI/预检用）
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "campus-kb" / "core"
DST = Path(__file__).resolve().parent / "course-audit" / "core"

MODULES = ("schema.py", "graph.py", "scoring.py")

INIT = '''"""课径核算内核（由 dify-plugin/sync_core.py 从 campus-kb/core 生成，勿手改）。"""

from . import graph, schema, scoring  # noqa: F401
'''


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args()

    missing = [m for m in MODULES if not (SRC / m).is_file()]
    if missing:
        print(f"缺少源文件：{missing}")
        return 1

    diffs = []
    for name in MODULES:
        src = SRC / name
        dst = DST / name
        if not dst.is_file() or digest(src) != digest(dst):
            diffs.append(name)

    if args.check:
        if diffs:
            print("内核不同步，请运行 python dify-plugin/sync_core.py：" + "、".join(diffs))
            return 1
        print("内核一致：" + "、".join(MODULES))
        return 0

    DST.mkdir(parents=True, exist_ok=True)
    for name in MODULES:
        shutil.copyfile(SRC / name, DST / name)
    (DST / "__init__.py").write_text(INIT, encoding="utf-8")
    changed = "、".join(diffs) if diffs else "无变化"
    print(f"已同步 {len(MODULES)} 个模块到 {DST.relative_to(ROOT)}（{changed}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
