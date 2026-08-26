from __future__ import annotations

import json
import threading

import httpx

from vision_relay.config import ProxyConfig, RelayConfig
from vision_relay.pipeline import Pipeline
from vision_relay.server import _select_relay, _upstream_url, run_server


class NoopVLM:
    def describe(self, image, question=None, tier=1):
        return "fake description"


def test_upstream_url_tolerates_with_and_without_v1():
    for base in ("http://127.0.0.1:9", "http://127.0.0.1:9/v1"):
        url = _upstream_url(RelayConfig(name="u", protocol="chat", base_url=base), "/chat/completions")
        assert url == "http://127.0.0.1:9/v1/chat/completions"


def test_upstream_url_build_versioned_url_rules():
    """按 Codex++ build_versioned_url 启发式的拼接规则（对齐其单测预期）。

    规则：纯 origin 加 /v1；已带 v<数字> 版本段 / 非版本路径 / # 结尾 → 直接拼；
    已以 path 结尾 → 原样；/v1/v1 去重。
    """
    cases = [
        # 纯 origin → 加 /v1
        ("http://127.0.0.1:9", "/chat/completions", "http://127.0.0.1:9/v1/chat/completions"),
        ("https://api.deepseek.com", "/chat/completions", "https://api.deepseek.com/v1/chat/completions"),
        # 已带 v<数字> 版本段 → 直接拼
        ("http://127.0.0.1:9/v1", "/chat/completions", "http://127.0.0.1:9/v1/chat/completions"),
        (
            "https://ark.example.com/api/coding/v3",
            "/chat/completions",
            "https://ark.example.com/api/coding/v3/chat/completions",
        ),
        ("https://ark.example.com/api/coding/v3", "/responses", "https://ark.example.com/api/coding/v3/responses"),
        ("https://api.example.com/v2", "/chat/completions", "https://api.example.com/v2/chat/completions"),
        ("https://api.example.com/v1beta", "/chat/completions", "https://api.example.com/v1beta/chat/completions"),
        # 非版本路径 → 直接拼
        ("https://api.example.com/openai", "/chat/completions", "https://api.example.com/openai/chat/completions"),
        ("https://api.deepseek.com/anthropic/v1", "/messages", "https://api.deepseek.com/anthropic/v1/messages"),
        ("https://ark.example.com/api/coding/v1", "/messages", "https://ark.example.com/api/coding/v1/messages"),
        # # 结尾 → 跳过版本前缀
        ("https://api.example.com/openai#", "/chat/completions", "https://api.example.com/openai/chat/completions"),
        # 已以 path 结尾 → 原样
        (
            "https://api.example.com/v1/chat/completions",
            "/chat/completions",
            "https://api.example.com/v1/chat/completions",
        ),
        # /v1/v1 去重
        ("https://api.example.com/v1/v1", "/chat/completions", "https://api.example.com/v1/chat/completions"),
    ]
    for base, path, expected in cases:
        url = _upstream_url(RelayConfig(name="u", protocol="chat", base_url=base), path)
        assert url == expected, f"{base} + {path} -> {url}, expected {expected}"


def test_upstream_url_anthropic_always_versions():
    """anthropic 协议：Anthropic 端点固定 /v1/messages，直连根（无版本段）也补 /v1；
    已带 /v1 时去重。"""
    cases = [
        ("https://ark.example.com/api/coding", "/messages", "https://ark.example.com/api/coding/v1/messages"),
        ("https://api.anthropic.com", "/messages", "https://api.anthropic.com/v1/messages"),
        ("https://api.deepseek.com/anthropic/v1", "/messages", "https://api.deepseek.com/anthropic/v1/messages"),
        ("https://api.example.com/v1/messages", "/messages", "https://api.example.com/v1/messages"),
    ]
    for base, path, expected in cases:
        url = _upstream_url(RelayConfig(name="u", protocol="anthropic", base_url=base), path)
        assert url == expected, f"{base} + {path} -> {url}, expected {expected}"


