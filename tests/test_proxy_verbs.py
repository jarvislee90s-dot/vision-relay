"""--json management verbs (spec §4/§8): envelope + contract_version on every verb."""

import json

import pytest

from vision_relay import verbs
from vision_relay.config import ProxyConfig, RelayConfig


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    return ProxyConfig()


def test_envelope_shape():
    e = verbs.envelope(True, {"x": 1})
    assert e == {"contract_version": 1, "ok": True, "data": {"x": 1}}


def test_status(cfg, monkeypatch):
    monkeypatch.setattr(
        verbs,
        "_observe_for_status",
        lambda c: {"service_alive": False, "harnesses": {}, "tools": [], "routing_on": False},
    )
    data = verbs.status(cfg)
    assert data["data"]["service_alive"] is False and data["contract_version"] == 1


def test_refresh(cfg, monkeypatch):
    monkeypatch.setattr(
        verbs, "_reconcile", lambda c, **kw: {"actions": [{"type": "reclaim"}], "needs_you": [], "observed": {}}
    )
    data = verbs.refresh(cfg)
    assert data["ok"] is True and data["data"]["actions"][0]["type"] == "reclaim"


def test_diagnose(cfg, monkeypatch):
    monkeypatch.setattr(
        verbs,
        "_reconcile",
        lambda c, **kw: {"actions": [], "needs_you": [{"type": "missing_key"}], "observed": {"service_alive": True}},
    )
    data = verbs.diagnose(cfg)
    assert data["data"]["needs_you"][0]["type"] == "missing_key"
    assert data["ok"] is False  # needs_you 非空 -> ok=False（GUI 据此亮黄条）


def test_models_scan(cfg, monkeypatch):
    monkeypatch.setattr(
        verbs,
        "_scan_triples",
        lambda c: [{"harness": "claude", "provider": "bigmodel", "model": "glm-5-air", "value": None, "source": None}],
    )
    data = verbs.models_scan(cfg)
    row = data["data"]["models"][0]
    assert row["value"] is None and row["source"] is None  # 未标注=空


def test_config_get_masks_secrets(cfg):
    cfg.vlm.api_key = "sk-secret"
    data = verbs.config_get(cfg)
    text = json.dumps(data)
    assert "sk-secret" not in text
    assert "api_key" in text and data["data"]["vlm"]["api_key"] == "●●●●"


def test_config_get_masks_relay_template_keys(cfg):
    """Major-1: 手编 relay_templates 的 api_key 会被 wiring 展开真实用于上游认证——必须打码。"""
    cfg.vlm.api_key = "sk-keep"
    cfg.routing.relay_templates["relay-x"] = {
        "protocol": "chat",
        "base_url": "https://up.example",
        "api_key": "sk-hidden",
        "models": ["*"],
    }
    data = verbs.config_get(cfg)
    text = json.dumps(data)
    assert "sk-hidden" not in text
    tpl = data["data"]["routing"]["relay_templates"]["relay-x"]
    assert tpl["api_key"] == "●●●●" and tpl["base_url"] == "https://up.example"
    # 回归守卫（裁决 2）：打码是输出层拷贝，绝不改调用方 cfg
    assert cfg.vlm.api_key == "sk-keep"
    assert cfg.routing.relay_templates["relay-x"]["api_key"] == "sk-hidden"


def test_models_scan_probes_tools_once_per_scan(cfg, monkeypatch):
    """Minor-1: 工具探测按 scan 提升为一次（原每组一次，3 组就放大 3x 端口超时）。"""
    from vision_relay.onboarding import ModelEntry, ModelGroup

    calls = []

    def _count():
        calls.append(1)
        return []

    monkeypatch.setattr(
        "vision_relay.onboarding.scan_model_groups",
        lambda c: [
            ModelGroup(group="claude", path="a", entries=[ModelEntry("m1")]),
            ModelGroup(group="codex", path="b", entries=[ModelEntry("m2")]),
            ModelGroup(group="qwen-code", path="c", entries=[ModelEntry("m3")]),
        ],
    )
    monkeypatch.setattr(verbs, "_probe_tools", _count)
    data = verbs.models_scan(cfg)
    assert len(data["data"]["models"]) == 3
    assert len(calls) == 1


