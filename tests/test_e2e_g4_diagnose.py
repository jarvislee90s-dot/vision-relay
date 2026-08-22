"""E2E G4：诊断报告与自动修复（M2 Plan 附录 A 场景 G4）。

手动步骤（a）：路由开着时强杀服务进程 → 点「诊断报告」→ 自动重启（状态恢复运行中，
报告含 auto_fix/restart）。
手动步骤（b）：把路由意图关掉再杀 → diagnose 自动 restore 回快照原值。
CLI 等效序列：start --detach → taskkill/F → diagnose --json（=诊断弹层数据源）→
status 复核；意图改写 state.json 模拟「崩溃前路由开关是关的」。
"""

from __future__ import annotations

import json
import os

import pytest
from integration_helpers import (
    envelope_of,
    free_port,
    read_harness_base_url,
    run_cli,
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
    return home, cfg_dir, port


def _pid_of(cfg_dir) -> int:
    # 决策⑤：pid 文件升级为 JSON {pid, token}；老格式纯数字仍兼容。
    raw = (cfg_dir / "proxy.pid").read_text(encoding="utf-8").strip()
    if raw.startswith("{"):
        return int(json.loads(raw)["pid"])
    return int(raw)


def _hard_kill(cfg_dir) -> None:
    os.kill(_pid_of(cfg_dir), 9)  # 模拟任务管理器强杀：无清理、pid 文件残留


def test_crash_with_routing_on_auto_restarts(env):
    """崩溃前路由开 → diagnose 自动重启并保持接管（spec §5 修复：最忠实还原意图）。"""
    home, cfg_dir, port = env
    proxy_url = f"http://127.0.0.1:{port}"
    try:
        assert run_cli(["start", "--detach"], cfg_dir, home).returncode == 0
        assert wait_port(port, up=True, timeout=20)
        assert read_harness_base_url(home, "claude") == proxy_url

        _hard_kill(cfg_dir)
        assert wait_port(port, up=False, timeout=20), "强杀后端口必须关闭（僵尸接线场景成立）"
        assert (cfg_dir / "proxy.pid").exists()  # pid 残留

        data = envelope_of(run_cli(["diagnose", "--json"], cfg_dir, home, timeout=90))["data"]
        fixes = [a for a in data["actions"] if a["type"] == "auto_fix" and a.get("fix") == "restart"]
        assert fixes and all(a.get("ok") is True for a in fixes), f"auto_fix restart 必须成功：{data['actions']}"

        assert wait_port(port, up=True, timeout=20), "自动重启后服务恢复"
        d = envelope_of(run_cli(["status", "--json"], cfg_dir, home))["data"]
        assert d["service_alive"] is True and d["routing_on"] is True
        assert read_harness_base_url(home, "claude") == proxy_url  # 保持接管
        rows = envelope_of(run_cli(["events", "--json"], cfg_dir, home))["data"]
        assert any(r["type"] == "auto_fix" and r.get("fix") == "restart" for r in rows)
    finally:
        run_cli(["stop"], cfg_dir, home, timeout=60)


def test_crash_with_routing_off_restores_snapshot(env):
    """崩溃前路由关 → diagnose 按接管组合快照还原原值（spec §5 修复分支 b）。"""
    home, cfg_dir, port = env
    try:
        assert run_cli(["start", "--detach"], cfg_dir, home).returncode == 0
        assert wait_port(port, up=True, timeout=20)

        _hard_kill(cfg_dir)
        assert wait_port(port, up=False, timeout=20)
        # 模拟「崩溃前把路由开关关掉」：改写意图状态
        (cfg_dir / "state.json").write_text(json.dumps({"routing_on": False, "updated_ts": 1}), encoding="utf-8")

        data = envelope_of(run_cli(["diagnose", "--json"], cfg_dir, home, timeout=90))["data"]
        fixes = [a for a in data["actions"] if a["type"] == "auto_fix" and a.get("fix") == "restore"]
        assert fixes, f"必须按快照还原：{data['actions']}"

        assert read_harness_base_url(home, "claude") == ORIGIN  # 快照里的接管前原值
        d = envelope_of(run_cli(["status", "--json"], cfg_dir, home))["data"]
        assert d["service_alive"] is False and d["routing_on"] is False
        assert not wait_port(port, up=True, timeout=3), "路由关崩溃不重启服务"
        # 还原后整文件备份已删（防 stop 用过期 .bak 二次覆盖，wiring_restore_by_snapshot 语义）
        assert not (home / ".claude" / "settings.json.vision-relay.bak").exists()
    finally:
        run_cli(["stop"], cfg_dir, home, timeout=60)