def test_select_relay_matches_model_then_protocol_then_default():
    cfg = ProxyConfig(
        relays=[
            RelayConfig(name="r1", protocol="chat", base_url="http://r1", models=["deepseek/*"]),
            RelayConfig(name="r2", protocol="chat", base_url="http://r2", models=["qwen-*"]),
            RelayConfig(name="r3", protocol="anthropic", base_url="http://r3"),
        ],
    )
    # model 匹配：deepseek-* 落到 r1
    assert _select_relay(cfg, "chat", "deepseek-v3").name == "r1"
    # model 匹配：qwen-* 落到 r2（同 protocol 下按 model 区分）
    assert _select_relay(cfg, "chat", "qwen-max").name == "r2"
    # 无 model 匹配 -> 回退 protocol 首条
    assert _select_relay(cfg, "chat", "glm-4").name == "r1"
    # 协议不匹配 -> 默认 relay（base_url 空）
    d = _select_relay(cfg, "responses", "deepseek-v3")
    assert d.base_url == "" and d.name == "default"
    # 空 model（旧签名）-> 回退协议
    assert _select_relay(cfg, "chat").name == "r1"


def test_proxy_forwards_text_request_and_transcribes_image(upstream):
    cfg = ProxyConfig(
        bind_port=0,  # ephemeral: avoid colliding with the default 8787 (or another test run)
        relays=[RelayConfig(name="up", protocol="chat", base_url=f"http://127.0.0.1:{upstream.port}/v1")],
        vlm=__import__("vision_relay.config", fromlist=["VLMConfig"]).VLMConfig(model="qwen-vl-max"),
    )
    pipe = Pipeline(NoopVLM(), __import__("vision_relay.cache", fromlist=["DescriptionCache"]).DescriptionCache())
    server = run_server(cfg)
    server.pipeline = pipe  # inject NoopVLM pipeline (brief omits this; run_server creates a real VLMClient)
    server_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        resp = httpx.Client(trust_env=False).post(
            f"http://127.0.0.1:{server_port}/v1/chat/completions",
            json={
                "model": "deepseek-v4-pro",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "看图"},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
                        ],
                    }
                ],
            },
        )
        assert resp.status_code == 200
        assert upstream.received
        # 上游收到的消息里图片已被替换成描述文字
        texts = json.dumps(upstream.received[-1])
        assert "fake description" in texts
        assert "base64,QUJD" not in texts
    finally:
        server.shutdown()


