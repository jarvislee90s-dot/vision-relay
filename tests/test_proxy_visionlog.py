"""vision call records (spec §6 识图记录 / §7.6): three segments + retention."""

import json
import os
import time

from vision_relay import visionlog


def _record(**kw):
    base = dict(
        ts=time.time(),
        harness="claude",
        session="sess-1",
        tier=1,
        question=None,
        prompt="T1 prompt",
        raw="desc text",
        injected="[图片描述] desc",
        duration_ms=120,
        cache_hit=False,
        image_hash="h1",
        vlm_model="qwen-vl-max",
    )
    base.update(kw)
    return base


def test_record_writes_daily_ndjson(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    visionlog.record(_record(), enabled=True, retention_days=7)
    files = os.listdir(str(tmp_path / "visionlog"))
    assert len(files) == 1 and files[0].endswith(".jsonl")
    row = json.loads((tmp_path / "visionlog" / files[0]).read_text(encoding="utf-8").splitlines()[0])
    assert row["prompt"] == "T1 prompt" and row["harness"] == "claude"


def test_disabled_records_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    visionlog.record(_record(), enabled=False, retention_days=7)
    assert not (tmp_path / "visionlog").exists()


def test_retention_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    old = tmp_path / "visionlog" / "2026-01-01.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps(_record()) + "\n", encoding="utf-8")
    removed = visionlog.cleanup(retention_days=7)
    assert removed == 1 and not old.exists()


def test_query_filters_by_harness(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    visionlog.record(_record(harness="claude"), enabled=True, retention_days=7)
    visionlog.record(_record(harness="codex", session="s2"), enabled=True, retention_days=7)
    rows = visionlog.query(harness="claude")
    assert len(rows) == 1 and rows[0]["harness"] == "claude"
    assert len(visionlog.query()) == 2


def test_record_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    visionlog.record({"bad": object()}, enabled=True, retention_days=7)  # 不可序列化也不许抛
    visionlog.record(None, enabled=True, retention_days=7)  # None 也不许抛（fail-open 铁律）
