"""--json management verbs (spec §4/§8): envelope + contract_version on every verb."""

import io
import json

import pytest

from vision_relay import verbs
from vision_relay.config import ProxyConfig, RelayConfig


def _set_stdin(monkeypatch, payload) -> None:
    """写动词共用：把 payload 作为 JSON 塞进 sys.stdin。"""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


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


def test_config_get_strips_auth_hints(cfg):
    """评审③（zcode 2026-08-26）：密钥指纹不进 GUI 通道（spec §3）——config 输出整体剥离 auth_hints。"""
    from vision_relay.config import RelayConfig

    cfg.relays.append(
        RelayConfig(
            name="zcode-k", protocol="anthropic", base_url="https://x", provider_id="k", auth_hints=["963b…9NVz@49"]
        )
    )
    data = verbs.config_get(cfg)
    text = json.dumps(data)
    assert "auth_hints" not in text and "963b" not in text
    assert all("auth_hints" not in r for r in data["data"]["relays"])
    assert cfg.relays[0].auth_hints == ["963b…9NVz@49"]  # 输出层剥离，不改调用方


def test_models_scan_reads_matrix_without_port_probing(cfg, monkeypatch):
    """矩阵来自工具档案(磁盘),models-scan 不再探测端口——离线/加速两得。"""
    from vision_relay.model_sources import ProviderRow

    calls = []
    monkeypatch.setattr(verbs, "_probe_tools", lambda: calls.append(1) or [])
    monkeypatch.setattr(
        "vision_relay.model_sources.harness_matrix",
        lambda c: {
            "claude": [ProviderRow("cc-switch", "claude", "火山Ark", "https://x", True, ["m1", "m2"])],
            "codex": [ProviderRow("codex-plus", "codex", "Openrouter", "https://y", False, ["gpt-5"])],
        },
    )
    data = verbs.models_scan(cfg)
    rows = data["data"]["models"]
    assert [(r["provider"], r["model"]) for r in rows] == [
        ("火山Ark", "m1"),
        ("火山Ark", "m2"),
        ("Openrouter", "gpt-5"),
    ]
    assert calls == []  # 不探端口
    # is_current 透出（2026-08-25）：GUI 折叠非当前供应商行 + 前端批量探测筛候选
    assert [r["is_current"] for r in rows] == [True, True, False]


def test_scan_triples_shadow_bucket_fallback(cfg, monkeypatch):
    """存量标注挂在 legacy / "?" 影子桶下时,按精确→legacy→"?" 兜底显示(键统一的读侧)。"""
    from vision_relay.model_sources import ProviderRow

    monkeypatch.setattr(
        "vision_relay.model_sources.harness_matrix",
        lambda c: {"claude": [ProviderRow("cc-switch", "claude", "火山Ark", "https://x", True, ["m1", "m2"])]},
    )
    cfg.model_capabilities["claude"] = {
        "legacy": {"m1": "image"},
        "?": {"m2": "text_only"},
    }
    cfg.capability_sources["claude"] = {"legacy": {"m1": "user"}}
    rows = verbs._scan_triples(cfg)
    by_model = {r["model"]: r for r in rows}
    assert (by_model["m1"]["value"], by_model["m1"]["source"]) == ("image", "user")
    assert by_model["m2"]["value"] == "text_only"  # "?" 桶兜底


def test_scan_triples_probe_cached_shadow_fallback(cfg, monkeypatch):
    from vision_relay.model_sources import ProviderRow

    monkeypatch.setattr(
        "vision_relay.model_sources.harness_matrix",
        lambda c: {"claude": [ProviderRow("cc-switch", "claude", "p1", "https://x", True, ["m1"])]},
    )
    cfg.probe_results["?"] = {"m1": {"result": "image", "ts": 1}}
    assert verbs._scan_triples(cfg)[0]["probe_cached"] == "image"


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
        verbs.vlm_secret,
    ):
        assert fn(cfg)["contract_version"] == verbs.CONTRACT_VERSION