def test_proxy_does_not_truncate_multibyte_upstream_response(upstream):
    """中文（多字节）上游回复不得被截断。

    回归 T1-T4 手测 bug：do_POST 曾用 len(text)（字符数）当 content-length，而实际写入的
    是 UTF-8 字节；中文每字 3 字节使 content-length 偏小，客户端读到截断的多字节尾部
    （中文被掐成半句、JSON 都不完整）。必须按字节数写 content-length。
    """
    upstream.content = "北京的秋天很美。" * 500  # 足够长的多字节回复
    cfg = ProxyConfig(
        bind_port=0,
        relays=[RelayConfig(name="up", protocol="chat", base_url=f"http://127.0.0.1:{upstream.port}/v1")],
        vlm=__import__("vision_relay.config", fromlist=["VLMConfig"]).VLMConfig(model="qwen-vl-max"),
    )
    pipe = Pipeline(NoopVLM(), __import__("vision_relay.cache", fromlist=["DescriptionCache"]).DescriptionCache())
    server = run_server(cfg)
    server.pipeline = pipe
    server_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        resp = httpx.Client(trust_env=False).post(
            f"http://127.0.0.1:{server_port}/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        )
        # JSON 能完整解析（没有被按错误的 content-length 掐断）、内容一字不缺
        data = resp.json()
        assert data["choices"][0]["message"]["content"] == "北京的秋天很美。" * 500
    finally:
        server.shutdown()


def test_upstream_url_codex_plus_local_proxy():
    # Codex++ 本地协议代理（protocol_proxy.rs，端口 57321，暴露 /v1，认 responses + chat）
    cases = [
        ("http://127.0.0.1:57321/v1", "chat", "/chat/completions", "http://127.0.0.1:57321/v1/chat/completions"),
        ("http://127.0.0.1:57321/v1", "responses", "/responses", "http://127.0.0.1:57321/v1/responses"),
        ("http://127.0.0.1:57321", "responses", "/responses", "http://127.0.0.1:57321/v1/responses"),
        ("http://127.0.0.1:57321", "chat", "/chat/completions", "http://127.0.0.1:57321/v1/chat/completions"),
    ]
    for base, proto, path, expected in cases:
        assert _upstream_url(RelayConfig(name="u", protocol=proto, base_url=base), path) == expected


def test_upstream_url_cc_switch_proxy():
    # CC Switch 本地反向代理（默认 15721），各工具用专属路径前缀 + /v1
    cases = [
        ("http://127.0.0.1:15721", "anthropic", "/messages", "http://127.0.0.1:15721/v1/messages"),
        ("http://127.0.0.1:15721", "chat", "/chat/completions", "http://127.0.0.1:15721/v1/chat/completions"),
        (
            "http://127.0.0.1:15721/grokbuild/v1",
            "chat",
            "/chat/completions",
            "http://127.0.0.1:15721/grokbuild/v1/chat/completions",
        ),
        ("http://127.0.0.1:15721/codex/v1", "responses", "/responses", "http://127.0.0.1:15721/codex/v1/responses"),
    ]
    for base, proto, path, expected in cases:
        assert _upstream_url(RelayConfig(name="u", protocol=proto, base_url=base), path) == expected


def test_proxy_two_layer_responses_preserves_model_and_strips_image(upstream):
    # 两层路由（我们代理→回环工具）responses 协议下的协议保真：
    # model 保留、图片剥离、协议形态（responses input）不变——工具能正常读模型
    cfg = ProxyConfig(
        bind_port=0,
        relays=[RelayConfig(name="tool", protocol="responses", base_url=f"http://127.0.0.1:{upstream.port}")],
        vlm=__import__("vision_relay.config", fromlist=["VLMConfig"]).VLMConfig(model="qwen-vl-max"),
    )
    pipe = Pipeline(NoopVLM(), __import__("vision_relay.cache", fromlist=["DescriptionCache"]).DescriptionCache())
    server = run_server(cfg)
    server.pipeline = pipe
    server_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        resp = httpx.Client(trust_env=False).post(
            f"http://127.0.0.1:{server_port}/v1/responses",
            json={
                "model": "test-model-x",  # 任意未标注名（勿用 kimi 等--启发式目录会建议 image，测的就不是未标注回落了）
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "看"},
                            {"type": "input_image", "image_url": "data:image/png;base64,QQ=="},
                        ],
                    },
                ],
            },
        )
        assert resp.status_code == 200
        sent = upstream.received[-1]
        assert sent["model"] == "test-model-x"  # model 保留
        assert sent.get("input") is not None  # 同协议（responses input）形态不变
        blob = json.dumps(sent)
        assert "fake description" in blob  # 图片被转写注入
        assert "QQ==" not in blob and "data:image" not in blob  # 图不再外泄
    finally:
        server.shutdown()


