"""E2E G2：路由开关（M2 Plan 附录 A 场景 G2）。

手动步骤：总览滑动开关 → 开（服务启动、拓扑链 relay 节点激活）→ 关（服务停止、
relay 节点置灰、harness 还原）。
CLI 等效序列：start --detach（=GUI RoutingToggle 的 startService / Tauri
start_core_detached 同参数）→ status → stop → status。
"""

from __future__ import annotations

import json

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


def test_routing_on_then_off_full_lifecycle(env):
    home, cfg_dir, port = env
    proxy_url = f"http://127.0.0.1:{port}"

    # ---- 开：start --detach（GUI 开关的 on 分支） ----
    proc = run_cli(["start", "--detach"], cfg_dir, home)
    assert proc.returncode == 0
    assert "started (detached)" in proc.stdout
    assert wait_port(port, up=True, timeout=20), "分离启动后服务端口必须监听"

    d = envelope_of(run_cli(["status", "--json"], cfg_dir, home))["data"]
    assert d["service_alive"] is True
    assert d["routing_on"] is True  # start 记录开启意图（崩溃修复依据）
    for harness in ("claude", "codex", "qwen-code"):
        assert read_harness_base_url(home, harness) == proxy_url
        assert d["harnesses"][harness]["ownership"] == "ours"  # 拓扑卡「已接管」

    # ---- 关：stop（GUI 开关的 off 分支） ----
    proc = run_cli(["stop"], cfg_dir, home)
    assert proc.returncode == 0
    assert wait_port(port, up=False, timeout=20), "stop 后端口必须关闭"
    d = envelope_of(run_cli(["status", "--json"], cfg_dir, home))["data"]
    assert d["service_alive"] is False
    assert d["routing_on"] is False  # stop 记录关闭意图
    for harness in ("claude", "codex", "qwen-code"):
        assert read_harness_base_url(home, harness) == ORIGIN  # 还原为接管前原值
        assert d["harnesses"][harness]["ownership"] != "ours"  # 拓扑卡 relay 置灰
    assert not (cfg_dir / "proxy.pid").exists()


def test_stop_after_absorb_restores_latest_snapshot(tmp_path):
    """偏离①验收：运行中吸收新供应商 B → stop 还原到 B（最新快照），而非最早的 A。"""
    home = tmp_path / "home"
    cfg_dir = tmp_path / "cfg"
    write_harness_configs(home, ORIGIN)  # A = ORIGIN
    cfg_dir.mkdir()
    port = free_port()
    write_proxy_json(cfg_dir, server={"bind_host": "127.0.0.1", "bind_port": port})
    proc = run_cli(["start", "--detach"], cfg_dir, home)
    assert proc.returncode == 0 and wait_port(port, up=True, timeout=20)

    (home / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://B.example/api"}}), encoding="utf-8"
    )
    data = envelope_of(run_cli(["refresh", "--json"], cfg_dir, home))["data"]
    assert any(a["type"] == "absorb" and a["harness"] == "claude" for a in data["actions"])

    assert run_cli(["stop"], cfg_dir, home).returncode == 0
    assert wait_port(port, up=False, timeout=20)
    assert read_harness_base_url(home, "claude") == "https://B.example/api"  # 最新快照，不是 ORIGIN
    assert read_harness_base_url(home, "codex") == ORIGIN  # 无吸收的 harness 仍按 .bak 还原原值


class TestZcodeE2E:
    def test_takeover_route_restore_cycle(self, tmp_path, monkeypatch):
        """接管→指纹选路→还原 全链路（伪造 zcode config + 内存 relay 选择）。"""
        from vision_relay import wiring
        from vision_relay.config import ProxyConfig
        from vision_relay.fingerprint import key_fingerprint
        from vision_relay.server import _select_relay

        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        providers = {
            "a": {
                "kind": "anthropic",
                "options": {"apiKey": "k-aaaaaaaaaa1", "baseURL": "https://a.example"},
                "enabled": True,
                "models": {"GLM": {"modalities": {"input": ["text"]}}},
            },
            "b": {
                "kind": "openai",
                "options": {"apiKey": "k-bbbbbbbbbb2", "baseURL": "https://b.example/v1"},
                "enabled": False,
                "models": {"GLM": {"modalities": {"input": ["text"]}}},
            },
        }
        p = tmp_path / ".zcode" / "v2" / "config.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"provider": providers}), encoding="utf-8")
        cfg = ProxyConfig()
        cfg.routing.harnesses = ["zcode"]
        wiring.wiring_backup_and_rewrite(cfg)
        # 同名模型 GLM 双协议：按各自协议+指纹精确命中
        ra = _select_relay(cfg, "anthropic", "GLM", key_fingerprint("k-aaaaaaaaaa1"))
        rb = _select_relay(cfg, "chat", "GLM", key_fingerprint("k-bbbbbbbbbb2"))
        assert ra.provider_id == "a" and rb.provider_id == "b"
        # 停止路由：全部还原
        msgs = wiring.wiring_restore_on_stop(cfg)
        d = json.loads(p.read_text(encoding="utf-8"))
        assert d["provider"]["a"]["options"]["baseURL"] == "https://a.example"
        assert d["provider"]["b"]["options"]["baseURL"] == "https://b.example/v1"
        assert d["provider"]["a"]["models"]["GLM"]["modalities"]["input"] == ["text"]
        assert any("providers restored" in m for m in msgs)
