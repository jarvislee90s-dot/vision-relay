"""wiring upgrades: ownership classify, snapshot on takeover, restore-by-snapshot, tool relays."""

import json
import os

from vision_relay import snapshot, wiring
from vision_relay.config import ProxyConfig, RelayConfig


def _write_harness(home, harness, base_url):
    h = wiring.HARNESS_CFG[harness]
    p = wiring._path(home, harness)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if h.kind == "toml":
        open(p, "w", encoding="utf-8").write(f'model = "gpt-5"\nbase_url = "{base_url}"\n')
    else:
        d = (
            {"env": {"ANTHROPIC_BASE_URL": base_url}}
            if harness == "claude"
            else {"model": {"baseUrl": base_url, "apiKey": "sk-x"}}
        )
        open(p, "w", encoding="utf-8").write(json.dumps(d))


class TestOwnership:
    def test_classify(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        assert wiring.classify_base_url("http://127.0.0.1:8787", 8787) == "ours"
        assert wiring.classify_base_url("http://127.0.0.1:8787/v1", 8787) == "ours"
        assert wiring.classify_base_url("http://127.0.0.1:15721", 8787) == "cc-switch"
        assert wiring.classify_base_url("http://127.0.0.1:57321/v1", 8787) == "codex-plus"
        assert wiring.classify_base_url("https://api.deepseek.com", 8787) == "other"
        assert wiring.classify_base_url(None, 8787) == "none"


class TestTakeoverWritesSnapshot:
    def test_backup_and_rewrite_snapshots_original(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_harness(tmp_path, "codex", "http://127.0.0.1:57321/v1")
        cfg = ProxyConfig()
        cfg.routing.relay_templates = {
            "codex-plus": {
                "protocol": "responses",
                "base_url": "http://127.0.0.1:57321/v1",
                "via": "codex-plus",
                "models": ["*"],
            }
        }
        wiring.relays_activate(cfg)
        wiring.wiring_backup_and_rewrite(cfg)
        snap = snapshot.load()["codex"]
        assert snap.base_url == "http://127.0.0.1:57321/v1"
        assert snap.second_hop == "codex-plus"


class TestRestoreBySnapshot:
    def test_restores_snapshot_combo(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_harness(tmp_path, "claude", "http://127.0.0.1:8787")
        snapshot.save(
            "claude",
            snapshot.Snapshot(
                base_url="https://real.example/api", key_ref="env.ANTHROPIC_AUTH_TOKEN", model="glm-5-air"
            ),
        )
        msgs = wiring.wiring_restore_by_snapshot(ProxyConfig())
        assert "claude: restored" in msgs[0]
        assert (
            wiring.read_base_url(wiring._path(str(tmp_path), "claude"), wiring.HARNESS_CFG["claude"])
            == "https://real.example/api"
        )


class TestToolRelays:
    def test_ensure_tool_relays_adds_missing_not_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(
            RelayConfig(
                name="cc-anthropic",
                **{"protocol": "anthropic", "base_url": "http://127.0.0.1:15721", "via": "cc-switch", "models": ["*"]},
            )
        )
        from vision_relay.tools import ToolState

        online = [ToolState("cc-switch", 15721, True), ToolState("codex-plus", 57321, True)]
        added = wiring.ensure_tool_relays(cfg, online)
        names = [r.name for r in cfg.relays]
        assert "cc-anthropic" in names and "cc-codex" in names and "codex-plus" in names
        assert set(added) == {"cc-codex", "codex-plus"}  # 已存在的不重复加

    def test_offline_tool_not_added(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        from vision_relay.tools import ToolState

        wiring.ensure_tool_relays(cfg, [ToolState("cc-switch", 15721, False)])
        assert cfg.relays == []
