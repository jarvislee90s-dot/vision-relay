"""E2E G3：刷新与抢回（M2 Plan 附录 A 场景 G3）。

手动步骤：服务运行中手改 codex base_url 为 :57321（模拟路由工具抢线）→ 点「刷新」→
拓扑卡回到已接管、「自动处理」区出现绿条、事件日志有 reclaim。
CLI 等效序列：start --detach → 手改 config.toml → refresh --json → events --json。
"""

from __future__ import annotations

import pytest
from integration_helpers import (
    envelope_of,
    free_port,
    read_harness_base_url,
    run_cli,
    skipif_github_macos,
    wait_port,
    write_harness_configs,
    write_proxy_json,
)

ORIGIN = "https://origin.example/api"


@pytest.fixture()
def env(tmp_path):
    home = tmp_path / "home"
    cfg_dir = tmp_path / "cfg"
    write_harness_configs(home, ORIGIN)
    cfg_dir.mkdir()
    port = free_port()
    write_proxy_json(cfg_dir, server={"bind_host": "127.0.0.1", "bind_port": port})
    proc = run_cli(["start", "--detach"], cfg_dir, home)
    assert proc.returncode == 0
    assert wait_port(port, up=True, timeout=20)
    yield home, cfg_dir, port
    run_cli(["stop"], cfg_dir, home, timeout=30)


@skipif_github_macos
def test_refresh_reclaims_tool_stolen_wiring(env):
    home, cfg_dir, port = env
    proxy_url = f"http://127.0.0.1:{port}"

    # 模拟 Codex++ 抢线：手改 codex base_url 到工具端口
    toml = home / ".codex" / "config.toml"
    toml.write_text('model = "gpt-5-codex"\nbase_url = "http://127.0.0.1:57321/v1"\n', encoding="utf-8")
    assert read_harness_base_url(home, "codex") == "http://127.0.0.1:57321/v1"

    data = envelope_of(run_cli(["refresh", "--json"], cfg_dir, home))["data"]
    reclaims = [a for a in data["actions"] if a["type"] == "reclaim" and a["harness"] == "codex"]
    assert reclaims, f"refresh 必须抢回被工具改走的接线，actions={data['actions']}"

    # 拓扑卡等效断言：接线回到本代理
    assert read_harness_base_url(home, "codex") == proxy_url

    # 「自动处理」区 / 事件日志页等效断言：reclaim 留痕
    rows = envelope_of(run_cli(["events", "--json"], cfg_dir, home))["data"]
    assert any(r["type"] == "reclaim" and r["harness"] == "codex" and r.get("to") == proxy_url for r in rows)

    # 幂等：无漂移再刷新不产生新动作（spec §5 规则矩阵第一行）
    data = envelope_of(run_cli(["refresh", "--json"], cfg_dir, home))["data"]
    assert not [a for a in data["actions"] if a["type"] == "reclaim"]
