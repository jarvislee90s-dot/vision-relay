"""route_fallback: 工具端口死时 relay 落直连(spec §5 "工具路由关→relay 回落直连")。

密钥窄豁免(2026-08-25 决策):档案密钥仅进内存转发头,不落盘/不进日志——
本文件有专门断言钉住。
"""

from __future__ import annotations

import json
import sqlite3
import threading

import httpx
import pytest

from vision_relay import route_fallback as rf
from vision_relay.cache import DescriptionCache
from vision_relay.config import ProxyConfig, RelayConfig, VLMConfig
from vision_relay.pipeline import Pipeline
from vision_relay.server import _select_relay, run_server


class NoopVLM:
    def describe(self, image, question=None, tier=1, **kw):
        return "fake description"


def _mk_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE providers (
            id TEXT NOT NULL, app_type TEXT NOT NULL, name TEXT NOT NULL,
            settings_config TEXT NOT NULL, is_current INTEGER,
            sort_index INTEGER, PRIMARY KEY (id, app_type))"""
    )
    conn.executemany("INSERT INTO providers VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


CLAUDE_ROW = (
    "a1",
    "claude",
    "火山Ark",
    json.dumps(
        {
            "env": {
                "ANTHROPIC_BASE_URL": "https://ark.example.com/api/coding",
                "ANTHROPIC_AUTH_TOKEN": "ark-secret-token",
            },
            "model": "fable",
        }
    ),
    1,
    0,
)


def _codex_row(wire_api: str, provider_id: str = "kimi_coding"):
    return (
        "c1",
        "codex",
        "Kimi For Coding",
        json.dumps(
            {
                "config": 'model_provider = "custom"\nmodel = "kimi-for-coding"\n\n'
                f'[model_providers.custom]\nname = "{provider_id}"\n'
                f'base_url = "https://api.example.com/coding/v1"\nwire_api = "{wire_api}"\n',
                "auth": json.dumps({"OPENAI_API_KEY": "sk-codex-key"}),
            }
        ),
        1,
        0,
    )


# ---------- 档案解析:cc-switch ----------


def test_ccswitch_claude_direct_reads_current_provider(tmp_path, monkeypatch):
    db = tmp_path / "cc-switch.db"
    _mk_db(db, [CLAUDE_ROW])
    monkeypatch.setattr(rf, "CCSWITCH_DB", str(db))
    r = rf.ccswitch_app_direct("claude", "cc-anthropic")
    assert r is not None
    assert r.protocol == "anthropic"
    assert r.base_url == "https://ark.example.com/api/coding"
    assert r.api_key == "ark-secret-token"
    assert r.via is None
    assert r.name == "cc-anthropic~direct"


def test_ccswitch_codex_direct_parses_wire_api(tmp_path, monkeypatch):
    db = tmp_path / "cc-switch.db"
    _mk_db(db, [_codex_row("chat")])
    monkeypatch.setattr(rf, "CCSWITCH_DB", str(db))
    r = rf.ccswitch_app_direct("codex", "cc-codex")
    assert r is not None and r.protocol == "chat"  # wire_api=chat → chat 直连
    assert r.base_url == "https://api.example.com/coding/v1"
    assert r.api_key == "sk-codex-key"

    db2 = tmp_path / "cc-switch2.db"
    _mk_db(db2, [_codex_row("responses")])
    monkeypatch.setattr(rf, "CCSWITCH_DB", str(db2))
    r2 = rf.ccswitch_app_direct("codex", "cc-codex")
    assert r2 is not None and r2.protocol == "responses"  # wire_api=responses 原生


def test_ccswitch_codex_direct_tomllib_absent_regex_fallback(tmp_path, monkeypatch):
    """Python 3.10 无 tomllib：codex 直连目标退正则解析（同 model_sources 模式）。

    语义与 tomllib 路径一致：取首个 [model_providers.<id>] 条目的 base_url/wire_api；
    顶层键与其他供应商段不串线（第二个段的值不得污染首条目）。
    """
    monkeypatch.setattr(rf, "tomllib", None)
    db = tmp_path / "cc-switch.db"
    _mk_db(
        db,
        [
            (
                "c1",
                "codex",
                "Kimi For Coding",
                json.dumps(
                    {
                        "config": 'model_provider = "custom"\nmodel = "kimi-for-coding"\n\n'
                        '[model_providers.first]\nname = "n1"\n'
                        'base_url = "https://first.example/coding/v1"\nwire_api = "chat"\n\n'
                        '[model_providers.second]\nbase_url = "https://second.example/v1"\nwire_api = "responses"\n',
                        "auth": json.dumps({"OPENAI_API_KEY": "sk-codex-key"}),
                    }
                ),
                1,
                0,
            )
        ],
    )
    monkeypatch.setattr(rf, "CCSWITCH_DB", str(db))
    r = rf.ccswitch_app_direct("codex", "cc-codex")
    assert r is not None and r.protocol == "chat"  # 首条目 wire_api=chat,非第二段的 responses
    assert r.base_url == "https://first.example/coding/v1"
    assert r.api_key == "sk-codex-key"


def test_codex_toml_target_regex_fallback_edges(monkeypatch):
    """3.10 正则兜底边界：无供应商段/段内缺键 → (None, None)；auth 损坏 → key 空串。"""
    monkeypatch.setattr(rf, "tomllib", None)
    assert rf._codex_toml_target({"config": 'model = "m"\n'}) == (None, None, "")
    assert rf._codex_toml_target({"config": '[model_providers.x]\nname = "n"\n', "auth": "not-json"}) == (
        None,
        None,
        "",
    )
    assert rf._codex_toml_target({}) == (None, None, "")


def test_ccswitch_direct_missing_or_bad_db_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(rf, "CCSWITCH_DB", str(tmp_path / "nope.db"))
    assert rf.ccswitch_app_direct("claude", "cc-anthropic") is None
    bad = tmp_path / "bad.db"
    bad.write_text("not sqlite")
    monkeypatch.setattr(rf, "CCSWITCH_DB", str(bad))
    assert rf.ccswitch_app_direct("claude", "cc-anthropic") is None


# ---------- 档案解析:codex++ ----------


def _mk_codexpp(path, profile: dict, active: str = "p1"):
    path.write_text(json.dumps({"activeRelayId": active, "relayProfiles": [{"id": "p1", "name": "档一", **profile}]}))


def test_codexpp_chatcompletions_falls_back_as_chat(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    _mk_codexpp(
        f,
        {
            "upstreamBaseUrl": "https://up.example.com/v1",
            "protocol": "chatCompletions",
            "relayMode": "pureApi",
            "authContents": json.dumps({"OPENAI_API_KEY": "sk-relay-key"}),
        },
    )
    monkeypatch.setattr(rf, "CODEXPP_SETTINGS", str(f))
    r = rf.codexpp_active_direct("codex-plus")
    assert r is not None and r.protocol == "chat"  # 上游是 chat → 本代理做 responses→chat 转换
    assert r.base_url == "https://up.example.com/v1"
    assert r.api_key == "sk-relay-key"
    assert r.name == "codex-plus~direct"


def test_codexpp_responses_pureapi_direct(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    _mk_codexpp(
        f,
        {
            "upstreamBaseUrl": "https://up.example.com/v1",
            "protocol": "responses",
            "relayMode": "pureApi",
            "authContents": json.dumps({"OPENAI_API_KEY": "sk-r"}),
        },
    )
    monkeypatch.setattr(rf, "CODEXPP_SETTINGS", str(f))
    r = rf.codexpp_active_direct("codex-plus")
    assert r is not None and r.protocol == "responses"  # 上游原生 responses → 原协议直发


def test_codexpp_official_or_urlless_returns_none(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    _mk_codexpp(f, {"upstreamBaseUrl": "", "protocol": "responses", "relayMode": "official"})
    monkeypatch.setattr(rf, "CODEXPP_SETTINGS", str(f))
    assert rf.codexpp_active_direct("codex-plus") is None  # ChatGPT OAuth 无法直连回落
    f2 = tmp_path / "s2.json"
    _mk_codexpp(f2, {"upstreamBaseUrl": "", "protocol": "chatCompletions", "relayMode": "pureApi"})
    monkeypatch.setattr(rf, "CODEXPP_SETTINGS", str(f2))
    assert rf.codexpp_active_direct("codex-plus") is None  # 无上游地址
    monkeypatch.setattr(rf, "CODEXPP_SETTINGS", str(tmp_path / "nope.json"))
    assert rf.codexpp_active_direct("codex-plus") is None  # 档案缺失


# ---------- resolve_effective_relay + 端口缓存 ----------


def test_resolve_online_keeps_two_layer_and_ttl_caches(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(rf, "_port_online", lambda p: calls.append(p) or True)
    db = tmp_path / "cc-switch.db"
    _mk_db(db, [CLAUDE_ROW])
    monkeypatch.setattr(rf, "CCSWITCH_DB", str(db))
    monkeypatch.setattr(rf, "_emit_mode", lambda *a: None)
    relay = RelayConfig(name="cc-anthropic", protocol="anthropic", base_url="http://127.0.0.1:15721", via="cc-switch")
    eff, reason = rf.resolve_effective_relay(relay, rf.PortCache())
    assert eff is relay and reason is None
    cache = rf.PortCache()
    rf.resolve_effective_relay(relay, cache)
    rf.resolve_effective_relay(relay, cache)  # TTL 内复用结果,不再探测
    assert len(calls) == 2  # 第一次独立缓存 1 次 + cache 首次 1 次;第三次命中 TTL 不探测


def test_resolve_offline_falls_back_and_no_direct(tmp_path, monkeypatch):
    monkeypatch.setattr(rf, "_port_online", lambda p: False)
    emitted = []
    monkeypatch.setattr(rf, "_emit_mode", lambda tool, mode: emitted.append((tool, mode)))
    db = tmp_path / "cc-switch.db"
    _mk_db(db, [CLAUDE_ROW])
    monkeypatch.setattr(rf, "CCSWITCH_DB", str(db))
    relay = RelayConfig(name="cc-anthropic", protocol="anthropic", base_url="http://127.0.0.1:15721", via="cc-switch")
    eff, reason = rf.resolve_effective_relay(relay, rf.PortCache())
    assert eff.base_url == "https://ark.example.com/api/coding"
    assert reason == "cc-switch:offline"
    assert emitted == [("cc-switch", "direct")]

    # 档案不可读:保持原 relay(死端口行为同现状),原因带 no-direct
    monkeypatch.setattr(rf, "CCSWITCH_DB", str(tmp_path / "nope.db"))
    eff2, reason2 = rf.resolve_effective_relay(relay, rf.PortCache())
    assert eff2 is relay and reason2 == "cc-switch:offline:no-direct"
    assert emitted[-1] == ("cc-switch", "dead-no-direct")


def test_resolve_non_via_relay_untouched(monkeypatch):
    def boom(p):
        raise AssertionError("非 via relay 不得探测端口")

    monkeypatch.setattr(rf, "_port_online", boom)
    relay = RelayConfig(name="direct-qwen-code", protocol="chat", base_url="https://dashscope.example.com/v1")
    eff, reason = rf.resolve_effective_relay(relay, rf.PortCache())
    assert eff is relay and reason is None


def test_emit_mode_dedupes_transitions(monkeypatch):
    events = []
    monkeypatch.setattr(rf, "_append_event", lambda *a: events.append(a))
    rf._last_mode.clear()
    rf._emit_mode("cc-switch", "direct")
    rf._emit_mode("cc-switch", "direct")  # 同模式重复不发
    rf._emit_mode("cc-switch", "two-layer")  # 转换才发
    assert len(events) == 2


# ---------- server 集成 ----------


def _start(cfg):
    pipe = Pipeline(NoopVLM(), DescriptionCache())
    server = run_server(cfg)
    server.pipeline = pipe
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


@pytest.fixture(autouse=True)
def _quiet_events(request, monkeypatch):
    if request.node.name == "test_emit_mode_dedupes_transitions":  # 该测试自己钉事件语义
        return
    monkeypatch.setattr(rf, "_emit_mode", lambda *a: None)


def test_server_offline_tool_falls_back_to_provider(upstream, tmp_path, monkeypatch):
    """cc-switch 端口死 → 直连其当前供应商(事故 2026-08-24 复盘场景)。"""
    monkeypatch.setattr(rf, "_port_online", lambda p: False)
    db = tmp_path / "cc-switch.db"
    # 供应商地址指向测试 upstream(真实 HTTP),token 是档案里的
    _mk_db(
        db,
        [
            (
                "a1",
                "claude",
                "火山Ark",
                json.dumps(
                    {
                        "env": {
                            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{upstream.port}",
                            "ANTHROPIC_AUTH_TOKEN": "ark-secret-token",
                        }
                    }
                ),
                1,
                0,
            )
        ],
    )
    monkeypatch.setattr(rf, "CCSWITCH_DB", str(db))
    cfg = ProxyConfig(
        bind_port=0,
        relays=[
            RelayConfig(name="cc-anthropic", protocol="anthropic", base_url="http://127.0.0.1:15721", via="cc-switch")
        ],
        vlm=VLMConfig(model="qwen-vl-max"),
    )
    server, port = _start(cfg)
    try:
        resp = httpx.Client(trust_env=False).post(
            f"http://127.0.0.1:{port}/v1/messages",
            json={
                "model": "deepseek-v4-flash",
                "max_tokens": 16,
                "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            },
        )
        assert resp.status_code == 200
        assert upstream.received, "必须落到直连供应商而非死端口"
        assert upstream.received_headers[-1].get("x-api-key") == "ark-secret-token"
        assert upstream.received[-1]["model"] == "deepseek-v4-flash"
    finally:
        server.shutdown()


def test_server_offline_codexpp_converts_responses_to_chat(upstream, tmp_path, monkeypatch):
    monkeypatch.setattr(rf, "_port_online", lambda p: False)
    f = tmp_path / "settings.json"
    _mk_codexpp(
        f,
        {
            "upstreamBaseUrl": f"http://127.0.0.1:{upstream.port}/v1",
            "protocol": "chatCompletions",
            "relayMode": "pureApi",
            "authContents": json.dumps({"OPENAI_API_KEY": "sk-relay-key"}),
        },
    )
    monkeypatch.setattr(rf, "CODEXPP_SETTINGS", str(f))
    cfg = ProxyConfig(
        bind_port=0,
        relays=[
            RelayConfig(name="codex-plus", protocol="responses", base_url="http://127.0.0.1:57321/v1", via="codex-plus")
        ],
        vlm=VLMConfig(model="qwen-vl-max"),
    )
    server, port = _start(cfg)
    try:
        resp = httpx.Client(trust_env=False).post(
            f"http://127.0.0.1:{port}/v1/responses",
            json={
                "model": "kimi-k2.7",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            },
        )
        assert resp.status_code == 200
        sent = upstream.received[-1]
        assert sent.get("messages") is not None  # responses→chat 转换发生
        assert sent["model"] == "kimi-k2.7"
        hdrs = {k.lower(): v for k, v in upstream.received_headers[-1].items()}
        assert hdrs.get("authorization") == "Bearer sk-relay-key"
    finally:
        server.shutdown()


def test_server_online_tool_keeps_two_layer(upstream, tmp_path, monkeypatch):
    monkeypatch.setattr(rf, "_port_online", lambda p: True)  # 工具端口"在线"
    cfg = ProxyConfig(
        bind_port=0,
        relays=[
            RelayConfig(
                name="cc-anthropic", protocol="anthropic", base_url=f"http://127.0.0.1:{upstream.port}", via="cc-switch"
            )
        ],
        vlm=VLMConfig(model="qwen-vl-max"),
    )
    server, port = _start(cfg)
    try:
        resp = httpx.Client(trust_env=False).post(
            f"http://127.0.0.1:{port}/v1/messages",
            json={"model": "m", "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200 and upstream.received
    finally:
        server.shutdown()


def test_server_connecterror_midflight_retries_direct(upstream, tmp_path, monkeypatch):
    """缓存说在线但实际端口已死(请求中途被关):ConnectError → 强制离线重解析重试一次。"""
    monkeypatch.setattr(rf, "_port_online", lambda p: True)  # 缓存被骗:探测说在线
    db = tmp_path / "cc-switch.db"
    _mk_db(
        db,
        [
            (
                "a1",
                "claude",
                "火山Ark",
                json.dumps(
                    {"env": {"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{upstream.port}", "ANTHROPIC_AUTH_TOKEN": "k"}}
                ),
                1,
                0,
            )
        ],
    )
    monkeypatch.setattr(rf, "CCSWITCH_DB", str(db))
    cfg = ProxyConfig(
        bind_port=0,
        relays=[
            RelayConfig(
                name="cc-anthropic", protocol="anthropic", base_url="http://127.0.0.1:9", via="cc-switch"
            )  # 真死端口
        ],
        vlm=VLMConfig(model="qwen-vl-max"),
    )
    server, port = _start(cfg)
    try:
        resp = httpx.Client(trust_env=False).post(
            f"http://127.0.0.1:{port}/v1/messages",
            json={"model": "m", "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200
        assert upstream.received, "重试必须落到直连目标"
    finally:
        server.shutdown()


def test_server_fallback_reason_logged_without_key(upstream, tmp_path, monkeypatch):
    """proxy_request 日志带 fallback 原因,且绝不含档案密钥。"""
    monkeypatch.setattr(rf, "_port_online", lambda p: False)
    db = tmp_path / "cc-switch.db"
    _mk_db(
        db,
        [
            (
                "a1",
                "claude",
                "火山Ark",
                json.dumps(
                    {
                        "env": {
                            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{upstream.port}",
                            "ANTHROPIC_AUTH_TOKEN": "topsecret-xyz",
                        }
                    }
                ),
                1,
                0,
            )
        ],
    )
    monkeypatch.setattr(rf, "CCSWITCH_DB", str(db))
    logged = []
    import vision_relay.server as srv

    monkeypatch.setattr(srv, "log_json", lambda d: logged.append(json.dumps(d, ensure_ascii=False)))
    cfg = ProxyConfig(
        bind_port=0,
        relays=[
            RelayConfig(name="cc-anthropic", protocol="anthropic", base_url="http://127.0.0.1:15721", via="cc-switch")
        ],
        vlm=VLMConfig(model="qwen-vl-max"),
    )
    server, port = _start(cfg)
    try:
        httpx.Client(trust_env=False).post(
            f"http://127.0.0.1:{port}/v1/messages",
            json={"model": "m", "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]},
        )
    finally:
        server.shutdown()
    req_logs = [line for line in logged if '"proxy_request"' in line]
    assert req_logs and '"fallback": "cc-switch:offline"' in req_logs[0]
    assert "topsecret-xyz" not in "".join(logged)


def test_dual_codex_tools_disambiguated_by_protocol():
    """codex 双工具:入站协议确定选线(chat→cc-switch / responses→codex++),不随机。"""
    cfg = ProxyConfig(
        bind_port=0,
        relays=[
            RelayConfig(name="cc-codex", protocol="chat", base_url="http://127.0.0.1:15721", via="cc-switch"),
            RelayConfig(
                name="codex-plus", protocol="responses", base_url="http://127.0.0.1:57321/v1", via="codex-plus"
            ),
        ],
        vlm=VLMConfig(model="qwen-vl-max"),
    )
    assert _select_relay(cfg, "chat", "kimi-k2.7").name == "cc-codex"
    assert _select_relay(cfg, "responses", "kimi-k2.7").name == "codex-plus"


def test_status_reports_upstream_effective(tmp_path, monkeypatch):
    """status 的 relay 视图带 upstream_effective:工具在线=两层地址;离线=档案真实地址;key 不外泄。"""
    import vision_relay.verbs as verbs_mod

    db = tmp_path / "cc-switch.db"
    _mk_db(db, [CLAUDE_ROW])
    monkeypatch.setattr(rf, "CCSWITCH_DB", str(db))
    relay = RelayConfig(name="cc-anthropic", protocol="anthropic", base_url="http://127.0.0.1:15721", via="cc-switch")
    cfg = ProxyConfig(bind_port=0, relays=[relay], vlm=VLMConfig(model="m"))

    monkeypatch.setattr(verbs_mod, "_observe_for_status", lambda c: {"tools": [{"name": "cc-switch", "online": False}]})
    out = verbs_mod.status(cfg)
    r0 = out["data"]["relays"][0]
    assert r0["upstream_effective"] == "https://ark.example.com/api/coding"
    assert "ark-secret-token" not in json.dumps(out)  # 档案 key 绝不进 status

    monkeypatch.setattr(verbs_mod, "_observe_for_status", lambda c: {"tools": [{"name": "cc-switch", "online": True}]})
    out2 = verbs_mod.status(cfg)
    assert out2["data"]["relays"][0]["upstream_effective"] == "http://127.0.0.1:15721"
