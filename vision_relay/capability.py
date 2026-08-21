"""Model capability judgment: user config > builtin list > unknown defaults to intercept."""

from __future__ import annotations

import fnmatch

from .config import ProxyConfig

BUILTIN_CAPABILITIES: dict[str, str] = {
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


class CapabilityTable:
    def __init__(self) -> None:
        self._cache: dict[tuple, str] = {}

    def judge(self, model: str, cfg: ProxyConfig, harness: str | None = None) -> str:
        key = (harness, model)
        if key in self._cache:
            return self._cache[key]
        capability = self._resolve(model, cfg, harness)
        self._cache[key] = capability
        return capability

    @staticmethod
    def _match_group(bucket: dict, model: str) -> str | None:
        """单组查找，兼容迁移后形态：两层 {model:cap} 精确/pattern、三层 {provider:{model:cap}} 子层精确。
        未命中返回 None（由调用方继续走后续优先级）。"""
        if model in bucket and isinstance(bucket[model], str):  # 两层精确
            return bucket[model]
        for sub in bucket.values():  # 三层 provider 子层精确（含迁移记的 'legacy'）
            if isinstance(sub, dict) and model in sub:
                return sub[model]
        for pattern, cap in bucket.items():  # 两层 pattern（旧扁平迁入 global 组的 'openai/*' 等）
            if isinstance(cap, str) and fnmatch.fnmatch(model, pattern):
                return cap
        return None

    @staticmethod
    def _resolve(model: str, cfg: ProxyConfig, harness: str | None = None) -> str:
        caps = cfg.model_capabilities
        # 1a. 按 harness 分组嵌套：先本组，再 global（旧扁平迁移后的兜底组）；
        #     组内兼容两层与迁移后三层形态，保持 用户存储 > 内置 的优先级
        for group in (harness, "global"):
            if group and isinstance(caps.get(group), dict):
                hit = CapabilityTable._match_group(caps[group], model)
                if hit is not None:
                    return hit
        # 1b. 旧扁平 map{model: cap}（未迁移前的兼容），顺序匹配命中即止
        for pattern, cap in caps.items():
            if isinstance(cap, str) and fnmatch.fnmatch(model, pattern):
                return cap
        # 2. 内置名单
        for pattern, cap in BUILTIN_CAPABILITIES.items():
            if fnmatch.fnmatch(model, pattern):
                return cap
        # 3. 未知 -> 默认（text_only 安全；可配 routing.unknown_default=image）
        return cfg.routing.unknown_default
