"""CLI 子系统重构守护测试：参数解析矩阵、envelope 契约、stdin 非法输入、交互确认流。

本文件为 CLI 重构后新增的守护测试（不改动任何既有断言）。沿用仓库隔离纪律
（VISION_RELAY_CONFIG_DIR 指沙箱），不触碰真实家目录。
"""

from __future__ import annotations

import io
import json

import pytest

from vision_relay import verbs
from vision_relay.cli_args import parse_args
from vision_relay.config import ProxyConfig, RelayConfig


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    return ProxyConfig()


def _stdin(monkeypatch, raw):
    monkeypatch.setattr("sys.stdin", io.StringIO(raw if isinstance(raw, str) else json.dumps(raw)))


# ── ① CLI 参数解析矩阵（16 子命令 × 常用旗标）──────────────────────────


@pytest.mark.parametrize(
    "argv,command",
    [
        (["start"], "start"),
        (["stop"], "stop"),
        (["status"], "status"),
        (["logs"], "logs"),
        (["test-image", "x.png"], "test-image"),
        (["check"], "check"),
        (["models"], "models"),
        (["models-scan"], "models-scan"),
        (["models-set"], "models-set"),
        (["refresh"], "refresh"),
        (["diagnose"], "diagnose"),
        (["tools"], "tools"),
        (["probe"], "probe"),
        (["events"], "events"),
        (["visionlog"], "visionlog"),
        (["config"], "config"),
        (["vlm-set"], "vlm-set"),
        (["vlm-test"], "vlm-test"),
        (["vlm-secret"], "vlm-secret"),
        (["settings-set"], "settings-set"),
        (["relay-set"], "relay-set"),
        (["zcode-restart"], "zcode-restart"),
        (["models-fetch"], "models-fetch"),
    ],
)
def test_parse_subcommand_name(argv, command):
    assert parse_args(argv).command == command


def test_parse_flags_matrix():
    assert parse_args(["start", "--detach"]).detach is True
    assert parse_args(["start"]).detach is False
    ns = parse_args(["test-image", "p.png", "--question", "what?"])
    assert ns.path == "p.png" and ns.question == "what?"
    ns = parse_args(["probe", "--harness", "claude", "--provider", "p", "--model", "m"])
    assert (ns.harness, ns.provider, ns.model) == ("claude", "p", "m")
    assert parse_args(["probe", "--all-untested"]).all_untested is True
    assert parse_args(["events", "--limit", "10"]).limit == 10
    assert parse_args(["events"]).limit == 50  # 默认值
    assert parse_args(["visionlog", "--harness", "codex"]).harness == "codex"
    # --json 旗标在管理子命令上可用
    for cmd in ("status", "config", "vlm-set", "settings-set"):
        assert parse_args([cmd, "--json"]).json is True


def test_json_map_covers_all_json_verbs():
    """_JSON_MAP 的键集 = 接受 --json 的子命令集（GUI 契约面）。"""
    from vision_relay.cli_args import _JSON_MAP

    for name in _JSON_MAP:
        assert parse_args([name, "--json"]).command == name
    # 关键：_JSON_MAP 的值都是可调用 verbs
    assert all(callable(fn) for fn in _JSON_MAP.values())


# ── ② envelope 契约 ────────────────────────────────────────────────────


def test_envelope_shape_ok():
    env = verbs.envelope(True, {"a": 1})
    assert env == {"contract_version": 1, "ok": True, "data": {"a": 1}}


def test_envelope_shape_error():
    env = verbs.envelope(False, {"error": "boom"})
    assert env["contract_version"] == 1 and env["ok"] is False
    assert env["data"] == {"error": "boom"}


def test_contract_version_pinned():
    assert verbs.CONTRACT_VERSION == 1
    assert verbs.envelope(True, None)["contract_version"] == verbs.CONTRACT_VERSION


def test_verbs_return_envelope_wrapped(tmp_path, monkeypatch):
    """各动词输出都被 envelope 包裹（contract_version/ok/data 三件套）。"""
    cfg = _cfg(tmp_path, monkeypatch)
    for out in (verbs.status(cfg), verbs.tools(cfg), verbs.events(cfg), verbs.config_get(cfg)):
        assert set(out) == {"contract_version", "ok", "data"}
        assert out["contract_version"] == 1


