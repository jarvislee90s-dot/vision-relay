"""Proxy capability: config load/save + env override + defaults."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vision_relay.cli import main as cli_main
from vision_relay.config import (
    PROTOCOLS,
    ConfigError,
    ProxyConfig,
    RelayConfig,
    default_config,
    load_config,
    save_config,
)


def test_default_config_defaults():
    cfg = default_config()
    assert cfg.bind_host == "127.0.0.1"
    assert cfg.bind_port == 8787
    assert cfg.vlm.model  # 非空默认
    assert cfg.vlm.format == "chat"


def test_load_config_reads_json_and_env(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(
        json.dumps(
            {
                "server": {"bind_port": 9000},
                "vlm": {"model": "qwen-vl-max"},
            }
        )
    )
    monkeypatch.setenv("VISION_RELAY_BIND_PORT", "9100")  # env > file
    cfg = load_config(str(cfg_path))
    assert cfg.bind_port == 9100
    assert cfg.vlm.model == "qwen-vl-max"


def test_empty_env_vlm_api_key_clears_config_key(tmp_path: Path, monkeypatch):
    """T7：环境变量显式设为空串必须清掉 proxy.json 里的 VLM key（而非被 falsy 吞掉）。"""
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(json.dumps({"vlm": {"model": "qwen-vl-max", "api_key": "super-secret"}}))
    monkeypatch.setenv("VISION_RELAY_VLM_API_KEY", "")
    cfg = load_config(str(cfg_path))
    assert cfg.vlm.model == "qwen-vl-max"  # 其他字段不受影响
    assert cfg.vlm.api_key == ""  # key 被显式清空


def test_unset_env_vlm_api_key_keeps_config_key(tmp_path: Path, monkeypatch):
    """仅当环境变量没设时才保留配置里的 key（空串 vs 未设置二者语义不同）。"""
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(json.dumps({"vlm": {"api_key": "super-secret"}}))
    monkeypatch.delenv("VISION_RELAY_VLM_API_KEY", raising=False)
    cfg = load_config(str(cfg_path))
    assert cfg.vlm.api_key == "super-secret"


def test_relay_via_field_optional_and_validated():
    # via 可选、纯描述性；合法值（cc-switch / codex-plus）通过，非法值报 ConfigError
    r = RelayConfig(name="x", protocol="responses", base_url="http://127.0.0.1:57321/v1", via="codex-plus")
    assert r.via == "codex-plus"
    r0 = RelayConfig(name="y", protocol="chat", base_url="http://x")
    assert r0.via is None  # 缺省=一层
    with pytest.raises(ConfigError):
        RelayConfig(name="bad", protocol="chat", base_url="http://x", via="nope")


def test_missing_config_file_uses_defaults(tmp_path: Path):
    cfg = load_config(str(tmp_path / "nope.json"))
    assert cfg.bind_port == 8787


def test_malformed_bind_port_raises_config_error(tmp_path: Path):
    """坏类型（bind_port 非 number）显式报错，不再静默回退默认。"""
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(json.dumps({"server": {"bind_port": "not-a-number"}}))
    with pytest.raises(ConfigError):
        load_config(str(cfg_path))


def test_malformed_relays_not_list_raises_config_error(tmp_path: Path):
    """relays 非 list 显式报错，不再静默回退默认。"""
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(json.dumps({"relays": "not-a-list"}))
    with pytest.raises(ConfigError):
        load_config(str(cfg_path))


def test_load_config_invalid_json_raises(tmp_path: Path):
    """JSON 语法损坏 -> ConfigError（不是静默默认）。"""
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot read proxy.json"):
        load_config(str(cfg_path))


def test_load_config_top_level_not_object_raises(tmp_path: Path):
    """顶层非对象 -> ConfigError。"""
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(ConfigError, match="expected a JSON object"):
        load_config(str(cfg_path))


def test_protocol_enum_matches_runtime_inbound_protocols():
    """锁死 config.PROTOCOLS 与 server 入站路径/解析器集合的同步不变量。
    单边新增协议（config 加了枚举但 ir/server 没加，或反之）会让请求级 relay
    回退因 __post_init__ 强校验抛 ConfigError 打成 502 fail-open。"""
    from vision_relay.server import _PARSERS, _PROTO_BY_PATH

    assert set(PROTOCOLS) == set(_PARSERS) == set(_PROTO_BY_PATH.values())


def test_relay_protocols_enum_has_three():
    assert set(PROTOCOLS) == {"anthropic", "responses", "chat"}


VALID_RELAY = {
    "name": "deepseek",
    "protocol": "chat",
    "base_url": "https://api.deepseek.com",
    "api_key": "sk-test",
    "models": ["deepseek-*"],
}


@pytest.mark.parametrize("protocol", ["anthropic", "responses", "chat"])
def test_relay_valid_protocol_loads(protocol):
    cfg = ProxyConfig.from_dict({"relays": [{**VALID_RELAY, "protocol": protocol}]})
    assert cfg.relays[0].protocol == protocol


def test_relay_invalid_protocol_raises_with_name():
    with pytest.raises(ConfigError, match="deepseek"):
        ProxyConfig.from_dict({"relays": [{**VALID_RELAY, "protocol": "foo"}]})


def test_relay_missing_protocol_raises_with_index_and_name():
    bad = {k: v for k, v in VALID_RELAY.items() if k != "protocol"}
    with pytest.raises(ConfigError, match=r"relays\[0\].*deepseek"):
        ProxyConfig.from_dict({"relays": [bad]})


def test_relay_unknown_field_raises():
    with pytest.raises(ConfigError, match="baseurl"):
        ProxyConfig.from_dict({"relays": [{**VALID_RELAY, "baseurl": "http://x"}]})


def test_relay_non_object_entry_raises():
    with pytest.raises(ConfigError, match=r"relays\[0\]"):
        ProxyConfig.from_dict({"relays": ["nope"]})


def test_cli_returns_2_on_config_error(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(json.dumps({"relays": [{**VALID_RELAY, "protocol": "bogus"}]}))
    rc = cli_main(["check"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "config error" in captured.err
    assert "protocol must be one of" in captured.err


def test_cli_lifecycle_commands_work_with_broken_config(tmp_path: Path, monkeypatch, capsys):
    """stop/status/logs 不依赖配置：损坏的 proxy.json 不能锁死生命周期命令。"""
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    (tmp_path / "proxy.json").write_text("{ broken", encoding="utf-8")
    rc = cli_main(["status"])
    captured = capsys.readouterr()
    assert rc == 1  # not running（无 pid 文件）
    assert "config error" not in captured.err


# ── 独立化兼容层：读旧写新 ────────────────────────────────────────────
def test_legacy_env_vars_still_override_with_warning(tmp_path: Path, monkeypatch, capsys):
    """旧 QWEN_MM_PROXY_* 环境变量回退读取（一次性 deprecation 提示）。"""
    from vision_relay import env_util

    monkeypatch.setattr(env_util, "_warned", set())
    monkeypatch.delenv("VISION_RELAY_BIND_PORT", raising=False)
    monkeypatch.setenv("QWEN_MM_PROXY_BIND_PORT", "9100")
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(json.dumps({"server": {"bind_port": 9000}}))
    cfg = load_config(str(cfg_path))
    assert cfg.bind_port == 9100
    assert "deprecated" in capsys.readouterr().err


def test_new_env_vars_win_over_legacy(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("VISION_RELAY_BIND_PORT", "9200")
    monkeypatch.setenv("QWEN_MM_PROXY_BIND_PORT", "9100")
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(json.dumps({"server": {"bind_port": 9000}}))
    assert load_config(str(cfg_path)).bind_port == 9200
    assert "deprecated" not in capsys.readouterr().err  # 新名命中时不打 deprecation 提示


def test_load_config_reads_legacy_dir_and_save_migrates(tmp_path: Path, monkeypatch, capsys):
    """读旧写新：新目录无 proxy.json 时回退读旧目录；save_config 落盘到新目录。"""
    from vision_relay import env_util

    new_dir, old_dir = tmp_path / "new", tmp_path / "old"
    new_dir.mkdir()
    old_dir.mkdir()
    (old_dir / "proxy.json").write_text(json.dumps({"server": {"bind_port": 9300}}), encoding="utf-8")
    monkeypatch.setattr(env_util, "config_dir", lambda: str(new_dir))
    monkeypatch.setattr(env_util, "legacy_config_dir", lambda: str(old_dir))
    cfg = load_config()
    assert cfg.bind_port == 9300
    assert "legacy" in capsys.readouterr().err.lower()
    save_config(cfg)
    # 读旧写新：新目录落盘且内容一致，旧目录未被写入/产生临时文件
    saved = json.loads((new_dir / "proxy.json").read_text(encoding="utf-8"))
    assert saved["server"]["bind_port"] == 9300
    assert not (old_dir / "proxy.json.tmp").exists()


# ---- Phase2 M1: tri-state capabilities / migration / vlm_by_harness / ui_port ----


class TestTriStateCapabilities:
    def test_legacy_vision_normalized_to_image(self):
        cfg = ProxyConfig.from_dict({"model_capabilities": {"claude": {"prov": {"m1": "vision", "m2": "text_only"}}}})
        assert cfg.model_capabilities["claude"]["prov"]["m1"] == "image"

    def test_legacy_flat_migrated_to_global_bucket(self):
        cfg = ProxyConfig.from_dict({"model_capabilities": {"deepseek/*": "text_only"}})
        assert cfg.model_capabilities["global"]["deepseek/*"] == "text_only"

    def test_legacy_grouped_kept(self):
        cfg = ProxyConfig.from_dict({"model_capabilities": {"claude": {"m1": "vision"}}})
        assert cfg.model_capabilities["claude"]["legacy"]["m1"] == "image"  # 旧两层→provider 记 'legacy'

    def test_absent_means_unannotated(self):
        cfg = ProxyConfig.from_dict({})
        assert "claude" not in cfg.model_capabilities  # 未标注 = 键缺失

    def test_invalid_value_rejected(self):
        import pytest

        with pytest.raises(ConfigError):
            ProxyConfig.from_dict({"model_capabilities": {"claude": {"p": {"m": "movie"}}}})

    def test_sources_roundtrip(self):
        cfg = ProxyConfig.from_dict({"capability_sources": {"claude": {"prov": {"m1": "probe"}}}})
        assert cfg.capability_sources["claude"]["prov"]["m1"] == "probe"
        assert cfg.to_dict()["capability_sources"] == {"claude": {"prov": {"m1": "probe"}}}

    def test_probe_results_roundtrip(self):
        cfg = ProxyConfig.from_dict({"probe_results": {"prov": {"m": {"result": "image", "ts": 1}}}})
        assert cfg.probe_results["prov"]["m"]["result"] == "image"

    def test_flat_migration_idempotent_across_save_load(self):
        """I1：load→save→load 两轮后形状稳定（global 组吸收为固定点，不再二次套 'legacy'）。"""
        cfg = ProxyConfig.from_dict({"model_capabilities": {"deepseek/*": "text_only"}})
        for _ in range(2):
            cfg = ProxyConfig.from_dict(cfg.to_dict())
        assert cfg.model_capabilities == {"global": {"deepseek/*": "text_only"}}


class TestUnknownDefault:
    def test_accepts_image_alias(self):
        assert ProxyConfig.from_dict({"routing": {"unknown_default": "image"}}).routing.unknown_default == "image"

    def test_accepts_legacy_vision(self):
        assert ProxyConfig.from_dict({"routing": {"unknown_default": "vision"}}).routing.unknown_default == "image"

    def test_rejects_other(self):
        import pytest

        with pytest.raises(ConfigError):
            ProxyConfig.from_dict({"routing": {"unknown_default": "movie"}})


class TestUiPortRemoved:
    def test_to_dict_has_no_ui_port(self):
        assert "ui_port" not in ProxyConfig.from_dict({}).to_dict()["server"]

    def test_from_dict_ignores_ui_port(self):
        cfg = ProxyConfig.from_dict({"server": {"ui_port": 8788}})
        assert not hasattr(cfg, "ui_port")


class TestVlmByHarness:
    def test_default_empty_and_roundtrip(self):
        cfg = ProxyConfig.from_dict({})
        assert cfg.vlm_by_harness == {}
        cfg.vlm_by_harness["claude"] = {"model": "qwen3.5-omni-plus", "api_key": "k"}
        assert cfg.to_dict()["vlm_by_harness"]["claude"]["model"] == "qwen3.5-omni-plus"

    def test_partial_fields_fill_from_defaults(self):
        cfg = ProxyConfig.from_dict({"vlm_by_harness": {"codex": {"model": "m"}}})
        merged = cfg.vlm_for("codex")
        assert merged.model == "m"
        assert merged.base_url == cfg.vlm.base_url  # 未填回落全局

    def test_false_override_takes_effect(self):
        """I2：None/空串回落全局，False 等显式覆盖必须生效（不被 falsy 过滤吞掉）。"""
        cfg = ProxyConfig.from_dict({"vlm": {"cache_disk": True}, "vlm_by_harness": {"codex": {"cache_disk": False}}})
        assert cfg.vlm_for("codex").cache_disk is False
        assert cfg.vlm_for("codex").auto_local_ollama is True  # 未覆盖字段回落全局默认

    def test_empty_string_override_falls_back(self):
        cfg = ProxyConfig.from_dict({"vlm_by_harness": {"codex": {"model": ""}}})
        assert cfg.vlm_for("codex").model == cfg.vlm.model  # 空串视为未覆盖

    def test_non_object_harness_value_rejected(self):
        import pytest

        with pytest.raises(ConfigError):
            ProxyConfig.from_dict({"vlm_by_harness": {"codex": "nope"}})


class TestVisionLogConfig:
    def test_defaults(self):
        cfg = ProxyConfig.from_dict({})
        assert cfg.vision_log.enabled is True
        assert cfg.vision_log.retention_days == 7

    def test_roundtrip(self):
        cfg = ProxyConfig.from_dict({"vision_log": {"enabled": False, "retention_days": 3}})
        assert cfg.to_dict()["vision_log"] == {"enabled": False, "retention_days": 3}

    def test_retention_days_zero_rejected(self):
        """retention_days=0 会让 cleanup 按整日 cutoff 误删当日文件；必须 >=1，关闭留存用 enabled=false。"""
        with pytest.raises(ConfigError, match="retention_days"):
            ProxyConfig.from_dict({"vision_log": {"retention_days": 0}})

    def test_retention_days_one_is_minimum_valid(self):
        assert ProxyConfig.from_dict({"vision_log": {"retention_days": 1}}).vision_log.retention_days == 1

    def test_retention_days_numeric_string_coerced(self):
        """M-3：数字字符串收拢为 int（"3" 等价 3，对齐 bind_port 惯例）。"""
        assert ProxyConfig.from_dict({"vision_log": {"retention_days": "3"}}).vision_log.retention_days == 3

    def test_retention_days_garbage_raises_config_error(self):
        """M-3：不可解析值显式 ConfigError（而非裸 TypeError/ValueError 逃逸）。"""
        with pytest.raises(ConfigError, match="retention_days"):
            ProxyConfig.from_dict({"vision_log": {"retention_days": "abc"}})
