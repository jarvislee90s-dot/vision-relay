"""set_version：一次写三处版本号（spec §6 版本一致性）。"""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "set_version.py"


def _make_root(tmp_path: Path) -> Path:
    (tmp_path / "vision_relay").mkdir()
    (tmp_path / "vision_relay" / "__init__.py").write_text('__version__ = "0.1.0"\n', encoding="utf-8")
    (tmp_path / "gui" / "src-tauri").mkdir(parents=True)
    (tmp_path / "gui" / "src-tauri" / "tauri.conf.json").write_text(
        '{\n  "productName": "vision-relay",\n  "version": "0.1.0"\n}\n', encoding="utf-8"
    )
    (tmp_path / "gui" / "package.json").write_text(
        '{\n  "name": "vision-relay-gui",\n  "version": "0.1.0"\n}\n', encoding="utf-8"
    )
    return tmp_path


def _run(root: Path, version: str) -> int:
    return subprocess.run([sys.executable, str(SCRIPT), version, "--root", str(root)]).returncode


def test_sets_three_places(tmp_path):
    root = _make_root(tmp_path)
    assert _run(root, "1.0.0") == 0
    assert '__version__ = "1.0.0"' in (root / "vision_relay" / "__init__.py").read_text(encoding="utf-8")
    for rel in ("gui/src-tauri/tauri.conf.json", "gui/package.json"):
        assert json.loads((root / rel).read_text(encoding="utf-8"))["version"] == "1.0.0"


def test_rejects_bad_version(tmp_path):
    root = _make_root(tmp_path)
    for bad in ("1.0", "v1.0.0", "1.0.0-beta", ""):
        assert _run(root, bad) == 2, bad
    assert '__version__ = "0.1.0"' in (root / "vision_relay" / "__init__.py").read_text(encoding="utf-8")
