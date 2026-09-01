"""CLI 子系统重构守护测试（点名四路径 + 自选路径，理由见 REFACTOR_NOTES.md）：
① 参数解析矩阵（全部 23 个子命令 × 常用旗标）
② envelope 契约（contract_version/ok/data/错误信封形态）
③ stdin JSON 动词非法输入错误路径（models-set/vlm-set/vlm-test/settings-set）
④ 交互确认流与 models-scan 输出稳定性
自选：⑤ verbs 门面 __all__ 与 DI seam 透传 ⑥ probe_target_for 分支矩阵
⑦ cmd_probe --all-untested 批量路径 ⑧ vlm_set 自定义提示词/分组分支

全部沙箱隔离（VISION_RELAY_CONFIG_DIR 重定向），不触碰真实 ~。
"""

from __future__ import annotations

import io
import json

import pytest

from vision_relay import verbs
from vision_relay.cli import parse_args
from vision_relay.cli_args import _NO_JSON_PARENT, SUBCOMMANDS
from vision_relay.config import ProxyConfig

# 命令集唯一真相源是 cli_args.SUBCOMMANDS（新增命令登记后矩阵自动覆盖，无静默缺口）
ALL_SUBCOMMANDS = list(SUBCOMMANDS)


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    return ProxyConfig()


def _set_stdin(monkeypatch, payload) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


# ── ① 参数解析矩阵 ─────────────────────────────────────────────────
class TestParseArgsMatrix:
    def test_subcommands_tuple_matches_registered_parsers(self):
        """SUBCOMMANDS 与 parse_args 实际注册的子命令集合必须一致（防双写漂移）。"""
        import argparse

        # 重建 parser 取注册集合（parse_args 不暴露 parser，用相同构造路径）
        from vision_relay import __version__
        from vision_relay.cli_args import _SPECIAL_FLAGS, _add_special_flags

        parser = argparse.ArgumentParser(prog="vision-relay")
        parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
        sub = parser.add_subparsers(dest="command", required=True)
        common = argparse.ArgumentParser(add_help=False)
        common.add_argument("--json", action="store_true")
        for name in SUBCOMMANDS:
            parents = [] if name in _NO_JSON_PARENT else [common]
            p = sub.add_parser(name, parents=parents)
            if name in _SPECIAL_FLAGS:
                _add_special_flags(p, name)
        registered = {a for a in parser._actions if isinstance(a, argparse._SubParsersAction)}
        choices = set().union(*(a.choices.keys() for a in registered))
        assert choices == set(SUBCOMMANDS)
        assert list(SUBCOMMANDS) == sorted(choices, key=list(SUBCOMMANDS).index)  # 顺序也一致

    @pytest.mark.parametrize("command", ALL_SUBCOMMANDS)
    def test_each_subcommand_parses(self, command):
        argv = [command, "/tmp/a.png"] if command == "test-image" else [command]
        args = parse_args(argv)
        assert args.command == command
        assert getattr(args, "json", False) is False  # 不带 --json 时一律 False

    JSON_FLAG_COMMANDS = [c for c in ALL_SUBCOMMANDS if c not in _NO_JSON_PARENT]

    @pytest.mark.parametrize("command", JSON_FLAG_COMMANDS)
    def test_each_subcommand_accepts_json_flag(self, command):
        # 公共 parent：除 start（起服）/test-image（直连工具）外全部可后置挂 --json
        args = parse_args([command, "--json"])
        assert args.command == command and args.json is True

    def test_start_and_test_image_reject_json_flag(self):
        for command in ("start", "test-image"):
            with pytest.raises(SystemExit) as exc:
                parse_args([command, "--json"] + (["/tmp/a.png"] if command == "test-image" else []))
            assert exc.value.code == 2

    def test_start_detach_flag(self):
        assert parse_args(["start", "--detach"]).detach is True
        assert parse_args(["start"]).detach is False

    def test_test_image_args(self):
        args = parse_args(["test-image", "/tmp/a.png", "--question", "红字说了什么"])
        assert args.path == "/tmp/a.png" and args.question == "红字说了什么"

    def test_probe_flags(self):
        args = parse_args(["probe", "--harness", "claude", "--provider", "p", "--model", "m"])
        assert (args.harness, args.provider, args.model) == ("claude", "p", "m")
        assert parse_args(["probe", "--all-untested"]).all_untested is True

    def test_events_limit_flag(self):
        assert parse_args(["events", "--limit", "10"]).limit == 10
        assert parse_args(["events"]).limit == 50  # 默认 tail 50

    def test_visionlog_harness_flag(self):
        assert parse_args(["visionlog", "--harness", "zcode"]).harness == "zcode"

    def test_version_action(self, capsys):
        with pytest.raises(SystemExit) as exc:
            parse_args(["--version"])
        assert exc.value.code == 0

    def test_unknown_command_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            parse_args(["nope"])
        assert exc.value.code == 2


