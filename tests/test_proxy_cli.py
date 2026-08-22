"""Proxy capability: lifecycle CLI (start/stop/status/logs/test-image/check)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from vision_relay import cli
from vision_relay.cli import parse_args

# ── parse_args tests ────────────────────────────────────────────────────────


def test_parse_args_test_image():
    args = parse_args(["test-image", "/tmp/a.png", "--question", "红字说了什么"])
    assert args.command == "test-image"
    assert args.path == "/tmp/a.png"
    assert args.question == "红字说了什么"


def test_parse_args_status():
    assert parse_args(["status"]).command == "status"


def test_parse_args_start():
    assert parse_args(["start"]).command == "start"


def test_parse_args_stop():
    assert parse_args(["stop"]).command == "stop"


def test_parse_args_logs():
    assert parse_args(["logs"]).command == "logs"


def test_parse_args_check():
    assert parse_args(["check"]).command == "check"


def test_parse_args_test_image_no_question():
    args = parse_args(["test-image", "/tmp/b.jpg"])
    assert args.command == "test-image"
    assert args.path == "/tmp/b.jpg"
    assert args.question is None


def test_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit):
        parse_args(["frobnicate"])


# ── cmd_status tests (no server) ───────────────────────────────────────────


def test_cmd_status_not_running(tmp_path):
    from vision_relay.cli import cmd_status

    with patch("vision_relay.cli._pid_path", return_value=str(tmp_path / "proxy.pid")):
        assert cmd_status() == 1


def test_cmd_stop_not_running(tmp_path):
    from vision_relay.cli import cmd_stop

    with patch("vision_relay.cli._pid_path", return_value=str(tmp_path / "proxy.pid")):
        assert cmd_stop() == 1


def test_cmd_status_running(tmp_path):
    from vision_relay.cli import cmd_status

    pid_file = tmp_path / "proxy.pid"
    pid_file.write_text("999999999")
    # Process 999999999 does not exist -> ProcessLookupError -> "not running"
    with patch("vision_relay.cli._pid_path", return_value=str(pid_file)):
        assert cmd_status() == 1


def test_cmd_stop_stale_pid(tmp_path):
    from vision_relay.cli import cmd_stop

    pid_file = tmp_path / "proxy.pid"
    pid_file.write_text("999999999")
    # kill of nonexistent pid -> ProcessLookupError -> "not running"
    with patch("vision_relay.cli._pid_path", return_value=str(pid_file)):
        assert cmd_stop() == 1


# ── cmd_logs tests ──────────────────────────────────────────────────────────


def test_cmd_logs_no_log(tmp_path):
    from vision_relay.cli import cmd_logs

    with patch("vision_relay.cli._log_path", return_value=str(tmp_path / "nonexistent.log")):
        assert cmd_logs() == 1


def test_cmd_logs_reads_tail(tmp_path):
    from vision_relay.cli import cmd_logs

    log_file = tmp_path / "proxy.log"
    log_file.write_text("line1\nline2\nline3\n")
    with patch("vision_relay.cli._log_path", return_value=str(log_file)):
        assert cmd_logs() == 0


# ── cmd_check tests (no relay, no port occupied) ────────────────────────────


def test_cmd_check_warns_no_relays():
    from vision_relay.cli import cmd_check
    from vision_relay.config import ProxyConfig

    cfg = ProxyConfig()
    # No relays -> problem reported, exit 1
    with patch("socket.socket") as mock_sock:
        mock_instance = mock_sock.return_value.__enter__.return_value
        mock_instance.connect_ex.return_value = 1  # port free
        assert cmd_check(cfg) == 1


def test_cmd_check_port_in_use():
    from vision_relay.cli import cmd_check
    from vision_relay.config import ProxyConfig

    cfg = ProxyConfig()
    with patch("socket.socket") as mock_sock:
        mock_instance = mock_sock.return_value.__enter__.return_value
        mock_instance.connect_ex.return_value = 0  # port occupied
        # Both ports in use + no relays
        result = cmd_check(cfg)
        assert result == 1


def test_cmd_start_already_running(tmp_path):
    from vision_relay.cli import cmd_start
    from vision_relay.config import ProxyConfig

    pid_file = tmp_path / "proxy.pid"
    pid_file.write_text("42")
    cfg = ProxyConfig()
    # L1 后守卫查 pid 存活性：monkeypatch 为"活着"保证跨机器确定性（pid 42 是否真实存在因机而异）
    with (
        patch("vision_relay.cli._pid_path", return_value=str(pid_file)),
        patch("vision_relay.cli._pid_running", return_value=True),
    ):
        assert cmd_start(cfg) == 1


# ── main() dispatch tests ───────────────────────────────────────────────────


def test_main_dispatch_status(tmp_path):
    from vision_relay.cli import main

    with patch("vision_relay.cli._pid_path", return_value=str(tmp_path / "proxy.pid")):
        with patch("vision_relay.config.load_config") as mock_cfg:
            from vision_relay.config import ProxyConfig

            mock_cfg.return_value = ProxyConfig()
            assert main(["status"]) == 1  # no process running


def test_main_dispatch_stop(tmp_path):
    from vision_relay.cli import main

    with patch("vision_relay.cli._pid_path", return_value=str(tmp_path / "proxy.pid")):
        with patch("vision_relay.config.load_config") as mock_cfg:
            from vision_relay.config import ProxyConfig

            mock_cfg.return_value = ProxyConfig()
            assert main(["stop"]) == 1  # no process running


def test_main_dispatch_logs(tmp_path):
    from vision_relay.cli import main

    with patch("vision_relay.cli._log_path", return_value=str(tmp_path / "nonexistent.log")):
        with patch("vision_relay.config.load_config") as mock_cfg:
            from vision_relay.config import ProxyConfig

            mock_cfg.return_value = ProxyConfig()
            assert main(["logs"]) == 1


# ── cmd_test_image VLM error handling ──────────────────────────────────────


def test_cmd_test_image_vlm_error(tmp_path):
    """I6: VLM errors should be caught and reported cleanly, not traceback."""
    from vision_relay.cli import cmd_test_image
    from vision_relay.config import ProxyConfig

    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"\x89PNG")
    cfg = ProxyConfig()
    args = parse_args(["test-image", str(img_file)])
    with patch("vision_relay.vlm.VLMClient") as mock_cls:
        mock_client = mock_cls.return_value
        from vision_relay.vlm import VLMError

        mock_client.describe.side_effect = VLMError("TIMEOUT", "timed out")
        assert cmd_test_image(args, cfg) == 1


def test_version_flag(capsys):
    from vision_relay import __version__
    from vision_relay.cli import parse_args

    with pytest.raises(SystemExit) as exc:
        parse_args(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


# ---- Phase2 M1: lifecycle intent + new verbs (json tested in Task 14) ----
# 注：任务稿替身 `lambda: x.setdefault(...) or <fallback>` 有 or-惯用法缺陷（记录值
# 为真时返回记录值而非回退值），这里改为显式记录+返回；断言与任务稿逐字一致。


class TestIntentState:
    def test_start_sets_routing_on_stop_clears(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay import reconcile

        cli.cmd_start_intent(True)
        assert reconcile.get_routing_on() is True
        cli.cmd_start_intent(False)
        assert reconcile.get_routing_on() is False


class TestDetachStart:
    def test_detach_spawns_child_and_returns(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        spawned = {}

        def _fake_spawn(argv):
            spawned["argv"] = argv
            return 0

        monkeypatch.setattr(cli, "_spawn_detached", _fake_spawn)
        rc = cli.cmd_start_detach(None)
        assert rc == 0
        assert spawned["argv"][1:] == ["-m", "vision_relay", "start"]


class TestRefreshVerb:
    def test_refresh_calls_reconcile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.config import ProxyConfig

        called = {}

        def _fake_reconcile(cfg, **kw):
            called["ok"] = True
            return {"actions": [], "needs_you": [], "observed": {}}

        monkeypatch.setattr("vision_relay.cli.reconcile_reconcile", _fake_reconcile)
        rc = cli.cmd_refresh(ProxyConfig())
        assert rc == 0 and called.get("ok") is True

    def test_refresh_warns_on_failed_auto_fix(self, tmp_path, monkeypatch, capsys):
        """L2: auto_fix 失败的 action 必须有显式警告行，不能混进普通日志。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.config import ProxyConfig

        def _fake_reconcile(cfg, **kw):
            return {
                "actions": [
                    {"type": "auto_fix", "harness": "claude", "fix": "restart", "ok": False},
                    {"type": "reclaim", "harness": "codex", "from": ":15721"},
                ],
                "needs_you": [],
                "observed": {},
            }

        monkeypatch.setattr("vision_relay.cli.reconcile_reconcile", _fake_reconcile)
        assert cli.cmd_refresh(ProxyConfig()) == 0
        out = capsys.readouterr().out
        assert "自动修复失败" in out
        assert "[reconcile]" in out  # 正常 action 照常打印

    def test_refresh_timeout_human_friendly(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.config import ProxyConfig

        def _boom(cfg, **kw):
            raise TimeoutError("config_lock: timeout after 30s")

        monkeypatch.setattr("vision_relay.cli.reconcile_reconcile", _boom)
        rc = cli.cmd_refresh(ProxyConfig())
        assert rc == 1
        assert "刷新失败" in capsys.readouterr().out


class TestDiagnoseVerb:
    @staticmethod
    def _obs():
        return {
            "service_alive": False,
            "routing_on": True,
            "tools": [
                {
                    "name": "cc-switch",
                    "port": 15721,
                    "online": True,
                    "active_provider": "prov-x",
                    "provider_base_url": "http://up",
                }
            ],
            "harnesses": {"claude": {"base_url": "http://127.0.0.1:8787", "ownership": "ours"}},
        }

    def test_diagnose_report_and_failed_auto_fix_warning(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.config import ProxyConfig

        def _fake_reconcile(cfg, **kw):
            return {
                "actions": [{"type": "auto_fix", "harness": "claude", "fix": "restart", "ok": False}],
                "needs_you": [{"type": "unresolvable", "harness": "claude", "hint": "快照缺失"}],
                "observed": self._obs(),
            }

        monkeypatch.setattr("vision_relay.cli.reconcile_reconcile", _fake_reconcile)
        rc = cli.cmd_diagnose(ProxyConfig())
        assert rc == 1  # needs_you 非空 -> 1
        out = capsys.readouterr().out
        assert "自动修复失败" in out
        assert "需要你" in out
        assert "未运行" in out and "路由意图: 开" in out
        assert "cc-switch" in out and "在线" in out and "prov-x" in out
        assert "base_url=http://127.0.0.1:8787 [ours]" in out

    def test_diagnose_clean_when_no_needs_you(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.config import ProxyConfig

        def _fake_reconcile(cfg, **kw):
            return {"actions": [], "needs_you": [], "observed": self._obs()}

        monkeypatch.setattr("vision_relay.cli.reconcile_reconcile", _fake_reconcile)
        assert cli.cmd_diagnose(ProxyConfig()) == 0

    def test_diagnose_timeout_human_friendly(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.config import ProxyConfig

        def _boom(cfg, **kw):
            raise TimeoutError("config_lock: timeout after 30s")

        monkeypatch.setattr("vision_relay.cli.reconcile_reconcile", _boom)
        assert cli.cmd_diagnose(ProxyConfig()) == 1
        assert "诊断失败" in capsys.readouterr().out


# ---- cmd_start 生命周期加固：L1 stale-pid / L3 restart-skip / intent / start 对账 ----


def _fake_start_env(tmp_path, monkeypatch):
    """cmd_start 全链路替身：onboarding/wiring/reconcile/run_server 全 fake，隔离真实 HOME。"""
    from vision_relay import wiring
    from vision_relay.config import ProxyConfig

    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("vision_relay.onboarding.run_onboarding", lambda cfg, *a, **k: True)
    monkeypatch.setattr(wiring, "relays_activate", lambda c: ["relay activated"])
    monkeypatch.setattr(wiring, "wiring_backup_and_rewrite", lambda c: ["wire rewritten"])
    monkeypatch.setattr(
        "vision_relay.cli.reconcile_reconcile",
        lambda cfg, **kw: {"actions": [], "needs_you": [], "observed": {}},
    )

    class _FakeServer:
        def serve_forever(self):
            pass

    monkeypatch.setattr("vision_relay.server.run_server", lambda cfg: _FakeServer())
    return ProxyConfig()


class TestStartLifecycle:
    def test_start_clears_stale_pid_and_starts(self, tmp_path, monkeypatch):
        """L1: pid 文件是硬崩溃残留（大 pid 不存在）-> 清掉继续启动，而非拒绝。"""
        cfg = _fake_start_env(tmp_path, monkeypatch)
        (tmp_path / "proxy.pid").write_text("999999999")
        assert cli.cmd_start(cfg) == 0
        assert not (tmp_path / "proxy.pid").exists()  # 正常收尾清理
        from vision_relay import reconcile

        assert reconcile.get_routing_on() is True  # start 记录路由意图

    def test_start_refuses_when_pid_alive(self, tmp_path, monkeypatch, capsys):
        cfg = _fake_start_env(tmp_path, monkeypatch)
        (tmp_path / "proxy.pid").write_text("999999999")
        monkeypatch.setattr(cli, "_pid_running", lambda pid: True)
        assert cli.cmd_start(cfg) == 1
        assert "already running" in capsys.readouterr().out
        assert (tmp_path / "proxy.pid").exists()  # 活着的 pid 文件不动

    def test_start_skips_onboarding_when_restart_env(self, tmp_path, monkeypatch, capsys):
        """L3: 分离重启子进程（VISION_RELAY_RESTART=1）无控制台，绝不能进交互向导。"""
        cfg = _fake_start_env(tmp_path, monkeypatch)
        monkeypatch.setenv("VISION_RELAY_RESTART", "1")
        from vision_relay import onboarding

        seen = []
        monkeypatch.setattr(onboarding, "run_onboarding", lambda cfg, *a, **k: seen.append(1) or True)
        assert cli.cmd_start(cfg) == 0
        assert seen == []
        assert "跳过" in capsys.readouterr().out

    def test_start_invokes_reconcile(self, tmp_path, monkeypatch):
        cfg = _fake_start_env(tmp_path, monkeypatch)
        calls = {}

        def _fake(cfg_, **kw):
            calls["trigger"] = kw.get("trigger")
            return {"actions": [{"type": "reclaim", "harness": "claude"}], "needs_you": [], "observed": {}}

        monkeypatch.setattr("vision_relay.cli.reconcile_reconcile", _fake)
        assert cli.cmd_start(cfg) == 0
        assert calls["trigger"] == "start"

    def test_start_survives_reconcile_timeout(self, tmp_path, monkeypatch, capsys):
        """对账拿不到锁（TimeoutError）不能阻止起服：降级为提示，服务照常。"""
        cfg = _fake_start_env(tmp_path, monkeypatch)

        def _boom(cfg_, **kw):
            raise TimeoutError("config_lock: timeout after 30s")

        monkeypatch.setattr("vision_relay.cli.reconcile_reconcile", _boom)
        assert cli.cmd_start(cfg) == 0
        assert "稍后" in capsys.readouterr().out


class TestStopIntent:
    def test_stop_clears_routing_intent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay import reconcile, wiring
        from vision_relay.config import ProxyConfig

        (tmp_path / "proxy.pid").write_text("999999999")
        monkeypatch.setattr(cli, "_pid_running", lambda pid: True)
        monkeypatch.setattr(cli, "_terminate", lambda pid: True)
        monkeypatch.setattr("vision_relay.config.load_config", lambda *a, **k: ProxyConfig())
        monkeypatch.setattr(wiring, "wiring_restore_on_stop", lambda c: [])
        monkeypatch.setattr(wiring, "relays_restore", lambda c: [])
        reconcile.set_routing_on(True)
        assert cli.cmd_stop() == 0
        assert reconcile.get_routing_on() is False  # stop 记录关闭意图


class TestRestartServiceEnv:
    def test_restart_service_spawn_injects_restart_env(self, tmp_path, monkeypatch):
        """L3: reconcile._restart_service spawn 时注入 VISION_RELAY_RESTART=1（拷贝，不污染原环境）。"""
        import os

        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay import reconcile
        from vision_relay.config import ProxyConfig

        captured = {}

        class _FakePopen:
            def __init__(self, argv, **kwargs):
                captured["argv"] = argv
                captured["env"] = kwargs.get("env")

        monkeypatch.setattr("subprocess.Popen", _FakePopen)
        monkeypatch.setattr(reconcile, "_wait_port_online", lambda port, timeout_s=2.0: True)
        assert reconcile._restart_service(ProxyConfig()) is True
        assert captured["argv"][1:] == ["-m", "vision_relay", "start"]
        assert captured["env"]["VISION_RELAY_RESTART"] == "1"
        assert "VISION_RELAY_RESTART" not in os.environ  # 不写回父进程环境


# ---- tools / probe / events / visionlog verbs + main dispatch ----


class TestToolsVerb:
    def test_tools_lists_tool_states(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.config import ProxyConfig
        from vision_relay.tools import ToolState

        monkeypatch.setattr(
            "vision_relay.tools.probe_tools",
            lambda *a, **k: [ToolState("cc-switch", 15721, True, "prov-x", "http://up")],
        )
        assert cli.cmd_tools(ProxyConfig()) == 0
        out = capsys.readouterr().out
        assert "cc-switch" in out and "在线" in out and "prov-x" in out


class TestProbeVerb:
    def test_probe_single_model(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.config import ProxyConfig

        monkeypatch.setattr(
            "vision_relay.reconcile.observe",
            lambda cfg, *a, **k: {"service_alive": False, "routing_on": False, "tools": [], "harnesses": {}},
        )
        monkeypatch.setattr("vision_relay.annotate.run_probe", lambda *a, **k: "image")
        args = cli.parse_args(["probe", "--harness", "claude", "--provider", "p", "--model", "m1"])
        assert cli.cmd_probe(args, ProxyConfig()) == 0
        assert "m1" in capsys.readouterr().out


class TestEventsVerb:
    def test_events_tails_recent(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay import reconcile

        reconcile.append_event("reclaim", "codex", {"from": "a", "to": "b"})
        assert cli.cmd_events(None) == 0
        out = capsys.readouterr().out
        assert "reclaim" in out and "codex" in out


class TestVisionlogVerb:
    def test_visionlog_queries_and_truncates(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.config import ProxyConfig

        monkeypatch.setattr(
            "vision_relay.visionlog.query",
            lambda harness=None, limit=200: [
                {"ts": "t", "harness": "claude", "tier": 1, "cache_hit": False, "injected": "x" * 100}
            ],
        )
        args = cli.parse_args(["visionlog"])
        assert cli.cmd_visionlog(args, ProxyConfig()) == 0
        out = capsys.readouterr().out
        assert "claude" in out and "cache=False" in out
        assert "x" * 60 in out and "x" * 61 not in out  # injected 截断到 60 字符


class TestNewVerbParseArgs:
    def test_parse_args_start_detach_flag(self):
        args = parse_args(["start", "--detach"])
        assert args.command == "start" and args.detach is True

    def test_parse_args_new_verbs(self):
        assert parse_args(["refresh"]).command == "refresh"
        assert parse_args(["diagnose"]).command == "diagnose"
        assert parse_args(["tools"]).command == "tools"
        assert parse_args(["events"]).command == "events"
        args = parse_args(["visionlog", "--harness", "claude"])
        assert args.command == "visionlog" and args.harness == "claude"

    def test_parse_args_probe_flags(self):
        args = parse_args(["probe", "--all-untested"])
        assert args.all_untested is True and args.model is None and args.harness is None


class TestMainDispatchNewVerbs:
    def test_main_dispatch_start_detach(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.config import ProxyConfig

        monkeypatch.setattr("vision_relay.config.load_config", lambda *a, **k: ProxyConfig())
        monkeypatch.setattr(cli, "_spawn_detached", lambda argv: 0)
        assert cli.main(["start", "--detach"]) == 0

    def test_main_dispatch_refresh(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.config import ProxyConfig

        monkeypatch.setattr("vision_relay.config.load_config", lambda *a, **k: ProxyConfig())
        monkeypatch.setattr(
            "vision_relay.cli.reconcile_reconcile",
            lambda cfg, **kw: {"actions": [], "needs_you": [], "observed": {}},
        )
        assert cli.main(["refresh"]) == 0


# ---- review fixes: M-1 events payload / m-1 detach env / m-2 start needs_you ----


class TestEventsPayload:
    def test_events_shows_payload_fields(self, tmp_path, monkeypatch, capsys):
        """M-1: 事件 payload（from/to/ok 等）必须可见——spec §5「从什么改成什么」可查。

        append_event 把 detail 扁平展开进事件行（没有 'detail' 键），渲染必须取
        ts/type/harness 之外的剩余字段，否则 reclaim/absorb/auto_fix 的内容全丢。
        """
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay import reconcile

        reconcile.append_event("reclaim", "codex", {"from": "http://old:1", "to": "http://127.0.0.1:8787", "ok": True})
        assert cli.cmd_events(None) == 0
        out = capsys.readouterr().out
        assert "http://old:1" in out
        assert "http://127.0.0.1:8787" in out
        assert "reclaim" in out and "codex" in out

    def test_events_row_without_payload(self, tmp_path, monkeypatch, capsys):
        """只有 ts/type/harness 的行不带尾坠 JSON，不打印 '{}'。"""
        import json

        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        tmp_path.joinpath("events.jsonl").write_text(
            json.dumps({"ts": 1, "type": "reclaim", "harness": None}) + "\n", encoding="utf-8"
        )
        assert cli.cmd_events(None) == 0
        assert "{}" not in capsys.readouterr().out


class TestDetachSpawnEnv:
    def test_spawn_detached_injects_restart_env(self, tmp_path, monkeypatch):
        """m-1: 任何分离 spawn 都无控制台——一律注入 VISION_RELAY_RESTART=1 跳过交互向导。"""
        import os

        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        captured = {}

        class _FakePopen:
            def __init__(self, argv, **kwargs):
                captured["argv"] = argv
                captured["env"] = kwargs.get("env")

        monkeypatch.setattr("subprocess.Popen", _FakePopen)
        assert cli._spawn_detached(["C:/py/python.exe", "-m", "vision_relay", "start"]) == 0
        assert captured["argv"][0] == "C:/py/python.exe"
        assert captured["env"]["VISION_RELAY_RESTART"] == "1"
        assert "VISION_RELAY_RESTART" not in os.environ  # 拷贝注入，不污染当前进程环境


class TestStartReconcileNeedsYou:
    def test_start_reconcile_surfaces_needs_you(self, tmp_path, monkeypatch, capsys):
        """m-2: start 对账返回的 needs_you 不能静默丢弃（如 direct-* 缺 key 提醒）。"""
        cfg = _fake_start_env(tmp_path, monkeypatch)

        def _fake(cfg_, **kw):
            return {
                "actions": [{"type": "reclaim", "harness": "claude"}],
                "needs_you": [{"type": "missing_key", "harness": "claude", "hint": "补 key"}],
                "observed": {},
            }

        monkeypatch.setattr("vision_relay.cli.reconcile_reconcile", _fake)
        assert cli.cmd_start(cfg) == 0
        out = capsys.readouterr().out
        assert "[需要你]" in out
        assert "补 key" in out


# ---- Task 14: --json verbs（spec §4 通信契约：envelope + contract_version）----


class TestJsonVerbsCli:
    def test_parse_args_json_flag_after_subcommand(self):
        """--json 挂在公共 parent parser 上：子命令后置 flag 可用。"""
        args = parse_args(["status", "--json"])
        assert args.command == "status" and args.json is True

    def test_main_config_json_envelope_and_masking(self, tmp_path, monkeypatch, capsys):
        """config --json：单行 JSON envelope（contract_version:1），且明文 key 绝不外泄。"""
        import json as _json

        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.config import ProxyConfig

        cfg = ProxyConfig()
        cfg.vlm.api_key = "sk-secret"
        monkeypatch.setattr("vision_relay.config.load_config", lambda *a, **k: cfg)
        assert cli.main(["config", "--json"]) == 0
        out = capsys.readouterr().out
        assert '"contract_version": 1' in out
        assert "sk-secret" not in out
        assert "●●●●" in out
        payload = _json.loads(out)
        assert payload["ok"] is True and payload["data"]["vlm"]["api_key"] == "●●●●"

    def test_main_json_config_error_envelope(self, tmp_path, monkeypatch, capsys):
        """Minor-2: --json 下配置损坏也要给 GUI 可统一解析的 envelope（ok=False，rc 仍 2）。"""
        import json as _json

        from vision_relay.config import ConfigError

        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))

        def _boom(*a, **k):
            raise ConfigError("bad json")

        monkeypatch.setattr("vision_relay.config.load_config", _boom)
        assert cli.main(["config", "--json"]) == 2
        payload = _json.loads(capsys.readouterr().out)
        assert payload["contract_version"] == 1 and payload["ok"] is False
        assert "bad json" in payload["data"]["error"]


# ---- Phase2 M1: onboarding tri-state default ----


class _Sink:
    def write(self, s):
        pass

    def flush(self):
        pass


class TestOnboardingTriState:
    def test_confirm_defaults_to_text_only_and_uses_image_value(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.onboarding import ModelEntry, ModelGroup, confirm_models

        groups = [ModelGroup(group="claude", path="x", entries=[ModelEntry(model="qwen-vl-max")])]
        keys = iter(["enter"])  # 直接回车 = 接受默认（未勾选=纯文本）
        result = confirm_models(groups, key_source=lambda: next(keys), out=_Sink())
        assert result["claude"]["qwen-vl-max"] == "text_only"

    def test_toggle_marks_image(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.onboarding import ModelEntry, ModelGroup, confirm_models

        groups = [ModelGroup(group="claude", path="x", entries=[ModelEntry(model="kimi-k2.7-code")])]
        keys = iter(["space", "enter"])
        result = confirm_models(groups, key_source=lambda: next(keys), out=_Sink())
        assert result["claude"]["kimi-k2.7-code"] == "image"  # 内部值 image（非 vision）


def test_onboarding_merge_writes_legacy_provider_bucket(tmp_path, monkeypatch):
    """I3 回归：onboarding 直接写三层 legacy provider 桶，与 config.from_dict 迁移固定点一致。"""
    from vision_relay.config import ProxyConfig
    from vision_relay.onboarding import _merge

    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    cfg = ProxyConfig()
    _merge(cfg, {"claude": {"qwen-vl-max": "image"}})
    assert cfg.model_capabilities["claude"]["legacy"]["qwen-vl-max"] == "image"
    reloaded = ProxyConfig.from_dict({"model_capabilities": cfg.model_capabilities})
    assert reloaded.model_capabilities == cfg.model_capabilities  # 迁移固定点：不再变形
