"""model_sources: 工具档案只读层(spec §5 模型矩阵来源)。密钥绝不读取。"""

import json
import sqlite3

from vision_relay import model_sources as ms


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


def test_ccswitch_matrix_claude_env_models(tmp_path, monkeypatch):
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
                            "ANTHROPIC_BASE_URL": "https://ark.cn-beijing.volces.com/api/coding",
                            "ANTHROPIC_AUTH_TOKEN": "ark-secret",  # 必须被忽略
                            "ANTHROPIC_DEFAULT_SONNET_MODEL": "MiniMax-M3",
                            "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "MiniMax-M3",  # 同模型去重
                            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "Kimi-K2.7-Code",
                            "ANTHROPIC_MODEL": "deepseek-v4-flash",
                        },
                        "model": "fable",
                    }
                ),
                1,
                0,
            )
        ],
    )
    monkeypatch.setattr(ms, "CCSWITCH_DB", str(db))
    rows = ms.ccswitch_matrix()["claude"]
    assert len(rows) == 1
    r = rows[0]
    assert r.provider == "火山Ark" and r.is_current is True
    assert r.base_url == "https://ark.cn-beijing.volces.com/api/coding"
    assert r.models == ["fable", "MiniMax-M3", "Kimi-K2.7-Code", "deepseek-v4-flash"]  # 去重、保序
    assert not any("secret" in m or "ark-" in m for m in r.models)


def test_ccswitch_matrix_codex_toml_and_catalog(tmp_path, monkeypatch):
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
                        '[model_providers.custom]\nname = "kimi_coding"\n'
                        'base_url = "https://api.kimi.com/coding/v1"\nwire_api = "responses"\n',
                        "modelCatalog": {"models": [{"model": "kimi-for-coding"}, {"model": "k2.7-code"}]},
                    }
                ),
                0,
                1,
            )
        ],
    )
    monkeypatch.setattr(ms, "CCSWITCH_DB", str(db))
    rows = ms.ccswitch_matrix()["codex"]
    r = rows[0]
    assert r.models == ["kimi-for-coding", "k2.7-code"]  # config.model + modelCatalog,去重
    assert r.base_url == "https://api.kimi.com/coding/v1"


def test_ccswitch_matrix_missing_or_corrupt_db_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "CCSWITCH_DB", str(tmp_path / "nope.db"))
    assert ms.ccswitch_matrix() == {}
    bad = tmp_path / "bad.db"
    bad.write_text("not a sqlite file")
    monkeypatch.setattr(ms, "CCSWITCH_DB", str(bad))
    assert ms.ccswitch_matrix() == {}


def test_ccswitch_matrix_ignores_other_app_types(tmp_path, monkeypatch):
    db = tmp_path / "cc-switch.db"
    _mk_db(db, [("g1", "gemini", "Google", "{}", 1, 0), ("h1", "hermes", "x", "{}", 0, 1)])
    monkeypatch.setattr(ms, "CCSWITCH_DB", str(db))
    assert ms.ccswitch_matrix() == {}


# ── Task 2:codex++ / 域名推导 / 编排 ──────────────────────────────


def test_codexpp_matrix_reads_profiles(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text(
        json.dumps(
            {
                "activeRelayId": "relay-mt7bt7s3",
                "relayProfiles": [
                    {
                        "id": "relay-mt7bt7s3",
                        "name": "Openrouter",
                        "upstreamBaseUrl": "https://openrouter.ai/api/v1",
                        "modelList": "stealth/ox-alpha\ndeepseek/deepseek-v4-pro",
                        "relayApiKey": "sk-or-secret",  # 必须被忽略
                        "modelVlm": '{"stealth/ox-alpha":"vlm"}',  # 用户裁决:禁用,不读
                    },
                    {
                        "id": "relay-mq92h08y",
                        "name": "opencode",
                        "upstreamBaseUrl": "https://opencode.ai/zen/go/v1",
                        "modelList": "deepseek-v4-flash\nminimax-m3",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ms, "CODEXPP_SETTINGS", str(f))
    rows = ms.codexpp_matrix()
    assert [(r.provider, r.is_current) for r in rows] == [("Openrouter", True), ("opencode", False)]
    assert rows[0].models == ["stealth/ox-alpha", "deepseek/deepseek-v4-pro"]
    assert rows[0].base_url == "https://openrouter.ai/api/v1"
    assert all(r.tool == "codex-plus" and r.harness == "codex" for r in rows)


def test_codexpp_matrix_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "CODEXPP_SETTINGS", str(tmp_path / "nope.json"))
    assert ms.codexpp_matrix() == []


def test_provider_from_url_known_map_and_fallback():
    assert ms.provider_from_url("https://ark.cn-beijing.volces.com/api/coding") == "volces-ark"
    assert ms.provider_from_url("https://coding.dashscope.aliyuncs.com/apps/anthropic") == "dashscope"
    assert ms.provider_from_url("https://openrouter.ai/api/v1") == "openrouter"
    assert ms.provider_from_url("https://origin.example/api") == "origin.example"  # 未知域名→主机名
    assert ms.provider_from_url("http://127.0.0.1:8787") is None
    assert ms.provider_from_url("http://localhost:15721") is None
    assert ms.provider_from_url("not a url") is None


def test_direct_provider_url_prefers_live_then_snapshot(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://live.example/api"}}), encoding="utf-8"
    )
    import vision_relay.wiring as W

    monkeypatch.setattr(W, "HOME", str(home))
    assert ms.direct_provider_url("claude") == "https://live.example/api"
    # live 是回环(已接线到代理)→ 退 snapshot 的接管前原始值
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8787"}}), encoding="utf-8"
    )
    import vision_relay.snapshot as S

    snap_dir = tmp_path / "cfg"
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(snap_dir))
    S._path  # noqa: B018 - 确认可导入
    (snap_dir).mkdir(exist_ok=True)
    (snap_dir / "snapshots.json").write_text(
        json.dumps(
            {
                "claude": {
                    "base_url": "https://snap.example/api",
                    "key_ref": "env.ANTHROPIC_AUTH_TOKEN",
                    "model": "m",
                    "second_hop": None,
                    "ts": 1,
                }
            }
        ),
        encoding="utf-8",
    )
    assert ms.direct_provider_url("claude") == "https://snap.example/api"


def test_resolve_probe_key_env_ref(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "ark-tok-123"}}), encoding="utf-8"
    )
    import vision_relay.wiring as W

    monkeypatch.setattr(W, "HOME", str(home))
    assert ms.resolve_probe_key("claude", "env.ANTHROPIC_AUTH_TOKEN") == "ark-tok-123"
    assert ms.resolve_probe_key("claude", None) == ""
    assert ms.resolve_probe_key("claude", "env.NOPE") == ""