# ── ③ stdin JSON 非法输入错误路径 ──────────────────────────────────────


@pytest.mark.parametrize(
    "verb,payload",
    [
        ("models_set", "not-json"),
        ("models_set", {"not": "array"}),  # 顶层类型错（应 array）
        ("models_set", [{"harness": "claude"}]),  # 缺 provider/model 键
        ("models_set", [{"harness": "claude", "provider": "p", "model": "m", "value": "movie"}]),  # 非法 value
        ("vlm_set", "not-json"),
        ("vlm_set", {"vlm": [1, 2]}),  # vlm 须 object
        ("vlm_set", {"vlm": {"api_key": "●●●●"}}),  # 打码占位拒绝
        ("vlm_test", "not-json"),
        ("vlm_test", {"mode": "bogus"}),  # mode 须 tier1|tier2
        ("settings_set", "not-json"),
        ("settings_set", {"routing": {"bogus_key": 1}}),  # 白名单外键
        ("settings_set", {"vision_log": {"retention_days": 0}}),  # retention_days>=1
    ],
)
def test_stdin_invalid_input_returns_error_envelope(tmp_path, monkeypatch, verb, payload):
    cfg = _cfg(tmp_path, monkeypatch)
    _stdin(monkeypatch, payload)
    out = getattr(verbs, verb)(cfg)
    assert out["ok"] is False
    assert isinstance(out["data"].get("error"), str) and out["data"]["error"]


# ── ④ 交互确认流与 models-scan 输出稳定性 ──────────────────────────────


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


def test_cmd_start_onboarding_failure_aborts(tmp_path, monkeypatch):
    """首次启用未完成能力确认 → 不起服、返回 1（交互确认流守卫）。"""
    cfg = _fake_start_env(tmp_path, monkeypatch)
    monkeypatch.setattr("vision_relay.onboarding.run_onboarding", lambda c: False)
    from vision_relay import cli

    assert cli.cmd_start(cfg) == 1
    assert not (tmp_path / "proxy.pid").exists()  # 未起服 → 未写 pid


def test_cmd_start_restart_skips_onboarding(tmp_path, monkeypatch, capsys):
    """VISION_RELAY_RESTART=1（分离重启子进程）无控制台，绝不能进交互向导。"""
    cfg = _fake_start_env(tmp_path, monkeypatch)
    monkeypatch.setenv("VISION_RELAY_RESTART", "1")
    from vision_relay import onboarding

    seen = []
    monkeypatch.setattr(onboarding, "run_onboarding", lambda c, *a, **k: seen.append(1) or True)
    from vision_relay import cli

    assert cli.cmd_start(cfg) == 0
    assert seen == []  # 跳过 onboarding
    assert "跳过" in capsys.readouterr().out
    assert not (tmp_path / "proxy.pid").exists()  # 正常起服后 finally 清理 pid


def test_models_scan_output_stable_keys(tmp_path, monkeypatch):
    """models-scan 每行稳定字段集（GUI 按 key 取值，key 漂移即破契约）。"""
    cfg = _cfg(tmp_path, monkeypatch)
    from vision_relay import model_sources

    monkeypatch.setattr(
        model_sources, "harness_matrix", lambda c: {}
    )  # 无供应商 → 空模型列表，仍验证信封与字段
    out = verbs.models_scan(cfg)
    assert out["ok"] is True and out["data"] == {"models": []}
    # 有一行时字段集稳定
    from vision_relay.model_sources import ProviderRow

    monkeypatch.setattr(
        model_sources,
        "harness_matrix",
        lambda c: {"claude": [ProviderRow("cc-switch", "claude", "bigmodel", "https://x", True, ["m1"])]},
    )
    out = verbs.models_scan(cfg)
    row = out["data"]["models"][0]
    assert set(row) == {"harness", "provider", "model", "value", "source", "probe_cached", "is_current"}
    assert row["harness"] == "claude" and row["model"] == "m1" and row["is_current"] is True


def test_cmd_models_scan_delegates_to_report(tmp_path, monkeypatch):
    """cmd_models_scan 调 onboarding.models_scan_report（非交互打印路径）。"""
    cfg = _cfg(tmp_path, monkeypatch)
    called = {}
    monkeypatch.setattr("vision_relay.onboarding.models_scan_report", lambda c: called.setdefault("c", c) or c)
    from vision_relay import cli

    assert cli.cmd_models_scan(cfg) == 0 and called["c"] is cfg


