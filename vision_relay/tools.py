"""Tool dossiers (spec §5): known local routing tools — port probe + active provider (READ-ONLY).

铁律：只读。绝不写 CC Switch / Codex++ 的任何配置或数据库。
探测=端口通断（不做内容指纹）；激活供应商读取按档案配置，全部 best-effort，
失败返回 (None, None)——真实上游显示退到"由工具决定（未知）"。
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
from dataclasses import dataclass

import httpx

# Codex++ manager settings（若上游改路径，这里单点可改；缺失即 best-effort 失败）
CODEXPP_SETTINGS = os.path.expanduser("~/.codex-session-delete/settings.json")
CCSWITCH_DB = os.path.expanduser("~/.cc-switch/cc-switch.db")


@dataclass(frozen=True)
class ToolDossier:
    name: str
    port: int
    harnesses: tuple[str, ...]


TOOL_DOSSIERS: dict[str, ToolDossier] = {
    "cc-switch": ToolDossier("cc-switch", 15721, ("claude", "codex")),
    "codex-plus": ToolDossier("codex-plus", 57321, ("codex",)),
}

# (tool, harness) -> relay 模板（RelayConfig kwargs，不含 name；spec §12.2 + §5）
_TEMPLATES: dict[tuple[str, str], dict] = {
    ("cc-switch", "claude"): {
        "protocol": "anthropic",
        "base_url": "http://127.0.0.1:15721",
        "via": "cc-switch",
        "models": ["*"],
    },
    ("cc-switch", "codex"): {
        "protocol": "chat",
        "base_url": "http://127.0.0.1:15721",
        "via": "cc-switch",
        "models": ["*"],
    },
    ("codex-plus", "codex"): {
        "protocol": "responses",
        "base_url": "http://127.0.0.1:57321/v1",
        "via": "codex-plus",
        "models": ["*"],
    },
}


def relay_template(tool: str, harness: str) -> dict | None:
    tpl = _TEMPLATES.get((tool, harness))
    return dict(tpl) if tpl else None


@dataclass
class ToolState:
    name: str
    port: int
    online: bool
    active_provider: str | None = None
    provider_base_url: str | None = None


def _port_online(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def probe_tools(port_overrides: dict[str, int] | None = None) -> list[ToolState]:
    out: list[ToolState] = []
    for name, d in TOOL_DOSSIERS.items():
        port = (port_overrides or {}).get(name, d.port)
        online = _port_online(port)
        provider = base = None
        if online:
            if name == "codex-plus":
                provider, base = _codexpp_active_provider()
            elif name == "cc-switch":
                provider, base = _ccswitch_active_provider(port)
        out.append(ToolState(name=name, port=port, online=online, active_provider=provider, provider_base_url=base))
    return out


def _codexpp_active_provider() -> tuple[str | None, str | None]:
    try:
        with open(CODEXPP_SETTINGS, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None, None
    active = data.get("activeRelayId")
    for p in data.get("relayProfiles", []):
        if isinstance(p, dict) and p.get("id") == active:
            return p.get("name") or active, p.get("baseUrl")
    return None, None


def _ccswitch_active_provider(port: int) -> tuple[str | None, str | None]:
    hit = _ccswitch_sqlite_provider()
    # sqlite 读取（或其测试替身）可能返回 None 而非 (None, None)：只在拿到真实命中时短路
    if isinstance(hit, tuple) and any(hit):
        return hit
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/status", timeout=1.0, trust_env=False)
        if resp.status_code != 200:
            return None, None
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None, None
    # best-effort：在状态 JSON 里找 claude/codex 当前供应商字段（字段名随版本可能变化）
    pools = data.get("current_providers") or data.get("providers") or {}
    if isinstance(pools, dict):
        for v in pools.values():
            if isinstance(v, dict) and (v.get("name") or v.get("baseUrl")):
                return v.get("name"), v.get("baseUrl")
    return None, None


def _ccswitch_sqlite_provider() -> tuple[str | None, str | None]:
    """读取 cc-switch 的 SQLite 配置库（只读）。schema 不稳——任何失败静默返回 (None, None)。"""
    if not os.path.exists(CCSWITCH_DB):
        return None, None
    try:
        conn = sqlite3.connect(f"file:{CCSWITCH_DB}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE '%current%' LIMIT 20").fetchall()
        finally:
            conn.close()
    except Exception:  # best-effort，任何 schema/锁问题都退回
        return None, None
    for key, value in rows:
        try:
            v = json.loads(value)
        except (TypeError, ValueError):
            continue
        if isinstance(v, dict) and (v.get("name") or v.get("baseUrl")):
            return v.get("name"), v.get("baseUrl")
        if isinstance(v, str):  # 值是 provider id，查 providers 表
            try:
                conn = sqlite3.connect(f"file:{CCSWITCH_DB}?mode=ro", uri=True)
                try:
                    row = conn.execute("SELECT settings_config FROM providers WHERE id = ? LIMIT 1", (v,)).fetchone()
                finally:
                    conn.close()
                if row and row[0]:
                    d = json.loads(row[0])
                    env = d.get("env", {}) if isinstance(d, dict) else {}
                    url = env.get("ANTHROPIC_BASE_URL") or env.get("OPENAI_BASE_URL")
                    if url:
                        return v, url
            except Exception:  # best-effort
                continue
    return None, None
