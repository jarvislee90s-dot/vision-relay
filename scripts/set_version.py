#!/usr/bin/env python3
"""发布版本号对齐：一次写入三处（spec 2026-08-26 §6 版本一致性）。

用法：python scripts/set_version.py X.Y.Z [--root DIR]（默认仓库根）。
tag v<X.Y.Z> 是发布时唯一事实源；代码库日常保持开发态版本。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("version", help="语义化三段式版本号，如 1.0.0")
    ap.add_argument("--root", default=Path(__file__).resolve().parent.parent, help="仓库根目录（默认：脚本所在仓库）")
    args = ap.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        print(f"invalid version: {args.version!r} (want X.Y.Z)", file=sys.stderr)
        return 2

    root = Path(args.root)
    targets = {
        "vision_relay/__init__.py": "regex",
        "gui/src-tauri/tauri.conf.json": "json",
        "gui/package.json": "json",
    }
    for rel in targets:
        if not (root / rel).is_file():
            print(f"missing target: {rel}", file=sys.stderr)
            return 1

    init = root / "vision_relay" / "__init__.py"
    src = init.read_text(encoding="utf-8")
    new, n = re.subn(r'__version__ = "[^"]+"', f'__version__ = "{args.version}"', src)
    if n != 1:
        print("__version__ pattern not found (or found multiple)", file=sys.stderr)
        return 1
    init.write_text(new, encoding="utf-8")

    for rel in ("gui/src-tauri/tauri.conf.json", "gui/package.json"):
        path = root / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = args.version
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"version set to {args.version} in: {', '.join(targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
