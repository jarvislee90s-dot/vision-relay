"""Reconcile engine (spec §5): the single write path for wiring/relays.

所有触发源（start/stop/refresh/diagnose/自动修复）都走 reconcile()；自方写者经
config_lock 串行；无漂移不写文件（幂等）。事件流水 events.jsonl 供 GUI 事件日志页。
"""

from __future__ import annotations

import json
import os
import time

from . import pid_util, snapshot, tools
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
    try:
        os.makedirs(config_dir(), exist_ok=True)
        with open(_events_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 事件是可观测通道，不 gate 收敛


def tail_events(n: int = 50) -> list[dict]:
    try:
        with open(_events_path(), encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    selected = lines if n is None or n <= 0 else lines[-n:]  # 0/None = 全量（导出，2026-08-23 决策④）
    out = []
    for line in selected:
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
    pid, token = pid_util.read_pid_file()
    if pid == -1:
        return False
    if token is None:
        return _pid_alive(pid)
    actual = pid_util.process_token(pid)
    return _pid_alive(pid) and actual is not None and actual == token


def _pid_alive(pid: int) -> bool:
    """薄包装：跨平台存活判定（Windows GetExitCodeProcess / Unix 信号 0 且 EPERM=活着）。
    决策⑤ 后与 pid_util.pid_alive 同一实现，保留此名供既有测试直接断言真实进程。"""
    return pid_util.pid_alive(pid)


def observe(cfg: ProxyConfig, tool_states: list | None = None) -> dict:
    """收集三信号：接线归属 / 工具在线 / 服务存活。只读，不写。"""
    from . import wiring

    tool_states = tool_states if tool_states is not None else tools.probe_tools()
    harness_rows = {}
    for name in cfg.routing.harnesses:
        p = wiring._path(HOME, name)
        exists = os.path.exists(p)
        cur = wiring.read_base_url(p, wiring.HARNESS_CFG[name]) if exists else None
        harness_rows[name] = {
            "base_url": cur,
            "ownership": wiring.classify_base_url(cur, cfg.bind_port),
            "has_snapshot": name in snapshot.load(),
            "config_exists": exists,  # 区分"文件不存在"与"文件在但读不到 base_url"（后者仍走 reclaim）
            "config_path": p,  # GUI 详情抽屉「配置文件」入口（2026-08-23 决策③）
        }
    return {
        "service_alive": _service_alive(cfg),
        "bind_port": cfg.bind_port,  # GUI 拓扑卡/横幅不再硬编码 8787（决策⑥c）
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
    """吸收新上游（spec §5）：新地址先落盘为直连 relay，再接管回本代理 + 快照更新。

    顺序即失败语义：relay 先存、base_url 后接管——write_base_url 失败时下轮
    owner 仍是 other，会再次 absorb 直至收敛；绝不出现"已接管但 relay 永缺"。
    """
    from . import wiring

    snap = snapshot.Snapshot(base_url=new_base, key_ref=snapshot.key_ref_for(harness), model="", second_hop=None)
    snapshot.save(harness, snap)
    name = f"direct-{harness}"
    cfg.relays = [r for r in cfg.relays if r.name != name]
    proto = {"claude": "anthropic", "codex": "responses", "qwen-code": "chat"}[harness]
    cfg.relays.append(RelayConfig(name=name, protocol=proto, base_url=new_base, models=["*"]))
    save_config(cfg)  # relay 先落盘；此步失败则中止在接管之前（下轮重试整个 absorb）
    ok = wiring.write_base_url(wiring._path(HOME, harness), wiring.HARNESS_CFG[harness], _expected_base(cfg))
    if harness == "codex" and ok:
        # 新上游常伴新 catalog（Codex++ 切供应商重新生成）——重接管须重打模态补丁
        wiring._patch_codex_catalog_modalities(wiring._path(HOME, harness))
    append_event("absorb", harness, {"new_base_url": new_base, "needs_key": True, "ok": ok})


def _wait_port_online(port: int, timeout_s: float = 8.0, interval_s: float = 0.2) -> bool:
    """短轮询本机端口直到通或超时（重启诚实化用；测试可 monkeypatch）。
    窗口 8s：Windows 冷启动 `python -m vision_relay start`（导入+接线+对账）实测可超 2s，
    过短会把成功重启谎报为 ok=False（G4 E2E 实测）。"""
    import socket

    deadline = time.monotonic() + timeout_s
    while True:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval_s)


def _clear_stale_pid() -> None:
    """僵尸接线场景清掉硬崩溃残留的 pid 文件。

    observe 已用「端口 + PID」双信号判定服务死亡；残留 pid 在 Windows 上可能撞上
    PID 复用（新进程恰好拿到同号且存活）→ 重启子进程 cmd_start 误判 already running
    直接退出、端口永不恢复（G4 E2E 间歇实测）。清掉后子进程自然重建 pid。"""
    try:
        os.unlink(os.path.join(config_dir(), "proxy.pid"))
    except OSError:
        pass


def _restart_service(cfg: ProxyConfig) -> bool:
    """分离进程重启服务（崩溃前路由开 -> 自动重启保持接管，spec §5 修复）。

    spawn 成功 ≠ 服务起来了（stale pid 等场景会谎报）：短轮询端口后如实返回。
    """
    import subprocess
    import sys

    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS（CREATE_NO_WINDOW 与其互斥，只留一个）
    else:
        kwargs["start_new_session"] = True
    # stdio 重定向 DEVNULL：分离重启子进程常驻，若继承调用方管道会让
    # `subprocess.run(capture_output=True)` 等不到 EOF 而挂起（G4 diagnose 曾 90s 超时）。
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    kwargs.setdefault("stdout", subprocess.DEVNULL)
    kwargs.setdefault("stderr", subprocess.DEVNULL)
    try:
        # 注入 VISION_RELAY_RESTART=1：分离重启的子进程无控制台，cmd_start 据此跳过
        # 交互 onboarding（capability_confirmed 未确认时否则会在无终端环境下挂死）。
        subprocess.Popen(
            [sys.executable, "-m", "vision_relay", "start"],
            env={**os.environ, "VISION_RELAY_RESTART": "1"},
            **kwargs,
        )
    except OSError:
        return False
    return _wait_port_online(cfg.bind_port)


def reconcile(
    cfg: ProxyConfig,
    tool_states: list | None = None,
    trigger: str = "manual",
    expected_wired: set[str] | None = None,
) -> dict:
    """执行对账。expected_wired：本轮应保持接管的 harness 集合（None=全部启用的）。
    返回 {"actions": [...], "needs_you": [...], "observed": {...}}（GUI/CLI 共用）。
    注意：config_lock 默认 30s 拿不到锁时，TimeoutError 会原样抛给调用方。
    """
    from . import wiring

    tool_states = tool_states if tool_states is not None else tools.probe_tools()
    expected_wired = expected_wired if expected_wired is not None else set(cfg.routing.harnesses)
    actions: list[dict] = []
    needs_you: list[dict] = []
    pending_restart: list[str] = []  # 服务级修复（spawn）必须移出锁外执行，见下方注释
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
                    # qwen 0.22.0 条目级接线：全局字段 ours 不代表条目没漂——
                    # 条目被外部改走时重接管并把新原值吸收进快照（absorb 语义）
                    if name == "qwen-code":
                        res = wiring.reconcile_qwen_providers(cfg)
                        if res:
                            actions.append({"type": "provider_absorb", "harness": name, "rewritten": res["rewritten"]})
                elif owner in tools.TOOL_DOSSIERS:
                    if _reclaim(cfg, name, cur or ""):
                        actions.append({"type": "reclaim", "harness": name, "from": cur})
                elif owner in ("other", "none"):
                    if owner == "other" and cur:
                        _absorb(cfg, name, cur)
                        actions.append({"type": "absorb", "harness": name, "new_base_url": cur})
                    elif not row["config_exists"]:
                        # 配置文件本身不存在：reclaim 必败（写入无从谈起），交给人
                        needs_you.append(
                            {"type": "no_config_file", "harness": name, "hint": "无 harness 配置文件，需手动创建"}
                        )
                    else:
                        if _reclaim(cfg, name, cur or ""):
                            actions.append({"type": "reclaim", "harness": name, "from": cur})
                # 吸收/抢回后可能缺 key（被动提醒，不代填）
                if any(a.get("type") == "absorb" and a.get("harness") == name for a in actions):
                    needs_you.append({"type": "missing_key", "harness": name, "hint": f"direct-{name} 需补 API key"})
            elif not obs["service_alive"] and owner == "ours":
                # 僵尸接线：按崩溃前意图推导（spec §5 修复流程）
                if obs["routing_on"]:
                    pending_restart.append(name)
                elif name in snapshot.load():
                    wiring.wiring_restore_by_snapshot(cfg)
                    append_event("auto_fix", name, {"fix": "restore"})
                    actions.append({"type": "auto_fix", "harness": name, "fix": "restore"})
                else:
                    needs_you.append(
                        {"type": "unresolvable", "harness": name, "hint": "快照缺失且服务未运行：需手选修复目标"}
                    )
        # qwen 一层直连 relay 维护（漂移吸收后快照已更新，故置于循环之后；
        # 缺失重建/原值消失清理，与 ensure_tool_relays 同风格）
        if obs["service_alive"] and "qwen-code" in expected_wired:
            for n in wiring.ensure_qwen_relays(cfg):
                actions.append({"type": "relay_added", "name": n})
                append_event("relay_added", None, {"name": n})
        if actions:
            save_config(cfg)  # relay 增删/吸收落盘（持锁内）
    # 服务重启必须在 config_lock 之外 spawn：子进程 cmd_start 内部的
    # reconcile(trigger="start") 要拿同一把锁——持锁等端口会与被等者互等（锁倒置
    # 死锁，G4 E2E 实测：ok 永远 False、端口在窗口内永不出现）。只 spawn 一次，
    # 多个僵尸 harness 共享结果、各自留痕（可见性补位）。
    if pending_restart:
        _clear_stale_pid()
        restarted = _restart_service(cfg)
        for name in pending_restart:
            append_event("auto_fix", name, {"fix": "restart", "ok": restarted})
            actions.append({"type": "auto_fix", "harness": name, "fix": "restart", "ok": restarted})
    return {"trigger": trigger, "actions": actions, "needs_you": needs_you, "observed": obs}