class TestSelectRelayAuthHints:
    def test_fingerprint_hit_wins_over_order(self):
        from vision_relay.config import ProxyConfig, RelayConfig
        from vision_relay.server import _select_relay

        cfg = ProxyConfig()
        cfg.relays = [
            RelayConfig(
                name="zcode-a",
                protocol="anthropic",
                base_url="https://a.example",
                models=["GLM-5.3"],
                provider_id="a",
                auth_hints=["aaaa…zzzz@20"],
            ),
            RelayConfig(
                name="zcode-b",
                protocol="anthropic",
                base_url="https://b.example",
                models=["GLM-5.3"],
                provider_id="b",
                auth_hints=["bbbb…yyyy@20"],
            ),
        ]
        r = _select_relay(cfg, "anthropic", "GLM-5.3", "bbbb…yyyy@20")
        assert r.name == "zcode-b"  # 顺序命中会选 a，指纹命中必须赢

    def test_no_fingerprint_falls_back_to_order(self):
        from vision_relay.config import ProxyConfig, RelayConfig
        from vision_relay.server import _select_relay

        cfg = ProxyConfig()
        cfg.relays = [RelayConfig(name="zcode-a", protocol="anthropic", base_url="https://a.example", models=["*"])]
        assert _select_relay(cfg, "anthropic", "m", None).name == "zcode-a"
        assert _select_relay(cfg, "anthropic", "m", "unknown…fp@10").name == "zcode-a"

    def test_fingerprint_mismatch_skips_hinted_in_model_layer(self):
        """评审①：指纹不匹配时第②层不得命中带 auth_hints 的 zcode 条目——防跨工具错家透传。"""
        from vision_relay.config import ProxyConfig, RelayConfig
        from vision_relay.server import _select_relay

        cfg = ProxyConfig()
        cfg.relays = [
            RelayConfig(
                name="zcode-a",
                protocol="chat",
                base_url="https://a.example",
                models=["GLM"],
                provider_id="a",
                auth_hints=["zzzz…aaaa@20"],
            ),
            RelayConfig(name="qwen-wild", protocol="chat", base_url="https://q.example", models=["*"]),
        ]
        r = _select_relay(cfg, "chat", "GLM", "wwww…qqqq@30")
        assert r.name == "qwen-wild"  # 外来指纹不得被 prepend 的 zcode 条目截胡

    def test_protocol_fallback_hinted_last_resort(self):
        """评审②：某协议仅剩指纹条目时，③层兜底命中它——失败形态是错家 401 自愈（spec P1-3），不是不可用。"""
        from vision_relay.config import ProxyConfig, RelayConfig
        from vision_relay.server import _select_relay

        cfg = ProxyConfig()
        cfg.relays = [
            RelayConfig(
                name="zcode-a",
                protocol="anthropic",
                base_url="https://a.example",
                models=["other-*"],
                auth_hints=["fp@10"],
            )
        ]
        r = _select_relay(cfg, "anthropic", "GLM-5.3", "fp@99")
        assert r.name == "zcode-a"  # 未列名模型+指纹未命中：协议级兜底仍走该条目（上游 401 可见）
        assert _select_relay(cfg, "anthropic", "GLM-5.3", None).name == "zcode-a"  # 不带鉴权头同样兜底


class TestHarnessAttribution:
    def test_zcode_relay_attributes_zcode(self):
        from vision_relay.config import RelayConfig
        from vision_relay.server import _HARNESS_BY_PROTO

        relay = RelayConfig(name="zcode-k", protocol="anthropic", base_url="https://x", provider_id="k")
        harness = "zcode" if getattr(relay, "provider_id", None) else _HARNESS_BY_PROTO.get("anthropic")
        assert harness == "zcode"

    def test_resolve_provider_prefers_provider_id(self):
        from vision_relay.config import ProxyConfig, RelayConfig
        from vision_relay.server import _resolve_provider

        r = RelayConfig(
            name="zcode-open.bigmodel.cn", protocol="anthropic", base_url="https://x", provider_id="builtin:bigmodel"
        )
        assert _resolve_provider(ProxyConfig(), r, {}) == "builtin:bigmodel"  # 能力/探针键=供应商 ID（spec §6.4）
        plain = RelayConfig(name="qwen-open.bigmodel.cn", protocol="chat", base_url="https://x")
        assert _resolve_provider(ProxyConfig(), plain, {}) == "qwen-open.bigmodel.cn"  # 非 zcode relay 语义不变

    def test_build_vlm_clients_includes_zcode(self):
        from vision_relay.config import ProxyConfig
        from vision_relay.server import build_vlm_clients

        assert "zcode" in build_vlm_clients(ProxyConfig())


