"""takeover combo snapshots (spec §5 备份、快照与回退语义)."""

import json
import time

from vision_relay import snapshot


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    snap = snapshot.Snapshot(
        base_url="https://open.bigmodel.cn/api/anthropic",
        key_ref="env.ANTHROPIC_AUTH_TOKEN",
        model="glm-5-air",
        second_hop="cc-switch",
    )
    snapshot.save("claude", snap)
    loaded = snapshot.load()["claude"]
    assert loaded.base_url == snap.base_url
    assert loaded.key_ref == "env.ANTHROPIC_AUTH_TOKEN"  # 只存位置，绝不存 key 值
    assert loaded.second_hop == "cc-switch"
    assert loaded.ts > 0


def test_latest_only_no_history(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    snapshot.save("codex", snapshot.Snapshot(base_url="http://a", key_ref="k", model="m"))
    time.sleep(0.01)
    snapshot.save("codex", snapshot.Snapshot(base_url="http://b", key_ref="k", model="m"))
    assert snapshot.load()["codex"].base_url == "http://b"  # 每 harness 只存最新一条
    raw = json.loads((tmp_path / "snapshots.json").read_text(encoding="utf-8"))
    assert len(raw["codex"]) == 1 or isinstance(raw["codex"], dict)


def test_missing_harness_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    assert "claude" not in snapshot.load()


def test_never_stores_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    snapshot.save("claude", snapshot.Snapshot(base_url="u", key_ref="env.ANTHROPIC_AUTH_TOKEN", model="m"))
    text = (tmp_path / "snapshots.json").read_text(encoding="utf-8")
    assert "sk-" not in text and "api_key" not in text


def test_key_ref_probe_claude(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "sk-secret"}}), encoding="utf-8"
    )
    monkeypatch.setattr(snapshot, "HOME", str(home))
    assert "ANTHROPIC_AUTH_TOKEN" in snapshot.key_ref_for("claude")
    assert "sk-secret" not in snapshot.key_ref_for("claude")
