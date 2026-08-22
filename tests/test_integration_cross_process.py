"""M1↔M2 跨进程集成测试：用 Python subprocess 复刻 Tauri run_core / start_core_detached 的调用方式。

覆盖契约（gui/src-tauri/src/lib.rs ↔ vision_relay CLI）：
- stdout 永远是合法 JSON envelope，stderr 不污染 stdout；
- 非 ASCII 输出在 PYTHONIOENCODING=utf-8 下可严格 UTF-8 解码（Rust from_utf8_lossy 前提）；
- stdin JSON 管道传递（Windows 引号地狱的解法，M2 plan 全局约定）；
- `stop` 走人类可读输出而非 envelope（core.ts stopService 依赖的契约）；
- rc 语义：--json 动词失败 rc=0 且带 ok:false envelope；配置损坏 rc=2 且 envelope 有 error。
"""

from __future__ import annotations

import json

from integration_helpers import envelope_of, load_proxy_json, run_cli, write_harness_configs, write_proxy_json


def test_run_core_style_stdout_is_pure_envelope(tmp_path):
    """复刻 run_core：stdout 必须整体是一个 JSON 文档（前后无杂散输出），stderr 为空。"""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    write_proxy_json(cfg_dir)
    proc = run_cli(["status", "--json"], cfg_dir)
    assert proc.returncode == 0
    assert proc.stderr == ""
    doc = json.loads(proc.stdout)  # 整体唯一 JSON 文档（首尾无 print 污染）
    assert doc["contract_version"] == 1


def test_non_ascii_output_utf8_strict_decodable(tmp_path):
    """PYTHONIOENCODING=utf-8（Tauri 注入）下，中文/emoji 输出必须可严格 UTF-8 解码。"""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    write_proxy_json(cfg_dir, vlm={"model": "模型✓", "base_url": "https://例.example/v1"})
    proc = run_cli(["config", "--json"], cfg_dir)
    raw = proc.stdout
    raw.encode("utf-8").decode("utf-8")  # 严格往返（text=True 已 utf-8 解码成功即证明）
    assert "模型✓" in raw
    doc = envelope_of(proc)
    assert doc["data"]["vlm"]["model"] == "模型✓"


def test_error_envelope_on_stdout_not_stderr(tmp_path):
    """stdin 坏 JSON：错误以 ok:false envelope 走 stdout（GUI 统一解析），stderr 不出现 JSON。"""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    write_proxy_json(cfg_dir)
    proc = run_cli(["models-set", "--json"], cfg_dir, stdin="not-json")
    assert proc.returncode == 0  # --json 动词失败也回 rc 0（GUI 靠 envelope.ok 判定）
    doc = envelope_of(proc)
    assert doc["ok"] is False and "invalid stdin json" in doc["data"]["error"]
    assert proc.stderr == ""


def test_config_error_returns_envelope_rc2(tmp_path):
    """proxy.json 损坏：rc=2 + ok:false envelope（cli.main 的 as_json 错误通道）。"""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "proxy.json").write_text("{not json", encoding="utf-8")
    proc = run_cli(["status", "--json"], cfg_dir)
    assert proc.returncode == 2
    doc = envelope_of(proc)
    assert doc["ok"] is False and "proxy.json" in doc["data"]["error"]


def test_stdin_pipe_delivers_json_to_verb(tmp_path):
    """stdin 管道传递：Windows 命令行引号地狱的解法——payload 再复杂也只走管道。"""
    home = tmp_path / "home"
    cfg_dir = tmp_path / "cfg"
    write_harness_configs(home, "https://origin.example")
    cfg_dir.mkdir()
    write_proxy_json(cfg_dir)
    payload = [
        {"harness": "claude", "provider": "供应·商#1", "model": "模型/带斜杠", "value": "image"},
        {"harness": "claude", "provider": "供应·商#1", "model": "m2", "value": "text_only"},
    ]
    proc = run_cli(["models-set", "--json"], cfg_dir, home, stdin=json.dumps(payload, ensure_ascii=False))
    assert proc.returncode == 0 and proc.stderr == ""
    assert envelope_of(proc)["data"]["updated"] == 2
    doc = load_proxy_json(cfg_dir)
    assert doc["model_capabilities"]["claude"]["供应·商#1"]["模型/带斜杠"] == "image"


def test_stop_outputs_human_text_not_envelope(tmp_path):
    """core.ts stopService 契约：`vision-relay stop`（无 --json）输出人类可读文本、
    run_core 只在 stdout 为空时才报错——所以 stop 必须始终有 stdout。"""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    proc = run_cli(["stop"], cfg_dir)
    assert proc.stdout.strip() != ""  # 无 pid 文件 → "not running"，仍非空
    assert proc.returncode == 1
    try:  # 而且不是 envelope（GUI 不得把 stop 输出当 JSON 解析）
        parsed = json.loads(proc.stdout)
        assert "contract_version" not in parsed
    except ValueError:
        pass  # 非 JSON 输出，符合预期
