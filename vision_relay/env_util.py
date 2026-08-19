"""Env/config accessor for vision-relay: env vars first, shell-style config file fallback.

VISION_RELAY_CONFIG_DIR / VISION_RELAY_CONFIG point at ~/.vision-relay and
~/.vision-relay/config. Legacy Qwen-MM-Plugins era env names (QWEN_MM_*) are
still honored with a one-time deprecation warning, so existing setups keep
working while migrating.
"""

from __future__ import annotations

import os
import sys

_warned: set[str] = set()


def _env(name: str, legacy: str | None = None) -> str | None:
    val = os.environ.get(name)
    if val is not None:
        return val
    if legacy is not None:
        val = os.environ.get(legacy)
        if val is not None:
            if legacy not in _warned:
                _warned.add(legacy)
                print(f"warning: env {legacy} is deprecated; use {name} instead", file=sys.stderr)
            return val
    return None


def get_env(name: str, legacy: str | None = None, default: str | None = None) -> str | None:
    val = _env(name, legacy)
    return val if val is not None else _config().get(name, default)


def config_dir() -> str:
    """State dir for vision-relay (config, pid, logs)."""
    if v := _env("VISION_RELAY_CONFIG_DIR", "QWEN_MM_CONFIG_DIR"):
        return os.path.expanduser(v)
    return os.path.expanduser("~/.vision-relay")


def legacy_config_dir() -> str:
    """Qwen-MM-Plugins era config dir; read-only fallback for migration."""
    return os.path.expanduser("~/.qwen-mm-plugins")


def _parse_config(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
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
    override = _env("VISION_RELAY_CONFIG", "QWEN_MM_CONFIG")
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
