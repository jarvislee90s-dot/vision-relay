"""请求期 relay 直连回落(spec §5 "工具路由关(端口离线)→relay 回落直连"的落地)。

两层 relay(via=cc-switch/codex-plus)的目标端口死时,读工具档案拿当前供应商
真实地址+key,构造一次性直连 RelayConfig——与 direct-* 一层语义一致;
端口恢复后自动回到两层。codex++ 上游是 chat 时由本代理做 responses→chat
转换(server 按回落 relay 的协议选 serializer)。

密钥窄豁免(2026-08-25 决策,spec §13):为完成转发,本模块读工具档案密钥字段
(cc-switch settings_config 的 ANTHROPIC_AUTH_TOKEN / auth.OPENAI_API_KEY、
codex++ 的 authContents),仅存进程内存且只进转发请求头;
不落盘、不进日志/status/events/GUI。model_sources 其余代码的"密钥不读"铁律不变。
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import time

from . import tools
from .config import RelayConfig

# 测试挂点(与 model_sources 同款:模块级别名,测试 monkeypatch 这里)
CCSWITCH_DB = tools.CCSWITCH_DB
CODEXPP_SETTINGS = tools.CODEXPP_SETTINGS

_WIRE_TO_PROTO = {"chat": "chat", "chatcompletions": "chat", "responses": "responses"}


def _port_online(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


class PortCache:
    """TTL 端口探测缓存:在线判定每 TTL 秒最多探一次;invalidate 供转发失败后强探。"""

    def __init__(self, ttl: float = 2.0):
        self._ttl = ttl
        self._state: dict[str, tuple[float, bool]] = {}

    def online(self, tool: str) -> bool:
        now = time.monotonic()
        hit = self._state.get(tool)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        ok = _port_online(tools.TOOL_DOSSIERS[tool].port)
        self._state[tool] = (now, ok)
        return ok

    def invalidate(self, tool: str) -> None:
        self._state.pop(tool, None)


def ccswitch_app_direct(app_type: str, template_name: str) -> RelayConfig | None:
    """cc-switch 某 app(claude|codex)当前供应商的直连目标。best-effort,失败 None。"""
    if not os.path.exists(CCSWITCH_DB):
        return None
    try:
        conn = sqlite3.connect(f"file:{CCSWITCH_DB}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT settings_config FROM providers WHERE app_type = ? AND is_current = 1 LIMIT 1",
                (app_type,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:  # best-effort:schema 变化/锁/损坏都退 None
        return None
    if not row or not row[0]:
        return None
    try:
        d = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    if app_type == "claude":
        env = d.get("env") if isinstance(d.get("env"), dict) else {}
        base = env.get("ANTHROPIC_BASE_URL")
        key = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY") or ""
        proto = "anthropic"
    else:
        base, wire, key = _codex_toml_target(d)
        proto = _WIRE_TO_PROTO.get(str(wire or "responses").lower(), "responses")
    if not base:
        return None
    return RelayConfig(name=f"{template_name}~direct", protocol=proto, base_url=base, api_key=key, models=["*"])


def _codex_toml_target(d: dict) -> tuple[str | None, str | None, str]:
    """codex 供应商 settings_config:config TOML 取 base_url/wire_api,auth JSON 取 key。"""
    import tomllib

    try:
        mp = tomllib.loads(d.get("config") or "").get("model_providers")
        entry = next(iter(mp.values())) if isinstance(mp, dict) and mp else {}
    except Exception:  # TOML 损坏 best-effort
        entry = {}
    if not isinstance(entry, dict):
        entry = {}
    try:
        auth = json.loads(d.get("auth") or "{}")
        key = auth.get("OPENAI_API_KEY") or "" if isinstance(auth, dict) else ""
    except (TypeError, ValueError):
        key = ""
    return entry.get("base_url"), entry.get("wire_api"), key


def codexpp_active_direct(template_name: str) -> RelayConfig | None:
    """codex++ 激活 profile 的直连目标。

    profile.protocol 语义(2026-08-25 用户澄清):"chatCompletions"=上游是 chat
    (codex++ 存在的意义即转换,路由开)→ 回落为 chat 协议;"responses"=上游原生
    responses(不开路由)→ 原协议直发。relayMode=official(ChatGPT OAuth)或无
    上游地址 → None(无法以 API key 复放,保持原行为)。
    """
    try:
        with open(CODEXPP_SETTINGS, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    active = data.get("activeRelayId")
    profile = next(
        (p for p in data.get("relayProfiles", []) if isinstance(p, dict) and p.get("id") == active),
        None,
    )
    if not profile:
        return None
    if profile.get("relayMode") != "pureApi":
        return None
    base = str(profile.get("upstreamBaseUrl") or "").strip()
    if not base:
        return None
    proto = "chat" if str(profile.get("protocol", "")).lower() == "chatcompletions" else "responses"
    try:
        auth = json.loads(profile.get("authContents") or "{}")
        key = auth.get("OPENAI_API_KEY") or "" if isinstance(auth, dict) else ""
    except (TypeError, ValueError):
        key = ""
    return RelayConfig(name=f"{template_name}~direct", protocol=proto, base_url=base, api_key=key, models=["*"])


# ---------- 端口判定 + 状态转换事件 ----------

_last_mode: dict[str, str] = {}


def _append_event(type_: str, harness: str | None, detail: dict) -> None:  # 测试替身挂点
    from .reconcile import append_event

    append_event(type_, harness, detail)


def _emit_mode(tool: str, mode: str) -> None:
    """两层↔回落转换才发事件(同模式重复不发);事件是可观测通道,失败不 gate。"""
    if _last_mode.get(tool) == mode:
        return
    _last_mode[tool] = mode
    _append_event("relay_fallback", None, {"tool": tool, "mode": mode})


def archive_direct(via: str, relay: RelayConfig) -> RelayConfig | None:
    """工具档案直连目标(公开:status 展示 upstream_effective 复用;返回值含 key,只取所需字段)。"""
    if via == "cc-switch":
        return ccswitch_app_direct("claude" if relay.protocol == "anthropic" else "codex", relay.name)
    if via == "codex-plus":
        return codexpp_active_direct(relay.name)
    return None


def resolve_effective_relay(
    relay: RelayConfig, cache: PortCache, *, force_offline: bool = False
) -> tuple[RelayConfig, str | None]:
    """via relay 的请求期目标解析:端口在线→两层原样;离线→档案直连回落。

    返回 (生效 relay, 原因标记)。原因形如 "cc-switch:offline";档案不可读时加
    ":no-direct" 并保持原 relay(死端口行为同现状,502 可见)。非 via relay 原样。
    """
    via = getattr(relay, "via", None)
    if not via:
        return relay, None
    online = False if force_offline else cache.online(via)
    if online:
        _emit_mode(via, "two-layer")
        return relay, None
    direct = archive_direct(via, relay)
    reason = f"{via}:offline"
    if direct is None:
        _emit_mode(via, "dead-no-direct")
        return relay, reason + ":no-direct"
    _emit_mode(via, "direct")
    return direct, reason