class TestModelsSet:
    def test_write_user_override_and_clear(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        _set_stdin(
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
        _set_stdin(monkeypatch, [{"harness": "c", "provider": "p", "model": "m", "value": "movie"}])
        out = verbs.models_set(cfg)
        assert out["ok"] is False and "movie" in json.dumps(out)
        assert cfg.model_capabilities == {}  # 校验失败不落盘

    def test_bad_json_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("sys.stdin", io.StringIO("not-json"))
        out = verbs.models_set(ProxyConfig())
        assert out["ok"] is False

    def test_models_set_purges_shadow_buckets(self, cfg, monkeypatch):
        """保存到规范供应商桶时,清掉 legacy/? 影子桶的同模型旧条目——防双写后兜底读到旧值。"""
        _set_stdin(monkeypatch, [{"harness": "claude", "provider": "火山Ark", "model": "m1", "value": "image"}])
        cfg.model_capabilities["claude"] = {"legacy": {"m1": "text_only"}, "?": {"m1": "text_only"}}
        cfg.capability_sources["claude"] = {"legacy": {"m1": "user"}}
        out = verbs.models_set(cfg)
        assert out["ok"] is True
        caps = cfg.model_capabilities["claude"]
        assert caps["火山Ark"]["m1"] == "image"
        assert "m1" not in caps.get("legacy", {}) and "m1" not in caps.get("?", {})
        assert "m1" not in cfg.capability_sources["claude"].get("legacy", {})


class TestVlmSet:
    def test_set_global_and_group_and_prompts(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        _set_stdin(
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
        _set_stdin(monkeypatch, {"vlm": {"model": "m2"}, "vlm_by_harness": {"codex": {"api_key": ""}}})
        verbs.vlm_set(cfg)
        assert cfg.vlm.api_key == "sk-old"  # 未提供/空串 = 不修改（GUI 看不到 key，无法回显）

    def test_masked_placeholder_rejected_as_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        _set_stdin(monkeypatch, {"vlm": {"api_key": "●●●●"}})
        out = verbs.vlm_set(cfg)
        assert out["ok"] is False

    def test_non_dict_sections_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        _set_stdin(monkeypatch, {"vlm": [1, 2]})
        assert verbs.vlm_set(cfg)["ok"] is False
        _set_stdin(monkeypatch, {"vlm_by_harness": "abc"})
        assert verbs.vlm_set(cfg)["ok"] is False


class TestVlmSecret:
    """vlm-secret：GUI「显示」按钮按需回显明文 key——工程宪法『输出不带 key』的唯一刻意豁免。"""

    def test_returns_real_keys_global_and_group(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.vlm.api_key = "sk-global"
        cfg.vlm_by_harness["claude"] = {"model": "m", "api_key": "sk-claude"}
        data = verbs.vlm_secret(cfg)["data"]
        assert data["vlm"]["api_key"] == "sk-global"
        assert data["vlm_by_harness"]["claude"]["api_key"] == "sk-claude"

    def test_readonly_never_mutates(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.vlm.api_key = "sk-global"
        cfg.vlm_by_harness["claude"] = {"model": "m", "api_key": "sk-claude"}
        verbs.vlm_secret(cfg)
        # 回归守卫（对照 config_get 打码不改调用方）：只读动词绝不能动调用方 cfg
        assert cfg.vlm.api_key == "sk-global"
        assert cfg.vlm_by_harness["claude"]["api_key"] == "sk-claude"

    def test_omits_harness_without_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.vlm.api_key = "sk-global"
        cfg.vlm_by_harness["claude"] = {"model": "m", "api_key": ""}
        cfg.vlm_by_harness["codex"] = {"model": "m2"}
        data = verbs.vlm_secret(cfg)["data"]
        assert data["vlm"]["api_key"] == "sk-global"
        assert "claude" not in data["vlm_by_harness"]  # 空 key = 跟随全局，不回显
        assert "codex" not in data["vlm_by_harness"]

    def test_config_get_still_masks(self, tmp_path, monkeypatch):
        """被动路径仍打码——本次豁免的显式回归护栏（只有 vlm-secret 才回明文）。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.vlm.api_key = "sk-global"
        cfg.vlm_by_harness["claude"] = {"model": "m", "api_key": "sk-claude"}
        text = json.dumps(verbs.config_get(cfg))
        assert "sk-global" not in text and "sk-claude" not in text
        data = verbs.config_get(cfg)["data"]
        assert data["vlm"]["api_key"] == "●●●●"
        assert data["vlm_by_harness"]["claude"]["api_key"] == "●●●●"

    def test_registered_in_json_map(self):
        from vision_relay import cli

        assert cli._JSON_MAP["vlm-secret"] is verbs.vlm_secret
        assert cli.parse_args(["vlm-secret", "--json"]).command == "vlm-secret"

    def test_never_leaks_relay_or_template_keys(self, tmp_path, monkeypatch):
        """防御纵深（评审补充）：vlm-secret 只回 vlm 作用域，relays / relay_templates 的 key 绝不出现。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.vlm.api_key = "sk-global"
        cfg.relays.append(RelayConfig(name="r1", protocol="chat", base_url="https://up.example", api_key="sk-relay"))
        cfg.routing.relay_templates["t1"] = {"base_url": "https://t.example", "api_key": "sk-tpl"}
        text = json.dumps(verbs.vlm_secret(cfg))
        assert "sk-relay" not in text and "sk-tpl" not in text
        assert cfg.relays[0].api_key == "sk-relay"  # 只读，不动调用方


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
    def test_set_unknown_default_and_vision_log(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        _set_stdin(
            monkeypatch,
            {"routing": {"unknown_default": "image"}, "vision_log": {"enabled": False, "retention_days": 3}},
        )
        out = verbs.settings_set(cfg)
        assert out["ok"] is True
        assert cfg.routing.unknown_default == "image"
        assert cfg.vision_log.enabled is False and cfg.vision_log.retention_days == 3

    def test_rejects_bad_values(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        _set_stdin(monkeypatch, {"routing": {"unknown_default": "movie"}})
        assert verbs.settings_set(ProxyConfig())["ok"] is False


class TestRelaySet:
    def test_suppress_and_unsuppress(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(
            RelayConfig(
                name="cc-claude", protocol="anthropic", base_url="http://127.0.0.1:15721", via="cc-switch", models=["*"]
            )
        )
        _set_stdin(monkeypatch, {"name": "cc-claude", "suppressed": True})
        assert verbs.relay_set(cfg)["ok"] is True
        assert cfg.routing.suppressed_relays == ["cc-claude"]
        _set_stdin(monkeypatch, {"name": "cc-claude", "suppressed": False})
        verbs.relay_set(cfg)
        assert cfg.routing.suppressed_relays == []

    def test_fill_key_on_direct_relay(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(RelayConfig(name="direct-claude", protocol="anthropic", base_url="https://x", models=["*"]))
        _set_stdin(monkeypatch, {"name": "direct-claude", "api_key": "sk-fill"})
        assert verbs.relay_set(cfg)["ok"] is True
        assert cfg.relays[0].api_key == "sk-fill"

    def test_unknown_relay_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        _set_stdin(monkeypatch, {"name": "ghost", "suppressed": True})
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
        monkeypatch.setattr(verbs, "_run_probe", lambda cfg, h, p, m, tb=None: "image")
        monkeypatch.setattr(
            verbs, "probe_target_info", lambda cfg, h, p, tb=None: ("https://up.example", "k", "chat", None)
        )
        out = verbs.probe_one(ProxyConfig(), harness="claude", provider="bigmodel", model="m1")
        assert out == {
            "contract_version": 1,
            "ok": True,
            "data": {"result": "image", "target_found": True, "reason": None},
        }

    def test_inconclusive_is_ok_with_null_result(self, tmp_path, monkeypatch):
        """含糊不下结论是合法结果(spec §5),不是错误:ok 恒 True。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(verbs, "_run_probe", lambda cfg, h, p, m, tb=None: None)
        monkeypatch.setattr(
            verbs, "probe_target_info", lambda cfg, h, p, tb=None: ("https://up.example", "k", "chat", None)
        )
        out = verbs.probe_one(ProxyConfig(), harness="claude", provider="p", model="m")
        assert out["ok"] is True and out["data"]["result"] is None and out["data"]["target_found"] is True

    def test_probe_one_no_target_reports_reason(self, tmp_path, monkeypatch):
        """无探测目标=ok 但 target_found=False + 原因(GUI 显示"不可达")。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(
            verbs,
            "probe_target_info",
            lambda cfg, h, p, tb=None: ("", "", "chat", "claude: 路由工具不在线,且未配置可探测的直连上游"),
        )
        out = verbs.probe_one(ProxyConfig(), harness="claude", provider="?", model="m")
        assert out["ok"] is True and out["data"]["result"] is None
        assert out["data"]["target_found"] is False
        assert out["data"]["reason"]  # 非空原因文案

    def test_probe_target_info_direct_upstream_candidate(self, tmp_path, monkeypatch):
        """工具离线时,harness 自身直连上游(live→snapshot)成为探测目标,key 按 key_ref 位置取。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text(
            json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://ark.example/api", "ANTHROPIC_AUTH_TOKEN": "tok-1"}}),
            encoding="utf-8",
        )
        import vision_relay.wiring as W

        monkeypatch.setattr(W, "HOME", str(home))
        base, key, proto, reason = verbs.probe_target_info(ProxyConfig(), "claude", "volces-ark", {})
        assert base == "https://ark.example/api" and key == "tok-1" and proto == "anthropic" and reason is None


class TestProbeAllUntested:
    def test_probes_only_uncached_current_provider(self, tmp_path, monkeypatch):
        """批量探测=当前激活供应商的无缓存模型;有缓存跳过;envelope 带 target_found/reason。"""
        from vision_relay.model_sources import ProviderRow

        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        monkeypatch.setattr(
            "vision_relay.model_sources.harness_matrix",
            lambda c: {
                "claude": [ProviderRow("cc-switch", "claude", "bigmodel", "https://x", True, ["m1", "m2", "m3", "m4"])],
                "codex": [ProviderRow("codex-plus", "codex", "Openrouter", "https://y", False, ["g5"])],
            },
        )
        monkeypatch.setattr(
            "vision_relay.model_sources.current_provider", lambda c, h: "bigmodel" if h == "claude" else "Openrouter"
        )
        monkeypatch.setattr(
            verbs, "probe_target_info", lambda cfg, h, p, tb=None: ("https://up.example", "", "chat", None)
        )
        called = []
        monkeypatch.setattr(verbs, "_run_probe", lambda cfg, h, p, m, tb=None: called.append((h, p, m)) or "image")
        cfg.probe_results.setdefault("bigmodel", {})["m1"] = {"result": "text_only", "ts": 1}
        cfg.probe_results.setdefault("bigmodel", {})["m2"] = {"result": "image", "ts": 1}
        out = verbs.probe_all_untested(cfg)
        assert out["ok"] is True
        assert out["data"]["probed"] == 2
        assert set(x[2] for x in called) == {"m3", "m4"}  # 只测无缓存的
        assert all(r["target_found"] is True and r["reason"] is None for r in out["data"]["results"])

    def test_all_untested_no_target_marks_reason(self, tmp_path, monkeypatch):
        from vision_relay.model_sources import ProviderRow

        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        monkeypatch.setattr(
            "vision_relay.model_sources.harness_matrix",
            lambda c: {"claude": [ProviderRow("cc-switch", "claude", "bigmodel", "https://x", True, ["m9"])]},
        )
        monkeypatch.setattr("vision_relay.model_sources.current_provider", lambda c, h: "bigmodel")
        monkeypatch.setattr(
            verbs,
            "probe_target_info",
            lambda cfg, h, p, tb=None: ("", "", "chat", "claude: 路由工具不在线,且未配置可探测的直连上游"),
        )
        out = verbs.probe_all_untested(cfg)
        r = out["data"]["results"][0]
        assert r["result"] is None and r["target_found"] is False and r["reason"]


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

    def test_loopback_and_suppressed_relays_reported_in_skipped(self, tmp_path, monkeypatch):
        """回环/被抑制 relay 不拉但要在 skipped 里说明原因（GUI 据此弹「清单在工具界面」文案）。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(RelayConfig(name="cc", protocol="chat", base_url="http://127.0.0.1:15721", models=["*"]))
        cfg.relays.append(RelayConfig(name="quiet", protocol="chat", base_url="https://up.example", models=["*"]))
        cfg.routing.suppressed_relays = ["quiet"]
        monkeypatch.setattr(verbs.httpx, "get", lambda url, **k: (_ for _ in ()).throw(AssertionError("不应发起请求")))
        out = verbs.models_fetch(cfg)
        assert out["ok"] is True
        assert out["data"]["providers"] == {} and out["data"]["errors"] == {}
        assert out["data"]["skipped"] == {"cc": "loopback", "quiet": "suppressed"}


class TestSettingsSetHardening:
    def test_non_dict_payload_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        _set_stdin(monkeypatch, [1, 2])
        assert verbs.settings_set(ProxyConfig())["ok"] is False

    def test_non_dict_section_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        _set_stdin(monkeypatch, {"routing": [{"unknown_default": "image"}]})
        assert verbs.settings_set(ProxyConfig())["ok"] is False

    def test_non_bool_values_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        _set_stdin(monkeypatch, {"vision_log": {"enabled": "false"}})
        assert verbs.settings_set(ProxyConfig())["ok"] is False

    def test_retention_days_zero_rejected(self, tmp_path, monkeypatch):
        """retention_days=0 能过动词校验但会被 VisionLogConfig 拒绝——落盘即成
        下次 load_config 必炸 ConfigError 的砖头文件，入口必须挡住。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        _set_stdin(monkeypatch, {"vision_log": {"retention_days": 0}})
        assert verbs.settings_set(ProxyConfig())["ok"] is False


class TestRelaySetBool:
    def test_non_bool_suppressed_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(
            RelayConfig(
                name="cc-claude", protocol="anthropic", base_url="http://127.0.0.1:15721", via="cc-switch", models=["*"]
            )
        )
        _set_stdin(monkeypatch, {"name": "cc-claude", "suppressed": "false"})
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


class TestWizardConfirm:
    def test_empty_models_set_marks_confirmed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("sys.stdin", io.StringIO("[]"))
        cfg = ProxyConfig()
        assert cfg.routing.capability_confirmed is False
        verbs.models_set(cfg)
        assert cfg.routing.capability_confirmed is True

    def test_nonempty_models_set_also_marks_confirmed(self, tmp_path, monkeypatch):
        """向导「完成（过目）」路径发非空行——任何一次成功的 models-set 都是显式确认（spec §6）。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        _set_stdin(monkeypatch, [{"harness": "codex", "provider": "?", "model": "m", "value": "image"}])
        cfg = ProxyConfig()
        verbs.models_set(cfg)
        assert cfg.routing.capability_confirmed is True


class TestEventsLimit:
    def test_limit_slices_and_zero_returns_all(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay import reconcile

        for i in range(3):
            reconcile.append_event("reclaim", "codex", {"i": i})
        assert len(verbs.events(ProxyConfig(), limit=2)["data"]) == 2  # 最近 2 条
        assert len(verbs.events(ProxyConfig(), limit=0)["data"]) == 3  # 0 = 全量（导出用）
        assert verbs.events(ProxyConfig(), limit=2)["data"][-1]["i"] == 2
