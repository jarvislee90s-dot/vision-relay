"""--json management verbs (spec §4/§8): envelope + contract_version on every verb."""

import json

import pytest

from vision_relay import verbs
from vision_relay.config import ProxyConfig


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
