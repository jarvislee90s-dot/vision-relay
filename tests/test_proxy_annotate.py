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
