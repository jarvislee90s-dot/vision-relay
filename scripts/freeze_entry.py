#!/usr/bin/env python3
"""PyInstaller 冻结入口：包内绝对导入（直接冻结 vision_relay/__main__.py 会有相对导入问题）。"""

from vision_relay.__main__ import main

if __name__ == "__main__":
    main()
