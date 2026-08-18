"""Standalone env/config accessor — the only shared-library dependency the proxy uses.

Replicates just the two functions the proxy needs from upstream Qwen-MM-Plugins
`shared.env` (get_env, config_dir), so the proxy runs as an independent package
without importing the host library.
"""
from __future__ import annotations

import os


def get_env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    return val if val is not None else _config().get(name, default)


def config_dir() -> str:
    return os.path.expanduser(os.environ.get("QWEN_MM_CONFIG_DIR") or "~/.qwen-mm-plugins")


def _parse_config(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        if key.strip():
            out[key.strip()] = val
    return out


_config_cache: dict[str, str] | None = None


def config_file() -> str:
    override = os.environ.get("QWEN_MM_CONFIG")
    return os.path.expanduser(override) if override else os.path.join(config_dir(), "config")


def _config() -> dict[str, str]:
    global _config_cache
    if _config_cache is None:
        try:
            with open(config_file(), encoding="utf-8") as f:
                _config_cache = _parse_config(f.read())
        except (OSError, UnicodeDecodeError):
            _config_cache = {}
    return _config_cache
