"""E2E G6：VLM 设置与测试（M2 Plan 附录 A 场景 G6）。

手动步骤：设置页改全局模型 + 为 claude 组自定义 + 四模式测试 → 保存后 config --json
复核（key 打码、分组正确）；tier2+自选提示词的返回 desc 非空、prompt_used=自选文本。
CLI 等效序列：vlm-set（全局+分组）→ config → vlm-test（harness=claude 走分组 VLM）。
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
    write_proxy_json(cfg_dir)
    yield base, cfg_dir
    stop_mock_upstream(servers)


def test_vlm_global_group_set_and_four_mode_test(env):
    base, cfg_dir = env

    # 设置页统一保存：全局 VLM + claude 分组自定义（跟随全局的组不动）
    run_cli(
        ["vlm-set", "--json"],
        cfg_dir,
        stdin=json.dumps(
            {
                "vlm": {"model": "vl-global", "base_url": base, "api_key": "sk-global"},
                "vlm_by_harness": {"claude": {"model": "vl-claude", "base_url": base, "api_key": "sk-claude"}},
            }
        ),
    )
    doc = envelope_of(run_cli(["config", "--json"], cfg_dir))["data"]
    assert doc["vlm"]["model"] == "vl-global"
    assert doc["vlm"]["api_key"] == "●●●●"  # key 打码
    assert doc["vlm_by_harness"]["claude"]["model"] == "vl-claude"
    assert doc["vlm_by_harness"]["claude"]["api_key"] == "●●●●"

    # 分组状态可见（status.vlm.groups）
    d = envelope_of(run_cli(["status", "--json"], cfg_dir))["data"]
    assert d["vlm"]["groups"] == ["claude"] and d["vlm"]["configured"] is True

    # tier2 + 自选提示词（四模式之一），harness=claude → 走分组 VLM（vlm_for 合并）
    proc = run_cli(
        ["vlm-test", "--json"],
        cfg_dir,
        stdin=json.dumps(
            {"mode": "tier2", "question": "图里有几个字", "custom_prompt": "我的自选提示词", "harness": "claude"}
        ),
    )
    data = envelope_of(proc)["data"]
    assert data["desc"]  # desc 非空（mock 回“红色 red”）
    assert data["prompt_used"] == "我的自选提示词"  # prompt_used = 自选文本
    assert data["model"] == "vl-claude"  # 生效的是 claude 分组的模型

    # 全局兜底：不带 harness → 用全局 VLM
    proc = run_cli(["vlm-test", "--json"], cfg_dir, stdin=json.dumps({"mode": "tier1"}))
    assert envelope_of(proc)["data"]["model"] == "vl-global"
