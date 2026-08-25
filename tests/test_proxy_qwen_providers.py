"""qwen-code modelProviders 条目级接线(≥0.22.0 条目 baseUrl 优先于 model.baseUrl)。

覆盖:条目改写 + envKey 映射快照、自动一层直连 relay、还原守卫、统一鉴权链
(direct relay 透传客户端头)、对账漂移吸收。隔离 HOME + 配置目录,无网络。
"""

from __future__ import annotations

import json
import os
import threading

from vision_relay import snapshot, wiring
from vision_relay.config import ProxyConfig, RelayConfig
from vision_relay.server import _forward, _passthrough_headers_for, _select_relay

PROXY = "http://127.0.0.1:8787"
ENV_KEY = "QWEN_CUSTOM_API_KEY_OPENAI_HTTPS_OLLAMA_COM_V1_CBB286296DAB"
REAL_KEY = "f65e47495bc44a5abb4b1a40d260ba6e.yzFH8nvGmrhj4Xd9Ch9csAbK"


def _qwen_home(tmp_path, monkeypatch):
    home = str(tmp_path)
    monkeypatch.setattr(wiring, "HOME", home)
    monkeypatch.setattr(snapshot, "HOME", home)
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
    return home


def _write_qwen_settings(home, providers, model_base="https://ollama.com/v1", env=None, model_name="m-1"):
    d = os.path.join(home, ".qwen")
    os.makedirs(d, exist_ok=True)
    settings = {
        "env": dict(env if env is not None else {ENV_KEY: REAL_KEY}),
        "modelProviders": providers,
        "security": {"auth": {"selectedType": "openai"}},
        "model": {"name": model_name, "baseUrl": model_base},
    }
    with open(os.path.join(d, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    return os.path.join(d, "settings.json")


def _entry(eid, base, env_key=ENV_KEY):
    return {"id": eid, "name": eid, "baseUrl": base, "envKey": env_key}


def _read(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _providers_of(p):
    return _read(p)["modelProviders"]["openai"]


# ── snapshot: provider_urls 字段 ───────────────────────────────────────


def test_snapshot_provider_urls_roundtrip(tmp_path, monkeypatch):
    _qwen_home(tmp_path, monkeypatch)
    snap = snapshot.Snapshot(
        base_url="https://ollama.com/v1",
        key_ref="model.apiKey",
        model="m-1",
        provider_urls={ENV_KEY: "https://ollama.com/v1"},
    )
    snapshot.save("qwen-code", snap)
    loaded = snapshot.load()["qwen-code"]
    assert loaded.provider_urls == {ENV_KEY: "https://ollama.com/v1"}


def test_snapshot_old_format_without_provider_urls_loads(tmp_path, monkeypatch):
    """旧快照文件没有 provider_urls 字段:加载不炸、字段为 None(向后兼容)。"""
    _qwen_home(tmp_path, monkeypatch)
    cfg_dir = os.environ["VISION_RELAY_CONFIG_DIR"]
    os.makedirs(cfg_dir, exist_ok=True)
    with open(os.path.join(cfg_dir, "snapshots.json"), "w", encoding="utf-8") as f:
        json.dump({"qwen-code": {"base_url": "https://x", "key_ref": "k", "model": "m", "ts": 1}}, f)
    assert snapshot.load()["qwen-code"].provider_urls is None


# ── 接管:条目改写 + 映射快照 ──────────────────────────────────────────


def test_takeover_rewrites_entries_and_model_base_and_snapshots_mapping(tmp_path, monkeypatch):
    home = _qwen_home(tmp_path, monkeypatch)
    p = _write_qwen_settings(
        home,
        {"openai": [_entry("m-1", "https://ollama.com/v1"), _entry("m-2", "https://ollama.com/v1")]},
    )
    cfg = ProxyConfig()
    cfg.routing.harnesses = ["qwen-code"]
    msgs = wiring.wiring_backup_and_rewrite(cfg)

    d = _read(p)
    assert d["model"]["baseUrl"] == PROXY
    assert all(e["baseUrl"] == PROXY for e in d["modelProviders"]["openai"])
    # env 段(真实 key 所在)绝不被触碰
    assert d["env"][ENV_KEY] == REAL_KEY
    # 快照:envKey → 原始 baseUrl(两个模型共用 envKey,映射收敛为一条)
    snap = snapshot.load()["qwen-code"]
    assert snap.provider_urls == {ENV_KEY: "https://ollama.com/v1"}
    assert any("2 entries" in m for m in msgs)


def test_snap_key_fallback_without_envkey_and_collision(tmp_path, monkeypatch):
    home = _qwen_home(tmp_path, monkeypatch)
    providers = {
        "openai": [
            {"id": "noenv", "baseUrl": "https://a.com/v1"},  # 无 envKey → authType|id|index
            _entry("same", "https://ollama.com/v1", env_key="K1"),
            _entry("diff", "https://b.com/v1", env_key="K1"),  # 同 envKey 不同 URL → #id 消歧
        ]
    }
    p = _write_qwen_settings(home, providers)
    cfg = ProxyConfig()
    cfg.routing.harnesses = ["qwen-code"]
    wiring.wiring_backup_and_rewrite(cfg)
    snap = snapshot.load()["qwen-code"]
    assert snap.provider_urls == {
        "openai|noenv|0": "https://a.com/v1",
        "K1": "https://ollama.com/v1",
        "K1#diff": "https://b.com/v1",
    }
    assert all(e["baseUrl"] == PROXY for e in _providers_of(p))


def test_skips_unsupported_shapes_and_counts(tmp_path, monkeypatch):
    """gemini 协议 / wrapped 旧形态不碰,计入 skipped;openai 条目正常改写。"""
    home = _qwen_home(tmp_path, monkeypatch)
    providers = {
        "openai": [_entry("m-1", "https://ollama.com/v1")],
        "gemini": [{"id": "g-1", "baseUrl": "https://gemini.example", "envKey": "GK"}],
        "custom": {"protocol": "openai", "models": [_entry("w-1", "https://w.example")]},  # wrapped 旧形态
    }
    p = _write_qwen_settings(home, providers)
    cfg = ProxyConfig()
    cfg.routing.harnesses = ["qwen-code"]
    msgs = wiring.wiring_backup_and_rewrite(cfg)
    d = _read(p)
    assert d["modelProviders"]["openai"][0]["baseUrl"] == PROXY
    assert d["modelProviders"]["gemini"][0]["baseUrl"] == "https://gemini.example"
    assert d["modelProviders"]["custom"]["protocol"] == "openai"  # 原样未动
    assert any("skipped" in m for m in msgs)


def test_no_key_values_in_our_artifacts(tmp_path, monkeypatch):
    """密钥铁律:settings.json 的 env 值绝不出现在 snapshots.json / proxy.json。"""
    home = _qwen_home(tmp_path, monkeypatch)
    _write_qwen_settings(home, {"openai": [_entry("m-1", "https://ollama.com/v1")]})
    cfg = ProxyConfig()
    cfg.routing.harnesses = ["qwen-code"]
    wiring.wiring_backup_and_rewrite(cfg)
    cfg_dir = os.environ["VISION_RELAY_CONFIG_DIR"]
    for fname in ("snapshots.json", "proxy.json"):
        path = os.path.join(cfg_dir, fname)
        if os.path.exists(path):
            assert REAL_KEY not in open(path, encoding="utf-8").read(), f"{fname} 泄漏 key"


# ── 自动一层直连 relay ────────────────────────────────────────────────


def test_auto_relay_groups_models_and_prepends(tmp_path, monkeypatch):
    home = _qwen_home(tmp_path, monkeypatch)
    providers = {
        "openai": [
            _entry("m-1", "https://ollama.com/v1"),
            _entry("m-2", "https://ollama.com/v1", env_key=ENV_KEY),  # 同组
            _entry("m-3", "https://api.x.com/v1", env_key="XK"),
            _entry("m-4", "http://127.0.0.1:15721", env_key="TK"),  # 工具端口 → 两层语义,不建
        ]
    }
    _write_qwen_settings(home, providers)
    cfg = ProxyConfig()
    cfg.routing.harnesses = ["qwen-code"]
    cfg.relays.append(RelayConfig(name="cc-codex", protocol="chat", base_url="http://127.0.0.1:15721"))
    wiring.wiring_backup_and_rewrite(cfg)

    qwen_relays = [r for r in cfg.relays if r.name.startswith("qwen-")]
    assert len(qwen_relays) == 2
    by_url = {r.base_url: r for r in qwen_relays}
    assert sorted(by_url["https://ollama.com/v1"].models) == ["m-1", "m-2"]
    assert by_url["https://api.x.com/v1"].models == ["m-3"]
    assert all(r.api_key == "" for r in qwen_relays)
    assert all(r.via is None for r in qwen_relays)
    # prepend:qwen relay 必须排在 cc-codex("*" 通配)之前
    assert [r.name for r in cfg.relays[:2]] == sorted(r.name for r in qwen_relays)
    assert set(r.name for r in qwen_relays) <= set(cfg.routing.activated_relays)

    # stop 移除
    wiring.relays_restore(cfg)
    assert not [r for r in cfg.relays if r.name.startswith("qwen-")]


def test_select_relay_exact_qwen_model_beats_wildcard():
    """qwen 条目 id 必须命中 qwen relay 而非 cc-codex 的 "*";其他模型仍落 cc-codex。"""
    cfg = ProxyConfig()
    cfg.relays = [
        RelayConfig(name="qwen-ollama.com", protocol="chat", base_url="https://ollama.com/v1", models=["m-1", "m-2"]),
        RelayConfig(name="cc-codex", protocol="chat", base_url="http://127.0.0.1:15721", models=["*"]),
    ]
    assert _select_relay(cfg, "chat", "m-1").name == "qwen-ollama.com"
    assert _select_relay(cfg, "chat", "other-model").name == "cc-codex"


# ── 还原:映射写回 + 守卫 ─────────────────────────────────────────────


def test_restore_on_stop_restores_entries_with_guard(tmp_path, monkeypatch):
    home = _qwen_home(tmp_path, monkeypatch)
    p = _write_qwen_settings(
        home, {"openai": [_entry("m-1", "https://ollama.com/v1"), _entry("m-2", "https://api.x.com/v1", env_key="XK")]}
    )
    cfg = ProxyConfig()
    cfg.routing.harnesses = ["qwen-code"]
    wiring.wiring_backup_and_rewrite(cfg)
    # 用户运行期把 m-2 改走别处 → 还原时不动它
    d = _read(p)
    d["modelProviders"]["openai"][1]["baseUrl"] = "https://elsewhere.example/v9"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    wiring.wiring_restore_on_stop(cfg)
    entries = _providers_of(p)
    assert entries[0]["baseUrl"] == "https://ollama.com/v1"
    assert entries[1]["baseUrl"] == "https://elsewhere.example/v9"
    assert _read(p)["model"]["baseUrl"] == "https://ollama.com/v1"


def test_restore_by_snapshot_restores_entries(tmp_path, monkeypatch):
    home = _qwen_home(tmp_path, monkeypatch)
    p = _write_qwen_settings(home, {"openai": [_entry("m-1", "https://ollama.com/v1")]})
    cfg = ProxyConfig()
    cfg.routing.harnesses = ["qwen-code"]
    wiring.wiring_backup_and_rewrite(cfg)
    wiring.wiring_restore_by_snapshot(cfg)
    assert _providers_of(p)[0]["baseUrl"] == "https://ollama.com/v1"


def test_retakeover_preserves_originals_for_already_ours_entries(tmp_path, monkeypatch):
    """重复 start(条目已指本代理)不得把 8787 记成"原始值",也不丢首次记录。"""
    home = _qwen_home(tmp_path, monkeypatch)
    _write_qwen_settings(home, {"openai": [_entry("m-1", "https://ollama.com/v1")]})
    cfg = ProxyConfig()
    cfg.routing.harnesses = ["qwen-code"]
    wiring.wiring_backup_and_rewrite(cfg)
    wiring.wiring_backup_and_rewrite(cfg)  # 二次接管
    snap = snapshot.load()["qwen-code"]
    assert snap.provider_urls == {ENV_KEY: "https://ollama.com/v1"}


# ── wiring_report ─────────────────────────────────────────────────────


def test_wiring_report_providers_stats(tmp_path, monkeypatch):
    home = _qwen_home(tmp_path, monkeypatch)
    _write_qwen_settings(
        home,
        {
            "openai": [_entry("m-1", "https://ollama.com/v1")],
            "gemini": [{"id": "g-1", "baseUrl": "https://gemini.example"}],
        },
    )
    cfg = ProxyConfig()
    row = next(r for r in wiring.wiring_report(cfg) if r["harness"] == "qwen-code")
    assert row["providers"] == {"total": 2, "eligible": 1, "wired": 0, "gated": 0, "skipped": 1}
    assert row["wired"] is False

    cfg.routing.harnesses = ["qwen-code"]
    wiring.wiring_backup_and_rewrite(cfg)
    row = next(r for r in wiring.wiring_report(cfg) if r["harness"] == "qwen-code")
    assert row["providers"]["wired"] == 1
    assert row["wired"] is True


# ── 统一鉴权优先级链(direct relay 透传客户端头) ─────────────────────


class _StubResponse:
    status_code = 200
    text = "{}"
    headers: dict = {}

    def json(self):
        return {}


class _StubClient:
    """捕获转发请求头,不发网络。"""

    last_headers = None
    last_url = None

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, headers=None):
        _StubClient.last_url = url
        _StubClient.last_headers = headers or {}
        return _StubResponse()


def _forward_with(monkeypatch, relay, passthrough=None):
    import vision_relay.server as server_mod

    monkeypatch.setattr(server_mod.httpx, "Client", _StubClient)
    _StubClient.last_headers = None
    _forward(relay, {"model": "m"}, False, passthrough_headers=passthrough)
    return dict(_StubClient.last_headers)


def test_forward_relay_key_wins_over_passthrough(tmp_path, monkeypatch):
    headers = _forward_with(
        monkeypatch,
        RelayConfig(name="d", protocol="chat", base_url="https://u.example/v1", api_key="relay-key"),
        passthrough={"Authorization": "Bearer client-key"},
    )
    assert headers.get("Authorization") == "Bearer relay-key"


def test_forward_direct_relay_passthrough_client_header(tmp_path, monkeypatch):
    headers = _forward_with(
        monkeypatch,
        RelayConfig(name="d", protocol="chat", base_url="https://u.example/v1"),
        passthrough={"Authorization": "Bearer client-key", "x-api-key": "sk", "anthropic-version": "v"},
    )
    assert headers.get("Authorization") == "Bearer client-key"
    assert headers.get("x-api-key") == "sk"
    assert headers.get("anthropic-version") == "v"


def test_forward_direct_relay_without_client_header_sends_none(tmp_path, monkeypatch):
    headers = _forward_with(monkeypatch, RelayConfig(name="d", protocol="chat", base_url="https://u.example/v1"))
    assert "Authorization" not in headers and "x-api-key" not in headers


def test_passthrough_headers_gate_for_via_relay():
    """via-relay(两层/工具)绝不透传客户端头,防把 key 泄给工具。"""
    via_relay = RelayConfig(name="cc", protocol="chat", base_url="http://127.0.0.1:15721", via="cc-switch")
    direct = RelayConfig(name="d", protocol="chat", base_url="https://u.example/v1")
    client = {"Authorization": "Bearer k"}
    assert _passthrough_headers_for(via_relay, client) is None
    assert _passthrough_headers_for(direct, client) == client
    assert _passthrough_headers_for(direct, {}) is None


# ── 对账:provider 条目漂移吸收 + relay 重建 ──────────────────────────


def test_reconcile_absorbs_provider_drift_and_rebuilds_relay(tmp_path, monkeypatch):
    from vision_relay import reconcile

    home = _qwen_home(tmp_path, monkeypatch)
    p = _write_qwen_settings(home, {"openai": [_entry("m-1", "https://ollama.com/v1")]})
    cfg = ProxyConfig()
    cfg.routing.harnesses = ["qwen-code"]
    wiring.wiring_backup_and_rewrite(cfg)
    assert any(r.name.startswith("qwen-") for r in cfg.relays)

    # 用户换供应商:条目改走新地址,relay 被删
    d = _read(p)
    d["modelProviders"]["openai"][0]["baseUrl"] = "https://new.example/v2"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    cfg.relays = [r for r in cfg.relays if not r.name.startswith("qwen-")]

    monkeypatch.setattr(
        reconcile,
        "observe",
        lambda cfg, ts: {
            "service_alive": True,
            "routing_on": True,
            "harnesses": {"qwen-code": {"base_url": PROXY, "ownership": "ours", "config_exists": True}},
        },
    )
    report = reconcile.reconcile(cfg, tool_states=[], trigger="test")

    assert _providers_of(p)[0]["baseUrl"] == PROXY  # 漂移被重接管
    snap = snapshot.load()["qwen-code"]
    assert snap.provider_urls[ENV_KEY] == "https://new.example/v2"  # 新原值吸收
    assert any(a.get("type") == "provider_absorb" for a in report["actions"])
    assert any(r.name.startswith("qwen-") and r.base_url == "https://new.example/v2" for r in cfg.relays)


# ── 流式响应 Content-Type 透传(回归:qwen OpenAI SDK 拒收 SSE 正文配 JSON 头) ──


class _SSEUpstream:
    """SSE 上游:对 stream 请求回 text/event-stream 的 chat.chunk 流。"""

    def __init__(self):
        self.port = None
        self._srv = None
        self.received = []

    def start(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        up = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(length))
                up.received.append(body)
                chunks = []
                for piece in ("你", "好"):
                    chunks.append(
                        "data: "
                        + json.dumps(
                            {
                                "id": "c1",
                                "object": "chat.completion.chunk",
                                "model": body.get("model", ""),
                                "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                            }
                        )
                        + "\n\n"
                    )
                chunks.append("data: [DONE]\n\n")
                payload = "".join(chunks).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self.port = self._srv.server_address[1]
        return self

    def stop(self):
        self._srv.shutdown()


def test_forward_returns_upstream_content_type(tmp_path, monkeypatch):
    class _SSEStubResponse(_StubResponse):
        text = "data: {}\n\n"

        def __init__(self):
            self.headers = {"content-type": "text/event-stream; charset=utf-8"}

    import vision_relay.server as server_mod

    monkeypatch.setattr(
        server_mod.httpx,
        "Client",
        type(
            "C",
            (),
            {
                "__init__": lambda s, *a, **k: None,
                "__enter__": lambda s: s,
                "__exit__": lambda s, *a: False,
                "post": lambda s, *a, **k: _SSEStubResponse(),
            },
        ),
    )
    status, text, ctype = _forward(
        RelayConfig(name="d", protocol="chat", base_url="https://u.example/v1"), {"model": "m"}, True
    )
    assert status == 200 and ctype == "text/event-stream"


def test_streaming_response_keeps_sse_content_type_end_to_end(tmp_path, monkeypatch):
    """回归(真机 qwen 报错):流式请求的响应必须 text/event-stream,正文为 SSE。"""
    import httpx

    from vision_relay.cache import DescriptionCache
    from vision_relay.config import VLMConfig
    from vision_relay.pipeline import Pipeline
    from vision_relay.server import run_server

    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
    up = _SSEUpstream().start()
    cfg = ProxyConfig(
        bind_port=0,
        relays=[RelayConfig(name="qwen-u", protocol="chat", base_url=f"http://127.0.0.1:{up.port}/v1")],
        vlm=VLMConfig(model="qwen-vl-max"),
    )
    server = run_server(cfg)
    server.pipeline = Pipeline(NoopVLMLike(), DescriptionCache())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        resp = httpx.Client(trust_env=False).post(
            f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions",
            json={"model": "m-1", "stream": True, "messages": [{"role": "user", "content": "你好"}]},
        )
        assert resp.status_code == 200
        assert resp.headers.get("content-type") == "text/event-stream"
        assert resp.text.startswith("data: ")
        assert "[DONE]" in resp.text
        assert up.received[-1].get("stream") is True
    finally:
        server.shutdown()
        up.stop()


class NoopVLMLike:
    def describe(self, image, question=None, tier=1):
        return "fake description"


# ── 准入门:接管自动开 modalities.image,还原恢复原值 ─────────────────


def test_takeover_opens_modalities_gate(tmp_path, monkeypatch):
    """qwen 准入门(generationConfig.modalities.image)不开,图片根本进不了请求——
    接管必须代开:所有可改写条目 image=true;已有 generationConfig 字段原样保留。"""
    home = _qwen_home(tmp_path, monkeypatch)
    p = _write_qwen_settings(
        home,
        {
            "openai": [
                _entry("m-1", "https://ollama.com/v1"),  # 无 generationConfig → 创建
                {
                    "id": "m-2",
                    "baseUrl": "https://a.com/v1",
                    "envKey": "K2",
                    "generationConfig": {"extra_body": {"enable_thinking": True}, "contextWindowSize": 1000000},
                },
            ]
        },
    )
    cfg = ProxyConfig()
    cfg.routing.harnesses = ["qwen-code"]
    wiring.wiring_backup_and_rewrite(cfg)
    entries = _providers_of(p)
    assert entries[0]["generationConfig"]["modalities"] == {"image": True}
    g2 = entries[1]["generationConfig"]
    assert g2["modalities"] == {"image": True}
    assert g2["extra_body"] == {"enable_thinking": True}  # 原有字段不动
    assert g2["contextWindowSize"] == 1000000
    snap = snapshot.load()["qwen-code"]
    assert snap.provider_modalities == {ENV_KEY: "~absent~", "K2": "~absent~"}


def test_modalities_restore_reverts_to_original(tmp_path, monkeypatch):
    """stop 还原:原本没有 modalities 的删除;原本 false/true 的恢复原值。"""
    home = _qwen_home(tmp_path, monkeypatch)
    p = _write_qwen_settings(
        home,
        {
            "openai": [
                _entry("m-1", "https://ollama.com/v1"),  # 原本 absent
                {
                    "id": "m-2",
                    "baseUrl": "https://a.com/v1",
                    "envKey": "K2",
                    "generationConfig": {"modalities": {"image": False}},
                },
            ]
        },
    )
    cfg = ProxyConfig()
    cfg.routing.harnesses = ["qwen-code"]
    wiring.wiring_backup_and_rewrite(cfg)
    wiring.wiring_restore_on_stop(cfg)
    entries = _providers_of(p)
    assert "modalities" not in entries[0].get("generationConfig", {})
    assert entries[1]["generationConfig"]["modalities"] == {"image": False}
    # URL 也一并还原
    assert entries[0]["baseUrl"] == "https://ollama.com/v1"


def test_modalities_already_open_not_rerecorded(tmp_path, monkeypatch):
    """用户已手动开过门的条目:不重复记录(还原时保持 true,幂等)。"""
    home = _qwen_home(tmp_path, monkeypatch)
    p = _write_qwen_settings(
        home,
        {
            "openai": [
                {
                    "id": "m-1",
                    "baseUrl": "https://ollama.com/v1",
                    "envKey": ENV_KEY,
                    "generationConfig": {"modalities": {"image": True}},
                },
            ]
        },
    )
    cfg = ProxyConfig()
    cfg.routing.harnesses = ["qwen-code"]
    wiring.wiring_backup_and_rewrite(cfg)
    wiring.wiring_backup_and_rewrite(cfg)  # 二次
    snap = snapshot.load()["qwen-code"]
    assert not snap.provider_modalities  # 无变更不记录(None/空)
    wiring.wiring_restore_on_stop(cfg)
    assert _providers_of(p)[0]["generationConfig"]["modalities"] == {"image": True}


def test_reconcile_reopens_closed_gate(tmp_path, monkeypatch):
    """接管态下用户/外部把门关了(modalities 删掉)→ 对账重开。"""
    from vision_relay import reconcile

    home = _qwen_home(tmp_path, monkeypatch)
    p = _write_qwen_settings(home, {"openai": [_entry("m-1", "https://ollama.com/v1")]})
    cfg = ProxyConfig()
    cfg.routing.harnesses = ["qwen-code"]
    wiring.wiring_backup_and_rewrite(cfg)
    d = _read(p)
    del d["modelProviders"]["openai"][0]["generationConfig"]["modalities"]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    monkeypatch.setattr(
        reconcile,
        "observe",
        lambda cfg, ts: {
            "service_alive": True,
            "routing_on": True,
            "harnesses": {"qwen-code": {"base_url": PROXY, "ownership": "ours", "config_exists": True}},
        },
    )
    reconcile.reconcile(cfg, tool_states=[], trigger="test")
    assert _providers_of(p)[0]["generationConfig"]["modalities"] == {"image": True}


def test_wiring_report_counts_gated(tmp_path, monkeypatch):
    home = _qwen_home(tmp_path, monkeypatch)
    _write_qwen_settings(home, {"openai": [_entry("m-1", "https://ollama.com/v1")]})
    row = next(r for r in wiring.wiring_report(ProxyConfig()) if r["harness"] == "qwen-code")
    assert row["providers"]["gated"] == 0
    cfg = ProxyConfig()
    cfg.routing.harnesses = ["qwen-code"]
    wiring.wiring_backup_and_rewrite(cfg)
    row = next(r for r in wiring.wiring_report(cfg) if r["harness"] == "qwen-code")
    assert row["providers"]["gated"] == 1
    assert row["wired"] is True