# ── 自选未覆盖路径（理由见 REFACTOR_NOTES CLI 段）──────────────────────


def test_config_get_masks_secrets_and_strips_auth_hints(tmp_path, monkeypatch):
    """config_get 打码：明文 key 不出被动输出，auth_hints 逐条剥离（工程宪法）。"""
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.vlm.api_key = "sk-secret"
    cfg.relays = [
        RelayConfig(name="r1", protocol="chat", base_url="https://x", api_key="sk-relay", auth_hints=["fp"])
    ]
    out = verbs.config_get(cfg)
    data = out["data"]
    assert data["vlm"]["api_key"] == "●●●●"
    assert data["relays"][0]["api_key"] == "●●●●"
    assert "auth_hints" not in data["relays"][0]
    # 不污染调用方 cfg（拷贝后打码）
    assert cfg.vlm.api_key == "sk-secret" and cfg.relays[0].api_key == "sk-relay"


def test_vlm_secret_is_the_only_plaintext_exemption(tmp_path, monkeypatch):
    """vlm-secret 显式回明文 key（config/status 仍打码的唯一豁免）。"""
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.vlm.api_key = "sk-plain"
    out = verbs.vlm_secret(cfg)
    assert out["data"]["vlm"]["api_key"] == "sk-plain"


def test_relay_set_suppressed_and_api_key(tmp_path, monkeypatch):
    """relay-set 停用压制 / 补 key 两条路径。"""
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.relays = [RelayConfig(name="r1", protocol="chat", base_url="https://x")]
    _stdin(monkeypatch, {"name": "r1", "suppressed": True})
    assert verbs.relay_set(cfg)["ok"] is True and "r1" in cfg.routing.suppressed_relays
    _stdin(monkeypatch, {"name": "r1", "api_key": "sk-new"})
    assert verbs.relay_set(cfg)["ok"] is True and cfg.relays[0].api_key == "sk-new"
    # 打码占位 / 空串拒绝
    _stdin(monkeypatch, {"name": "r1", "api_key": "●●●●"})
    assert verbs.relay_set(cfg)["ok"] is False
    _stdin(monkeypatch, {"name": "ghost", "suppressed": True})
    assert verbs.relay_set(cfg)["ok"] is False  # 未知 relay


# ── 自审补强 ①：facade 公共面契约（防 __all__/重导出漂移只在运行时暴露）──────

_VERBS_ORIGINAL_SURFACE = {
    "CONTRACT_VERSION", "envelope", "_stdin_json", "_locked_save",
    "_observe_for_status", "_reconcile", "_probe_tools", "_tail_events", "_vl_query",
    "_lookup_cap", "_lookup_probe", "_scan_triples",
    "status", "refresh", "diagnose", "models_scan", "config_get", "tools", "events", "visionlog",
    "models_set", "vlm_set", "vlm_secret", "vlm_test", "_VLMClient",
    "settings_set", "relay_set", "zcode_restart",
    "probe_target_for", "probe_target_info", "_run_probe", "probe_one", "probe_all_untested",
    "models_fetch", "httpx",
}

_CLI_ORIGINAL_SURFACE = {
    "PID_FILE", "LOG_FILE", "_JSON_MAP", "parse_args",
    "_pid_path", "_log_path", "_write_pid",
    "cmd_start", "cmd_stop", "cmd_start_intent", "_spawn_detached", "cmd_start_detach",
    "cmd_refresh", "cmd_diagnose", "cmd_tools", "cmd_probe", "_provider_for_group",
    "cmd_events", "cmd_visionlog", "_terminate", "_pid_running", "_pid_matches_ours",
    "cmd_status", "cmd_logs", "cmd_test_image", "cmd_models", "cmd_models_scan", "cmd_check",
    "_safe_stdio", "main", "reconcile_reconcile",
}