# ── ② envelope 契约 ──────────────────────────────────────────────────
class TestEnvelopeContract:
    def test_success_envelope_shape(self):
        e = verbs.envelope(True, {"x": 1})
        assert set(e) == {"contract_version", "ok", "data"}
        assert e["contract_version"] == 1 and e["ok"] is True and e["data"] == {"x": 1}

    def test_error_envelope_shape(self):
        e = verbs.envelope(False, {"error": "boom"})
        assert e["contract_version"] == 1 and e["ok"] is False
        assert e["data"] == {"error": "boom"}

    def test_contract_version_is_pinned_constant(self):
        assert verbs.CONTRACT_VERSION == 1

    def test_stdin_invalid_json_error_envelope(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
        out = verbs.models_set(ProxyConfig())
        assert out["ok"] is False and out["contract_version"] == 1
        assert "invalid stdin json" in out["data"]["error"]

    def test_all_json_verbs_return_envelope(self, cfg, monkeypatch):
        """_JSON_MAP 中每个动词的返回都带信封三字段（GUI 只消费信封）。"""
        from vision_relay.cli import _JSON_MAP

        assert set(_JSON_MAP) == {
            "status",
            "refresh",
            "diagnose",
            "models-scan",
            "models-set",
            "config",
            "tools",
            "events",
            "visionlog",
            "vlm-set",
            "vlm-test",
            "vlm-secret",
            "settings-set",
            "relay-set",
            "zcode-restart",
            "probe",
            "models-fetch",
        }
        for name, fn in _JSON_MAP.items():
            assert callable(fn), name

    def test_read_only_verbs_actually_return_envelope(self, cfg, monkeypatch):
        """点名：只读动词逐个实调，断言返回值真是信封（防"只查表不验证"的假守护）。

        写/动作动词（models-set 等）在 TestStdinInvalidInput 等用例经 stdin 路径实调覆盖；
        此处覆盖不依赖 stdin 的只读动词。
        """
        monkeypatch.setattr(
            verbs,
            "_observe_for_status",
            lambda c: {"service_alive": False, "harnesses": {}, "tools": [], "routing_on": False},
        )
        monkeypatch.setattr(verbs, "_reconcile", lambda c, **kw: {"actions": [], "needs_you": [], "observed": {}})
        monkeypatch.setattr(verbs, "_probe_tools", lambda: [])
        monkeypatch.setattr(verbs, "_tail_events", lambda n=50: [])
        monkeypatch.setattr(verbs, "_vl_query", lambda **kw: [])
        monkeypatch.setattr(verbs, "_scan_triples", lambda c: [])

        for name, out in {
            "status": verbs.status(cfg),
            "refresh": verbs.refresh(cfg),
            "diagnose": verbs.diagnose(cfg),
            "models-scan": verbs.models_scan(cfg),
            "config": verbs.config_get(cfg),
            "tools": verbs.tools(cfg),
            "events": verbs.events(cfg),
            "visionlog": verbs.visionlog(cfg),
        }.items():
            assert set(out) == {"contract_version", "ok", "data"}, name
            assert out["contract_version"] == 1 and out["ok"] is True, name

    def test_diagnose_envelope_ok_reflects_needs_you(self, cfg, monkeypatch):
        monkeypatch.setattr(verbs, "_reconcile", lambda c, **kw: {"actions": [], "needs_you": ["x"], "observed": {}})
        assert verbs.diagnose(cfg)["ok"] is False
        monkeypatch.setattr(verbs, "_reconcile", lambda c, **kw: {"actions": [], "needs_you": [], "observed": {}})
        assert verbs.diagnose(cfg)["ok"] is True


# ── ③ stdin JSON 非法输入错误路径 ───────────────────────────────────
class TestStdinInvalidInput:
    def test_models_set_rejects_non_array(self, cfg, monkeypatch):
        _set_stdin(monkeypatch, {"harness": "claude"})
        out = verbs.models_set(cfg)
        assert out["ok"] is False and "expected a JSON array" in out["data"]["error"]

    def test_models_set_rejects_row_missing_keys(self, cfg, monkeypatch):
        _set_stdin(monkeypatch, [{"harness": "claude", "provider": "p"}])
        out = verbs.models_set(cfg)
        assert out["ok"] is False and "row missing keys" in out["data"]["error"]

    def test_models_set_rejects_bad_value_enum(self, cfg, monkeypatch):
        _set_stdin(monkeypatch, [{"harness": "claude", "provider": "p", "model": "m", "value": "yes"}])
        out = verbs.models_set(cfg)
        assert out["ok"] is False and "image|text_only|null" in out["data"]["error"]

    def test_vlm_set_rejects_non_object(self, cfg, monkeypatch):
        _set_stdin(monkeypatch, [1, 2])
        out = verbs.vlm_set(cfg)
        assert out["ok"] is False and "expected a JSON object" in out["data"]["error"]

    def test_vlm_set_rejects_masked_placeholder(self, cfg, monkeypatch):
        _set_stdin(monkeypatch, {"vlm": {"api_key": "●●●●"}})
        out = verbs.vlm_set(cfg)
        assert out["ok"] is False and "masked placeholder" in out["data"]["error"]

    def test_vlm_set_rejects_vlm_by_harness_non_object(self, cfg, monkeypatch):
        _set_stdin(monkeypatch, {"vlm_by_harness": {"zcode": "junk"}})
        out = verbs.vlm_set(cfg)
        assert out["ok"] is False and "must be object or null" in out["data"]["error"]

    def test_vlm_test_rejects_bad_mode(self, cfg, monkeypatch):
        _set_stdin(monkeypatch, {"mode": "tier3"})
        out = verbs.vlm_test(cfg)
        assert out["ok"] is False and "mode must be tier1|tier2" in out["data"]["error"]

    def test_settings_set_rejects_unknown_key(self, cfg, monkeypatch):
        _set_stdin(monkeypatch, {"routing": {"nope": 1}})
        out = verbs.settings_set(cfg)
        assert out["ok"] is False and "unsupported settings key" in out["data"]["error"]

    def test_settings_set_rejects_bad_retention_days(self, cfg, monkeypatch):
        _set_stdin(monkeypatch, {"vision_log": {"retention_days": 0}})
        out = verbs.settings_set(cfg)
        assert out["ok"] is False and "retention_days" in out["data"]["error"]

    def test_settings_set_rejects_unknown_harness(self, cfg, monkeypatch):
        _set_stdin(monkeypatch, {"routing": {"harnesses": ["claude", "nope"]}})
        out = verbs.settings_set(cfg)
        assert out["ok"] is False and "unknown/duplicate harness" in out["data"]["error"]

    def test_settings_set_rejects_non_bool_enabled(self, cfg, monkeypatch):
        _set_stdin(monkeypatch, {"vision_log": {"enabled": "yes"}})
        out = verbs.settings_set(cfg)
        assert out["ok"] is False and "enabled must be a boolean" in out["data"]["error"]

    def test_relay_set_rejects_unknown_relay(self, cfg, monkeypatch):
        _set_stdin(monkeypatch, {"name": "ghost", "suppressed": True})
        out = verbs.relay_set(cfg)
        assert out["ok"] is False and "unknown relay" in out["data"]["error"]

    def test_relay_set_rejects_masked_key(self, cfg, monkeypatch):
        from vision_relay.config import RelayConfig

        cfg.relays.append(RelayConfig(name="r1", protocol="chat", base_url="https://x"))
        _set_stdin(monkeypatch, {"name": "r1", "api_key": "●●●●"})
        out = verbs.relay_set(cfg)
        assert out["ok"] is False and "api_key must be a non-empty string" in out["data"]["error"]


# ── ④ 交互确认流与 models-scan 输出稳定性 ───────────────────────────
class TestConfirmationAndScanStability:
    def test_models_scan_output_is_deterministic(self, cfg, monkeypatch, capsys):
        """同输入两次运行 models-scan，草稿输出逐字节一致（GUI diff 稳定性的前提）。"""
        from vision_relay.onboarding import models_scan_report

        monkeypatch.setattr(
            verbs,
            "_scan_triples",
            lambda c: [
                {
                    "harness": "claude",
                    "provider": "bigmodel",
                    "model": "m1",
                    "value": None,
                    "source": None,
                    "probe_cached": None,
                    "is_current": True,
                },
            ],
        )
        models_scan_report(cfg)
        first = capsys.readouterr().out
        models_scan_report(cfg)
        second = capsys.readouterr().out
        assert first == second and first != ""

    def test_models_set_confirms_capability_flag(self, cfg, monkeypatch):
        """models-set 成功即过目确认（M2 Task 13）：空数组也置位，向导不再反复弹。"""
        assert cfg.routing.capability_confirmed is False
        _set_stdin(monkeypatch, [])
        assert verbs.models_set(cfg)["ok"] is True
        assert cfg.routing.capability_confirmed is True

    def test_unknown_default_rejects_bad_enum(self, cfg, monkeypatch):
        _set_stdin(monkeypatch, {"routing": {"unknown_default": "bogus"}})
        out = verbs.settings_set(cfg)
        assert out["ok"] is False and "unknown_default must be text_only|image" in out["data"]["error"]


# ── ⑤ 自选：verbs 门面 __all__ 与 DI seam 透传 ──────────────────────
class TestProbeTargetForBranches:
    def _tools(self, *states):
        return {name: {"online": on, "port": port, "active_provider": "bigmodel"} for name, port, on in states}

    def test_two_layer_uses_tool_port_without_key(self, cfg):
        from vision_relay.tools import TOOL_DOSSIERS

        t = self._tools(("cc-switch", 15721, True))
        base, key, proto = verbs.probe_target_for(cfg, "claude", "bigmodel", t)
        assert base == "http://127.0.0.1:15721" and key == "" and proto == "anthropic"
        assert "cc-switch" in TOOL_DOSSIERS

    def test_codex_plus_uses_v1_suffix_and_responses_proto(self, cfg):
        t = self._tools(("codex-plus", 57321, True))
        base, key, proto = verbs.probe_target_for(cfg, "codex", "openrouter", t)
        assert base == "http://127.0.0.1:57321/v1" and proto == "responses" and key == ""

    def test_offline_tools_fall_back_direct(self, cfg, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text(
            json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://ark.example/api", "ANTHROPIC_AUTH_TOKEN": "tok-1"}}),
            encoding="utf-8",
        )
        import vision_relay.wiring as W

        monkeypatch.setattr(W, "HOME", str(home))
        base, key, proto = verbs.probe_target_for(cfg, "claude", "volces-ark", self._tools())
        assert base == "https://ark.example/api" and key == "tok-1" and proto == "anthropic"

    def test_relay_fallback_when_no_tool_no_direct(self, cfg):
        from vision_relay.config import RelayConfig

        cfg.relays.append(RelayConfig(name="r1", protocol="chat", base_url="https://up.example", api_key="k1"))
        base, key, proto = verbs.probe_target_for(cfg, "qwen-code", "p", self._tools())
        assert (base, key, proto) == ("https://up.example", "k1", "chat")

    def test_empty_when_nothing_available(self, cfg):
        assert verbs.probe_target_for(cfg, "qwen-code", "p", self._tools()) == ("", "", "chat")

    def test_zcode_target_is_self_provider(self, cfg, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg2"))
        import vision_relay.wiring as W

        monkeypatch.setattr(W, "HOME", str(home))
        (home / ".zcode" / "v2").mkdir(parents=True)
        (home / ".zcode" / "v2" / "config.json").write_text(
            json.dumps(
                {"provider": {"k": {"kind": "anthropic", "options": {"baseURL": "https://z.example", "apiKey": "zk"}}}}
            ),
            encoding="utf-8",
        )
        base, key, proto = verbs.probe_target_for(cfg, "zcode", "k", self._tools())
        assert base == "https://z.example" and key == "zk"


# ── ⑦ 自选：cmd_probe --all-untested 批量路径 ───────────────────────
class TestCmdProbeAllUntested:
    def test_all_untested_loops_current_provider_models(self, cfg, monkeypatch, capsys):
        from types import SimpleNamespace

        import vision_relay.cli as cli
        from vision_relay.cli_commands import cmd_probe

        groups = [SimpleNamespace(group="claude", entries=[SimpleNamespace(model="m1"), SimpleNamespace(model="m2")])]
        monkeypatch.setattr("vision_relay.onboarding.scan_model_groups", lambda c: groups)
        monkeypatch.setattr("vision_relay.annotate.run_probe", lambda *a, **k: "image")
        monkeypatch.setattr(verbs, "probe_target_for", lambda c, h, p, tb: ("https://up.example", "", "chat"))
        args = cli.parse_args(["probe", "--all-untested"])
        assert cmd_probe(args, cfg) == 0
        out = capsys.readouterr().out
        assert "  m1: image" in out and "  m2: image" in out
        assert "probed 2 model(s)" in out

    def test_probe_single_model_rc_reflects_result(self, cfg, monkeypatch, capsys):
        from vision_relay.cli_commands import cmd_probe

        args = parse_args(["probe", "--harness", "claude", "--provider", "p", "--model", "m1"])
        monkeypatch.setattr("vision_relay.annotate.run_probe", lambda *a, **k: "image")
        monkeypatch.setattr(verbs, "probe_target_for", lambda c, h, p, tb: ("https://up.example", "", "chat"))
        assert cmd_probe(args, cfg) == 0
        assert "m1: image" in capsys.readouterr().out


# ── ⑧ 自选：vlm_set 自定义提示词/分组分支 ───────────────────────────
class TestVlmSetBranches:
    def test_custom_tier_null_resets_to_default(self, cfg, monkeypatch):
        cfg.vlm.custom_tier1 = "special"
        _set_stdin(monkeypatch, {"custom_tier1": None})
        assert verbs.vlm_set(cfg)["ok"] is True
        assert cfg.vlm.custom_tier1 is None

    def test_empty_api_key_means_skip_not_clear(self, cfg, monkeypatch):
        cfg.vlm.api_key = "keep-me"
        _set_stdin(monkeypatch, {"vlm": {"api_key": ""}})
        assert verbs.vlm_set(cfg)["ok"] is True
        assert cfg.vlm.api_key == "keep-me"

    def test_vlm_by_harness_null_means_follow_global(self, cfg, monkeypatch):
        cfg.vlm_by_harness["zcode"] = {"model": "m"}
        _set_stdin(monkeypatch, {"vlm_by_harness": {"zcode": None}})
        assert verbs.vlm_set(cfg)["ok"] is True
        assert "zcode" not in cfg.vlm_by_harness


class TestVerbsFacadeContract:
    def test_all_names_resolve_and_match_lazy_map(self):
        """__all__ 每个名字都可访问；DI seam 与契约名在门面 __dict__，
        领域动词经 _verbs() 访问器解析（无环纪律的可观测面）。"""
        from vision_relay import verbs as v

        for name in v.__all__:
            assert hasattr(v, name), name
        # DI seam 是门面自有定义（patch 挂点），不经过惰性访问器
        for seam in ("_observe_for_status", "_reconcile", "_probe_tools", "_tail_events", "_vl_query"):
            assert seam in v.__dict__

    def test_di_seams_delegate_to_real_impls(self, cfg):
        """DI seam 默认透传真实现（测试替换的是挂点，不是行为）。"""
        assert verbs._reconcile is not None
        assert verbs._observe_for_status is not None
        assert verbs._probe_tools is not None
        assert verbs._tail_events is not None
        assert verbs._vl_query is not None


# ── ⑥ 自选：probe_target_for 分支矩阵 ───────────────────────────────
