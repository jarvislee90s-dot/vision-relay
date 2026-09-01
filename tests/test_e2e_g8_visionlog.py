"""E2E G8（+G12 核心）：识图记录 / 未标注开关（M2 Plan 附录 A 场景 G8、G12）。

G8 手动步骤：发带图请求 → 识图记录页 harness→会话分组正确、三段明细齐全、
③段以 [图片描述] 开头。这里跑通完整数据面：真实起服务（start --detach）→
curl 等效 POST 带图 chat 请求 → 代理调用 mock VLM 转述 → 注入后转发 mock 上游 →
visionlog --json 复核三段明细；另用 anthropic 协议验证会话标识提取（spec §6 二级会话）。
G12：未标注模型开关——text_only 走 VLM 转述 / image 直通（重启服务生效）。
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from integration_helpers import (
    envelope_of,
    free_port,
    reset_upstream_requests,
    run_cli,
    skipif_github_macos,
    start_mock_upstream,
    stop_mock_upstream,
    upstream_requests,
    wait_port,
    write_proxy_json,
)

from vision_relay.probe import red_png

RED_B64 = base64.b64encode(red_png()).decode()


@pytest.fixture()
def env(tmp_path):
    base, _, servers = start_mock_upstream()
    home = tmp_path / "home"
    cfg_dir = tmp_path / "cfg"
    home.mkdir()
    cfg_dir.mkdir()
    port = free_port()
    write_proxy_json(
        cfg_dir,
        server={"bind_host": "127.0.0.1", "bind_port": port},
        relays=[
            {"name": "mock-direct", "protocol": "chat", "base_url": base, "models": ["*"]},
            {"name": "mock-anthropic", "protocol": "anthropic", "base_url": base, "models": ["*"]},
        ],
        vlm={"model": "vl-mock", "base_url": base, "api_key": "k"},
        # harnesses=[]：本场景只测数据面，不接管/不还原任何真实接线，
        # 也避免 reconcile 把假 home 的陌生地址误 absorb。
        routing={"auto_wire": True, "harnesses": []},
    )
    assert run_cli(["start", "--detach"], cfg_dir, home).returncode == 0
    assert wait_port(port, up=True, timeout=20)
    yield base, cfg_dir, home, port
    run_cli(["stop"], cfg_dir, home, timeout=60)
    stop_mock_upstream(servers)


def _post_chat(port: int, model="plain-text-model", question="这是什么"):
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + RED_B64}},
                ],
            }
        ],
    }
    return httpx.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=body, timeout=30.0, trust_env=False)


@skipif_github_macos
def test_g8_image_request_injected_logged_and_forwarded(env):
    """带图请求走完整链路：VLM 转述注入 → 上游收到 [图片描述] 文本 → 留痕三段齐全。"""
    base, cfg_dir, home, port = env
    reset_upstream_requests()

    resp = _post_chat(port)
    assert resp.status_code == 200
    assert "红" in resp.json()["choices"][0]["message"]["content"]  # mock 上游的回答透传

    # 上游确实收到了注入文本（不是原始图片 base64）
    sent = upstream_requests[-1]
    assert "[图片描述]" in json.dumps(sent, ensure_ascii=False)
    assert RED_B64 not in json.dumps(sent)

    # 识图记录：harness=qwen-code（chat 协议）、无会话标识（→「未识别会话」）、三段明细
    rows = envelope_of(run_cli(["visionlog", "--json"], cfg_dir, home))["data"]
    assert rows, "VLM 调用必须留痕"
    row = rows[0]
    assert row["harness"] == "qwen-code"
    assert row["session"] is None
    assert row["tier"] == 2  # 带问题 → Tier2 聚焦
    assert "图中是什么" in row["prompt"] or "Answer the question" in row["prompt"]
    assert "红" in row["raw"]
    assert row["injected"].startswith("[图片描述]")  # ③段以 [图片描述] 开头
    assert row["vlm_model"] == "vl-mock"
    assert row["image_hash"] and len(row["image_hash"]) == 64  # sha256 指纹


@skipif_github_macos
def test_g8_anthrop_session_id_extracted(env):
    """anthropic 请求的 metadata.user_id → 会话短名提取（spec §6 二级会话尽力识别）。"""
    _, cfg_dir, home, port = env
    body = {
        "model": "plain-text-model",
        "metadata": {"user_id": "user_abc__session-77"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": RED_B64}},
                    {"type": "text", "text": "这是什么"},
                ],
            }
        ],
        "max_tokens": 64,
    }
    resp = httpx.post(f"http://127.0.0.1:{port}/v1/messages", json=body, timeout=30.0, trust_env=False)
    assert resp.status_code in (200, 404)  # mock 上游没有 anthropic 端点；留痕在转发前完成
    rows = envelope_of(
        run_cli(
            ["visionlog", "--json"],
            cfg_dir,
            home,
        )
    )["data"]
    assert any(r["harness"] == "claude" and r["session"] == "session-77" for r in rows)


@skipif_github_macos
def test_g12_unannotated_switch_passthrough_vs_transcribe(env):
    """未标注模型默认开关：text_only=走 VLM 转述；切 image=直通（不经 VLM、不留痕）。"""
    base, cfg_dir, home, port = env

    # 默认（text_only）：上面 G8 用例已证走 VLM；这里先清留痕基线
    before = len(envelope_of(run_cli(["visionlog", "--json"], cfg_dir, home))["data"])

    # 切「直通」→ 保存 → 重启服务生效（运行中的服务持有旧配置）
    run_cli(["settings-set", "--json"], cfg_dir, home, stdin=json.dumps({"routing": {"unknown_default": "image"}}))
    run_cli(["stop"], cfg_dir, home)
    assert run_cli(["start", "--detach"], cfg_dir, home).returncode == 0
    assert wait_port(port, up=True, timeout=20)
    reset_upstream_requests()

    resp = _post_chat(port)
    assert resp.status_code == 200
    sent = json.dumps(upstream_requests[-1], ensure_ascii=False)
    assert "[图片描述]" not in sent  # 直通：不注入描述
    assert RED_B64 in sent  # 原图原样到达上游
    after = len(envelope_of(run_cli(["visionlog", "--json"], cfg_dir, home))["data"])
    assert after == before  # 不经 VLM → 无新留痕

    # 切回「走识图」→ 重启 → 同请求重新走 VLM 转述
    run_cli(["settings-set", "--json"], cfg_dir, home, stdin=json.dumps({"routing": {"unknown_default": "text_only"}}))
    run_cli(["stop"], cfg_dir, home)
    assert run_cli(["start", "--detach"], cfg_dir, home).returncode == 0
    assert wait_port(port, up=True, timeout=20)
    reset_upstream_requests()
    resp = _post_chat(port)
    assert "[图片描述]" in json.dumps(upstream_requests[-1], ensure_ascii=False)
    assert len(envelope_of(run_cli(["visionlog", "--json"], cfg_dir, home))["data"]) > before
