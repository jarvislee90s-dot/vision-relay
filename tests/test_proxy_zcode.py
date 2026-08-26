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


class TestZcodeRelays:
    def test_one_relay_per_provider_with_fingerprint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_zcode_config(
            tmp_path,
            {
                "builtin:bigmodel": _provider(
                    enabled=True, models={"GLM-5-Turbo": {"name": "glm-5-turbo", "modalities": {"input": ["text"]}}}
                ),
                "ark": _provider(
                    url="https://ark.cn-beijing.volces.com/api/coding/v3",
                    key="ark-xyz1234567890",
                    kind="openai",
                    enabled=False,
                    models={"DeepSeek-V4-Flash": {"name": "deepseek-v4-flash"}},
                ),
            },
        )
        cfg = ProxyConfig()
        added = wiring.ensure_zcode_relays(cfg)
        zs = [r for r in cfg.relays if r.provider_id]
        assert len(zs) == 2
        assert zs[0].provider_id == "builtin:bigmodel"  # 激活供应商排最前
        ark = next(r for r in zs if r.provider_id == "ark")
        assert ark.protocol == "chat"  # openai kind → chat
        assert zs[0].protocol == "anthropic"
        assert set(zs[0].models) == {"GLM-5-Turbo", "glm-5-turbo"}  # 双名收录
        assert ark.base_url == "https://ark.cn-beijing.volces.com/api/coding/v3"
        assert len(zs[0].auth_hints) == 1 and zs[0].auth_hints[0].startswith("k-12")  # 指纹形态
        assert zs[0].api_key == ""  # 鉴权透传
        assert set(added) == {r.name for r in zs}

    def test_loopback_original_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_zcode_config(tmp_path, {"k": _provider(url="http://127.0.0.1:15721")})  # cc-switch 端口
        cfg = ProxyConfig()
        assert wiring.ensure_zcode_relays(cfg) == []
        assert not [r for r in cfg.relays if r.provider_id]

    def test_reorder_enabled_first_and_cleanup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_zcode_config(
            tmp_path,
            {
                "a": _provider(url="https://a.example", enabled=False),
                "b": _provider(url="https://b.example", enabled=True),
            },
        )
        cfg = ProxyConfig()
        wiring.ensure_zcode_relays(cfg)
        _write_zcode_config(  # 用户切换激活供应商 a
            tmp_path,
            {
                "a": _provider(url="https://a.example", enabled=True),
                "b": _provider(url="https://b.example", enabled=False),
            },
        )
        wiring.ensure_zcode_relays(cfg)
        zs = [r for r in cfg.relays if r.provider_id]
        assert [r.provider_id for r in zs] == ["a", "b"]  # 激活优先重排序
        _write_zcode_config(tmp_path, {"a": _provider(url="https://a.example", enabled=True)})  # b 消失
        wiring.ensure_zcode_relays(cfg)
        assert [r.provider_id for r in cfg.relays if r.provider_id] == ["a"]  # 清理

    def test_backup_and_rewrite_wires_zcode_and_snapshots(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        p = _write_zcode_config(tmp_path, {"k": _provider(models={"m": _text_model()})})
        cfg = ProxyConfig()
        cfg.routing.harnesses = ["zcode"]
        msgs = wiring.wiring_backup_and_rewrite(cfg)
        d = json.load(open(p, encoding="utf-8"))
        assert d["provider"]["k"]["options"]["baseURL"] == "http://127.0.0.1:8787"
        from vision_relay import snapshot

        snap = snapshot.load()["zcode"]
        assert snap.provider_urls == {"k::anthropic": "https://open.bigmodel.cn/api/anthropic"}
        assert snap.second_hop is None
        assert any("zcode: providers" in m for m in msgs)
        assert any(r.provider_id == "k" for r in cfg.relays)  # 接管顺带建 relay

    def test_reconcile_zcode_providers_absorbs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        p = _write_zcode_config(tmp_path, {"k": _provider()})
        cfg = ProxyConfig()
        cfg.routing.harnesses = ["zcode"]
        wiring.wiring_backup_and_rewrite(cfg)
        # 模拟 zcode 运行期回写：改走新上游
        d = json.load(open(p, encoding="utf-8"))
        d["provider"]["k"]["options"]["baseURL"] = "https://new.example/api"
        json.dump(d, open(p, "w", encoding="utf-8"))
        res = wiring.reconcile_zcode_providers(cfg)
        assert res and res["rewritten"] == 1
        from vision_relay import snapshot

        merged = snapshot.load()["zcode"].provider_urls
        assert merged["k::anthropic"] == "https://new.example/api"  # 新原值吸收


class TestZcodeProc:
    def test_find_parses_batched_details(self, monkeypatch):
        """评审⑦：Windows 枚举收敢单次 PowerShell（JSON 三元组），不再逐 pid 双查询。"""
        from vision_relay import zcode_proc

        calls: list[list[str]] = []

        def fake_run(cmd, timeout=3.0):
            calls.append(cmd)
            return '[{"id":4242,"ts":100,"exe":"C:/zcode.exe"}]'

        monkeypatch.setattr(zcode_proc, "_run", fake_run)
        procs = zcode_proc.find_zcode_processes(force=True)
        assert procs == [{"pid": 4242, "start_ts": 100.0, "exe": "C:/zcode.exe"}]
        powershell_calls = [c for c in calls if c and c[0] == "powershell"]
        assert len(powershell_calls) == 1  # 单次批量，而非每进程 2 次

    def test_find_skips_relay_self(self, monkeypatch):
        from vision_relay import zcode_proc

        monkeypatch.setattr(
            zcode_proc,
            "_run",
            lambda cmd, timeout=3.0: (
                '[{"id":1,"ts":5,"exe":"C:\\\\tools\\\\zcode-relay.exe"},{"id":2,"ts":6,"exe":"C:/zcode.exe"}]'
            ),
        )
        procs = zcode_proc.find_zcode_processes(force=True)
        assert [p["pid"] for p in procs] == [2]  # 本代理自身不误报

    def test_needs_restart_logic(self, monkeypatch):
        from vision_relay import zcode_proc

        monkeypatch.setattr(
            zcode_proc, "find_zcode_processes", lambda force=False: [{"pid": 1, "start_ts": 100.0, "exe": "x"}]
        )
        assert zcode_proc.zcode_needs_restart(200.0) is True  # 启动早于改写
        assert zcode_proc.zcode_needs_restart(50.0) is False  # 改写后已重启
        assert zcode_proc.zcode_needs_restart(0.0) is False  # 无改写记录

    def test_needs_restart_not_running(self, monkeypatch):
        from vision_relay import zcode_proc

        monkeypatch.setattr(zcode_proc, "find_zcode_processes", lambda force=False: [])
        assert zcode_proc.zcode_needs_restart(999.0) is False

    def test_restart_kills_and_relaunches(self, monkeypatch, tmp_path):
        from vision_relay import zcode_proc

        exe = tmp_path / "zcode.exe"
        exe.write_bytes(b"x")
        launched: list[list[str]] = []
        monkeypatch.setattr(
            zcode_proc,
            "find_zcode_processes",
            lambda force=False: [{"pid": 7, "start_ts": 0.0, "exe": str(exe)}],
        )
        monkeypatch.setattr(zcode_proc, "_run", lambda cmd, timeout=3.0: launched.append(cmd) or "")
        monkeypatch.setattr(
            zcode_proc.subprocess,
            "Popen",
            lambda argv, **kw: launched.append(argv),
        )
        assert zcode_proc.restart_zcode() is True
        assert any(
            "kill" in " ".join(map(str, c)).lower() or "taskkill" in " ".join(map(str, c)).lower() for c in launched
        )
        assert launched[-1] == [str(exe)]  # 最后拉起 exe

    def test_restart_no_process_returns_false(self, monkeypatch):
        from vision_relay import zcode_proc

        monkeypatch.setattr(zcode_proc, "find_zcode_processes", lambda force=False: [])
        assert zcode_proc.restart_zcode() is False


class TestSettingsSetHarnesses:
    def _stdin(self, monkeypatch, payload):
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    def test_harnesses_update_and_uncheck_restores(self, tmp_path, monkeypatch):
        from vision_relay import snapshot, verbs

        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        p = _write_zcode_config(tmp_path, {"k": _provider(models={"m": _text_model()})})
        cfg = ProxyConfig()
        cfg.routing.harnesses = ["zcode"]
        wiring.wiring_backup_and_rewrite(cfg)
        assert json.load(open(p, encoding="utf-8"))["provider"]["k"]["options"]["baseURL"] == "http://127.0.0.1:8787"
        snap = snapshot.load()["zcode"]

        self._stdin(monkeypatch, {"routing": {"harnesses": ["claude", "codex", "qwen-code"]}})
        out = verbs.settings_set(cfg)
        assert out["ok"] is True
        d = json.load(open(p, encoding="utf-8"))
        assert d["provider"]["k"]["options"]["baseURL"] == "https://open.bigmodel.cn/api/anthropic"  # 取消即还原
        assert "restored" in out["data"] and any("zcode" in m for m in out["data"]["restored"])
        assert cfg.routing.harnesses == ["claude", "codex", "qwen-code"]
        assert snap.provider_urls  # 快照在还原前已存在（守卫还原的依据）

    def test_unknown_harness_rejected(self, monkeypatch):
        from vision_relay import verbs
        from vision_relay.config import ProxyConfig as PC

        cfg = PC()
        self._stdin(monkeypatch, {"routing": {"harnesses": ["claude", "nope"]}})
        out = verbs.settings_set(cfg)
        assert out["ok"] is False and "unknown" in out["data"]["error"]

    def test_empty_or_duplicate_rejected(self, monkeypatch):
        from vision_relay import verbs
        from vision_relay.config import ProxyConfig as PC

        cfg = PC()
        self._stdin(monkeypatch, {"routing": {"harnesses": []}})
        assert verbs.settings_set(cfg)["ok"] is False
        self._stdin(monkeypatch, {"routing": {"harnesses": ["claude", "claude"]}})
        assert verbs.settings_set(cfg)["ok"] is False

    def test_zcode_uncheck_reports_needs_restart(self, tmp_path, monkeypatch):
        from vision_relay import verbs, zcode_proc

        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_zcode_config(tmp_path, {"k": _provider()})
        cfg = ProxyConfig()
        cfg.routing.harnesses = ["zcode", "claude"]
        monkeypatch.setattr(
            zcode_proc, "find_zcode_processes", lambda force=False: [{"pid": 1, "start_ts": 0.0, "exe": ""}]
        )
        self._stdin(monkeypatch, {"routing": {"harnesses": ["claude"]}})
        out = verbs.settings_set(cfg)
        assert out["data"].get("needs_zcode_restart") is True

    def test_uncheck_removes_zcode_relays(self, tmp_path, monkeypatch):
        """评审④：取消勾选 zcode 时，其一层直连 relay 同步移除并清出 activated_relays。"""
        from vision_relay import verbs

        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_zcode_config(tmp_path, {"k": _provider(models={"m": _text_model()})})
        cfg = ProxyConfig()
        cfg.routing.harnesses = ["zcode"]
        wiring.wiring_backup_and_rewrite(cfg)
        assert [r for r in cfg.relays if r.provider_id]  # 前置：接管已建 relay
        self._stdin(monkeypatch, {"routing": {"harnesses": ["claude"]}})
        out = verbs.settings_set(cfg)
        assert out["ok"] is True
        assert not [r for r in cfg.relays if getattr(r, "provider_id", None)]
        assert not [r for r in cfg.relays if r.name.startswith("zcode-")]
        assert not [n for n in cfg.routing.activated_relays if n.startswith("zcode-")]


class TestZcodeRelayRotation:
    def test_ensure_rebuilds_on_key_rotation(self, tmp_path, monkeypatch):
        """评审⑤：用户换 key 后指纹必须跟随现场更新，不得静默过期。"""
        from vision_relay.fingerprint import key_fingerprint

        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_zcode_config(tmp_path, {"k": _provider(key="k-old1234567890")})
        cfg = ProxyConfig()
        assert wiring.ensure_zcode_relays(cfg)  # 首建
        assert cfg.relays[0].auth_hints == [key_fingerprint("k-old1234567890")]
        _write_zcode_config(tmp_path, {"k": _provider(key="k-new1234567890")})  # 密钥轮换
        assert wiring.ensure_zcode_relays(cfg) == []  # 成员/地址未变 → 无「新增」
        assert cfg.relays[0].auth_hints == [key_fingerprint("k-new1234567890")]  # 但指纹已重建


class TestZcodeZombieReconcile:
    def test_dead_service_wired_entries_trigger_restart(self, tmp_path, monkeypatch):
        """评审⑥：激活供应商是空 key 未接管者（全局 owner≠ours）时，僵尸接线判定改用
        条目级信号——有已接线供应商就按崩溃前意图修复，不留断头接管。"""
        from vision_relay import reconcile

        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        # 激活供应商空 key 直连别处；另一家已接管（baseURL=本代理）
        _write_zcode_config(
            tmp_path,
            {
                "idle": _provider(key="", url="https://elsewhere.example"),
                "wired": _provider(url="http://127.0.0.1:8787", enabled=False),
            },
        )
        from vision_relay.config import ProxyConfig as PC

        cfg = PC()
        cfg.routing.harnesses = ["zcode"]
        monkeypatch.setattr(reconcile.tools, "probe_tools", lambda: [])
        monkeypatch.setattr(reconcile, "_service_alive", lambda c: False)
        monkeypatch.setattr(reconcile, "get_routing_on", lambda: True)
        monkeypatch.setattr(reconcile, "_clear_stale_pid", lambda: None)
        monkeypatch.setattr(reconcile, "_restart_service", lambda c: True)
        res = reconcile.reconcile(cfg)
        assert any(a["type"] == "auto_fix" and a.get("fix") == "restart" for a in res["actions"])


def test_probe_reason_zcode_accurate(monkeypatch):
    """M7: zcode 无目标的 reason 不能再说「路由工具不在线」——zcode 没有路由工具概念。"""
    from vision_relay import model_sources, verbs
    from vision_relay.config import ProxyConfig

    monkeypatch.setattr(model_sources, "zcode_probe_target", lambda cfg, provider: ("", "", "chat"))
    _base, _key, _proto, reason = verbs.probe_target_info(ProxyConfig(), "zcode", "ghost-provider", {})
    assert reason is not None and reason.startswith("zcode:")
    assert "路由工具" not in reason
