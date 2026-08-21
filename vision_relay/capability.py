"""Model capability: triple-key tri-state resolution (spec §5 模型能力标注).

判定优先级：用户覆盖（capability_sources=user；含无 sources 记录的存量数据——
两层/扁平旧形态一律视为历史用户数据）> 实测探针（probe_results）> 目录/建议写入值
（capability_sources=probe|catalog）> 内置模式名单 > 未标注（空，运行行为由
routing.unknown_default 开关决定：默认 text_only=走识图，安全侧）。
judge 只返回 "image" | "text_only"：存量 "vision" 在 config 层(from_dict)归一，
未经 from_dict 的内存数据（直接构造、onboarding 写入）在读取处兜底归一。
"""

from __future__ import annotations

import fnmatch

from .config import ProxyConfig

# 内置建议名单（models.dev 风格目录的内置快照；只取输入模态字段，spec §5）
BUILTIN_CAPABILITIES: dict[str, str] = {
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


def _norm(cap: str) -> str:
    """存量 'vision' 词汇读取时归一为 'image'（judge 输出契约只有 image|text_only）。"""
    return "image" if cap == "vision" else cap


class CapabilityTable:
    def __init__(self) -> None:
        self._cache: dict[tuple, str] = {}

    def judge(self, model: str, cfg: ProxyConfig, harness: str | None = None, provider: str | None = None) -> str:
        """判定 model 的输入模态（"image" | "text_only"），按 (harness, provider, model) 缓存。

        契约：缓存绑定单一 cfg 快照——本表实例只对构造时的判定结果负责，
        cfg（model_capabilities/probe_results/routing 等）后续变更不会反映到已缓存
        判定；cfg 变更后应丢弃旧表、换新实例重建缓存。
        """
        key = (harness, provider, model)
        if key in self._cache:
            return self._cache[key]
        capability = self._resolve(model, cfg, harness, provider)
        self._cache[key] = capability
        return capability

    @staticmethod
    def _stored(harness: str, provider: str, model: str, caps: dict, sources: dict) -> tuple[str, str] | None:
        """三层 {harness:{provider:{model:cap}}} 精确查找；返回 (cap, source) 或 None。
        sources 缺失的键按 "user" 处理（存量数据先于一切自动标注）。"""
        group = caps.get(harness)
        sub = group.get(provider) if isinstance(group, dict) else None
        cap = sub.get(model) if isinstance(sub, dict) else None
        if not isinstance(cap, str):
            return None
        gsrc = sources.get(harness)
        psrc = gsrc.get(provider) if isinstance(gsrc, dict) else None
        src = (psrc.get(model) if isinstance(psrc, dict) else None) or "user"
        return (cap, src)

    @staticmethod
    def _match_two_level(harness: str, model: str, caps: dict) -> str | None:
        """组内两层查找（Task1 迁移形态：旧扁平 pattern 迁入的 global 组、直接构造的
        {harness:{model:cap}}）：{model:cap} 精确优先，再组内 str 值 fnmatch pattern。
        两层条目无 sources 结构，视为历史用户数据；未命中返回 None。"""
        group = caps.get(harness)
        if not isinstance(group, dict):
            return None
        cap = group.get(model)
        if isinstance(cap, str):  # 两层精确
            return cap
        for pattern, c in group.items():  # 两层 pattern（global 组里的 'openai/*' 等）
            if isinstance(c, str) and fnmatch.fnmatch(model, pattern):
                return c
        return None

    @staticmethod
    def _resolve(model: str, cfg: ProxyConfig, harness: str | None = None, provider: str | None = None) -> str:
        caps = cfg.model_capabilities
        sources = cfg.capability_sources
        groups = (harness or "", "global")
        providers = (provider or "", "legacy")

        # 1) 用户覆盖，同层内子顺序：三层精确（含 global 兜底组、迁移记 'legacy'）优先于
        #    两层 pattern——两层条目并入用户层是 Task1 迁移形态的硬要求，否则 global 组里
        #    的旧扁平覆盖（如 openai/*: text_only）会被内置名单反杀。
        for h in groups:
            for p in providers:
                hit = CapabilityTable._stored(h, p, model, caps, sources)
                if hit is not None and hit[1] == "user":
                    return _norm(hit[0])
        for h in groups:
            cap = CapabilityTable._match_two_level(h, model, caps)
            if cap is not None:
                return _norm(cap)
        for pattern, cap in caps.items():  # 直接构造的旧扁平顶层 {model|pattern:cap}（未经 from_dict 迁移）
            if isinstance(cap, str) and fnmatch.fnmatch(model, pattern):
                return _norm(cap)

        # 2) 实测探针（provider 维度缓存，spec §5）
        if provider:
            entry = cfg.probe_results.get(provider, {}).get(model)
            result = entry.get("result") if isinstance(entry, dict) else None
            if result in ("image", "text_only"):
                return result

        # 3) 非 user 来源的存储值（probe/catalog 自动标注产物）
        for h in groups:
            for p in providers:
                hit = CapabilityTable._stored(h, p, model, caps, sources)
                if hit is not None:
                    return _norm(hit[0])

        # 4) 内置建议名单（模式匹配）
        for pattern, cap in BUILTIN_CAPABILITIES.items():
            if fnmatch.fnmatch(model, pattern):
                return cap

        # 5) 未标注 -> 开关（默认 text_only 安全侧）
        return _norm(cfg.routing.unknown_default)
