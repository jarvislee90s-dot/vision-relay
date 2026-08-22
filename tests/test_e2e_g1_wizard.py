"""E2E G1：首次向导（M2 Plan 附录 A 场景 G1）。

手动步骤：删 ~/.vision-relay/proxy.json → 启动 GUI → 向导弹层出现 →
①配置 VLM（必填）→ ②过目模型能力（可跳过）→ 完成后 capability_confirmed=true、
向导不再出现（first_run=false）。
CLI 等效序列（GUI 是薄壳，spec §4：全部动作都走动词）：status → vlm-set →
models-set（过目=非空行 / 跳过=空数组）→ status 复核。
"""

from __future__ import annotations

import json

from integration_helpers import envelope_of, run_cli, write_harness_configs


def _fresh_env(tmp_path):
    home = tmp_path / "home"
    cfg_dir = tmp_path / "cfg"
    write_harness_configs(home, "https://origin.example/api")
    cfg_dir.mkdir()  # 无 proxy.json = 首次运行
    return home, cfg_dir


def _status(cfg_dir, home):
    return envelope_of(run_cli(["status", "--json"], cfg_dir, home))["data"]


def test_wizard_review_path_marks_confirmed(tmp_path):
    """走完两步（①填 VLM → ②过目非空三元组）→ confirmed=true、first_run=false。"""
    home, cfg_dir = _fresh_env(tmp_path)

    d = _status(cfg_dir, home)
    assert d["first_run"] is True
    assert d["setup_state"] == {"has_config": False, "capability_confirmed": False, "vlm_configured": False}

    # ① 配置 VLM（必填：模型 + base_url + key）
    run_cli(
        ["vlm-set", "--json"],
        cfg_dir,
        home,
        stdin=json.dumps({"vlm": {"model": "qwen-vl-max", "base_url": "https://dash.example/v1", "api_key": "sk-wiz"}}),
    )
    d = _status(cfg_dir, home)
    assert d["setup_state"]["vlm_configured"] is True
    assert d["first_run"] is True  # 未过目 → 向导仍在

    # ② 过目完成（非空行 = 用户看到并确认了标注）
    run_cli(
        ["models-set", "--json"],
        cfg_dir,
        home,
        stdin=json.dumps([{"harness": "codex", "provider": "?", "model": "gpt-5-codex", "value": "text_only"}]),
    )
    d = _status(cfg_dir, home)
    assert d["setup_state"]["capability_confirmed"] is True
    assert d["first_run"] is False  # 向导关闭，不再触发


def test_wizard_skip_path_marks_confirmed(tmp_path):
    """② 选择「跳过（按默认）」= models-set 空数组 → 同样置首次确认标志。"""
    home, cfg_dir = _fresh_env(tmp_path)
    run_cli(
        ["vlm-set", "--json"], cfg_dir, home, stdin=json.dumps({"vlm": {"model": "qwen-vl-max", "api_key": "sk-wiz"}})
    )
    run_cli(["models-set", "--json"], cfg_dir, home, stdin="[]")
    d = _status(cfg_dir, home)
    assert d["setup_state"]["capability_confirmed"] is True
    assert d["first_run"] is False
    # 已配置的 key 在 config 视图打码（密钥铁律）
    cfg = envelope_of(run_cli(["config", "--json"], cfg_dir, home))["data"]
    assert cfg["vlm"]["api_key"] == "●●●●"


def test_wizard_only_reappears_when_unconfirmed(tmp_path):
    """升级 / 新模型不重跑向导：已确认状态下新增模型标注，first_run 保持 false（走模型能力页增量）。"""
    home, cfg_dir = _fresh_env(tmp_path)
    run_cli(["vlm-set", "--json"], cfg_dir, home, stdin=json.dumps({"vlm": {"api_key": "sk-wiz"}}))
    run_cli(["models-set", "--json"], cfg_dir, home, stdin="[]")
    run_cli(
        ["models-set", "--json"],
        cfg_dir,
        home,
        stdin=json.dumps([{"harness": "codex", "provider": "?", "model": "new-model", "value": "image"}]),
    )
    assert _status(cfg_dir, home)["first_run"] is False
