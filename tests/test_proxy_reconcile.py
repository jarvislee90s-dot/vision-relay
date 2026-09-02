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

    def test_absorb_missing_key_hint_suppressed_when_key_location_exists(self, env, monkeypatch):
        """direct-* 直连中继设计上无自有 key（认证靠客户端请求头透传）：harness 配置
        里 key 位置真实存在（如 env.ANTHROPIC_AUTH_TOKEN）时，吸收后不得误告缺 key
        （2026-09-02 回归：direct-claude 透传可用、GUI/对账却持续告"缺 API key"）。"""
        home, cfgdir = env
        monkeypatch.setattr(snapshot, "HOME", str(home))  # key_ref_for 读 snapshot.HOME，须同源隔离
        claude_dir = os.path.join(str(home), ".claude")
        os.makedirs(claude_dir, exist_ok=True)
        open(os.path.join(claude_dir, "settings.json"), "w", encoding="utf-8").write(
            json.dumps(
                {"env": {"ANTHROPIC_BASE_URL": "https://new.upstream.example/api", "ANTHROPIC_AUTH_TOKEN": "sk-x"}}
            )
        )
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, True)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert any(a["type"] == "absorb" for a in report["actions"])
        assert not any(n["type"] == "missing_key" for n in report["needs_you"])

    def test_absorb_missing_key_hint_kept_when_key_truly_absent(self, env, monkeypatch):
        """key 位置确实缺失（key_ref=not-found）→ 保留被动提醒。"""
        home, cfgdir = env
        monkeypatch.setattr(snapshot, "HOME", str(home))
        _write_harness(home, "claude", "https://new.upstream.example/api")  # 无 AUTH_TOKEN
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, True)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert any(n["type"] == "missing_key" and n["harness"] == "claude" for n in report["needs_you"])

    def test_stale_direct_relay_removed_under_two_hop_truth(self, env, monkeypatch):
        """两跳接线真相（快照 second_hop=工具）下清理陈旧 direct-{harness}：旧 absorb
        遗留无指纹钉死，选路②层按列表顺序截胡工具中继（2026-09-02 实测：claude 流量
        被陈旧 direct-claude 引去 ark，401/10054、fail-open 502）。"""
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")  # 已接管态
        from vision_relay.config import RelayConfig

        cfg = ProxyConfig()
        cfg.relays = [
            RelayConfig(name="direct-claude", protocol="anthropic", base_url="https://old-ark.example", models=["*"]),
            RelayConfig(
                name="cc-anthropic",
                protocol="anthropic",
                base_url="http://127.0.0.1:15721",
                via="cc-switch",
                models=["*"],
            ),
        ]
        snapshot.save(
            "claude",
            snapshot.Snapshot(
                base_url="http://127.0.0.1:15721", key_ref="env.ANTHROPIC_AUTH_TOKEN", model="", second_hop="cc-switch"
            ),
        )
        _set_running(monkeypatch, cfgdir, True)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert any(a["type"] == "relay_removed" and a["name"] == "direct-claude" for a in report["actions"])
        assert [r.name for r in cfg.relays] == ["cc-anthropic"]

    def test_direct_relay_kept_when_truth_is_direct(self, env, monkeypatch):
        """直连真相（second_hop=None）：direct-{harness} 是接线原值的载体，保留。"""
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")
        from vision_relay.config import RelayConfig

        cfg = ProxyConfig()
        cfg.relays = [
            RelayConfig(name="direct-claude", protocol="anthropic", base_url="https://ark.example", models=["*"]),
        ]
        snapshot.save(
            "claude",
            snapshot.Snapshot(
                base_url="https://ark.example", key_ref="env.ANTHROPIC_AUTH_TOKEN", model="", second_hop=None
            ),
        )
        _set_running(monkeypatch, cfgdir, True)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert not any(a["type"] == "relay_removed" for a in report["actions"])
        assert [r.name for r in cfg.relays] == ["direct-claude"]

    def test_absorb_codex_repatches_catalog_modalities(self, env, monkeypatch):
        """Codex++ 切供应商生成新目录（纯文本模态）后 absorb 重接管：目录须重新补 image，
        否则 Codex 按 catalog 拒绝 view_image/贴图，图片进不了请求、代理转写收不到图。"""
        home, cfgdir = env
        p = os.path.join(str(home), ".codex", "config.toml")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(
            'base_url = "https://new.upstream.example/v1"\nmodel_catalog_json = "model-catalogs/relay-new.json"\n'
        )
        cat = os.path.join(str(home), ".codex", "model-catalogs", "relay-new.json")
        os.makedirs(os.path.dirname(cat), exist_ok=True)
        json.dump({"models": [{"slug": "m-text", "input_modalities": ["text"]}]}, open(cat, "w", encoding="utf-8"))

        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, True)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"codex"})
        assert any(a["type"] == "absorb" and a["harness"] == "codex" for a in report["actions"])
        mods = json.load(open(cat, encoding="utf-8"))["models"][0]["input_modalities"]
        assert "image" in mods

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

    def test_multiple_zombie_harnesses_restart_once_with_per_harness_actions(self, env, monkeypatch):
        """服务级修复只 spawn 一次；每个僵尸 harness 各留一条 auto_fix 动作（可见性补位）。"""
        home, cfgdir = env
        for h in ("claude", "codex", "qwen-code"):
            _write_harness(home, h, "http://127.0.0.1:8787")
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, False)
        reconcile.set_routing_on(True)
        calls = []
        monkeypatch.setattr(reconcile, "_restart_service", lambda cfg: calls.append(1) or True)
        report = reconcile.reconcile(cfg, tool_states=[])
        assert len(calls) == 1
        fixes = [a for a in report["actions"] if a["type"] == "auto_fix" and a["fix"] == "restart"]
        assert {a["harness"] for a in fixes} == {"claude", "codex", "qwen-code"}
        assert all(a["ok"] is True for a in fixes)

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

    def test_restart_frozen_core_spawns_subcommand_directly(self, env, monkeypatch):
        """打包版自愈重启同样不能带 `-m`：argparse 拒绝 → 子进程 exit 2、端口永不恢复。"""
        import subprocess
        import sys

        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")
        cfg = ProxyConfig()
        _set_running(monkeypatch, cfgdir, False)
        reconcile.set_routing_on(True)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        argv_seen = {}
        monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: argv_seen.update(argv=argv))
        monkeypatch.setattr(reconcile, "_wait_port_online", lambda port, timeout_s=2.0: False, raising=False)
        reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert argv_seen["argv"] == [sys.executable, "start"]


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


