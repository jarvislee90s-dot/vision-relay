"""E2E G7：识图提示词编辑（M2 Plan 附录 A 场景 G7）。

手动步骤：设置折叠区编辑 Tier1 → 保存 → 发一张图过代理，识图记录①段 prompt = 自定义
文本；「恢复默认」后回到内置。
本文件验证动词层等效链路（vlm-set custom_tier1 → vlm-test 的 prompt_used 随之变化、
null 恢复默认）；「发图过代理看留痕」的完整版在 test_e2e_g8_visionlog.py 覆盖。
"""

from __future__ import annotations

import json

import pytest
from integration_helpers import (
    envelope_of,
    run_cli,
    start_mock_upstream,
    stop_mock_upstream,
    write_proxy_json,
)


@pytest.fixture()
def env(tmp_path):
    base, _, servers = start_mock_upstream()
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    write_proxy_json(cfg_dir, vlm={"model": "vl-mock", "base_url": base, "api_key": "k"})
    yield cfg_dir
    stop_mock_upstream(servers)


def test_tier1_custom_then_restore_default(env):
    cfg_dir = env

    # 编辑自定义 Tier1 → 保存
    run_cli(["vlm-set", "--json"], cfg_dir, stdin=json.dumps({"custom_tier1": "自定义T1：请仔细看图"}))
    proc = run_cli(["vlm-test", "--json"], cfg_dir, stdin=json.dumps({"mode": "tier1"}))
    data = envelope_of(proc)["data"]
    assert data["prompt_used"] == "自定义T1：请仔细看图"  # ①段 prompt = 自定义文本

    # 「恢复默认」= custom_tier1 置 null
    run_cli(["vlm-set", "--json"], cfg_dir, stdin=json.dumps({"custom_tier1": None}))
    proc = run_cli(["vlm-test", "--json"], cfg_dir, stdin=json.dumps({"mode": "tier1"}))
    data = envelope_of(proc)["data"]
    assert "Describe the image" in data["prompt_used"]  # 回到内置默认
    doc = envelope_of(run_cli(["config", "--json"], cfg_dir))["data"]
    assert doc["vlm"]["custom_tier1"] is None

    # Tier2 同理：自定义生效、null 恢复（tier2 默认提示词含问题文本）
    run_cli(["vlm-set", "--json"], cfg_dir, stdin=json.dumps({"custom_tier2": "聚焦T2提示词"}))
    proc = run_cli(["vlm-test", "--json"], cfg_dir, stdin=json.dumps({"mode": "tier2", "question": "图中是什么"}))
    assert envelope_of(proc)["data"]["prompt_used"] == "聚焦T2提示词"
    run_cli(["vlm-set", "--json"], cfg_dir, stdin=json.dumps({"custom_tier2": None}))
    proc = run_cli(["vlm-test", "--json"], cfg_dir, stdin=json.dumps({"mode": "tier2", "question": "图中是什么"}))
    assert "图中是什么" in envelope_of(proc)["data"]["prompt_used"]  # 默认模板带问题