class TestSelectRelaySuppressed:
    """停用转发=选路跳过（spec §7.5 语义收严，2026-08-26 故障复盘）。

    复盘背景：direct-claude 的 base_url 是残留坏值 https://x，三层选路全部落到它，
    Claude Code 全线 502；用户已点「停用转发」但选路无视压制名单，无法自救。
    """

    def _cfg(self):
        cfg = ProxyConfig()
        cfg.relays = [
            RelayConfig(
                name="zcode-hinted",
                protocol="anthropic",
                base_url="https://h.example",
                models=["GLM-*"],
                auth_hints=["fp@10"],
            ),
            RelayConfig(name="direct-claude", protocol="anthropic", base_url="https://x", models=["*"]),
        ]
        cfg.routing.suppressed_relays = ["direct-claude"]
        return cfg

    def test_suppressed_wildcard_skipped_falls_to_last_resort(self):
        # ②层 * 通配本会命中 direct-claude：压制后跳过，④层兜底到 hinted 条目
        assert _select_relay(self._cfg(), "anthropic", "deepseek-v4-flash", None).name == "zcode-hinted"
        # 指纹门挡掉 hinted（GLM 模型+外来指纹）后同样不得落回被压制条目
        assert _select_relay(self._cfg(), "anthropic", "GLM-5.3", "other…fp@9").name == "zcode-hinted"

    def test_fingerprint_hit_cannot_rescue_suppressed(self):
        """①层指纹精确命中也跳过被停用条目——停用是显式用户意图，优先级最高。"""
        cfg = ProxyConfig()
        cfg.relays = [
            RelayConfig(
                name="zcode-a", protocol="chat", base_url="https://a.example", models=["m"], auth_hints=["fp@10"]
            )
        ]
        cfg.routing.suppressed_relays = ["zcode-a"]
        assert _select_relay(cfg, "chat", "m", "fp@10").name == "default"

    def test_all_of_protocol_suppressed_falls_to_default(self):
        cfg = ProxyConfig()
        cfg.relays = [RelayConfig(name="direct-claude", protocol="anthropic", base_url="https://x")]
        cfg.routing.suppressed_relays = ["direct-claude"]
        r = _select_relay(cfg, "anthropic", "m", None)
        assert r.name == "default" and r.base_url == ""


def test_502_fail_open_names_relay_and_upstream(monkeypatch):
    """fail-open 502 必须自曝选路目标（relay 名+地址）。

    复盘背景：错误体/日志只有 "proxy internal error" + 异常字符串，定位
    direct-claude→https://x 全靠翻配置。base_url 不含 key，可安全外露给本机客户端。
    """
    logged: list[dict] = []
    monkeypatch.setattr("vision_relay.server.log_json", lambda d: logged.append(d))
    sock = __import__("socket").socket()
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()  # 立刻释放：此端口几乎必然连接拒绝

    cfg = ProxyConfig(
        bind_port=0,
        relays=[RelayConfig(name="direct-claude", protocol="anthropic", base_url=f"http://127.0.0.1:{dead_port}")],
        vlm=__import__("vision_relay.config", fromlist=["VLMConfig"]).VLMConfig(model="qwen-vl-max"),
    )
    pipe = Pipeline(NoopVLM(), __import__("vision_relay.cache", fromlist=["DescriptionCache"]).DescriptionCache())
    server = run_server(cfg)
    server.pipeline = pipe
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        resp = httpx.Client(trust_env=False).post(
            f"http://127.0.0.1:{server.server_address[1]}/v1/messages",
            json={"model": "m", "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]},
            timeout=30,
        )
        assert resp.status_code == 502
        assert "direct-claude" in resp.text  # 错误体带 relay 名
        assert f"127.0.0.1:{dead_port}" in resp.text  # 带上游地址
        err = next(e for e in logged if e.get("event") == "proxy_error")
        assert err["relay"] == "direct-claude"
        assert err["upstream"] == f"http://127.0.0.1:{dead_port}"
    finally:
        server.shutdown()


def test_data_plane_hot_reloads_config_on_change(tmp_path, monkeypatch, upstream):
    """数据面按 mtime 热加载 proxy.json（2026-08-26 复盘补位）。

    复盘背景：relay-set（停用转发/补 key）等控制面动词在独立进程写盘，运行中的
    服务此前永远用启动时的内存旧配置——即使选路尊重压制名单也看不见新名单。
    """
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    from conftest import RecordingUpstream

    from vision_relay.config import load_config, save_config

    up2 = RecordingUpstream().start()
    server = None
    try:
        cfg = ProxyConfig(
            bind_port=0,
            relays=[RelayConfig(name="r", protocol="chat", base_url=f"http://127.0.0.1:{upstream.port}")],
            vlm=__import__("vision_relay.config", fromlist=["VLMConfig"]).VLMConfig(model="qwen-vl-max"),
        )
        save_config(cfg)
        server = run_server(load_config())
        server.pipeline = Pipeline(
            NoopVLM(), __import__("vision_relay.cache", fromlist=["DescriptionCache"]).DescriptionCache()
        )
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        client = httpx.Client(trust_env=False)
        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        r1 = client.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=body, timeout=30)
        assert r1.status_code == 200
        assert len(upstream.received) == 1 and not up2.received  # 初始走 relay 指向的 up1
        # 控制面写盘换 relay 目标 → 下一请求必须走新目标（无需重启服务）
        cfg2 = load_config()
        cfg2.relays = [RelayConfig(name="r", protocol="chat", base_url=f"http://127.0.0.1:{up2.port}")]
        save_config(cfg2)
        r2 = client.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=body, timeout=30)
        assert r2.status_code == 200
        assert len(up2.received) == 1  # 热加载生效：同一 relay 名改指向后请求变道
    finally:
        if server is not None:
            server.shutdown()
        up2.stop()


