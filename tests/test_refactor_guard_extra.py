"""重构守护测试（C2 自选补测路径，选择理由见 REFACTOR_NOTES.md）：
① env-kind（legacy .env 形态）读写分支——无现役 harness 使用但代码保留，零覆盖；
② toml 追加分支——codex 配置无 base_url 行时的首次接线边缘，零覆盖；
③ classify_base_url 的 "other" 归属——端口不匹配任何工具档案时的观测信号；
④ _first_model 抽取与失败兜底——快照记录用，OSError 兜底零覆盖；
⑤ zcode 无快照时的 .bak 整文件还原回退——stop 真实路径，零覆盖；
⑥ wiring_restore_harness 未知 harness 消息；⑦ relay 命名撞名消歧；⑧ zcode 条目计数。

全部沙箱隔离，不触碰真实 ~。
"""

from __future__ import annotations

import json

import pytest

import vision_relay.wiring as wiring
from vision_relay.config import ProxyConfig

PROXY = "http://127.0.0.1:8787"


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(wiring, "HOME", str(home))
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
    return home


# ── C2-① env-kind（legacy .env 形态）读写分支 ───────────────────────
class TestEnvKindLegacyBranch:
    def test_env_read_strips_value(self, tmp_path):
        h = wiring._Harness("env", (".qwen-code", ".env"), "QWEN_BASE_URL")
        p = tmp_path / ".env"
        p.write_text("OTHER=1\nQWEN_BASE_URL=https://a.example\n", encoding="utf-8")
        assert wiring.read_base_url(str(p), h) == "https://a.example"

    def test_env_read_missing_key_is_none(self, tmp_path):
        h = wiring._Harness("env", (".qwen-code", ".env"), "QWEN_BASE_URL")
        p = tmp_path / ".env"
        p.write_text("OTHER=1\n", encoding="utf-8")
        assert wiring.read_base_url(str(p), h) is None

    def test_env_write_replaces_in_place(self, tmp_path):
        h = wiring._Harness("env", (".qwen-code", ".env"), "QWEN_BASE_URL")
        p = tmp_path / ".env"
        p.write_text("OTHER=1\nQWEN_BASE_URL=https://a.example\n", encoding="utf-8")
        assert wiring.write_base_url(str(p), h, "https://b.example") is True
        text = p.read_text(encoding="utf-8")
        assert "QWEN_BASE_URL=https://b.example\n" in text
        assert "OTHER=1" in text

    def test_env_write_appends_when_missing(self, tmp_path):
        h = wiring._Harness("env", (".qwen-code", ".env"), "QWEN_BASE_URL")
        p = tmp_path / ".env"
        p.write_text("OTHER=1\n", encoding="utf-8")
        assert wiring.write_base_url(str(p), h, "https://b.example") is True
        assert p.read_text(encoding="utf-8") == "OTHER=1\nQWEN_BASE_URL=https://b.example\n"


# ── C2-② toml 追加分支（codex 配置无 base_url 行）───────────────────
class TestTomlWriteBranch:
    def test_toml_append_when_base_url_absent(self, tmp_path):
        h = wiring._Harness("toml", (".codex", "config.toml"), "base_url")
        p = tmp_path / "config.toml"
        p.write_text('model = "gpt"\n', encoding="utf-8")
        assert wiring.write_base_url(str(p), h, PROXY) is True
        text = p.read_text(encoding="utf-8")
        assert 'base_url = "http://127.0.0.1:8787"' in text  # 追加而非替换
        assert 'model = "gpt"' in text  # 原内容保留

    def test_toml_read_missing_base_url_is_none(self, tmp_path):
        h = wiring._Harness("toml", (".codex", "config.toml"), "base_url")
        p = tmp_path / "config.toml"
        p.write_text('model = "gpt"\n', encoding="utf-8")
        assert wiring.read_base_url(str(p), h) is None


