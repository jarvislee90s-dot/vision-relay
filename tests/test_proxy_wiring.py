"""wiring upgrades: ownership classify, snapshot on takeover, restore-by-snapshot, tool relays."""

import json
import os

from vision_relay import snapshot, wiring
from vision_relay.config import ProxyConfig, RelayConfig


def _write_harness(home, harness, base_url):
    h = wiring.HARNESS_CFG[harness]
    p = wiring._path(home, harness)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if h.kind == "toml":
        open(p, "w", encoding="utf-8").write(f'model = "gpt-5"\nbase_url = "{base_url}"\n')
    else:
        d = (
            {"env": {"ANTHROPIC_BASE_URL": base_url}}
            if harness == "claude"
            else {"model": {"baseUrl": base_url, "apiKey": "sk-x"}}
        )
        open(p, "w", encoding="utf-8").write(json.dumps(d))


class TestOwnership:
    def test_classify(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        assert wiring.classify_base_url("http://127.0.0.1:8787", 8787) == "ours"
        assert wiring.classify_base_url("http://127.0.0.1:8787/v1", 8787) == "ours"
        assert wiring.classify_base_url("http://127.0.0.1:15721", 8787) == "cc-switch"
        assert wiring.classify_base_url("http://127.0.0.1:57321/v1", 8787) == "codex-plus"
        assert wiring.classify_base_url("https://api.deepseek.com", 8787) == "other"
        assert wiring.classify_base_url(None, 8787) == "none"


class TestTakeoverWritesSnapshot:
    def test_backup_and_rewrite_snapshots_original(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_harness(tmp_path, "codex", "http://127.0.0.1:57321/v1")
        cfg = ProxyConfig()
        cfg.routing.relay_templates = {
            "codex-plus": {
                "protocol": "responses",
                "base_url": "http://127.0.0.1:57321/v1",
                "via": "codex-plus",
                "models": ["*"],
            }
        }
        wiring.relays_activate(cfg)
        wiring.wiring_backup_and_rewrite(cfg)
        snap = snapshot.load()["codex"]
        assert snap.base_url == "http://127.0.0.1:57321/v1"
        assert snap.second_hop == "codex-plus"


class TestRestoreBySnapshot:
    def test_restores_snapshot_combo(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_harness(tmp_path, "claude", "http://127.0.0.1:8787")
        snapshot.save(
            "claude",
            snapshot.Snapshot(
                base_url="https://real.example/api", key_ref="env.ANTHROPIC_AUTH_TOKEN", model="glm-5-air"
            ),
        )
        msgs = wiring.wiring_restore_by_snapshot(ProxyConfig())
        assert "claude: restored" in msgs[0]
        assert (
            wiring.read_base_url(wiring._path(str(tmp_path), "claude"), wiring.HARNESS_CFG["claude"])
            == "https://real.example/api"
        )

    def test_restore_by_snapshot_drops_stale_bak(self, tmp_path, monkeypatch):
        """快照还原成功后必须删 .bak：防止 stop 走 wiring_restore 用过期整文件备份覆盖已还原状态。"""
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_harness(tmp_path, "claude", "http://127.0.0.1:8787")
        p = wiring._path(str(tmp_path), "claude")
        with open(p + wiring.BAK_SUFFIX, "w", encoding="utf-8") as f:
            f.write("{}")
        snapshot.save("claude", snapshot.Snapshot(base_url="https://real.example/api", key_ref="k", model="m"))
        wiring.wiring_restore_by_snapshot(ProxyConfig())
        assert wiring.read_base_url(p, wiring.HARNESS_CFG["claude"]) == "https://real.example/api"
        assert wiring._find_bak(p) is None


class TestTakeoverGuards:
    def test_snapshot_failure_never_blocks_takeover(self, tmp_path, monkeypatch):
        """快照子系统炸出任何异常（不止 OSError）都不得打断接管本身。"""
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_harness(tmp_path, "claude", "https://real.example/api")

        def boom(*args, **kwargs):
            raise RuntimeError("snapshot subsystem broken")

        monkeypatch.setattr(snapshot, "save", boom)
        cfg = ProxyConfig()
        wiring.wiring_backup_and_rewrite(cfg)  # 不得抛
        assert (
            wiring.read_base_url(wiring._path(str(tmp_path), "claude"), wiring.HARNESS_CFG["claude"])
            == "http://127.0.0.1:8787"
        )


class TestToolRelays:
    def test_ensure_tool_relays_adds_missing_not_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(
            RelayConfig(
                name="cc-anthropic",
                **{"protocol": "anthropic", "base_url": "http://127.0.0.1:15721", "via": "cc-switch", "models": ["*"]},
            )
        )
        from vision_relay.tools import ToolState

        online = [ToolState("cc-switch", 15721, True), ToolState("codex-plus", 57321, True)]
        added = wiring.ensure_tool_relays(cfg, online)
        names = [r.name for r in cfg.relays]
        assert "cc-anthropic" in names and "cc-codex" in names and "codex-plus" in names
        assert set(added) == {"cc-codex", "codex-plus"}  # 已存在的不重复加

    def test_offline_tool_not_added(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        from vision_relay.tools import ToolState

        wiring.ensure_tool_relays(cfg, [ToolState("cc-switch", 15721, False)])
        assert cfg.relays == []

    def test_no_save_when_nothing_added(self, tmp_path, monkeypatch):
        """无新增 relay 时不落盘（幂等：无漂移不写文件）。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        from vision_relay.tools import ToolState

        wiring.ensure_tool_relays(cfg, [ToolState("cc-switch", 15721, False)])
        assert not (tmp_path / "proxy.json").exists()


class TestRestoreOnStop:
    """stop 统一还原（spec §5 + 2026-08-23 决策）：最新快照优先，.bak 兜底。"""

    def _env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))

    def test_snapshot_preferred_over_stale_bak(self, tmp_path, monkeypatch):
        """absorb 更新过快照后 stop：还原到快照值（最新），不是 .bak 里的最早原值。"""
        self._env(tmp_path, monkeypatch)
        import shutil

        for h, content in {
            "claude": json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://A.example"}}),
            "codex": 'model = "m"\nbase_url = "https://A.example"\n',
        }.items():
            p = wiring._path(str(tmp_path), h)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(content)
            shutil.copyfile(p, p + wiring.BAK_SUFFIX)
        wiring.write_base_url(
            wiring._path(str(tmp_path), "claude"), wiring.HARNESS_CFG["claude"], "http://127.0.0.1:8787"
        )
        wiring.write_base_url(
            wiring._path(str(tmp_path), "codex"), wiring.HARNESS_CFG["codex"], "http://127.0.0.1:8787"
        )
        snapshot.save("claude", snapshot.Snapshot(base_url="https://B.example", key_ref="k", model="m"))
        snapshot.save("codex", snapshot.Snapshot(base_url="https://B.example", key_ref="k", model="m"))

        msgs = wiring.wiring_restore_on_stop(ProxyConfig())
        assert msgs  # 两条 harness 均产生快照还原消息
        assert (
            wiring.read_base_url(wiring._path(str(tmp_path), "claude"), wiring.HARNESS_CFG["claude"])
            == "https://B.example"
        )
        assert (
            wiring.read_base_url(wiring._path(str(tmp_path), "codex"), wiring.HARNESS_CFG["codex"])
            == "https://B.example"
        )
        assert not os.path.exists(wiring._path(str(tmp_path), "claude") + wiring.BAK_SUFFIX)

    def test_bak_fallback_when_snapshot_missing(self, tmp_path, monkeypatch):
        """快照不可得的 harness：退回第一次接管前的整文件 .bak（用户确认的兜底路径）。"""
        self._env(tmp_path, monkeypatch)
        import shutil

        p = wiring._path(str(tmp_path), "claude")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        original = json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://A.example", "ANTHROPIC_AUTH_TOKEN": "sk-x"}})
        open(p, "w", encoding="utf-8").write(original)
        shutil.copyfile(p, p + wiring.BAK_SUFFIX)
        wiring.write_base_url(p, wiring.HARNESS_CFG["claude"], "http://127.0.0.1:8787")

        wiring.wiring_restore_on_stop(ProxyConfig())
        d = json.load(open(p, encoding="utf-8"))
        assert d["env"]["ANTHROPIC_BASE_URL"] == "https://A.example"
        assert d["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-x"
        assert not os.path.exists(p + wiring.BAK_SUFFIX)

    def test_noop_when_base_url_not_ours(self, tmp_path, monkeypatch):
        """当前不指向本代理：不动文件（与其他还原函数同守卫）。"""
        self._env(tmp_path, monkeypatch)
        p = wiring._path(str(tmp_path), "claude")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://elsewhere.example"}}))
        wiring.wiring_restore_on_stop(ProxyConfig())
        assert json.load(open(p, encoding="utf-8"))["env"]["ANTHROPIC_BASE_URL"] == "https://elsewhere.example"


def _write_codex_catalog(home, models):
    """codex config.toml 带 model_catalog_json 引用 + 对应目录文件；返回目录路径。"""
    p = wiring._path(str(home), "codex")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write(
        'model = "gpt-5"\nbase_url = "http://127.0.0.1:57321/v1"\nmodel_catalog_json = "model-catalogs/relay-x.json"\n'
    )
    cat = os.path.join(str(home), ".codex", "model-catalogs", "relay-x.json")
    os.makedirs(os.path.dirname(cat), exist_ok=True)
    json.dump({"models": models}, open(cat, "w", encoding="utf-8"))
    return cat


_CATALOG_MODELS = [
    {"slug": "m-text", "input_modalities": ["text"]},
    {"slug": "m-image", "input_modalities": ["text", "image"]},
    {"slug": "m-none"},
]


class TestCodexCatalogModalities:
    """接管期目录模态补丁：Codex 按 catalog 的 input_modalities 放行 view_image/贴图，
    纯文本标注把图片挡在请求之外（代理转写收不到图）。接管时统一补 image，stop 还原。"""

    def _env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        return ProxyConfig()

    def test_patch_adds_image_to_all_models(self, tmp_path, monkeypatch):
        cfg = self._env(tmp_path, monkeypatch)
        cat = _write_codex_catalog(tmp_path, [dict(m) for m in _CATALOG_MODELS])
        msgs = wiring.wiring_backup_and_rewrite(cfg)
        mods = {m["slug"]: m.get("input_modalities") for m in json.load(open(cat, encoding="utf-8"))["models"]}
        assert "image" in mods["m-text"]
        assert mods["m-image"].count("image") == 1  # 已支持者不重复加
        assert "image" in mods["m-none"]  # 缺字段视为可补
        assert os.path.exists(cat + wiring.BAK_SUFFIX)
        assert any("catalog" in m for m in msgs)

    def test_patch_idempotent_backup_keeps_original(self, tmp_path, monkeypatch):
        cfg = self._env(tmp_path, monkeypatch)
        cat = _write_codex_catalog(tmp_path, [dict(m) for m in _CATALOG_MODELS])
        wiring.wiring_backup_and_rewrite(cfg)
        wiring.wiring_backup_and_rewrite(cfg)
        orig = json.load(open(cat + wiring.BAK_SUFFIX, encoding="utf-8"))
        assert orig["models"][0]["input_modalities"] == ["text"]  # 备份始终是首次接管前内容
        cur = json.load(open(cat, encoding="utf-8"))
        assert cur["models"][0]["input_modalities"] == ["text", "image"]

    def test_stop_restores_catalog(self, tmp_path, monkeypatch):
        cfg = self._env(tmp_path, monkeypatch)
        cat = _write_codex_catalog(tmp_path, [dict(m) for m in _CATALOG_MODELS])
        wiring.wiring_backup_and_rewrite(cfg)
        msgs = wiring.wiring_restore_on_stop(cfg)
        mods = {m["slug"]: m.get("input_modalities") for m in json.load(open(cat, encoding="utf-8"))["models"]}
        assert mods["m-text"] == ["text"]  # 还原为原始标注
        assert not os.path.exists(cat + wiring.BAK_SUFFIX)
        assert any("catalog" in m for m in msgs)

    def test_stop_skips_catalog_when_base_url_not_ours(self, tmp_path, monkeypatch):
        cfg = self._env(tmp_path, monkeypatch)
        cat = _write_codex_catalog(tmp_path, [dict(m) for m in _CATALOG_MODELS])
        wiring.wiring_backup_and_rewrite(cfg)
        p = wiring._path(str(tmp_path), "codex")
        wiring.write_base_url(p, wiring.HARNESS_CFG["codex"], "https://elsewhere.example/v1")
        wiring.wiring_restore_on_stop(cfg)
        mods = {m["slug"]: m.get("input_modalities") for m in json.load(open(cat, encoding="utf-8"))["models"]}
        assert "image" in mods["m-text"]  # 守卫生效：目录补丁与备份原样保留
        assert os.path.exists(cat + wiring.BAK_SUFFIX)

    def test_no_catalog_reference_is_noop(self, tmp_path, monkeypatch):
        cfg = self._env(tmp_path, monkeypatch)
        _write_harness(tmp_path, "codex", "http://127.0.0.1:57321/v1")
        msgs = wiring.wiring_backup_and_rewrite(cfg)
        assert not any("catalog" in m for m in msgs)

    def test_invalid_catalog_json_skipped(self, tmp_path, monkeypatch):
        cfg = self._env(tmp_path, monkeypatch)
        cat = _write_codex_catalog(tmp_path, [])
        open(cat, "w", encoding="utf-8").write("{not json")
        msgs = wiring.wiring_backup_and_rewrite(cfg)  # 不抛异常
        assert not any("catalog" in m for m in msgs)
        assert not os.path.exists(cat + wiring.BAK_SUFFIX)