def test_harness_matrix_direct_fallback_and_current_provider(tmp_path, monkeypatch):
    from vision_relay.config import ProxyConfig

    monkeypatch.setattr(ms, "CCSWITCH_DB", str(tmp_path / "nope.db"))
    monkeypatch.setattr(ms, "CODEXPP_SETTINGS", str(tmp_path / "nope.json"))
    home = tmp_path / "home"
    (home / ".qwen").mkdir(parents=True)
    (home / ".qwen" / "settings.json").write_text(
        json.dumps({"model": {"baseUrl": "https://dashscope.aliyuncs.com/x", "model": "qwen3-coder"}}),
        encoding="utf-8",
    )
    import vision_relay.wiring as W

    monkeypatch.setattr(W, "HOME", str(home))
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
    cfg = ProxyConfig()
    matrix = ms.harness_matrix(cfg)
    assert matrix["qwen-code"][0].provider == "dashscope"
    assert matrix["qwen-code"][0].models == ["qwen3-coder"]
    assert matrix["qwen-code"][0].is_current is True
    assert matrix["claude"][0].provider == "?"  # 无工具、无 harness 配置 → 直连未知
    assert ms.current_provider(cfg, "claude") == "?"


class TestZcodeMatrix:
    def _setup(self, tmp_path, monkeypatch, providers):
        from vision_relay import wiring

        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        p = tmp_path / ".zcode" / "v2" / "config.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"provider": providers}), encoding="utf-8")
        return wiring

    def test_matrix_rows_and_empty_key_excluded(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path,
            monkeypatch,
            {
                "builtin:bigmodel": {
                    "name": "B",
                    "kind": "anthropic",
                    "options": {"apiKey": "k-1234567890", "baseURL": "https://b.example"},
                    "enabled": True,
                    "models": {"GLM-5-Turbo": {"name": "glm-5-turbo"}, "GLM-5.3": {}},
                },
                "nokey": {
                    "name": "N",
                    "kind": "anthropic",
                    "options": {"apiKey": "", "baseURL": "https://n.example"},
                    "enabled": False,
                    "models": {"m": {}},
                },
            },
        )
        from vision_relay.config import ProxyConfig

        rows = ms.zcode_matrix(ProxyConfig())
        assert len(rows) == 1  # 空 key 供应商整行不产
        r = rows[0]
        assert r.provider == "builtin:bigmodel" and r.is_current is True
        assert r.models == ["glm-5-turbo", "GLM-5.3"]  # API 名（name 优先）
        assert r.tool == "zcode" and r.harness == "zcode"

    def test_harness_matrix_has_zcode_no_direct_fallback(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path,
            monkeypatch,
            {"k": {"kind": "anthropic", "options": {"apiKey": "k", "baseURL": "https://x"}, "models": {"m": {}}}},
        )
        from vision_relay.config import ProxyConfig

        cfg = ProxyConfig()
        cfg.routing.harnesses = ["zcode"]
        out = ms.harness_matrix(cfg)
        assert len(out["zcode"]) == 1 and out["zcode"][0].tool == "zcode"

    def test_probe_target_uses_snapshot_original(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path,
            monkeypatch,
            {
                "k": {
                    "kind": "openai",
                    "options": {"apiKey": "sk-abcdefgh", "baseURL": "http://127.0.0.1:8787"},
                    "models": {},
                }
            },
        )
        from vision_relay import snapshot
        from vision_relay.config import ProxyConfig

        snapshot.save(
            "zcode",
            snapshot.Snapshot(
                base_url="x",
                key_ref="provider[].options.apiKey",
                model="",
                provider_urls={"k::openai": "https://real.example/v1"},
            ),
        )
        base, key, proto = ms.zcode_probe_target(ProxyConfig(), "k")
        assert (base, key, proto) == ("https://real.example/v1", "sk-abcdefgh", "chat")