# ── C2-③ classify_base_url 的 "other" 归属 ──────────────────────────
class TestClassifyOther:
    def test_port_not_in_tool_dossiers_is_other(self):
        assert wiring.classify_base_url("http://127.0.0.1:59999", 8787) == "other"

    def test_url_without_port_is_other(self):
        assert wiring.classify_base_url("https://api.deepseek.com", 8787) == "other"

    def test_none_is_none_owner(self):
        assert wiring.classify_base_url(None, 8787) == "none"


# ── C2-④ _first_model 抽取与失败兜底 ────────────────────────────────
class TestFirstModel:
    def test_extract_model_from_jsonish(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text('{"model": "gpt-4o"}', encoding="utf-8")
        assert wiring._first_model(str(p)) == "gpt-4o"

    def test_no_match_returns_empty(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text('{"foo": 1}', encoding="utf-8")
        assert wiring._first_model(str(p)) == ""

    def test_missing_file_returns_empty(self, tmp_path):
        assert wiring._first_model(str(tmp_path / "nope.json")) == ""


# ── C2-⑤ zcode 无快照时 .bak 整文件还原回退（stop 真实路径）─────────
class TestZcodeBakFallbackOnStop:
    def _live(self, fake_home, baseurl=PROXY):
        p = fake_home / ".zcode" / "v2" / "config.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {"provider": {"p1": {"kind": "anthropic", "enabled": True, "options": {"baseURL": baseurl, "apiKey": "k"}}}}
            ),
            encoding="utf-8",
        )
        return p

    def test_no_snapshot_wired_bak_restored(self, fake_home):
        p = self._live(fake_home)
        bak = p.parent / "config.json.vision-relay.bak"
        bak.write_text(
            json.dumps(
                {"provider": {"p1": {"kind": "anthropic", "enabled": True, "options": {"baseURL": "https://real.example", "apiKey": "k"}}}}
            ),
            encoding="utf-8",
        )
        msg = wiring.wiring_restore_harness(ProxyConfig(bind_port=8787), "zcode")
        assert msg == ["zcode: bak restored"]
        assert json.loads(p.read_text(encoding="utf-8"))["provider"]["p1"]["options"]["baseURL"] == "https://real.example"
        assert not bak.exists()

    def test_no_snapshot_not_wired_noop(self, fake_home):
        self._live(fake_home, baseurl="https://elsewhere.example")  # wired==0 → 不动
        assert wiring.wiring_restore_harness(ProxyConfig(bind_port=8787), "zcode") == []

    def test_no_snapshot_no_bak_skip_message(self, fake_home):
        self._live(fake_home)  # wired==1 但无备份
        assert wiring.wiring_restore_harness(ProxyConfig(bind_port=8787), "zcode") == ["zcode: 无快照且无备份，跳过"]


# ── C2-⑥ wiring_restore_harness 未知 harness ────────────────────────
class TestRestoreUnknownHarness:
    def test_unknown_harness_message(self):
        assert wiring.wiring_restore_harness(ProxyConfig(bind_port=8787), "nope") == ["nope: unknown harness"]


# ── C2-⑦ qwen relay 命名撞名消歧 ───────────────────────────────────
class TestQwenRelayNameCollision:
    def test_name_and_collision_suffixes(self):
        n1 = wiring._qwen_relay_name("https://api.example.com/v1", set())
        assert n1 == "qwen-api.example.com"
        assert wiring._qwen_relay_name("https://api.example.com/v1", {n1}) == f"{n1}-2"
        assert wiring._qwen_relay_name("https://api.example.com/v1", {n1, f"{n1}-2"}) == f"{n1}-3"

    def test_localhost_hostname(self):
        assert wiring._qwen_relay_name("http://127.0.0.1:1234", set()) == "qwen-127.0.0.1"