class TestFacadeContract:
    def test_verbs_original_surface_still_reachable(self):
        """重构后原 verbs 的全部符号仍可经 verbs.X 访问（防搬运丢失/改名）。"""
        missing = [s for s in sorted(_VERBS_ORIGINAL_SURFACE) if not hasattr(verbs, s)]
        assert missing == [], f"verbs facade 丢失符号: {missing}"

    def test_cli_original_surface_still_reachable(self):
        import vision_relay.cli as cli

        missing = [s for s in sorted(_CLI_ORIGINAL_SURFACE) if not hasattr(cli, s)]
        assert missing == [], f"cli facade 丢失符号: {missing}"

    def test_all_dunder_resolves(self):
        """两个 facade 的 __all__ 每项都须真实存在（防 star-import 静默缺项）。"""
        import vision_relay.cli as cli

        for mod in (verbs, cli):
            stale = [n for n in mod.__all__ if not hasattr(mod, n)]
            assert stale == [], f"{mod.__name__}.__all__ 含不可解析项: {stale}"


# ── 自审补强 ②：三处分发注册一致性（防新增子命令漏注册致 main 静默 return 1）──


def test_every_subcommand_is_dispatched():
    """每个 parse_args 子命令要么在 _JSON_MAP（--json 通道），要么有 cmd_* 处理器。

    main() 末尾 `return 1` 是兜底：若新增子命令但漏登记 _JSON_MAP / 漏写 cmd_*，
    会静默走到兜底。本测试把三处分发注册（_JSON_MAP / cmd_* / parse_args）锁成一致。
    """
    import vision_relay.cli as cli
    from vision_relay.cli_args import _JSON_MAP

    commands = [
        "start", "stop", "status", "logs", "test-image", "check", "models", "models-scan",
        "models-set", "refresh", "diagnose", "tools", "probe", "events", "visionlog", "config",
        "vlm-set", "vlm-test", "vlm-secret", "settings-set", "relay-set", "zcode-restart",
        "models-fetch",
    ]
    assert len(commands) == len(set(commands))  # 无重复
    for cmd in commands:
        argv = [cmd, "x"] if cmd == "test-image" else [cmd]  # test-image 需位置参数 path
        assert parse_args(argv).command == cmd  # 子命令可解析
        in_json = cmd in _JSON_MAP
        handler = "cmd_" + cmd.replace("-", "_")
        has_handler = hasattr(cli, handler)
        assert in_json or has_handler, (
            f"{cmd}: 不在 _JSON_MAP 也无 {handler} → main() 会静默 return 1（漏注册）"
        )


def test_dual_commands_have_both_paths():
    """双路命令（既可 --json 又有交互 cmd_*）两处都在，缺一即漂移。"""
    import vision_relay.cli as cli
    from vision_relay.cli_args import _JSON_MAP

    for cmd in ("status", "refresh", "diagnose", "models-scan", "tools", "events", "visionlog", "probe"):
        assert cmd in _JSON_MAP, f"{cmd} 应在 _JSON_MAP"
        assert hasattr(cli, "cmd_" + cmd.replace("-", "_")), f"{cmd} 应有 cmd_* 处理器"


# ── 自审补强 ③：models_fetch 的网络 patch 点契约（verbs.httpx 重导出非显然）────


def test_models_fetch_intercepted_by_verbs_httpx_patch(tmp_path, monkeypatch):
    """契约：patch verbs.httpx.get 即拦截 models_fetch 的网络请求（不发真实 HTTP）。

    verbs.py facade 的 `import httpx as httpx` 是为测试 patch 而存在的重导出；
    models_fetch 用本模块顶层 httpx，二者同一模块对象，故 patch facade 的 .get 生效。
    本测试把这条非显然的桥接锁成显式契约。
    """
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.relays = [RelayConfig(name="r1", protocol="chat", base_url="https://up.example/v1")]
    seen: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"data": [{"id": "m1"}, {"id": "m2"}]}

    monkeypatch.setattr(verbs.httpx, "get", lambda url, **k: seen.__setitem__("url", url) or _Resp())
    out = verbs.models_fetch(cfg)
    assert seen["url"] == "https://up.example/v1/models"  # 真实 URL 拼接
    assert out["ok"] is True and out["data"]["providers"] == {"r1": ["m1", "m2"]}
    # 回环 relay 不发请求：只进 skipped，不进 providers
    cfg2 = _cfg(tmp_path / "x", monkeypatch)
    cfg2.relays = [RelayConfig(name="r2", protocol="chat", base_url="http://127.0.0.1:9")]
    out2 = verbs.models_fetch(cfg2)
    assert out2["data"]["providers"] == {} and out2["data"]["skipped"] == {"r2": "loopback"}
