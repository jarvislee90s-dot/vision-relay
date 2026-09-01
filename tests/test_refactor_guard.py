"""重构守护测试（C1 点名路径）：
① legacy .qwen-mm-proxy.bak 迁移 ② zcode 条目级配置读取与还原 ③ stop 回滚时序。

全部经 monkeypatch wiring.HOME + VISION_RELAY_CONFIG_DIR 沙箱隔离，不触碰真实 ~。
"""

from __future__ import annotations

import json

import pytest

import vision_relay.wiring as wiring
from vision_relay import snapshot
from vision_relay.config import ProxyConfig, RoutingConfig

PROXY = "http://127.0.0.1:8787"


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(wiring, "HOME", str(home))
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
    return home


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, str):
        path.write_text(obj, encoding="utf-8")
    else:
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _cfg(*harnesses: str) -> ProxyConfig:
    return ProxyConfig(bind_port=8787, routing=RoutingConfig(harnesses=list(harnesses)))


# ── C1-① legacy .qwen-mm-proxy.bak 迁移 ──────────────────────────────
class TestLegacyBakMigration:
    def test_restore_prefers_new_suffix_when_both_exist(self, fake_home):
        """新旧备份并存时新后缀优先（_find_bak 先查 .vision-relay.bak）；未选中的旧备份保留。"""
        f = _write(fake_home / ".claude" / "settings.json", {"env": {"ANTHROPIC_BASE_URL": PROXY}})
        _write(
            fake_home / ".claude" / "settings.json.vision-relay.bak",
            {"env": {"ANTHROPIC_BASE_URL": "https://new.example"}},
        )
        _write(
            fake_home / ".claude" / "settings.json.qwen-mm-proxy.bak",
            {"env": {"ANTHROPIC_BASE_URL": "https://old.example"}},
        )
        msg = wiring.wiring_restore(_cfg("claude"))
        assert any("claude: restored" in m for m in msg)
        assert json.loads(f.read_text(encoding="utf-8"))["env"]["ANTHROPIC_BASE_URL"] == "https://new.example"
        assert not (fake_home / ".claude" / "settings.json.vision-relay.bak").exists()
        assert (fake_home / ".claude" / "settings.json.qwen-mm-proxy.bak").exists()  # 未被选中的不动

    def test_restore_accepts_legacy_suffix_and_deletes_it(self, fake_home):
        """只有旧后缀备份时：照样还原并删除该备份（升级前接的线，升级后 stop 仍能收尾）。"""
        f = _write(fake_home / ".claude" / "settings.json", {"env": {"ANTHROPIC_BASE_URL": PROXY}})
        legacy = _write(
            fake_home / ".claude" / "settings.json.qwen-mm-proxy.bak",
            {"env": {"ANTHROPIC_BASE_URL": "https://real.example"}},
        )
        msg = wiring.wiring_restore(_cfg("claude"))
        assert any("claude: restored" in m for m in msg)
        assert json.loads(f.read_text(encoding="utf-8"))["env"]["ANTHROPIC_BASE_URL"] == "https://real.example"
        assert not legacy.exists()

    def test_start_does_not_overwrite_existing_legacy_bak(self, fake_home):
        """已有旧后缀备份时 start 不新建备份、不覆盖——原配置不丢（不静默覆盖丢失原配置）。"""
        f = _write(fake_home / ".claude" / "settings.json", {"env": {"ANTHROPIC_BASE_URL": PROXY}})
        legacy = _write(
            fake_home / ".claude" / "settings.json.qwen-mm-proxy.bak",
            {"env": {"ANTHROPIC_BASE_URL": "https://real.example"}},
        )
        wiring.wiring_backup_and_rewrite(_cfg("claude"))
        assert not (fake_home / ".claude" / "settings.json.vision-relay.bak").exists()
        assert json.loads(legacy.read_text(encoding="utf-8"))["env"]["ANTHROPIC_BASE_URL"] == "https://real.example"
        assert json.loads(f.read_text(encoding="utf-8"))["env"]["ANTHROPIC_BASE_URL"] == PROXY

    def test_restore_without_any_bak_is_noop(self, fake_home):
        _write(fake_home / ".claude" / "settings.json", {"env": {"ANTHROPIC_BASE_URL": PROXY}})
        assert wiring.wiring_restore(_cfg("claude")) == []


# ── C1-② zcode 条目级配置（provider.<id>.options.baseURL）读取与还原 ──
def _zcode_cfg(providers: dict) -> dict:
    return {"provider": providers}


def _zcode_provider(url="https://orig.example", key="k-1", kind="anthropic", enabled=True, models=None):
    return {
        "kind": kind,
        "enabled": enabled,
        "options": {"baseURL": url, "apiKey": key},
        "models": models if models is not None else {},
    }


