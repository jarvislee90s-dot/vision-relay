"""wiring 重构守护测试：legacy 备份迁移、zcode 条目级读取/还原、部分接线失败回滚时序。

本文件为重构后新增的守护测试（不改动任何既有断言），针对 C1 三条点名路径与
C2 自选的此前未覆盖路径。沿用仓库 HOME monkeypatch 隔离纪律，不触碰真实家目录。
"""

from __future__ import annotations

import json
import os

from vision_relay import snapshot, wiring
from vision_relay.config import ProxyConfig

PROXY = "http://127.0.0.1:8787"
LEGACY = wiring.LEGACY_BAK_SUFFIX
NEW_BAK = wiring.BAK_SUFFIX


def _env(tmp_path, monkeypatch):
    """隔离 HOME + 配置目录（wiring 与 snapshot 各自有 HOME 挂点，一并隔离）。"""
    home = str(tmp_path)
    monkeypatch.setattr(wiring, "HOME", home)
    monkeypatch.setattr(snapshot, "HOME", home)
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
    return home


def _write(home, harness, content: bytes | str) -> str:
    p = wiring._path(home, harness)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(p, mode, encoding=None if isinstance(content, bytes) else "utf-8") as f:
        f.write(content)
    return p


def _claude(home, base_url):
    return _write(home, "claude", json.dumps({"env": {"ANTHROPIC_BASE_URL": base_url}}))


def _codex(home, base_url):
    return _write(home, "codex", f'model = "gpt-5"\nbase_url = "{base_url}"\n')


# ── C1-① legacy .qwen-mm-proxy.bak 迁移 ──────────────────────────────


