"""reconcile engine (spec §5): observe -> expected -> converge, single write path under lock."""

import json
import os

import pytest

from vision_relay import reconcile, snapshot
from vision_relay.config import ProxyConfig


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from vision_relay import wiring

    home = tmp_path / "home"
    home.mkdir()
    cfgdir = tmp_path / "cfg"
    cfgdir.mkdir()
    monkeypatch.setattr(reconcile, "HOME", str(home))
    monkeypatch.setattr(wiring, "HOME", str(home))  # wiring_restore_by_snapshot 走 wiring.HOME，必须同源隔离
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(cfgdir))
    return home, cfgdir


def _write_harness(home, harness, base_url):
    import json as j

    from vision_relay import wiring

    h = wiring.HARNESS_CFG[harness]
    p = wiring._path(str(home), harness)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if h.kind == "toml":
        open(p, "w", encoding="utf-8").write(f'base_url = "{base_url}"\n')
    else:
        d = {"env": {"ANTHROPIC_BASE_URL": base_url}} if harness == "claude" else {"model": {"baseUrl": base_url}}
        open(p, "w", encoding="utf-8").write(j.dumps(d))


def _set_running(monkeypatch, cfgdir, running: bool):
    reconcile.set_routing_on(True)
    if running:
        (cfgdir / "proxy.pid").write_text("99999999")  # 不会真实存活 -> service_alive False
    # 直接 monkeypatch service_alive 更稳；经 monkeypatch 保证测试间无模块级残留：
    monkeypatch.setattr(reconcile, "_service_alive", lambda cfg: running)  # type: ignore[assignment]


class TestState:
    def test_routing_on_persists(self, env):
        assert reconcile.get_routing_on() is False  # 默认未开
        reconcile.set_routing_on(True)
        assert reconcile.get_routing_on() is True
        assert json.loads((env[1] / "state.json").read_text(encoding="utf-8"))["routing_on"] is True


class TestEvents:
    def test_append_and_tail(self, env):
        reconcile.append_event("reclaim", "codex", {"from": ":15721", "to": ":8787"})
        rows = reconcile.tail_events(10)
        assert rows[-1]["type"] == "reclaim" and rows[-1]["harness"] == "codex"


