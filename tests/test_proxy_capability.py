from __future__ import annotations

from vision_relay.capability import CapabilityTable
from vision_relay.config import ProxyConfig

BUILTIN = {
    "deepseek/*": "text_only",
    "glm/*": "text_only",
    "zai/*": "text_only",
    "openai/*": "image",
    "anthropic/*": "image",
    "google/*": "image",
    "qwen-vl-*": "image",
    "qwen3.5-omni-*": "image",
    "kimi-k2.7-code*": "image",
    "openrouter/deepseek/*": "text_only",
}


def test_builtin_list_matches_spec():
    table = CapabilityTable()
    assert table.judge("deepseek/deepseek-chat", ProxyConfig()) == "text_only"
    assert table.judge("qwen-vl-max", ProxyConfig()) == "image"
    assert table.judge("openrouter/deepseek/v3", ProxyConfig()) == "text_only"


def test_user_config_overrides_builtin():
    cfg = ProxyConfig(model_capabilities={"deepseek-vl-2": "vision"})  # 直接构造的旧词汇，judge 读取时归一
    assert CapabilityTable().judge("deepseek-vl-2", cfg) == "image"


def test_unknown_model_defaults_to_intercept():
    assert CapabilityTable().judge("mystery-model", ProxyConfig()) == "text_only"


# ---- Task1 fix: migrated shapes stay readable by current consumers ----


def test_migrated_flat_override_beats_builtin():
    """旧扁平用户覆盖迁到 global 组后仍须压过内置名单（不能被 builtin 'vision' 反杀）。"""
    cfg = ProxyConfig.from_dict({"model_capabilities": {"openai/*": "text_only"}})
    assert CapabilityTable().judge("openai/gpt-x1", cfg, "codex") == "text_only"


def test_migrated_grouped_two_level_readable():
    """旧两层 onboarding 产物（provider 记 'legacy'）迁三层后，本 harness 组仍可读且归一为 image。"""
    cfg = ProxyConfig.from_dict({"model_capabilities": {"claude": {"sonnet": "vision"}}})
    assert CapabilityTable().judge("sonnet", cfg, "claude") == "image"


def test_migrated_unknown_default_image_passthrough():
    """routing.unknown_default 旧值 'vision' 归一为 'image' 后：带图请求零开销直通（VLM 不被调用）。"""
    from vision_relay.cache import DescriptionCache
    from vision_relay.ir import parse_chat
    from vision_relay.pipeline import Pipeline

    class _SpyVLM:
        def __init__(self):
            self.calls = 0

        def describe(self, image, question=None, tier=1):
            self.calls += 1
            return "desc"

    cfg = ProxyConfig.from_dict({"routing": {"unknown_default": "vision"}})
    assert CapabilityTable().judge("mystery", cfg, "claude") == "image"
    ir = parse_chat(
        {
            "model": "mystery",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "看图"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
                    ],
                }
            ],
        }
    )
    spy = _SpyVLM()
    result = Pipeline(spy, DescriptionCache()).process(ir, cfg, "claude")
    assert spy.calls == 0 and result.vlm_calls == 0 and result.injected == 0
    assert any(b.type == "image" for m in result.ir.messages for b in m.content)  # 原图原样返回


# ---- Phase2 M1: triple-key tri-state resolution ----


def _cfg(caps=None, probe=None, unknown="text_only"):
    return ProxyConfig.from_dict(
        {
            "model_capabilities": caps or {},
            "probe_results": probe or {},
            "routing": {"unknown_default": unknown},
        }
    )


class TestTripleResolution:
    def test_user_override_wins(self):
        cfg = _cfg(
            caps={"claude": {"bigmodel": {"m1": "image"}}}, probe={"bigmodel": {"m1": {"result": "text_only", "ts": 1}}}
        )
        assert CapabilityTable().judge("m1", cfg, "claude", "bigmodel") == "image"

    def test_probe_beats_catalog_written(self):
        cfg = _cfg(
            caps={"claude": {"bigmodel": {"m1": "text_only"}}}, probe={"bigmodel": {"m1": {"result": "image", "ts": 1}}}
        )
        # caps 值来自 catalog 自动标注（sources 可证），probe 更新——判定取 probe
        cfg.capability_sources = {"claude": {"bigmodel": {"m1": "catalog"}}}
        assert CapabilityTable().judge("m1", cfg, "claude", "bigmodel") == "image"

    def test_user_beats_probe_when_source_user(self):
        cfg = _cfg(
            caps={"claude": {"bigmodel": {"m1": "text_only"}}}, probe={"bigmodel": {"m1": {"result": "image", "ts": 1}}}
        )
        cfg.capability_sources = {"claude": {"bigmodel": {"m1": "user"}}}
        assert CapabilityTable().judge("m1", cfg, "claude", "bigmodel") == "text_only"

    def test_probe_used_when_no_caps(self):
        cfg = _cfg(probe={"bigmodel": {"m2": {"result": "image", "ts": 1}}})
        assert CapabilityTable().judge("m2", cfg, "claude", "bigmodel") == "image"

    def test_builtin_pattern_fallback(self):
        cfg = _cfg()
        assert CapabilityTable().judge("qwen-vl-max", cfg, "claude", "any") == "image"
        assert CapabilityTable().judge("deepseek-v4", cfg, "codex", "any") == "text_only"

    def test_unannotated_falls_to_unknown_default_switch(self):
        assert CapabilityTable().judge("mystery", _cfg(unknown="text_only"), "claude", "p") == "text_only"
        assert CapabilityTable().judge("mystery", _cfg(unknown="image"), "claude", "p") == "image"

    def test_provider_mismatch_does_not_leak(self):
        cfg = _cfg(caps={"claude": {"provA": {"m1": "image"}}})
        assert CapabilityTable().judge("m1", cfg, "claude", "provB") == "text_only"  # 落到未标注→开关

    def test_harness_none_still_resolves_via_probe_and_builtin(self):
        cfg = _cfg(probe={"bigmodel": {"m1": {"result": "image", "ts": 1}}})
        assert CapabilityTable().judge("m1", cfg, None, "bigmodel") == "image"

    def test_cache_key_includes_provider(self):
        t = CapabilityTable()
        cfg = _cfg(probe={"a": {"m": {"result": "image", "ts": 1}}})
        assert t.judge("m", cfg, "claude", "a") == "image"
        assert t.judge("m", cfg, "claude", "b") == "text_only"  # 同名模型不同 provider 不同结果
