"""tool dossiers (spec §5 路由工具档案): port probe + read-only active provider."""

import json
import socket
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from vision_relay import tools


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestDossiers:
    def test_registry_covers_both_tools_with_harness_matrix(self):
        assert tools.TOOL_DOSSIERS["cc-switch"].harnesses == ("claude", "codex")
        assert tools.TOOL_DOSSIERS["codex-plus"].harnesses == ("codex",)

    def test_relay_template_per_harness(self):
        assert tools.relay_template("cc-switch", "claude")["protocol"] == "anthropic"
        assert tools.relay_template("cc-switch", "codex")["protocol"] == "chat"
        assert tools.relay_template("codex-plus", "codex")["protocol"] == "responses"
        assert tools.relay_template("cc-switch", "qwen-code") is None  # 不支持的工具-harness 组合


class TestPortProbe:
    def test_online_when_listening(self):
        port = _free_port()
        srv = HTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            states = tools.probe_tools(port_overrides={"cc-switch": port})
            cc = next(s for s in states if s.name == "cc-switch")
            assert cc.online is True
            assert cc.port == port
        finally:
            srv.shutdown()

    def test_offline_when_closed(self):
        port = _free_port()  # bound then closed -> nothing listens
        states = tools.probe_tools(port_overrides={"cc-switch": port})
        cc = next(s for s in states if s.name == "cc-switch")
        assert cc.online is False
        assert cc.active_provider is None and cc.provider_base_url is None


class TestCodexPlusProvider:
    def test_reads_active_relay_profile(self, tmp_path, monkeypatch):
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "activeRelayId": "relay-b",
                    "relayProfiles": [
                        {"id": "relay-a", "name": "Old", "baseUrl": "https://a.example"},
                        {"id": "relay-b", "name": "deepseek", "baseUrl": "https://api.deepseek.com"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(tools, "CODEXPP_SETTINGS", str(settings))
        name, url = tools._codexpp_active_provider()
        assert name == "deepseek" and url == "https://api.deepseek.com"

    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tools, "CODEXPP_SETTINGS", str(tmp_path / "nope.json"))
        assert tools._codexpp_active_provider() == (None, None)

    def test_no_base_url_still_returns_name_empty_url(self, tmp_path, monkeypatch):
        """M3：档案没填上游地址的激活供应商不隐身——名字照常返回，地址为空串（GUI 占位「未接线」）。"""
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps({"activeRelayId": "r1", "relayProfiles": [{"id": "r1", "name": "naked"}]}),
            encoding="utf-8",
        )
        monkeypatch.setattr(tools, "CODEXPP_SETTINGS", str(settings))
        assert tools._codexpp_active_provider() == ("naked", "")

    def test_reads_upstream_base_url_before_base_url(self, tmp_path, monkeypatch):
        """与 model_sources.codexpp_matrix 同一白名单读法：upstreamBaseUrl 优先，而非只看 baseUrl。"""
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "activeRelayId": "r1",
                    "relayProfiles": [{"id": "r1", "name": "up", "upstreamBaseUrl": "https://up.example"}],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(tools, "CODEXPP_SETTINGS", str(settings))
        assert tools._codexpp_active_provider() == ("up", "https://up.example")


class TestCcSwitchProvider:
    def test_status_endpoint_fallback(self, monkeypatch):
        class _Resp:
            status_code = 200

            def json(self):
                return {"current_providers": {"claude": {"name": "bigmodel", "baseUrl": "https://open.bigmodel.cn"}}}

        monkeypatch.setattr(tools.httpx, "get", lambda *a, **k: _Resp(), raising=False)
        monkeypatch.setattr(tools, "_ccswitch_sqlite_provider", lambda: None)
        name, url = tools._ccswitch_active_provider(port=15721)
        assert name == "bigmodel" and url == "https://open.bigmodel.cn"

    def test_sqlite_before_http(self, monkeypatch):
        monkeypatch.setattr(tools, "_ccswitch_sqlite_provider", lambda: ("from-db", "https://db.example"))
        name, url = tools._ccswitch_active_provider(port=15721)
        assert (name, url) == ("from-db", "https://db.example")

    def test_sqlite_hit_with_empty_url_short_circuits(self, monkeypatch):
        """M3 配套：sqlite 命中（名字在、地址空）不得再落到 HTTP 探测——供应商以「未接线」呈现。"""

        def _no_http(*a, **k):
            raise AssertionError("sqlite 已命中，不应再打 HTTP /status")

        monkeypatch.setattr(tools, "_ccswitch_sqlite_provider", lambda: ("prov-x", ""))
        monkeypatch.setattr(tools.httpx, "get", _no_http, raising=False)
        assert tools._ccswitch_active_provider(port=15721) == ("prov-x", "")

    def test_sqlite_provider_id_without_base_url_not_dropped(self, tmp_path, monkeypatch):
        """M3：settings 值是 provider id 且该档案没填 base_url → 仍返回命中（M3 修复点：
        旧代码 `if url:` 把整家供应商丢掉，status 工具统计/GUI 里完全隐身）。"""
        db = tmp_path / "cc-switch.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE providers (id TEXT PRIMARY KEY, settings_config TEXT)")
        conn.execute("INSERT INTO settings VALUES ('current:claude', ?)", (json.dumps("prov-x"),))
        conn.execute(
            "INSERT INTO providers VALUES ('prov-x', ?)",
            (json.dumps({"env": {"ANTHROPIC_BASE_URL": ""}}),),
        )
        conn.commit()
        conn.close()
        monkeypatch.setattr(tools, "CCSWITCH_DB", str(db))
        assert tools._ccswitch_sqlite_provider() == ("prov-x", "")