class TestZcodeEntryRead:
    def test_read_returns_enabled_provider_baseurl(self, fake_home):
        p = _write(
            fake_home / ".zcode" / "v2" / "config.json",
            _zcode_cfg(
                {
                    "p-off": _zcode_provider("https://off.example", enabled=False),
                    "p-on": _zcode_provider("https://on.example", kind="openai"),
                }
            ),
        )
        assert wiring.read_base_url(str(p), wiring.HARNESS_CFG["zcode"]) == "https://on.example"

    def test_read_none_when_no_enabled_provider(self, fake_home):
        p = _write(
            fake_home / ".zcode" / "v2" / "config.json",
            _zcode_cfg({"p1": _zcode_provider(enabled=False)}),
        )
        assert wiring.read_base_url(str(p), wiring.HARNESS_CFG["zcode"]) is None

    def test_read_none_when_baseurl_empty(self, fake_home):
        p = _write(
            fake_home / ".zcode" / "v2" / "config.json",
            _zcode_cfg({"p1": _zcode_provider(url="")}),
        )
        assert wiring.read_base_url(str(p), wiring.HARNESS_CFG["zcode"]) is None


class TestZcodeEntryRestore:
    def test_identity_key_is_pid_kind_kind_change_misses(self, fake_home):
        """身份键 pid::kind 现场重算：kind 变更的条目不命中→原样保留（对账吸收新值）。"""
        p = _write(
            fake_home / ".zcode" / "v2" / "config.json",
            _zcode_cfg(
                {
                    "p1": _zcode_provider(PROXY, kind="openai"),  # 快照里记的是 anthropic
                    "p2": _zcode_provider(PROXY),
                }
            ),
        )
        urls = {"p1::anthropic": "https://orig-1.example", "p2::anthropic": "https://orig-2.example"}
        n = wiring._restore_zcode_providers(str(p), PROXY, urls, None)
        d = json.loads(p.read_text(encoding="utf-8"))
        assert n == 1  # 仅 p2 命中
        assert d["provider"]["p1"]["options"]["baseURL"] == PROXY
        assert d["provider"]["p2"]["options"]["baseURL"] == "https://orig-2.example"

    def test_guard_skips_entry_pointing_elsewhere(self, fake_home):
        """守卫：当前 baseURL 不指本代理的条目不动（用户运行期改走别处）。"""
        p = _write(
            fake_home / ".zcode" / "v2" / "config.json",
            _zcode_cfg({"p1": _zcode_provider("https://elsewhere.example")}),
        )
        n = wiring._restore_zcode_providers(str(p), PROXY, {"p1::anthropic": "https://orig.example"}, None)
        assert n == 0
        assert (
            json.loads(p.read_text(encoding="utf-8"))["provider"]["p1"]["options"]["baseURL"]
            == "https://elsewhere.example"
        )

    def test_modalities_flag_absent_removes_key_and_empty_shell(self, fake_home):
        """flag=~absent~：整列表写回原值并删 modalitiesConfigured；zcode 壳空了一并移除（M5）。"""
        p = _write(
            fake_home / ".zcode" / "v2" / "config.json",
            _zcode_cfg(
                {
                    "p1": _zcode_provider(
                        PROXY,
                        models={
                            "m1": {"modalities": {"input": ["text", "image"]}, "zcode": {"modalitiesConfigured": True}}
                        },
                    )
                }
            ),
        )
        mods = {"p1::anthropic::m1": {"input": ["text"], "flag": wiring._MOD_ABSENT}}
        n = wiring._restore_zcode_providers(str(p), PROXY, {"p1::anthropic": "https://orig.example"}, mods)
        m1 = json.loads(p.read_text(encoding="utf-8"))["provider"]["p1"]["models"]["m1"]
        assert n == 1
        assert m1["modalities"]["input"] == ["text"]
        assert "zcode" not in m1

    def test_modalities_flag_value_written_back_and_user_shell_kept(self, fake_home):
        """flag 有原值：写回原值；zcode 壳里有用户其他数据时整个保留（非空=用户数据不动）。"""
        p = _write(
            fake_home / ".zcode" / "v2" / "config.json",
            _zcode_cfg(
                {
                    "p1": _zcode_provider(
                        PROXY,
                        models={
                            "m1": {
                                "modalities": {"input": ["text", "image"]},
                                "zcode": {"modalitiesConfigured": True, "userNote": "keep"},
                            }
                        },
                    )
                }
            ),
        )
        mods = {"p1::anthropic::m1": {"input": ["text"], "flag": False}}
        n = wiring._restore_zcode_providers(str(p), PROXY, {"p1::anthropic": "https://orig.example"}, mods)
        m1 = json.loads(p.read_text(encoding="utf-8"))["provider"]["p1"]["models"]["m1"]
        assert n == 1
        assert m1["zcode"]["modalitiesConfigured"] is False
        assert m1["zcode"]["userNote"] == "keep"


