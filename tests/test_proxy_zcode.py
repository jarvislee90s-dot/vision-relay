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


def _text_model():
    return {
        "limit": {"context": 1000000},
        "modalities": {"input": ["text"], "output": ["text"]},
        "zcode": {"modified": False},
    }


def _vision_model():
    return {"modalities": {"input": ["text", "image"], "output": ["text"]}}


class TestZcodeRewrite:
    def test_rewrite_takes_over_and_gates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        p = _write_zcode_config(
            tmp_path,
            {
                "k": _provider(
                    models={"GLM-5.2": _text_model(), "kimi": _vision_model()},
                ),
                "nokey": _provider(key="", models={"m": _text_model()}),
            },
        )
        proxy = "http://127.0.0.1:8787"
        urls, mods, stats = wiring._rewrite_zcode_providers(p, proxy)
        d = json.load(open(p, encoding="utf-8"))
        assert d["provider"]["k"]["options"]["baseURL"] == proxy
        assert d["provider"]["nokey"]["options"]["baseURL"] == "https://open.bigmodel.cn/api/anthropic"  # 空 key 不动
        glm = d["provider"]["k"]["models"]["GLM-5.2"]
        assert "image" in glm["modalities"]["input"]  # 纯文本模型开门
        assert glm["zcode"]["modalitiesConfigured"] is True  # 置位簿记标志
        kimi = d["provider"]["k"]["models"]["kimi"]
        assert "video" not in kimi["modalities"]["input"]  # 已开门的模型不动
        assert urls == {"k::anthropic": "https://open.bigmodel.cn/api/anthropic"}
        # flag 记哨兵：GLM 原无 modalitiesConfigured 字段，spec §5.1 要求「原本没有」与显式 False 可区分
        assert mods["k::anthropic::GLM-5.2"] == {"input": ["text"], "flag": wiring._MOD_ABSENT}
        assert "k::anthropic::kimi" not in mods  # 幂等：已开门不产生记录
        assert stats["skipped_nokey"] == 1 and stats["gated"] == 1

    def test_rewrite_idempotent_no_new_records(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        p = _write_zcode_config(tmp_path, {"k": _provider(models={"m": _text_model()})})
        proxy = "http://127.0.0.1:8787"
        wiring._rewrite_zcode_providers(p, proxy)
        urls2, mods2, _ = wiring._rewrite_zcode_providers(p, proxy)
        assert urls2 == {} and mods2 == {}  # 二次接管不产生新记录

    def test_rewrite_marks_timestamp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        p = _write_zcode_config(tmp_path, {"k": _provider()})
        wiring._rewrite_zcode_providers(p, "http://127.0.0.1:8787")
        assert wiring.zcode_rewrite_ts() > 0.0

    def test_missing_modality_input_skips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        p = _write_zcode_config(tmp_path, {"k": _provider(models={"m": {"limit": {}}})})
        _urls, _mods, stats = wiring._rewrite_zcode_providers(p, "http://127.0.0.1:8787")
        assert stats["skipped_mod"] == 1  # 形态不认识不硬造（spec §5.1）


class TestZcodeRestore:
    def _taken_over(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        p = _write_zcode_config(tmp_path, {"k": _provider(models={"GLM-5.2": _text_model()})})
        proxy = "http://127.0.0.1:8787"
        urls, mods, _ = wiring._rewrite_zcode_providers(p, proxy)
        return p, proxy, urls, mods

    def test_restore_reverts_url_and_gate(self, tmp_path, monkeypatch):
        p, proxy, urls, mods = self._taken_over(tmp_path, monkeypatch)
        n = wiring._restore_zcode_providers(p, proxy, urls, mods)
        d = json.load(open(p, encoding="utf-8"))
        glm = d["provider"]["k"]["models"]["GLM-5.2"]
        assert d["provider"]["k"]["options"]["baseURL"] == "https://open.bigmodel.cn/api/anthropic"
        assert glm["modalities"]["input"] == ["text"]
        assert "modalitiesConfigured" not in glm["zcode"]  # flag 原值缺失（哨兵）→ 还原删字段；本例 zcode 子对象存在
        assert n == 1

    def test_restore_skips_kind_changed_entry(self, tmp_path, monkeypatch):
        p, proxy, urls, mods = self._taken_over(tmp_path, monkeypatch)
        d = json.load(open(p, encoding="utf-8"))
        d["provider"]["k"]["kind"] = "openai"  # zcode 更新换了协议族 → 身份键不命中
        json.dump(d, open(p, "w", encoding="utf-8"))
        n = wiring._restore_zcode_providers(p, proxy, urls, mods)
        d2 = json.load(open(p, encoding="utf-8"))
        assert d2["provider"]["k"]["options"]["baseURL"] == proxy  # 原样保留，交给对账吸收
        assert n == 0

    def test_restore_skips_user_repointed_entry(self, tmp_path, monkeypatch):
        p, proxy, urls, mods = self._taken_over(tmp_path, monkeypatch)
        d = json.load(open(p, encoding="utf-8"))
        d["provider"]["k"]["options"]["baseURL"] = "https://user-changed.example"
        json.dump(d, open(p, "w", encoding="utf-8"))
        wiring._restore_zcode_providers(p, proxy, urls, mods)
        d2 = json.load(open(p, encoding="utf-8"))
        assert d2["provider"]["k"]["options"]["baseURL"] == "https://user-changed.example"

    def test_flag_true_restored(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        m = _text_model()
        m["zcode"]["modalitiesConfigured"] = True  # 原本就配置过
        p = _write_zcode_config(tmp_path, {"k": _provider(models={"m": m})})
        proxy = "http://127.0.0.1:8787"
        _urls, mods, _ = wiring._rewrite_zcode_providers(p, proxy)
        wiring._restore_zcode_providers(p, proxy, _urls, mods)
        d = json.load(open(p, encoding="utf-8"))
        assert d["provider"]["k"]["models"]["m"]["zcode"]["modalitiesConfigured"] is True

    def test_wiring_restore_harness_single(self, tmp_path, monkeypatch):
        """取消勾选单 harness 还原：与 stop 同一路径，只动指定 harness。"""
        p, proxy, urls, mods = self._taken_over(tmp_path, monkeypatch)
        from vision_relay import snapshot

        snapshot.save(
            "zcode",
            snapshot.Snapshot(
                base_url="https://open.bigmodel.cn/api/anthropic",
                key_ref="provider[].options.apiKey",
                model="",
                provider_urls=urls,
                provider_modalities=mods,
            ),
        )
        cfg = ProxyConfig()
        msgs = wiring.wiring_restore_harness(cfg, "zcode")
        d = json.load(open(p, encoding="utf-8"))
        assert d["provider"]["k"]["options"]["baseURL"] != proxy
        assert any("providers restored" in m for m in msgs)

    def test_stop_restores_zcode(self, tmp_path, monkeypatch):
        p, proxy, urls, mods = self._taken_over(tmp_path, monkeypatch)
        from vision_relay import snapshot

        snapshot.save(
            "zcode",
            snapshot.Snapshot(
                base_url="https://open.bigmodel.cn/api/anthropic",
                key_ref="provider[].options.apiKey",
                model="",
                provider_urls=urls,
                provider_modalities=mods,
            ),
        )
        cfg = ProxyConfig()
        cfg.routing.harnesses = ["zcode"]
        msgs = wiring.wiring_restore_on_stop(cfg)
        d = json.load(open(p, encoding="utf-8"))
        assert d["provider"]["k"]["options"]["baseURL"] == "https://open.bigmodel.cn/api/anthropic"
        assert any("providers restored" in m for m in msgs)
