"""tool dossiers (spec §5 路由工具档案): port probe + read-only active provider."""

import json
import socket
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