class TestLegacyBakMigration:
    def test_find_bak_prefers_new_over_legacy(self, tmp_path):
        p = str(tmp_path / "x.json")
        open(p, "w").close()
        open(p + NEW_BAK, "w").write("new")
        open(p + LEGACY, "w").write("legacy")
        assert wiring._find_bak(p) == p + NEW_BAK  # 新后缀优先

    def test_find_bak_falls_back_to_legacy(self, tmp_path):
        p = str(tmp_path / "x.json")
        open(p, "w").close()
        open(p + LEGACY, "w").write("legacy")
        assert wiring._find_bak(p) == p + LEGACY  # 仅 legacy 时回退
        assert wiring._find_bak(str(tmp_path / "nope.json")) is None

    def test_backup_does_not_clobber_existing_legacy_bak(self, tmp_path, monkeypatch):
        """已有 legacy 备份时不覆盖（防把代理地址存成"原始值"），也不新建新后缀备份。"""
        home = _env(tmp_path, monkeypatch)
        p = _claude(home, "https://real.example/api")
        with open(p + LEGACY, "w", encoding="utf-8") as f:
            f.write(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://ORIGINAL.example"}}))
        wiring.wiring_backup_and_rewrite(ProxyConfig())
        # legacy 备份原样保留（未被代理态覆盖），且未另建 .vision-relay.bak
        assert json.load(open(p + LEGACY, encoding="utf-8"))["env"]["ANTHROPIC_BASE_URL"] == "https://ORIGINAL.example"
        assert not os.path.exists(p + NEW_BAK)
        # 现场已被改写到代理
        assert wiring.read_base_url(p, wiring.HARNESS_CFG["claude"]) == PROXY

    def test_restore_on_stop_consumes_legacy_bak(self, tmp_path, monkeypatch):
        """无快照、仅 legacy 备份：stop 走 .bak 兜底还原并删除 legacy 备份。"""
        home = _env(tmp_path, monkeypatch)
        p = _claude(home, PROXY)  # 当前指向本代理（守卫通过）
        with open(p + LEGACY, "w", encoding="utf-8") as f:
            f.write(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://ORIGINAL.example", "K": "v"}}))
        msgs = wiring.wiring_restore_on_stop(ProxyConfig())
        assert any("ORIGINAL" not in m for m in msgs)  # 产生了还原消息
        d = json.load(open(p, encoding="utf-8"))
        assert d["env"]["ANTHROPIC_BASE_URL"] == "https://ORIGINAL.example"
        assert d["env"]["K"] == "v"  # 整文件还原（.bak 兜底，非仅 base_url）
        assert not os.path.exists(p + LEGACY) and not os.path.exists(p + NEW_BAK)

    def test_restore_skips_when_not_pointing_at_proxy(self, tmp_path, monkeypatch):
        """legacy 备份存在但当前 base_url 非本代理：不动文件、保留备份（守卫）。"""
        home = _env(tmp_path, monkeypatch)
        p = _claude(home, "https://elsewhere.example")
        with open(p + LEGACY, "w", encoding="utf-8") as f:
            f.write(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://ORIGINAL.example"}}))
        wiring.wiring_restore_on_stop(ProxyConfig())
        assert json.load(open(p, encoding="utf-8"))["env"]["ANTHROPIC_BASE_URL"] == "https://elsewhere.example"
        assert os.path.exists(p + LEGACY)  # 备份保留


# ── C1-② zcode 条目级配置 provider.<id>.options.baseURL 读取与还原 ────


def _zcode(home, providers: dict) -> str:
    p = wiring._path(home, "zcode")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"provider": providers}, f)
    return p


def _zprov(url="https://real.example/api", key="k-1234567890abcdef", kind="anthropic", enabled=True, models=None):
    return {
        "name": "P",
        "kind": kind,
        "options": {"apiKey": key, "baseURL": url},
        "enabled": enabled,
        "models": models if models is not None else {},
    }


class TestZcodeEntryLevel:
    def test_read_base_url_picks_enabled_skips_malformed(self, tmp_path, monkeypatch):
        """读取：返回 enabled 供应商地址；disabled 与无 options 的畸形条目跳过。"""
        home = _env(tmp_path, monkeypatch)
        p = _zcode(
            home,
            {
                "off": _zprov(url="https://off.example", enabled=False),
                "bad": {"kind": "anthropic"},  # 无 options 字典
                "on": _zprov(url="https://on.example", enabled=True),
            },
        )
        assert wiring.read_base_url(p, wiring.HARNESS_CFG["zcode"]) == "https://on.example"

    def test_rewrite_restore_entry_level_roundtrip(self, tmp_path, monkeypatch):
        """改写→还原：provider.<id>.options.baseURL 与模态门原值往返一致。"""
        home = _env(tmp_path, monkeypatch)
        p = _zcode(
            home,
            {
                "a": _zprov(url="https://a.example", models={"m": {"modalities": {"input": ["text"]}}}),
                "b": _zprov(url="https://b.example", kind="openai", models={}),
            },
        )
        urls, mods, _stats = wiring._rewrite_zcode_providers(p, PROXY)
        assert set(urls) == {"a::anthropic", "b::openai"}
        d = json.load(open(p, encoding="utf-8"))
        assert d["provider"]["a"]["options"]["baseURL"] == PROXY
        assert "image" in d["provider"]["a"]["models"]["m"]["modalities"]["input"]
        n = wiring._restore_zcode_providers(p, PROXY, urls, mods)
        assert n == 2
        d = json.load(open(p, encoding="utf-8"))
        assert d["provider"]["a"]["options"]["baseURL"] == "https://a.example"
        assert d["provider"]["a"]["models"]["m"]["modalities"]["input"] == ["text"]  # 整列表写回原值
        assert d["provider"]["b"]["options"]["baseURL"] == "https://b.example"

    def test_restore_skips_provider_user_moved_off_proxy(self, tmp_path, monkeypatch):
        """还原守卫：用户把某条目改走别处后，还原只动仍指本代理的条目。"""
        home = _env(tmp_path, monkeypatch)
        p = _zcode(home, {"a": _zprov(url="https://a.example"), "b": _zprov(url="https://b.example")})
        urls, mods, _ = wiring._rewrite_zcode_providers(p, PROXY)
        # 用户运行期把 a 改走别处
        d = json.load(open(p, encoding="utf-8"))
        d["provider"]["a"]["options"]["baseURL"] = "https://user-changed.example"
        json.dump(d, open(p, "w", encoding="utf-8"))
        n = wiring._restore_zcode_providers(p, PROXY, urls, mods)
        assert n == 1  # 仅 b 还原
        d = json.load(open(p, encoding="utf-8"))
        assert d["provider"]["a"]["options"]["baseURL"] == "https://user-changed.example"  # 不动
        assert d["provider"]["b"]["options"]["baseURL"] == "https://b.example"  # 还原


# ── C1-③ 部分接线失败时 stop 的回滚时序 ───────────────────────────────


class TestPartialFailureRollback:
    def test_backup_taken_before_failed_write_allows_stop_restore(self, tmp_path, monkeypatch):
        """回滚时序：备份在改写之前完成；改写失败后 stop 仍能从快照/.bak 回滚。

        注入 snapshot.save 抛错（接管期快照子系统失败），验证：
        接管本身不被打断（备份已落、文件已改写），stop 退回 .bak 整文件兜底还原。
        """
        home = _env(tmp_path, monkeypatch)
        p = _codex(home, "https://real.example/v1")
        original = open(p, encoding="utf-8").read()

        def boom(*a, **k):
            raise RuntimeError("snapshot subsystem broken")

        monkeypatch.setattr(snapshot, "save", boom)  # 快照失败（既有测试已验证不打断接管）
        wiring.wiring_backup_and_rewrite(ProxyConfig())  # 不抛
        # 备份在改写前已落盘；现场已指向代理
        assert os.path.exists(p + NEW_BAK)
        assert open(p + NEW_BAK, encoding="utf-8").read() == original
        assert wiring.read_base_url(p, wiring.HARNESS_CFG["codex"]) == PROXY
        # stop：无快照 → .bak 兜底整文件还原（时序：备份先于改写，故原始值得以恢复）
        wiring.wiring_restore_on_stop(ProxyConfig())
        assert open(p, encoding="utf-8").read() == original
        assert not os.path.exists(p + NEW_BAK)

    def test_stop_restores_each_harness_independently(self, tmp_path, monkeypatch):
        """多 harness 部分失败：一个被用户改走别处（跳过），另一个照常还原，互不影响。"""
        home = _env(tmp_path, monkeypatch)
        pa = _claude(home, "https://a.example")
        pb = _codex(home, "https://b.example/v1")
        cfg = ProxyConfig()
        wiring.wiring_backup_and_rewrite(cfg)
        # 模拟部分失败：codex 被用户改走别处（stop 守卫跳过），claude 仍指本代理
        wiring.write_base_url(pb, wiring.HARNESS_CFG["codex"], "https://user-elsewhere/v1")
        wiring.wiring_restore_on_stop(cfg)
        # claude 还原到快照原值
        assert json.load(open(pa, encoding="utf-8"))["env"]["ANTHROPIC_BASE_URL"] == "https://a.example"
        # codex 守卫命中：未还原，保留用户值
        assert wiring.read_base_url(pb, wiring.HARNESS_CFG["codex"]) == "https://user-elsewhere/v1"


# ── C2 自选未覆盖路径（理由见 REFACTOR_NOTES.md）──────────────────────


class TestHarnessIoFormats:
    def test_env_format_read_write_roundtrip(self, tmp_path, monkeypatch):
        """env 格式（KEY=VALUE）读写：当前四种 harness 未用，但 read/write 保留该分支。"""
        home = _env(tmp_path, monkeypatch)
        h = wiring._Harness("env", (".custom", "x.env"), "MY_BASE_URL")
        p = os.path.join(home, ".custom", "x.env")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write("OTHER=1\nMY_BASE_URL=https://old.example\n")
        assert wiring.read_base_url(p, h) == "https://old.example"
        assert wiring.write_base_url(p, h, PROXY) is True
        content = open(p, encoding="utf-8").read()
        assert "MY_BASE_URL=" + PROXY in content and "OTHER=1" in content  # 原位改写、不丢其它行
        # 缺 key 时追加
        open(p, "w", encoding="utf-8").write("OTHER=1\n")
        wiring.write_base_url(p, h, PROXY)
        assert "MY_BASE_URL=" + PROXY in open(p, encoding="utf-8").read()

    def test_toml_write_appends_when_no_base_url(self, tmp_path, monkeypatch):
        """toml 无 base_url 行时追加（既有测试都带 base_url，append 分支未覆盖）。"""
        home = _env(tmp_path, monkeypatch)
        p = _write(home, "codex", 'model = "gpt-5"\n')  # 无 base_url
        assert wiring.read_base_url(p, wiring.HARNESS_CFG["codex"]) is None
        assert wiring.write_base_url(p, wiring.HARNESS_CFG["codex"], PROXY) is True
        assert f'base_url = "{PROXY}"' in open(p, encoding="utf-8").read()


class TestRelayMaintenance:
    def test_ensure_tool_relays_respects_suppressed(self, tmp_path, monkeypatch):
        """用户显式停用（suppressed_relays）优先于自动探测：在线工具也不建 relay。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.tools import ToolState

        cfg = ProxyConfig()
        cfg.routing.suppressed_relays = ["cc-anthropic", "cc-codex"]
        added = wiring.ensure_tool_relays(cfg, [ToolState("cc-switch", 15721, True)])
        assert added == [] and cfg.relays == []

    def test_relays_restore_removes_prefixed_one_hop_relays(self, tmp_path, monkeypatch):
        """relays_restore 按 qwen-/zcode- 前缀 + activated 识别并移除一层直连 relay。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.config import RelayConfig

        cfg = ProxyConfig()
        cfg.relays = [
            RelayConfig(name="qwen-ollama", protocol="chat", base_url="https://ollama.com/v1", models=["*"]),
            RelayConfig(name="zcode-bigmodel", protocol="anthropic", base_url="https://x.example", models=["*"]),
            RelayConfig(name="manual", protocol="chat", base_url="https://manual.example", models=["*"]),
        ]
        cfg.routing.activated_relays = ["qwen-ollama", "zcode-bigmodel", "manual"]
        msgs = wiring.relays_restore(cfg)
        names = {r.name for r in cfg.relays}
        assert "manual" in names and "qwen-ollama" not in names and "zcode-bigmodel" not in names
        assert cfg.routing.activated_relays == []
        assert any("qwen-ollama" in m for m in msgs)
