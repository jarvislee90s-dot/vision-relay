"""auto annotation (spec §5): scan models -> probe/catalog/annotate into triple store."""

from vision_relay import annotate
from vision_relay.config import ProxyConfig


class TestRunProbe:
    def test_writes_probe_results_and_never_overwrites_user(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig.from_dict(
            {
                "model_capabilities": {"claude": {"bigmodel": {"m1": "text_only"}}},
                "capability_sources": {"claude": {"bigmodel": {"m1": "user"}}},
            }
        )
        monkeypatch.setattr(annotate, "_probe_one", lambda provider, model, *a: "image")
        result = annotate.run_probe(
            cfg, harness="claude", provider="bigmodel", model="m1", base_url="http://x", api_key="", protocol="chat"
        )
        assert result == "text_only"  # user 来源不被探针覆盖
        assert cfg.probe_results["bigmodel"]["m1"]["result"] == "image"  # 但缓存记录实测

    def test_overwrites_catalog_sourced(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig.from_dict(
            {
                "model_capabilities": {"claude": {"bigmodel": {"m1": "text_only"}}},
                "capability_sources": {"claude": {"bigmodel": {"m1": "catalog"}}},
            }
        )
        monkeypatch.setattr(annotate, "_probe_one", lambda provider, model, *a: "image")
        result = annotate.run_probe(
            cfg, harness="claude", provider="bigmodel", model="m1", base_url="http://x", api_key="", protocol="chat"
        )
        assert result == "image"
        assert cfg.model_capabilities["claude"]["bigmodel"]["m1"] == "image"
        assert cfg.capability_sources["claude"]["bigmodel"]["m1"] == "probe"


class TestAutoAnnotate:
    def test_new_model_annotated_from_probe_or_catalog(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig.from_dict({})
        scan = [
            {"harness": "claude", "provider": "bigmodel", "model": "glm-5-air"},  # probe 结论
            {"harness": "claude", "provider": "bigmodel", "model": "zzz-unknown"},  # 无探针->目录->仍未中->不落值
        ]
        monkeypatch.setattr(
            annotate,
            "_probe_one",
            lambda provider, model, *a: "text_only" if model == "glm-5-air" else None,
        )
        report = annotate.auto_annotate(cfg, scan, probe_targets={"glm-5-air": ("http://t", "", "chat")})
        assert cfg.model_capabilities["claude"]["bigmodel"]["glm-5-air"] == "text_only"
        assert cfg.capability_sources["claude"]["bigmodel"]["glm-5-air"] == "probe"
        assert "zzz-unknown" not in cfg.model_capabilities.get("claude", {}).get("bigmodel", {})  # 未标注=不落键
        assert any(r["model"] == "zzz-unknown" and r["result"] is None for r in report)

    def test_existing_user_value_untouched(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig.from_dict(
            {
                "model_capabilities": {"claude": {"bigmodel": {"m1": "image"}}},
                "capability_sources": {"claude": {"bigmodel": {"m1": "user"}}},
            }
        )
        annotate.auto_annotate(cfg, [{"harness": "claude", "provider": "bigmodel", "model": "m1"}], probe_targets={})
        assert cfg.model_capabilities["claude"]["bigmodel"]["m1"] == "image"


class TestSourcelessStoredProtected:
    """Major-1：caps 有存储值但 sources 缺键 = 存量用户数据（镜像 capability._stored 读方语义），绝不被自动标注覆盖。"""

    def test_run_probe_treats_sourceless_stored_as_user(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig.from_dict({"model_capabilities": {"claude": {"bigmodel": {"m1": "text_only"}}}})
        monkeypatch.setattr(annotate, "_probe_one", lambda provider, model, *a: "image")
        result = annotate.run_probe(
            cfg, harness="claude", provider="bigmodel", model="m1", base_url="http://x", api_key="", protocol="chat"
        )
        assert result == "text_only"  # 生效值=存储值，不被实测覆盖
        assert cfg.model_capabilities["claude"]["bigmodel"]["m1"] == "text_only"
        assert "m1" not in cfg.capability_sources.get("claude", {}).get("bigmodel", {})  # source 不被改写
        assert cfg.probe_results["bigmodel"]["m1"]["result"] == "image"  # 缓存仍如实记录实测

    def test_auto_annotate_treats_sourceless_stored_as_user(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig.from_dict({"model_capabilities": {"claude": {"bigmodel": {"m1": "image"}}}})

        def must_not_probe(*a, **k):
            raise AssertionError("user 数据（含无 source 存量值）不得触发探测改写")

        monkeypatch.setattr(annotate, "_probe_one", must_not_probe)
        report = annotate.auto_annotate(
            cfg,
            [{"harness": "claude", "provider": "bigmodel", "model": "m1"}],
            probe_targets={"m1": ("http://t", "", "chat")},
        )
        assert cfg.model_capabilities["claude"]["bigmodel"]["m1"] == "image"  # 未被覆盖
        assert report[0]["result"] == "image" and report[0]["by"] == "user"
        assert "m1" not in cfg.capability_sources.get("claude", {}).get("bigmodel", {})


class TestLockDiscipline:
    """Major-2：网络探测在锁外（单模型最长 30s 不能持锁探测）；写回落盘段持 config_lock。"""

    @staticmethod
    def _lock_held() -> bool:
        from vision_relay import locking

        lk = locking.try_config_lock()
        if lk is None:
            return True  # 拿不到=持锁中
        with lk:  # 拿到了立即归还，防 fd 泄漏卡死后续加锁
            return False

    def test_run_probe_probes_outside_and_saves_inside_lock(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        flags = {}

        def probe_spy(provider, model, *a):
            flags["probe_held"] = self._lock_held()
            return "image"

        def save_spy(cfg2, path=None):
            flags["save_held"] = self._lock_held()
            return path

        monkeypatch.setattr(annotate, "_probe_one", probe_spy)
        monkeypatch.setattr(annotate, "save_config", save_spy)
        cfg = ProxyConfig.from_dict({})
        annotate.run_probe(
            cfg, harness="claude", provider="bigmodel", model="m1", base_url="http://x", api_key="", protocol="chat"
        )
        assert flags.get("probe_held") is False  # 探测时未持锁
        assert flags.get("save_held") is True  # 写盘时持锁

    def test_auto_annotate_probes_outside_and_saves_inside_lock(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        flags = {}

        def probe_spy(provider, model, *a):
            flags["probe_held"] = self._lock_held()
            return "text_only"

        def save_spy(cfg2, path=None):
            flags["save_held"] = self._lock_held()
            return path

        monkeypatch.setattr(annotate, "_probe_one", probe_spy)
        monkeypatch.setattr(annotate, "save_config", save_spy)
        cfg = ProxyConfig.from_dict({})
        annotate.auto_annotate(
            cfg,
            [{"harness": "claude", "provider": "bigmodel", "model": "glm-5-air"}],
            probe_targets={"glm-5-air": ("http://t", "", "chat")},
        )
        assert flags.get("probe_held") is False
        assert flags.get("save_held") is True


class TestMinorContracts:
    def test_run_probe_user_branch_normalizes_legacy_vision(self, tmp_path, monkeypatch):
        """直接构造的内存数据可能残留 'vision'：user 分支生效值须归一为 'image'（对齐 capability._norm）。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig(
            model_capabilities={"claude": {"bigmodel": {"m1": "vision"}}},
            capability_sources={"claude": {"bigmodel": {"m1": "user"}}},
        )
        monkeypatch.setattr(annotate, "_probe_one", lambda provider, model, *a: "text_only")
        result = annotate.run_probe(
            cfg, harness="claude", provider="bigmodel", model="m1", base_url="http://x", api_key="", protocol="chat"
        )
        assert result == "image"

    def test_no_rewrite_when_cache_matches_stored(self, tmp_path, monkeypatch):
        """diff-aware：probe-cache 命中且值/来源均未变 → 不重写盘。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig.from_dict(
            {
                "model_capabilities": {"claude": {"bigmodel": {"m1": "text_only"}}},
                "capability_sources": {"claude": {"bigmodel": {"m1": "probe"}}},
                "probe_results": {"bigmodel": {"m1": {"result": "text_only", "ts": 1}}},
            }
        )
        calls = {"save": 0}

        def save_spy(cfg2, path=None):
            calls["save"] += 1
            return path

        def must_not_probe(*a, **k):
            raise AssertionError("cache 命中不得重新探测")

        monkeypatch.setattr(annotate, "save_config", save_spy)
        monkeypatch.setattr(annotate, "_probe_one", must_not_probe)
        report = annotate.auto_annotate(
            cfg, [{"harness": "claude", "provider": "bigmodel", "model": "m1"}], probe_targets={}
        )
        assert report[0]["result"] == "text_only" and report[0]["by"] == "probe-cache"
        assert calls["save"] == 0  # 无真差异 → 零写盘

    def test_summary_event_counts_scanned_and_changed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.reconcile import tail_events

        cfg = ProxyConfig.from_dict(
            {
                "model_capabilities": {"claude": {"bigmodel": {"m1": "image"}}},
                "capability_sources": {"claude": {"bigmodel": {"m1": "user"}}},
            }
        )
        monkeypatch.setattr(annotate, "_probe_one", lambda provider, model, *a: "text_only")
        annotate.auto_annotate(
            cfg,
            [
                {"harness": "claude", "provider": "bigmodel", "model": "glm-5-air"},  # 会改
                {"harness": "claude", "provider": "bigmodel", "model": "m1"},  # user 保护，不改
            ],
            probe_targets={"glm-5-air": ("http://t", "", "chat")},
        )
        ev = tail_events(1)[0]
        assert ev["type"] == "auto_annotate"
        assert ev["scanned"] == 2 and ev["changed"] == 1

    def test_run_probe_event_marks_applied(self, tmp_path, monkeypatch):
        """run_probe 事件带 applied：user 保护生效时 False，实际覆盖时 True。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.reconcile import tail_events

        monkeypatch.setattr(annotate, "_probe_one", lambda provider, model, *a: "image")
        cfg = ProxyConfig.from_dict(
            {
                "model_capabilities": {"claude": {"bigmodel": {"m1": "text_only"}}},
                "capability_sources": {"claude": {"bigmodel": {"m1": "user"}}},
            }
        )
        annotate.run_probe(
            cfg, harness="claude", provider="bigmodel", model="m1", base_url="http://x", api_key="", protocol="chat"
        )
        cfg2 = ProxyConfig.from_dict({})
        annotate.run_probe(
            cfg2, harness="claude", provider="bigmodel", model="m2", base_url="http://x", api_key="", protocol="chat"
        )
        evs = [e for e in tail_events(10) if e["type"] == "auto_annotate" and e.get("by") == "probe"]
        assert [e["applied"] for e in evs] == [False, True]
