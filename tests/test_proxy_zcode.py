"""zcode harness: fingerprint / wiring / relay / matrix / verbs（spec 2026-08-26）。"""

import json
import os

from vision_relay import wiring
from vision_relay.config import ProxyConfig


def _write_zcode_config(home, providers: dict) -> str:
    p = os.path.join(str(home), ".zcode", "v2", "config.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"provider": providers}, f, ensure_ascii=False, indent=2)
    return p


def _provider(
    url="https://open.bigmodel.cn/api/anthropic", key="k-1234567890abcdef", kind="anthropic", enabled=True, models=None
):
    return {
        "name": "P",
        "kind": kind,
        "options": {"apiKey": key, "baseURL": url},
        "enabled": enabled,
        "models": models if models is not None else {},
    }


class TestFingerprint:
    def test_shape(self):
        from vision_relay.fingerprint import key_fingerprint

        fp = key_fingerprint("963b01ba64764f1f86e4f53223f0df2c.4bGDCSCynWRi9NVz")
        # 样例键实际长 49；计划原文 @51 为笔误（与 short@真实长度 语义矛盾），按 spec 语义修正。
        assert fp.startswith("963b") and fp.endswith("9NVz@49")
        assert "…" in fp

    def test_short_key_hides_chars(self):
        from vision_relay.fingerprint import key_fingerprint

        assert key_fingerprint("abc") == "short@3"
        assert key_fingerprint("") == "short@0"

    def test_from_headers_bearer_and_xapikey(self):
        from vision_relay.fingerprint import fingerprint_from_headers, key_fingerprint

        bearer = fingerprint_from_headers({"Authorization": "Bearer sk-abcdefgh1234"})
        assert bearer == key_fingerprint("sk-abcdefgh1234")
        assert fingerprint_from_headers({"x-api-key": "sk-abcdefgh1234"}) == key_fingerprint("sk-abcdefgh1234")
        assert fingerprint_from_headers({}) is None
        assert fingerprint_from_headers({"Authorization": ""}) is None


class TestZcodeRegistration:
    def test_harness_cfg_registered(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        p = _write_zcode_config(tmp_path, {"builtin:bigmodel": _provider(enabled=True)})
        h = wiring.HARNESS_CFG["zcode"]
        assert h.kind == "zcode-v2"
        assert wiring._path(str(tmp_path), "zcode") == p

    def test_read_base_url_returns_enabled_provider(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        _write_zcode_config(
            tmp_path,
            {
                "a": _provider(url="https://a.example", enabled=False),
                "b": _provider(url="https://b.example", enabled=True),
            },
        )
        p = wiring._path(str(tmp_path), "zcode")
        assert wiring.read_base_url(p, wiring.HARNESS_CFG["zcode"]) == "https://b.example"

    def test_entries_admission(self):
        d = {
            "provider": {
                "k": _provider(),  # 可接管
                "nokey": _provider(key=""),  # 空 key 跳过
                "badkind": _provider(kind="gemini"),  # 未知 kind 跳过
                "nourl": _provider(url=""),  # 无 URL 跳过
            }
        }
        items, nokey, badkind = wiring._zcode_entries(d)
        assert [pid for pid, _k, _e in items] == ["k"]
        assert nokey == 1 and badkind == 1

    def test_stats_counts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        proxy = "http://127.0.0.1:8787"
        _write_zcode_config(
            tmp_path,
            {
                "wired": _provider(
                    url=proxy, models={"m": {"modalities": {"input": ["text", "image"], "output": ["text"]}}}
                ),
                "nowired": _provider(url="https://x.example"),
                "nokey": _provider(key="", url="https://y.example"),
            },
        )
        stats = wiring._zcode_provider_stats(wiring._path(str(tmp_path), "zcode"), proxy)
        # gated=2：nowired 虽未接线但无模型清单，按 _zcode_provider_gated「无模型视为 True」计入门已开
        assert stats == {"total": 3, "eligible": 2, "wired": 1, "gated": 2, "skipped_nokey": 1, "skipped_kind": 0}

    def test_snapshot_key_ref_for_zcode(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        from vision_relay import snapshot

        monkeypatch.setattr(snapshot, "HOME", str(tmp_path))  # 既有约定：snapshot 有独立 HOME，需一并隔离
        assert snapshot.key_ref_for("zcode") == "not-found"
        _write_zcode_config(tmp_path, {"k": _provider()})
        assert snapshot.key_ref_for("zcode") == "provider[].options.apiKey"
