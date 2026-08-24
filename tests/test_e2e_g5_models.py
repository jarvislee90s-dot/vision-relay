"""E2E G5：模型能力页（M2 Plan 附录 A 场景 G5）。

手动步骤：模型能力页切换某模型三态 → 保存 → models-scan --json 复核 source=user；
「重测」更新实测列（probe_cached）；GUI 是薄壳，动词链路通了 GUI 即通。
CLI 等效序列：models-scan → models-set → models-scan → probe → models-scan。
"""

from __future__ import annotations

import json

import pytest
from integration_helpers import (
    envelope_of,
    run_cli,
    start_mock_upstream,
    stop_mock_upstream,
    write_harness_configs,
    write_proxy_json,
)


@pytest.fixture()
def env(tmp_path):
    base, _, servers = start_mock_upstream()
    home = tmp_path / "home"
    cfg_dir = tmp_path / "cfg"
    write_harness_configs(home, "https://origin.example/api")
    # qwen-code 指向 mock 上游:回环(localhost)让直连候选被跳过,重测落到 mock relay 拿真结论;
    # claude/codex 仍指 origin.example,保住域名推导供应商的断言。
    qwen = home / ".qwen" / "settings.json"
    qwen.write_text(json.dumps({"model": {"baseUrl": base, "model": "qwen3-coder"}}), encoding="utf-8")
    cfg_dir.mkdir()
    write_proxy_json(
        cfg_dir, relays=[{"name": "mock-direct", "protocol": "chat", "base_url": base, "models": ["mock-*"]}]
    )
    yield home, cfg_dir
    stop_mock_upstream(servers)


def _scan(cfg_dir, home):
    return envelope_of(run_cli(["models-scan", "--json"], cfg_dir, home))["data"]["models"]


def test_models_page_triset_save_and_retest(env):
    home, cfg_dir = env
    # 初始：扫描到 harness 配置里的模型，未标注（value=null）
    rows = _scan(cfg_dir, home)
    assert any(r["harness"] == "codex" and r["model"] == "gpt-5-codex" and r["value"] is None for r in rows)
    assert any(r["harness"] == "qwen-code" and r["model"] == "qwen3-coder" and r["value"] is None for r in rows)
    assert any(r["provider"] == "origin.example" for r in rows)  # 直连态:域名推导供应商名

    # 切换三态 → 保存（GUI「保存修改」= 一次 models-set 批量写）
    run_cli(
        ["models-set", "--json"],
        cfg_dir,
        home,
        stdin=json.dumps(
            [{"harness": "codex", "provider": "origin.example", "model": "gpt-5-codex", "value": "image"}]
        ),
    )
    rows = _scan(cfg_dir, home)
    row = next(r for r in rows if r["harness"] == "codex" and r["model"] == "gpt-5-codex")
    assert row["value"] == "image" and row["source"] == "user"  # 界面与 models-scan 一致、依据=user

    # 「重测」更新实测列：mock 上游答对颜色 → image 缓存
    proc = run_cli(
        ["probe", "--json", "--harness", "qwen-code", "--provider", "?", "--model", "qwen3-coder"], cfg_dir, home
    )
    assert envelope_of(proc)["data"]["result"] in ("image", "text_only")
    rows = _scan(cfg_dir, home)
    row = next(r for r in rows if r["harness"] == "qwen-code" and r["model"] == "qwen3-coder")
    assert row["probe_cached"] is not None  # 实测列出现缓存结论（mock 答“红色” → image）
