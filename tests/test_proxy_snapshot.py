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


def test_load_tolerates_bad_entries(tmp_path, monkeypatch):
    """残缺/多余字段的条目不得击穿 load：好条目保留，坏条目跳过，绝不抛。"""
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    good_with_extra = {
        "base_url": "https://a.example/api",
        "key_ref": "env.ANTHROPIC_AUTH_TOKEN",
        "model": "glm-5-air",
        "ts": 1.0,
        "unknown_field": "x",  # 多余字段：过滤后保留
    }
    bad_missing_model = {"base_url": "https://b.example", "key_ref": "k"}  # 缺必填 model：跳过
    (tmp_path / "snapshots.json").write_text(
        json.dumps({"claude": good_with_extra, "codex": bad_missing_model, "qwen-code": "not-a-dict"}),
        encoding="utf-8",
    )
    loaded = snapshot.load()  # 不抛
    assert loaded["claude"].base_url == "https://a.example/api"
    assert loaded["claude"].model == "glm-5-air"
    assert "codex" not in loaded
    assert "qwen-code" not in loaded