def test_tools(cfg, monkeypatch):
    from vision_relay.tools import ToolState

    monkeypatch.setattr(verbs, "_probe_tools", lambda: [ToolState("cc-switch", 15721, True, "bigmodel", "https://x")])
    data = verbs.tools(cfg)
    assert data["data"][0]["active_provider"] == "bigmodel"


def test_events(cfg, monkeypatch):
    monkeypatch.setattr(verbs, "_tail_events", lambda n: [{"type": "reclaim", "harness": "codex"}])
    data = verbs.events(cfg)
    assert data["data"][0]["type"] == "reclaim"


def test_visionlog(cfg, monkeypatch):
    monkeypatch.setattr(verbs, "_vl_query", lambda **kw: [{"harness": "claude", "tier": 1}])
    data = verbs.visionlog(cfg, harness="claude")
    assert data["data"][0]["harness"] == "claude"


def test_every_verb_has_contract_version(cfg, monkeypatch):
    monkeypatch.setattr(
        verbs,
        "_observe_for_status",
        lambda c: {"service_alive": False, "harnesses": {}, "tools": [], "routing_on": False},
    )
    monkeypatch.setattr(verbs, "_reconcile", lambda c, **kw: {"actions": [], "needs_you": [], "observed": {}})
    monkeypatch.setattr(verbs, "_scan_triples", lambda c: [])
    monkeypatch.setattr(verbs, "_probe_tools", lambda: [])
    monkeypatch.setattr(verbs, "_tail_events", lambda n: [])
    monkeypatch.setattr(verbs, "_vl_query", lambda **kw: [])
    for fn in (
        verbs.status,
        verbs.refresh,
        verbs.diagnose,
        verbs.models_scan,
        verbs.config_get,
        verbs.tools,
        verbs.events,
        verbs.visionlog,
    ):
        assert fn(cfg)["contract_version"] == verbs.CONTRACT_VERSION


