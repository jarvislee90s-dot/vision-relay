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


def _set_running(cfgdir, running: bool):
    reconcile.set_routing_on(True)
    if running:
        (cfgdir / "proxy.pid").write_text("99999999")  # 不会真实存活 -> service_alive False
        # 直接 monkeypatch service_alive 更稳：
    import vision_relay.reconcile as r

    r._service_alive = lambda cfg: running  # type: ignore[assignment]


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
    def test_noop_when_consistent(self, env):
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")
        cfg = ProxyConfig()
        _set_running(cfgdir, True)
        cfg.routing.capability_confirmed = True
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert report["actions"] == []  # 幂等：无漂移不写文件

    def test_reclaim_when_tool_stole(self, env):
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:15721")
        cfg = ProxyConfig()
        _set_running(cfgdir, True)
        from vision_relay.tools import ToolState

        report = reconcile.reconcile(cfg, tool_states=[ToolState("cc-switch", 15721, True)], expected_wired={"claude"})
        assert any(a["type"] == "reclaim" and a["harness"] == "claude" for a in report["actions"])
        from vision_relay import wiring

        cur = wiring.read_base_url(wiring._path(str(home), "claude"), wiring.HARNESS_CFG["claude"])
        assert cur == "http://127.0.0.1:8787"

    def test_absorb_stranger_address(self, env):
        home, cfgdir = env
        _write_harness(home, "claude", "https://new.upstream.example/api")
        cfg = ProxyConfig()
        _set_running(cfgdir, True)
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
        _set_running(cfgdir, False)
        reconcile.set_routing_on(True)
        spawned = {}
        monkeypatch.setattr(reconcile, "_restart_service", lambda cfg: spawned.setdefault("n", 0) or 1)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert any(a["type"] == "auto_fix" and a["fix"] == "restart" for a in report["actions"])
        assert spawned

    def test_dead_and_wired_with_routing_off_restores_snapshot(self, env):
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")
        snapshot.save("claude", snapshot.Snapshot(base_url="https://real.example", key_ref="k", model="m"))
        cfg = ProxyConfig()
        _set_running(cfgdir, False)
        reconcile.set_routing_on(False)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert any(a["type"] == "auto_fix" and a["fix"] == "restore" for a in report["actions"])
        from vision_relay import wiring

        cur = wiring.read_base_url(wiring._path(str(home), "claude"), wiring.HARNESS_CFG["claude"])
        assert cur == "https://real.example"

    def test_tool_relay_generated_when_online(self, env):
        home, cfgdir = env
        _write_harness(home, "codex", "http://127.0.0.1:8787")
        cfg = ProxyConfig()
        _set_running(cfgdir, True)
        from vision_relay.tools import ToolState

        report = reconcile.reconcile(cfg, tool_states=[ToolState("codex-plus", 57321, True)], expected_wired={"codex"})
        assert any(a["type"] == "relay_added" and a["name"] == "codex-plus" for a in report["actions"])

    def test_lock_held_during_reconcile(self, env, monkeypatch):
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")
        cfg = ProxyConfig()
        _set_running(cfgdir, True)
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


class TestPidAlive:
    def test_current_process_alive(self):
        assert reconcile._pid_alive(os.getpid()) is True

    def test_nonexistent_pid_dead(self):
        assert reconcile._pid_alive(99999999) is False