def test_retention_once_removes_expired_and_keeps_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    from vision_relay import server
    from vision_relay.config import ProxyConfig, VisionLogConfig

    d = tmp_path / "visionlog"
    d.mkdir()
    (d / "2020-01-01.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    (d / "2999-01-01.jsonl").write_text('{"x":2}\n', encoding="utf-8")
    cfg = ProxyConfig(vision_log=VisionLogConfig(enabled=True, retention_days=7))
    server._retention_once(cfg)
    assert not (d / "2020-01-01.jsonl").exists()
    assert (d / "2999-01-01.jsonl").exists()


def test_retention_disabled_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    from vision_relay import server
    from vision_relay.config import ProxyConfig, VisionLogConfig

    d = tmp_path / "visionlog"
    d.mkdir()
    (d / "2020-01-01.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    server._retention_once(ProxyConfig(vision_log=VisionLogConfig(enabled=False)))
    assert (d / "2020-01-01.jsonl").exists()


def test_retention_worker_disabled_never_starts(monkeypatch):
    from vision_relay import server
    from vision_relay.config import ProxyConfig, VisionLogConfig

    called = []
    monkeypatch.setattr(server, "_retention_once", lambda cfg: called.append(1))
    server._start_retention_worker(ProxyConfig(vision_log=VisionLogConfig(enabled=False)))
    assert not called, "disabled 时不该有任何清理动作"


def test_retention_cleanup_scoped_to_snapshot_directory(tmp_path, monkeypatch):
    """终审 I4：cleanup 可显式指定目录；worker 起线程时快照目录，隔离还原后不触碰真实家目录。"""
    from vision_relay import server, visionlog
    from vision_relay.config import ProxyConfig, VisionLogConfig

    d = tmp_path / "snapshotted"
    d.mkdir()
    (d / "2020-01-01.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    # cleanup 指定 directory：只动该目录
    removed = visionlog.cleanup(7, directory=str(d))
    assert removed == 1 and not (d / "2020-01-01.jsonl").exists()

    # worker 快照：monkeypatch 环境 → 起线程前捕获目录 → 还原 env 后捕获值不变
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "live"))
    seen = {}
    real_dir = visionlog._dir
    monkeypatch.setattr(visionlog, "_dir", lambda: seen.setdefault("dir", real_dir()))
    monkeypatch.setattr(
        server.visionlog if hasattr(server, "visionlog") else visionlog,
        "cleanup",
        lambda days, directory=None: seen.setdefault("cleanup_dir", directory) or 0,
    )
    cfg = ProxyConfig(vision_log=VisionLogConfig(enabled=True))
    server._start_retention_worker(cfg)
    import time as _t

    for _ in range(50):  # 轮询等线程首次执行（防固定 sleep 的空过竞态）
        if "dir" in seen:
            break
        _t.sleep(0.05)
    monkeypatch.delenv("VISION_RELAY_CONFIG_DIR")
    assert seen.get("cleanup_dir") == seen.get("dir"), "worker 必须用起线程时快照的目录"