class TestModelsSet:
    def _stdin(self, monkeypatch, payload):
        import io as _io

        monkeypatch.setattr("sys.stdin", _io.StringIO(json.dumps(payload)))

    def test_write_user_override_and_clear(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        self._stdin(
            monkeypatch,
            [
                {"harness": "claude", "provider": "bigmodel", "model": "m1", "value": "image"},
                {"harness": "claude", "provider": "bigmodel", "model": "m2", "value": None},
            ],
        )
        cfg.model_capabilities.setdefault("claude", {}).setdefault("bigmodel", {})["m2"] = "text_only"
        cfg.capability_sources.setdefault("claude", {}).setdefault("bigmodel", {})["m2"] = "probe"
        out = verbs.models_set(cfg)
        assert out["ok"] is True
        assert cfg.model_capabilities["claude"]["bigmodel"]["m1"] == "image"
        assert cfg.capability_sources["claude"]["bigmodel"]["m1"] == "user"
        assert "m2" not in cfg.model_capabilities["claude"]["bigmodel"]
        assert "m2" not in cfg.capability_sources["claude"]["bigmodel"]

    def test_invalid_value_rejected_without_partial_write(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        self._stdin(monkeypatch, [{"harness": "c", "provider": "p", "model": "m", "value": "movie"}])
        out = verbs.models_set(cfg)
        assert out["ok"] is False and "movie" in json.dumps(out)
        assert cfg.model_capabilities == {}  # 校验失败不落盘

    def test_bad_json_rejected(self, tmp_path, monkeypatch):
        import io as _io

        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("sys.stdin", _io.StringIO("not-json"))
        out = verbs.models_set(ProxyConfig())
        assert out["ok"] is False


class TestVlmSet:
    def _stdin(self, monkeypatch, payload):
        import io as _io

        monkeypatch.setattr("sys.stdin", _io.StringIO(json.dumps(payload)))

    def test_set_global_and_group_and_prompts(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        self._stdin(
            monkeypatch,
            {
                "vlm": {"model": "qwen3.5-omni-plus", "base_url": "https://x/v1"},
                "vlm_by_harness": {"claude": {"model": "m-c", "api_key": "sk-new"}},
                "custom_tier1": "自定义 T1",
                "custom_tier2": None,
            },
        )
        out = verbs.vlm_set(cfg)
        assert out["ok"] is True
        assert cfg.vlm.model == "qwen3.5-omni-plus"
        assert cfg.vlm_by_harness["claude"]["model"] == "m-c"
        assert cfg.vlm.custom_tier1 == "自定义 T1"
        assert cfg.vlm.custom_tier2 is None  # null = 恢复默认

    def test_absent_fields_unchanged_and_api_key_blank_keeps_old(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.vlm.api_key = "sk-old"
        self._stdin(monkeypatch, {"vlm": {"model": "m2"}, "vlm_by_harness": {"codex": {"api_key": ""}}})
        verbs.vlm_set(cfg)
        assert cfg.vlm.api_key == "sk-old"  # 未提供/空串 = 不修改（GUI 看不到 key，无法回显）

    def test_masked_placeholder_rejected_as_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        self._stdin(monkeypatch, {"vlm": {"api_key": "●●●●"}})
        out = verbs.vlm_set(cfg)
        assert out["ok"] is False

    def test_non_dict_sections_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        self._stdin(monkeypatch, {"vlm": [1, 2]})
        assert verbs.vlm_set(cfg)["ok"] is False
        self._stdin(monkeypatch, {"vlm_by_harness": "abc"})
        assert verbs.vlm_set(cfg)["ok"] is False


class TestVlmTest:
    def test_four_modes_use_overrides(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        calls = []

        class FakeClient:
            def __init__(self, vlm_cfg):
                self.cfg = vlm_cfg

            def describe(self, image, question=None, tier=1, detail=None, prompt_override=None):
                calls.append({"tier": tier, "q": question, "ov": prompt_override, "model": self.cfg.model})
                if detail is not None:
                    detail["prompt"] = prompt_override or "default-prompt"
                    detail["raw"] = "RAW"
                return "红色"

        monkeypatch.setattr(verbs, "_VLMClient", FakeClient)
        payload = {"mode": "tier2", "question": "图里几个字", "custom_prompt": "我的自定义提示词", "harness": "claude"}
        out = verbs.vlm_test(ProxyConfig(), payload=payload)
        assert out["ok"] is True and out["data"]["desc"] == "红色"
        assert calls[0]["ov"] == "我的自定义提示词"
        assert calls[0]["q"] == "图里几个字" and calls[0]["tier"] == 2


class TestSettingsSet:
    def _stdin(self, monkeypatch, payload):
        import io as _io

        monkeypatch.setattr("sys.stdin", _io.StringIO(json.dumps(payload)))

    def test_set_unknown_default_and_vision_log(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        self._stdin(
            monkeypatch,
            {"routing": {"unknown_default": "image"}, "vision_log": {"enabled": False, "retention_days": 3}},
        )
        out = verbs.settings_set(cfg)
        assert out["ok"] is True
        assert cfg.routing.unknown_default == "image"
        assert cfg.vision_log.enabled is False and cfg.vision_log.retention_days == 3

    def test_rejects_bad_values(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        self._stdin(monkeypatch, {"routing": {"unknown_default": "movie"}})
        assert verbs.settings_set(ProxyConfig())["ok"] is False


class TestRelaySet:
    def _stdin(self, monkeypatch, payload):
        import io as _io

        monkeypatch.setattr("sys.stdin", _io.StringIO(json.dumps(payload)))

    def test_suppress_and_unsuppress(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(
            RelayConfig(
                name="cc-claude", protocol="anthropic", base_url="http://127.0.0.1:15721", via="cc-switch", models=["*"]
            )
        )
        self._stdin(monkeypatch, {"name": "cc-claude", "suppressed": True})
        assert verbs.relay_set(cfg)["ok"] is True
        assert cfg.routing.suppressed_relays == ["cc-claude"]
        self._stdin(monkeypatch, {"name": "cc-claude", "suppressed": False})
        verbs.relay_set(cfg)
        assert cfg.routing.suppressed_relays == []

    def test_fill_key_on_direct_relay(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(RelayConfig(name="direct-claude", protocol="anthropic", base_url="https://x", models=["*"]))
        self._stdin(monkeypatch, {"name": "direct-claude", "api_key": "sk-fill"})
        assert verbs.relay_set(cfg)["ok"] is True
        assert cfg.relays[0].api_key == "sk-fill"

    def test_unknown_relay_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        self._stdin(monkeypatch, {"name": "ghost", "suppressed": True})
        assert verbs.relay_set(ProxyConfig())["ok"] is False

    def test_suppressed_relay_not_re_added(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay import wiring
        from vision_relay.tools import ToolState

        cfg = ProxyConfig()
        cfg.routing.suppressed_relays = ["cc-claude"]
        added = wiring.ensure_tool_relays(cfg, [ToolState("cc-switch", 15721, True)])
        assert "cc-claude" not in added and added == ["cc-codex"]


class TestProbeJson:
    def test_probe_verb_envelope(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(verbs, "_run_probe", lambda cfg, h, p, m: "image")
        out = verbs.probe_one(ProxyConfig(), harness="claude", provider="bigmodel", model="m1")
        assert out == {"contract_version": 1, "ok": True, "data": {"result": "image"}}


class TestModelsFetch:
    def test_fetch_lists_model_ids(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(
            RelayConfig(name="qwen-direct", protocol="chat", base_url="https://dash.example/v1", models=["qwen-*"])
        )

        class _Resp:
            status_code = 200

            def json(self):
                return {"data": [{"id": "qwen3-coder"}, {"id": "qwen-vl-max"}]}

        monkeypatch.setattr(verbs.httpx, "get", lambda url, **k: _Resp())
        out = verbs.models_fetch(cfg)
        assert out["ok"] is True
        assert out["data"]["providers"]["qwen-direct"] == ["qwen3-coder", "qwen-vl-max"]

    def test_unreachable_provider_reported_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(RelayConfig(name="r", protocol="chat", base_url="https://down.example/v1", models=["*"]))
        import httpx as _hx

        def boom(url, **k):
            raise _hx.ConnectError("down")

        monkeypatch.setattr(verbs.httpx, "get", boom)
        out = verbs.models_fetch(cfg)
        assert out["ok"] is True and out["data"]["providers"]["r"] == []
        assert out["data"]["errors"]["r"]


class TestSettingsSetHardening:
    def _stdin(self, monkeypatch, payload):
        import io as _io

        monkeypatch.setattr("sys.stdin", _io.StringIO(json.dumps(payload)))

    def test_non_dict_payload_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        self._stdin(monkeypatch, [1, 2])
        assert verbs.settings_set(ProxyConfig())["ok"] is False

    def test_non_dict_section_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        self._stdin(monkeypatch, {"routing": [{"unknown_default": "image"}]})
        assert verbs.settings_set(ProxyConfig())["ok"] is False

    def test_non_bool_values_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        self._stdin(monkeypatch, {"vision_log": {"enabled": "false"}})
        assert verbs.settings_set(ProxyConfig())["ok"] is False


class TestRelaySetBool:
    def _stdin(self, monkeypatch, payload):
        import io as _io

        monkeypatch.setattr("sys.stdin", _io.StringIO(json.dumps(payload)))

    def test_non_bool_suppressed_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(
            RelayConfig(
                name="cc-claude", protocol="anthropic", base_url="http://127.0.0.1:15721", via="cc-switch", models=["*"]
            )
        )
        self._stdin(monkeypatch, {"name": "cc-claude", "suppressed": "false"})
        assert verbs.relay_set(cfg)["ok"] is False


class TestStatusRich:
    def test_payload_contains_relays_snapshots_setup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(
            RelayConfig(
                name="cc-claude", protocol="anthropic", base_url="http://127.0.0.1:15721", via="cc-switch", models=["*"]
            )
        )
        monkeypatch.setattr(
            verbs,
            "_observe_for_status",
            lambda c: {
                "service_alive": False,
                "routing_on": False,
                "harnesses": {"claude": {"base_url": None, "ownership": "none", "has_snapshot": False}},
                "tools": [],
            },
        )
        out = verbs.status(cfg)
        d = out["data"]
        assert d["relays"][0]["name"] == "cc-claude" and d["relays"][0]["via"] == "cc-switch"
        assert d["vlm"]["configured"] is False and "api_key" not in d["vlm"]
        assert d["setup_state"] == {
            "has_config": False,
            "capability_confirmed": False,
            "vlm_configured": False,
        }
        assert "first_run" in d and d["first_run"] is True

    def test_key_never_in_status(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.vlm.api_key = "sk-leak"
        monkeypatch.setattr(
            verbs,
            "_observe_for_status",
            lambda c: {"service_alive": False, "routing_on": False, "harnesses": {}, "tools": []},
        )
        assert "sk-leak" not in json.dumps(verbs.status(cfg))