# ── C2-⑧ zcode 条目计数（nokey / badkind / 非 dict 跳过）────────────
class TestZcodeEntriesCounting:
    def test_nokey_badkind_and_junk_skipped(self):
        d = {
            "provider": {
                "p1": {"kind": "anthropic", "options": {"baseURL": "u", "apiKey": "k"}},
                "p2": {"kind": "anthropic", "options": {"baseURL": "u"}},  # nokey
                "p3": {"kind": "weird", "options": {"baseURL": "u", "apiKey": "k"}},  # badkind
                "p4": "junk",
                "p5": {"options": "junk"},
            }
        }
        items, nokey, badkind = wiring._zcode_entries(d)
        assert [i[0] for i in items] == ["p1"]
        assert (nokey, badkind) == (1, 1)


# ── C2-⑨ HOME seam 纪律（架构守护）─────────────────────────────────
class TestWiringHomeSeam:
    """HOME seam 纪律：实现模块不得把 HOME 缓存为模块级全局。

    测试纪律是 monkeypatch wiring.HOME 做沙箱隔离；实现模块若
    `from .harness_registry import HOME` 会在导入时绑定真实家目录，patch 失效
    ——本重构中 restore.py（15 败）与 takeover.py（24 败）各踩过一次，
    此守护把纪律从事后兜底升级为事前防住。新 wiring 实现模块须加入 IMPL_MODULES。
    """

    IMPL_MODULES = (
        "harness_registry",
        "qwen_wiring",
        "zcode_wiring",
        "codex_catalog",
        "relay_maint",
        "restore",
        "takeover",
        "status",
    )

    def test_no_impl_module_binds_home_global(self):
        import ast
        import pathlib

        import vision_relay

        pkg = pathlib.Path(vision_relay.__file__).parent
        offenders = []
        for mod in self.IMPL_MODULES:
            tree = ast.parse((pkg / f"{mod}.py").read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("harness_registry"):
                    if any(a.name == "HOME" for a in node.names):
                        offenders.append(f"{mod}: from-import 缓存 HOME")
                # harness_registry 是 HOME 的定义方（模块级赋值是其职责），豁免
                if mod != "harness_registry" and isinstance(node, ast.Assign):
                    if any(isinstance(t, ast.Name) and t.id == "HOME" for t in node.targets):
                        offenders.append(f"{mod}: 模块级 HOME 赋值")
        assert offenders == [], f"缓存 HOME 全局会使 monkeypatch wiring.HOME 失效: {offenders}"

    def test_wiring_home_follows_live_patch(self, monkeypatch):
        from vision_relay.harness_registry import _wiring_home

        monkeypatch.setattr(wiring, "HOME", "/nonexistent-sandbox-home")
        assert _wiring_home() == "/nonexistent-sandbox-home"

    def test_guard_lists_cover_all_impl_modules(self):
        """IMPL_MODULES 与包内实际实现模块同步（新增模块忘登记则本测试红）。"""
        import pathlib

        import vision_relay

        pkg = pathlib.Path(vision_relay.__file__).parent
        actual = {
            f.stem
            for f in pkg.glob("*.py")
            if f.stem
            not in {
                "wiring",  # 门面：re-export HOME 是其职责
                "__init__",
                "__main__",
                "annotate",
                "cache",
                "capability",
                "cli",  # CLI 门面（不用 wiring.HOME）
                "cli_args",  # CLI 子系统（不用 wiring.HOME）
                "cli_commands",
                "cli_lifecycle",
                "config",
                "env_util",
                "fingerprint",
                "ir",
                "locking",
                "logging_util",
                "model_sources",
                "onboarding",
                "pid_util",
                "pipeline",
                "probe",
                "reconcile",
                "route_fallback",
                "server",
                "snapshot",
                "stream",
                "tools",
                "verb_contract",  # 契约层（不用 wiring.HOME）
                "verbs",  # verbs 门面（DI seam，不用 wiring.HOME）
                "verbs_action",
                "verbs_obs",
                "verbs_probe",
                "verbs_vlm",
                "verbs_write",
                "visionlog",
                "vlm",
                "zcode_proc",
            }
        }
        assert actual == set(self.IMPL_MODULES), f"实现模块集合与守护清单不一致: {actual ^ set(self.IMPL_MODULES)}"