class TestObserveContractFields:
    """决策③/⑥c：status 契约增量——harnesses.*.config_path + 顶层 bind_port（只读，不写）。"""

    def test_harness_rows_expose_config_path_and_bind_port(self, env):
        home, cfgdir = env
        cfg = ProxyConfig()
        obs = reconcile.observe(cfg, tool_states=[])
        assert obs["bind_port"] == cfg.bind_port
        row = obs["harnesses"]["claude"]
        assert row["config_path"].endswith(os.path.join(".claude", "settings.json"))
        assert row["config_path"].startswith(str(home))  # 隔离 home 生效（HOME 被 monkeypatch）


class TestZcodeObserveOwnership:
    """zcode 接管归属用条目级信号（2026-09-02）：任一可接管条目指本代理即 ours——
    zcode CLI 会把它托管的 builtin 条目改回原值（磁盘漂移），只看激活条目地址会把
    正在走中继的会话误显示成"已旁路"。"""

    @staticmethod
    def _write_zcode(home, providers: dict) -> None:
        p = os.path.join(str(home), ".zcode", "v2", "config.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(json.dumps({"provider": providers}))

    def _observe_zcode(self, env, tool_states=None):
        cfg = ProxyConfig()
        return reconcile.observe(cfg, tool_states=tool_states or [])["harnesses"]["zcode"]

    def test_wired_entry_means_ours_even_if_active_entry_drifted_back(self, env):
        # 激活条目被 zcode 改回原值（漂移），但另一可接管条目仍指本代理 → ours
        home, _ = env
        self._write_zcode(
            home,
            {
                "builtin:plan": {
                    "kind": "anthropic",
                    "options": {"apiKey": "k-1", "baseURL": "https://open.bigmodel.cn/api/anthropic"},
                    "enabled": True,
                    "models": {},
                },
                "custom": {
                    "kind": "openai",
                    "options": {"apiKey": "k-2", "baseURL": "http://127.0.0.1:8787"},
                    "enabled": False,
                    "models": {},
                },
            },
        )
        row = self._observe_zcode(env)
        assert row["ownership"] == "ours"

    def test_no_wired_entry_keeps_disk_classification(self, env):
        # 无任何条目指本代理：按磁盘实际归属（other）如实显示旁路
        home, _ = env
        self._write_zcode(
            home,
            {
                "builtin:plan": {
                    "kind": "anthropic",
                    "options": {"apiKey": "k-1", "baseURL": "https://open.bigmodel.cn/api/anthropic"},
                    "enabled": True,
                    "models": {},
                },
            },
        )
        row = self._observe_zcode(env)
        assert row["ownership"] == "other"
