from __future__ import annotations

from vision_relay.capability import CapabilityTable
from vision_relay.config import ProxyConfig

BUILTIN = {
    "deepseek/*": "text_only",
    "glm/*": "text_only",
    "zai/*": "text_only",
    "openai/*": "vision",
    "anthropic/*": "vision",
    "google/*": "vision",
    "qwen-vl-*": "vision",
    "qwen3.5-omni-*": "vision",
    "kimi-k2.7-code*": "vision",
    "openrouter/deepseek/*": "text_only",
}


def test_builtin_list_matches_spec():
    table = CapabilityTable()
    assert table.judge("deepseek/deepseek-chat", ProxyConfig()) == "text_only"
    assert table.judge("qwen-vl-max", ProxyConfig()) == "vision"
    assert table.judge("openrouter/deepseek/v3", ProxyConfig()) == "text_only"


def test_user_config_overrides_builtin():
    cfg = ProxyConfig(model_capabilities={"deepseek-vl-2": "vision"})
    assert CapabilityTable().judge("deepseek-vl-2", cfg) == "vision"


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
