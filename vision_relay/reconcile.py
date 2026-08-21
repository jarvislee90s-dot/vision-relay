"""Reconcile engine (spec §5): the single write path for wiring/relays.

所有触发源（start/stop/refresh/diagnose/自动修复）都走 reconcile()；自方写者经
config_lock 串行；无漂移不写文件（幂等）。事件流水 events.jsonl 供 GUI 事件日志页。
"""

from __future__ import annotations

import json
import os
import time

from . import snapshot, tools
from .config import ProxyConfig, RelayConfig, save_config
from .env_util import config_dir
from .locking import config_lock

# 测试可 monkeypatch。
HOME = os.path.expanduser("~")


# ---------- state.json（routing_on = 用户路由意图，崩溃后修复据此推导） ----------


def _state_path() -> str:
    return os.path.join(config_dir(), "state.json")


def get_routing_on() -> bool:
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return bool(json.load(f).get("routing_on"))
    except (OSError, ValueError):
        return False


def set_routing_on(value: bool) -> None:
    data = {}
    if os.path.exists(_state_path()):
        try:
            with open(_state_path(), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
    data["routing_on"] = value
    data["updated_ts"] = time.time()
    os.makedirs(config_dir(), exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, _state_path())


# ---------- events.jsonl ----------


def _events_path() -> str:
    return os.path.join(config_dir(), "events.jsonl")


def append_event(type_: str, harness: str | None, detail: dict) -> None:
    row = {"ts": time.time(), "type": type_, "harness": harness, **detail}
    os.makedirs(config_dir(), exist_ok=True)
    with open(_events_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def tail_events(n: int = 50) -> list[dict]:
    try:
        with open(_events_path(), encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# ---------- 观测 ----------


def _service_alive(cfg: ProxyConfig) -> bool:
    """PID 文件 + 端口双信号（spec §5 观测信号③）。"""
    import socket

    with socket.socket() as s:
        s.settimeout(0.3)
        if s.connect_ex(("127.0.0.1", cfg.bind_port)) == 0:
            return True
    pid_file = os.path.join(config_dir(), "proxy.pid")
    try:
        with open(pid_file, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return False
    return _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    # Windows 上 os.kill(pid, 0) 会真杀进程（TerminateProcess 语义）——绝不能用；
    # 用 OpenProcess 探测。Unix 用信号 0 探测。
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == 259  # STILL_ACTIVE
            return False
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def observe(cfg: ProxyConfig, tool_states: list | None = None) -> dict:
    """收集三信号：接线归属 / 工具在线 / 服务存活。只读，不写。"""
    from . import wiring

    tool_states = tool_states if tool_states is not None else tools.probe_tools()
    harness_rows = {}
    for name in cfg.routing.harnesses:
        p = wiring._path(HOME, name)
        cur = wiring.read_base_url(p, wiring.HARNESS_CFG[name]) if os.path.exists(p) else None
        harness_rows[name] = {
            "base_url": cur,
            "ownership": wiring.classify_base_url(cur, cfg.bind_port),
            "has_snapshot": name in snapshot.load(),
        }
    return {
        "service_alive": _service_alive(cfg),
        "harnesses": harness_rows,
        "tools": [
            {
                "name": t.name,
                "port": t.port,
                "online": t.online,
                "active_provider": t.active_provider,
                "provider_base_url": t.provider_base_url,
            }
            for t in tool_states
        ],
        "routing_on": get_routing_on(),
    }


# ---------- 对账（唯一写路径；spec §5 规则矩阵） ----------


def _expected_base(cfg: ProxyConfig) -> str:
    return f"http://127.0.0.1:{cfg.bind_port}"


def _reclaim(cfg: ProxyConfig, harness: str, cur: str) -> bool:
    from . import wiring

    p = wiring._path(HOME, harness)
    ok = wiring.write_base_url(p, wiring.HARNESS_CFG[harness], _expected_base(cfg))
    append_event("reclaim", harness, {"from": cur, "to": _expected_base(cfg), "ok": ok})
    return ok


def _absorb(cfg: ProxyConfig, harness: str, new_base: str) -> None:
    """吸收新上游（spec §5）：接管回本代理 + 新地址成为直连 relay + 快照更新。"""
    from . import wiring

    snap = snapshot.Snapshot(base_url=new_base, key_ref=snapshot.key_ref_for(harness), model="", second_hop=None)
    snapshot.save(harness, snap)
    name = f"direct-{harness}"
    cfg.relays = [r for r in cfg.relays if r.name != name]
    proto = {"claude": "anthropic", "codex": "responses", "qwen-code": "chat"}[harness]
    cfg.relays.append(RelayConfig(name=name, protocol=proto, base_url=new_base, models=["*"]))
    wiring.write_base_url(wiring._path(HOME, harness), wiring.HARNESS_CFG[harness], _expected_base(cfg))
    append_event("absorb", harness, {"new_base_url": new_base, "needs_key": True})


def _restart_service(cfg: ProxyConfig) -> bool:
    """分离进程重启服务（崩溃前路由开 -> 自动重启保持接管，spec §5 修复）。"""
    import subprocess
    import sys

    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([sys.executable, "-m", "vision_relay", "start"], **kwargs)
        return True
    except OSError:
        return False


def reconcile(
    cfg: ProxyConfig,
    tool_states: list | None = None,
    trigger: str = "manual",
    expected_wired: set[str] | None = None,
) -> dict:
    """执行对账。expected_wired：本轮应保持接管的 harness 集合（None=全部启用的）。
    返回 {"actions": [...], "needs_you": [...], "observed": {...}}（GUI/CLI 共用）。"""
    from . import wiring

    tool_states = tool_states if tool_states is not None else tools.probe_tools()
    expected_wired = expected_wired if expected_wired is not None else set(cfg.routing.harnesses)
    actions: list[dict] = []
    needs_you: list[dict] = []
    with config_lock():
        obs = observe(cfg, tool_states)
        # 0) 在线工具 → 自动 relay（name 去重不覆盖，离线不加；§12 只增不覆盖）
        added = wiring.ensure_tool_relays(cfg, tool_states)
        for n in added:
            actions.append({"type": "relay_added", "name": n})
            append_event("relay_added", None, {"name": n})
        for name, row in obs["harnesses"].items():
            cur, owner = row["base_url"], row["ownership"]
            if obs["service_alive"] and name in expected_wired:
                # 服务在跑 + 该接管：始终接管（spec §3）
                if owner == "ours":
                    pass  # 幂等：无漂移不写文件
                elif owner in tools.TOOL_DOSSIERS:
                    if _reclaim(cfg, name, cur or ""):
                        actions.append({"type": "reclaim", "harness": name, "from": cur})
                elif owner in ("other", "none"):
                    if owner == "other" and cur:
                        _absorb(cfg, name, cur)
                        actions.append({"type": "absorb", "harness": name, "new_base_url": cur})
                    else:
                        if _reclaim(cfg, name, cur or ""):
                            actions.append({"type": "reclaim", "harness": name, "from": cur})
                # 吸收/抢回后可能缺 key（被动提醒，不代填）
                if any(a.get("type") == "absorb" and a.get("harness") == name for a in actions):
                    needs_you.append({"type": "missing_key", "harness": name, "hint": f"direct-{name} 需补 API key"})
            elif not obs["service_alive"] and owner == "ours":
                # 僵尸接线：按崩溃前意图推导（spec §5 修复流程）
                if obs["routing_on"]:
                    ok = _restart_service(cfg)
                    append_event("auto_fix", name, {"fix": "restart", "ok": ok})
                    actions.append({"type": "auto_fix", "harness": name, "fix": "restart", "ok": ok})
                elif name in snapshot.load():
                    wiring.wiring_restore_by_snapshot(cfg)
                    append_event("auto_fix", name, {"fix": "restore"})
                    actions.append({"type": "auto_fix", "harness": name, "fix": "restore"})
                else:
                    needs_you.append(
                        {"type": "unresolvable", "harness": name, "hint": "快照缺失且服务未运行：需手选修复目标"}
                    )
        if actions:
            save_config(cfg)  # relay 增删/吸收落盘（持锁内）
    return {"trigger": trigger, "actions": actions, "needs_you": needs_you, "observed": obs}
