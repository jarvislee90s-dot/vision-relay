"""zcode harness: fingerprint / wiring / relay / matrix / verbs（spec 2026-08-26）。"""

import json
import os


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