# ── C1-③ 部分接线失败时 stop 的回滚时序 ─────────────────────────────
class TestStopRollbackOrdering:
    def test_codex_catalog_restored_before_config_swap(self, fake_home):
        """时序不变量：codex 目录还原必须在 config 整文件换回之前。

        反证：若先换回 config，原始 config 已无 model_catalog_json 引用，
        _restore_codex_catalog 找不到目录→目录永不还原、catalog.bak 残留。
        """
        codex_dir = fake_home / ".codex"
        catalog = codex_dir / "catalog.json"
        original_catalog = {"models": [{"id": "gpt", "input_modalities": ["text"]}]}
        original_config = 'model = "gpt"\n'  # 原始 config 无 catalog 引用
        live_config = 'model = "gpt"\nbase_url = "http://127.0.0.1:8787/v1"\nmodel_catalog_json = "catalog.json"\n'
        _write(codex_dir / "config.toml", live_config)
        _write(codex_dir / "config.toml.vision-relay.bak", original_config)
        _write(catalog, {"models": [{"id": "gpt", "input_modalities": ["text", "image"]}]})
        _write(codex_dir / "catalog.json.vision-relay.bak", original_catalog)
        msg = wiring.wiring_restore(_cfg("codex"))
        assert any("catalog" in m for m in msg)
        assert json.loads(catalog.read_text(encoding="utf-8")) == original_catalog  # 目录先被还原
        assert not (codex_dir / "catalog.json.vision-relay.bak").exists()
        assert (codex_dir / "config.toml").read_text(encoding="utf-8") == original_config
        assert not (codex_dir / "config.toml.vision-relay.bak").exists()

    def test_zcode_snapshot_restore_deletes_stale_bak(self, fake_home):
        """zcode 快照还原后立即删过期整文件备份（providers 还原先于备份删除的回滚时序）。"""
        p = _write(
            fake_home / ".zcode" / "v2" / "config.json",
            _zcode_cfg({"p1": _zcode_provider(PROXY)}),
        )
        bak = fake_home / ".zcode" / "v2" / "config.json.vision-relay.bak"
        _write(bak, _zcode_cfg({"p1": _zcode_provider("https://stale.example")}))
        snapshot.save(
            "zcode",
            snapshot.Snapshot(
                base_url=PROXY,
                key_ref=snapshot.key_ref_for("zcode"),
                model="",
                second_hop=None,
                provider_urls={"p1::anthropic": "https://orig.example"},
                provider_modalities=None,
            ),
        )
        msg = wiring.wiring_restore_by_snapshot(_cfg("zcode"))
        assert any("providers restored (1 entries)" in m for m in msg)
        assert (
            json.loads(p.read_text(encoding="utf-8"))["provider"]["p1"]["options"]["baseURL"] == "https://orig.example"
        )
        assert not bak.exists()

    def test_qwen_snapshot_restore_then_bak_cleanup(self, fake_home):
        """qwen 快照路径：base_url 写回 → 条目原值写回 → 删过期 .bak（尾段时序）。"""
        settings = {
            "model": {"baseUrl": PROXY},
            "modelProviders": {
                "openai": [
                    {"id": "m1", "baseUrl": PROXY, "envKey": "EK", "generationConfig": {"modalities": {"image": True}}}
                ]
            },
        }
        p = _write(fake_home / ".qwen" / "settings.json", settings)
        _write(
            fake_home / ".qwen" / "settings.json.vision-relay.bak",
            {"model": {"baseUrl": "https://real.example"}},
        )
        snapshot.save(
            "qwen-code",
            snapshot.Snapshot(
                base_url="https://real.example",
                key_ref=snapshot.key_ref_for("qwen-code"),
                model="m1",
                second_hop=None,
                provider_urls={"EK": "https://orig.example"},
                provider_modalities=None,
            ),
        )
        msg = wiring.wiring_restore_on_stop(_cfg("qwen-code"))
        d = json.loads(p.read_text(encoding="utf-8"))
        assert any("snapshot restored" in m for m in msg)
        assert d["model"]["baseUrl"] == "https://real.example"
        assert d["modelProviders"]["openai"][0]["baseUrl"] == "https://orig.example"
        assert not (fake_home / ".qwen" / "settings.json.vision-relay.bak").exists()

    def test_stop_independent_per_harness_skip_keeps_backup(self, fake_home):
        """多 harness stop：codex 当前不指代理→跳过且保留备份，claude 照常还原（互不阻塞）。"""
        f = _write(fake_home / ".claude" / "settings.json", {"env": {"ANTHROPIC_BASE_URL": PROXY}})
        _write(
            fake_home / ".claude" / "settings.json.vision-relay.bak",
            {"env": {"ANTHROPIC_BASE_URL": "https://real.example"}},
        )
        codex_bak = _write(fake_home / ".codex" / "config.toml.vision-relay.bak", 'base_url = "https://real.example"\n')
        _write(fake_home / ".codex" / "config.toml", 'base_url = "https://elsewhere.example"\n')
        msg = wiring.wiring_restore(_cfg("claude", "codex"))
        assert any("claude: restored" in m for m in msg)
        assert any("codex" in m and "非本代理" in m for m in msg)
        assert json.loads(f.read_text(encoding="utf-8"))["env"]["ANTHROPIC_BASE_URL"] == "https://real.example"
        assert codex_bak.exists()  # 被跳过的备份保留