class TestMatrix:
    def test_noop_when_consistent(self, env, monkeypatch):
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, True)
        cfg.routing.capability_confirmed = True
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert report["actions"] == []  # 幂等：无漂移不写文件

    def test_reclaim_when_tool_stole(self, env, monkeypatch):
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:15721")
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, True)
        from vision_relay.tools import ToolState

        report = reconcile.reconcile(cfg, tool_states=[ToolState("cc-switch", 15721, True)], expected_wired={"claude"})
        assert any(a["type"] == "reclaim" and a["harness"] == "claude" for a in report["actions"])
        from vision_relay import wiring

        cur = wiring.read_base_url(wiring._path(str(home), "claude"), wiring.HARNESS_CFG["claude"])
        assert cur == "http://127.0.0.1:8787"

    def test_absorb_stranger_address(self, env, monkeypatch):
        home, cfgdir = env
        _write_harness(home, "claude", "https://new.upstream.example/api")
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, True)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        absorb = [a for a in report["actions"] if a["type"] == "absorb"]
        assert absorb and absorb[0]["harness"] == "claude"
        # 吸收 = 接管回 8787 + 新地址记为直连上游 + 快照更新
        assert snapshot.load()["claude"].base_url == "https://new.upstream.example/api"
        direct = [r for r in cfg.relays if r.base_url == "https://new.upstream.example/api"]
        assert direct, "吸收的新地址必须成为直连 relay"

    def test_dead_and_wired_with_routing_on_triggers_restart(self, env, monkeypatch):
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, False)
        reconcile.set_routing_on(True)
        spawned = {}
        monkeypatch.setattr(reconcile, "_restart_service", lambda cfg: spawned.setdefault("n", 0) or 1)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert any(a["type"] == "auto_fix" and a["fix"] == "restart" for a in report["actions"])
        assert spawned

    def test_dead_and_wired_with_routing_off_restores_snapshot(self, env, monkeypatch):
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")
        snapshot.save("claude", snapshot.Snapshot(base_url="https://real.example", key_ref="k", model="m"))
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, False)
        reconcile.set_routing_on(False)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert any(a["type"] == "auto_fix" and a["fix"] == "restore" for a in report["actions"])
        from vision_relay import wiring

        cur = wiring.read_base_url(wiring._path(str(home), "claude"), wiring.HARNESS_CFG["claude"])
        assert cur == "https://real.example"

    def test_tool_relay_generated_when_online(self, env, monkeypatch):
        home, cfgdir = env
        _write_harness(home, "codex", "http://127.0.0.1:8787")
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, True)
        from vision_relay.tools import ToolState

        report = reconcile.reconcile(cfg, tool_states=[ToolState("codex-plus", 57321, True)], expected_wired={"codex"})
        assert any(a["type"] == "relay_added" and a["name"] == "codex-plus" for a in report["actions"])

    def test_lock_held_during_reconcile(self, env, monkeypatch):
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, True)
        from vision_relay import locking

        orig = reconcile._reclaim
        flags = {}

        def spy(cfg2, name, cur):
            flags["held"] = locking.try_config_lock() is None  # 持锁中应拿不到
            return orig(cfg2, name, cur)

        monkeypatch.setattr(reconcile, "_reclaim", spy)
        _write_harness(home, "claude", "http://127.0.0.1:15721")
        from vision_relay.tools import ToolState

        reconcile.reconcile(cfg, tool_states=[ToolState("cc-switch", 15721, True)], expected_wired={"claude"})
        assert flags.get("held") is True

    def test_absorb_persists_relay_before_takeover(self, env, monkeypatch):
        """I-2：save_config 失败不得造成"base_url 已接管但 direct relay 永不落盘"。"""
        home, cfgdir = env
        _write_harness(home, "claude", "https://new.upstream.example/api")
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, True)
        real_save = reconcile.save_config
        calls = {"n": 0}

        def flaky(cfg2, path=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("disk full")
            return real_save(cfg2, path)

        monkeypatch.setattr(reconcile, "save_config", flaky)
        with pytest.raises(OSError):
            reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        # 第二轮：模拟重启后从磁盘重载（磁盘上没有 direct relay），save_config 已恢复
        cfg2 = ProxyConfig()
        reconcile.reconcile(cfg2, tool_states=[], expected_wired={"claude"})
        direct = [r for r in cfg2.relays if r.base_url == "https://new.upstream.example/api"]
        assert direct, "absorb 失败序必须收敛：第二轮必须补出 direct relay"
        from vision_relay import wiring

        cur = wiring.read_base_url(wiring._path(str(home), "claude"), wiring.HARNESS_CFG["claude"])
        assert cur == "http://127.0.0.1:8787"

    def test_absorb_idempotent_on_second_reconcile(self, env, monkeypatch):
        """守护：absorb 成功后再对账——无新 absorb action，direct relay 不重复。"""
        home, cfgdir = env
        _write_harness(home, "claude", "https://new.upstream.example/api")
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, True)
        reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert not any(a["type"] == "absorb" for a in report["actions"])
        assert len([r for r in cfg.relays if r.name == "direct-claude"]) == 1

    def test_missing_config_file_goes_to_needs_you(self, env, monkeypatch):
        """M-1：配置文件不存在时 reclaim 必败——改为跳过收敛并进 needs_you。"""
        home, cfgdir = env
        # claude 配置文件不存在：base_url=None / owner="none"
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, True)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert any(n["type"] == "no_config_file" and n["harness"] == "claude" for n in report["needs_you"])
        assert not any(a["type"] == "reclaim" for a in report["actions"])

    def test_unreadable_base_url_still_reclaims_when_file_exists(self, env, monkeypatch):
        """守护：文件存在但读不出 base_url 的情形维持原 reclaim 语义（区分 os.path.exists）。"""
        home, cfgdir = env
        from vision_relay import wiring

        p = wiring._path(str(home), "claude")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{}")  # 文件存在但读不到 base_url
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, True)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert any(a["type"] == "reclaim" and a["harness"] == "claude" for a in report["actions"])
        assert not any(n["type"] == "no_config_file" for n in report["needs_you"])

    def test_append_event_failure_does_not_block_convergence(self, env, monkeypatch):
        """I-4：事件通道损坏不得打断收敛（reclaim 必须完成）。"""
        home, cfgdir = env
        (cfgdir / "events.jsonl").mkdir()  # 事件通道损坏（路径被目录占用）
        _write_harness(home, "claude", "http://127.0.0.1:15721")
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, True)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})  # 不得抛
        assert any(a["type"] == "reclaim" and a["harness"] == "claude" for a in report["actions"])
        from vision_relay import wiring

        cur = wiring.read_base_url(wiring._path(str(home), "claude"), wiring.HARNESS_CFG["claude"])
        assert cur == "http://127.0.0.1:8787"


class TestRestartHonesty:
    def test_restart_reports_failure_when_port_never_opens(self, env, monkeypatch):
        """I-1：spawn 成功但端口一直不通 -> 事件/action 的 ok 必须是 False。"""
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, False)
        reconcile.set_routing_on(True)
        import subprocess

        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: None)
        monkeypatch.setattr(reconcile, "_wait_port_online", lambda port, timeout_s=2.0: False, raising=False)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        fix = [a for a in report["actions"] if a["type"] == "auto_fix" and a["fix"] == "restart"]
        assert fix and fix[0]["ok"] is False

    def test_restart_reports_success_when_port_opens(self, env, monkeypatch):
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, False)
        reconcile.set_routing_on(True)
        import subprocess

        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: None)
        monkeypatch.setattr(reconcile, "_wait_port_online", lambda port, timeout_s=2.0: True, raising=False)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        fix = [a for a in report["actions"] if a["type"] == "auto_fix" and a["fix"] == "restart"]
        assert fix and fix[0]["ok"] is True


class TestWaitPortOnline:
    def test_port_open(self):
        import socket

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            assert reconcile._wait_port_online(s.getsockname()[1], timeout_s=1.0) is True

    def test_port_closed(self):
        import socket

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        assert reconcile._wait_port_online(port, timeout_s=0.5) is False


class TestPidAlive:
    def test_current_process_alive(self):
        assert reconcile._pid_alive(os.getpid()) is True

    def test_nonexistent_pid_dead(self):
        assert reconcile._pid_alive(99999999) is False

    def test_unix_permissionerror_means_alive(self, monkeypatch):
        """M-2：Unix EPERM＝进程存在但属他人，视为活着（必须排在 OSError 之前）。"""

        def deny(pid, sig):
            raise PermissionError("owned by another user")

        monkeypatch.setattr(reconcile.os, "name", "posix")
        monkeypatch.setattr(reconcile.os, "kill", deny)
        assert reconcile._pid_alive(4242) is True
