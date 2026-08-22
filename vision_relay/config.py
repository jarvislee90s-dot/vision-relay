"""Proxy configuration: JSON file (~/.vision-relay/proxy.json, 0600) + env overrides.

Spec says proxy.toml; the repo floor is Python 3.10 (no guaranteed tomllib), so we use JSON
(see plan Global Constraints). Read via vision_relay.env_util.get_env for env overrides.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

from .env_util import get_env

VLM_FORMATS = ("chat", "anthropic")

# relay 上游转发协议枚举：配置 proxy.json 时 protocol 字段只能是这三个之一（强校验）。
PROTOCOLS = ("anthropic", "responses", "chat")

# 已知的本地路由工具（两层拓扑）与其默认端口。via 只是描述性标签＋供 check 校验，
# 不参与 URL 拼接——URL 始终由 base_url 决定（回环=经工具两层、远端=直连一层）。
VIA_TOOLS = {"cc-switch": 15721, "codex-plus": 57321}

# start/stop 自动接线覆盖的 harness（第一跳 base_url→本代理）。
HARNESSES = ("claude", "codex", "qwen-code")


@dataclass
class RoutingConfig:
    """自动接线与路由的开关/状态。"""

    auto_wire: bool = True  # start 自动改三处 harness base_url→本代理、stop 自动还原
    harnesses: list[str] = field(default_factory=lambda: list(HARNESSES))  # 可排除任一
    relay_templates: dict[str, dict] = field(default_factory=dict)  # name -> {protocol,base_url,via?,models[]}
    capability_confirmed: bool = False  # 首次是否已显式确认过各模型看图能力
    unknown_default: str = "text_only"  # 未归类模型默认：text_only(安全) | vision
    activated_relays: list[str] = field(default_factory=list)  # start 激活、stop 还原的 relay name 记录

    def __post_init__(self) -> None:
        bad = [h for h in self.harnesses if h not in HARNESSES]
        if bad:
            raise ConfigError(
                f"routing.harnesses: unknown harness {','.join(map(repr, bad))}; must be in {list(HARNESSES)}"
            )
        if self.unknown_default not in ("text_only", "image", "vision"):
            raise ConfigError(f"routing.unknown_default must be 'text_only' or 'image', got {self.unknown_default!r}")


class ConfigError(Exception):
    """proxy.json 配置非法（显式错误，不静默回退默认配置）。"""


@dataclass
class VLMConfig:
    model: str = "qwen-vl-max"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = ""
    format: str = "chat"  # chat | anthropic（responses 留二阶段）
    cache_disk: bool = False
    auto_local_ollama: bool = True
    timeout_ms: int = 120_000
    max_tokens: int = 4096
    custom_tier1: str | None = None  # 自定义 Tier1 提示词（None=默认；spec §7.4）
    custom_tier2: str | None = None  # 自定义 Tier2 提示词（None=默认；spec §7.4）


@dataclass
class RelayConfig:
    name: str
    protocol: str  # anthropic | responses | chat
    base_url: str
    api_key: str = ""
    models: list[str] = field(default_factory=list)
    capability: str | None = None  # 显式覆盖能力判定
    via: str | None = None  # 可选：本 relay 是否经本地路由工具转发(两层拓扑)。纯描述性，不参与 URL 拼接。

    def __post_init__(self) -> None:
        if self.protocol not in PROTOCOLS:
            raise ConfigError(
                f"relay {self.name!r}: protocol must be one of {', '.join(PROTOCOLS)}, got {self.protocol!r}"
            )
        if self.via is not None and self.via not in VIA_TOOLS:
            raise ConfigError(
                f"relay {self.name!r}: via must be one of {sorted(VIA_TOOLS)} (or omitted), got {self.via!r}"
            )


CAPABILITY_VALUES = ("image", "text_only")

# capability_sources 叶子合法来源（spec §5）：用户覆盖 / 实测探针 / 目录建议。
CAPABILITY_SOURCE_VALUES = ("user", "probe", "catalog")


@dataclass
class VisionLogConfig:
    enabled: bool = True
    retention_days: int = 7

    def __post_init__(self) -> None:
        # 数字字符串收拢为 int（对齐 bind_port=int(...) 惯例）；不可解析值显式 ConfigError。
        try:
            self.retention_days = int(self.retention_days)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"vision_log.retention_days must be an integer, got {self.retention_days!r}") from exc
        # retention_days=0 会让 cleanup 按整日 cutoff 误删当日文件；要关闭留存请用 enabled=false。
        if self.retention_days < 1:
            raise ConfigError(
                f"vision_log.retention_days must be >= 1, got {self.retention_days!r}; "
                "to disable retention, set vision_log.enabled=false"
            )


@dataclass
class ProxyConfig:
    bind_host: str = "127.0.0.1"
    bind_port: int = 8787
    relays: list[RelayConfig] = field(default_factory=list)
    vlm: VLMConfig = field(default_factory=VLMConfig)
    vlm_by_harness: dict[str, dict] = field(default_factory=dict)  # harness -> 覆盖字段
    model_capabilities: dict[str, dict] = field(default_factory=dict)  # {harness:{provider:{model:cap}}}
    capability_sources: dict[str, dict] = field(default_factory=dict)  # 同构，值 user|probe|catalog
    probe_results: dict[str, dict] = field(default_factory=dict)  # {provider:{model:{result,ts}}}
    vision_log: VisionLogConfig = field(default_factory=VisionLogConfig)
    model_capabilities_legacy_flat_seen: bool = False  # 迁移标记（只读提示用）
    routing: RoutingConfig = field(default_factory=RoutingConfig)

    @classmethod
    def from_dict(cls, data: dict) -> "ProxyConfig":
        server = data.get("server", {})
        vlm = data.get("vlm", {})
        routing = dict(data.get("routing", {}))
        if routing.get("unknown_default") == "vision":
            routing["unknown_default"] = "image"
        caps, legacy_flat = _parse_capabilities(data.get("model_capabilities", {}))
        vlh = data.get("vlm_by_harness", {})
        if not isinstance(vlh, dict):
            raise ConfigError(f"vlm_by_harness: expected an object, got {type(vlh).__name__}")
        for hk, hv in vlh.items():
            if not isinstance(hv, dict):
                raise ConfigError(f"vlm_by_harness[{hk}]: expected an object, got {type(hv).__name__}")
        probe_results = _parse_probe_results(data.get("probe_results", {}))
        capability_sources = _parse_capability_sources(data.get("capability_sources", {}))
        return cls(
            bind_host=server.get("bind_host", "127.0.0.1"),
            bind_port=int(server.get("bind_port", 8787)),
            relays=_parse_relays(data.get("relays", [])),
            vlm=VLMConfig(**{k: v for k, v in vlm.items() if k in VLMConfig.__dataclass_fields__}),
            vlm_by_harness=vlh,
            model_capabilities=caps,
            capability_sources=capability_sources,
            probe_results=probe_results,
            vision_log=VisionLogConfig(
                **{k: v for k, v in data.get("vision_log", {}).items() if k in VisionLogConfig.__dataclass_fields__}
            ),
            model_capabilities_legacy_flat_seen=legacy_flat,
            routing=RoutingConfig(**{k: v for k, v in routing.items() if k in RoutingConfig.__dataclass_fields__}),
        )

    def to_dict(self) -> dict:
        return {
            "server": {"bind_host": self.bind_host, "bind_port": self.bind_port},
            "relays": [r.__dict__ for r in self.relays],
            "vlm": self.vlm.__dict__,
            "vlm_by_harness": self.vlm_by_harness,
            "model_capabilities": self.model_capabilities,
            "capability_sources": self.capability_sources,
            "probe_results": self.probe_results,
            "vision_log": self.vision_log.__dict__,
            "routing": self.routing.__dict__,
        }

    def vlm_for(self, harness: str | None) -> VLMConfig:
        """按 harness 合成生效 VLM 配置：显式覆盖字段 > 全局默认（spec §7.1）。
        None/空串视为未覆盖（回落全局）；False 等显式值必须生效。"""
        override = self.vlm_by_harness.get(harness or "", {})
        merged = {**self.vlm.__dict__, **{k: v for k, v in override.items() if v is not None and v != ""}}
        return VLMConfig(**{k: v for k, v in merged.items() if k in VLMConfig.__dataclass_fields__})


def _normalize_cap(value: object) -> str:
    if value == "vision":
        return "image"
    if value in CAPABILITY_VALUES:
        return str(value)
    raise ConfigError(
        f"model_capabilities: value must be one of {CAPABILITY_VALUES} (or legacy 'vision'), got {value!r}"
    )


def _parse_probe_results(raw: object) -> dict:
    """probe_results 轻校验 {provider:{model:{result,ts}}}：
    result 为遗留 'vision' 归一 'image'；其他非法值/形态 → ConfigError。"""
    if not isinstance(raw, dict):
        raise ConfigError(f"probe_results: expected an object, got {type(raw).__name__}")
    out: dict[str, dict] = {}
    for provider, models in raw.items():
        if not isinstance(models, dict):
            raise ConfigError(f"probe_results[{provider!r}]: expected an object, got {type(models).__name__}")
        bucket: dict[str, dict] = {}
        for model, entry in models.items():
            if not isinstance(entry, dict):
                raise ConfigError(f"probe_results[{provider!r}][{model!r}]: expected an object with result/ts")
            result = entry.get("result")
            if result == "vision":
                result = "image"
            if result not in CAPABILITY_VALUES:
                raise ConfigError(
                    f"probe_results[{provider!r}][{model!r}].result: must be one of {CAPABILITY_VALUES} "
                    f"(or legacy 'vision'), got {result!r}"
                )
            bucket[model] = {"result": result, "ts": entry.get("ts")}
        out[provider] = bucket
    return out


def _parse_capability_sources(raw: object) -> dict:
    """capability_sources 轻校验：叶子值必须 ∈ {user, probe, catalog}，否则 ConfigError。"""
    if not isinstance(raw, dict):
        raise ConfigError(f"capability_sources: expected an object, got {type(raw).__name__}")
    for harness, providers in raw.items():
        if not isinstance(providers, dict):
            raise ConfigError(f"capability_sources[{harness!r}]: expected an object, got {type(providers).__name__}")
        for provider, models in providers.items():
            if not isinstance(models, dict):
                raise ConfigError(
                    f"capability_sources[{harness!r}][{provider!r}]: expected an object, got {type(models).__name__}"
                )
            for model, src in models.items():
                if src not in CAPABILITY_SOURCE_VALUES:
                    raise ConfigError(
                        f"capability_sources[{harness!r}][{provider!r}][{model!r}]: must be one of "
                        f"{CAPABILITY_SOURCE_VALUES}, got {src!r}"
                    )
    return raw


def _parse_capabilities(raw: dict) -> tuple[dict, bool]:
    """三种历史形态归一为 {harness:{provider:{model:cap}}}：
    1) 新三层嵌套（provider 层可能是 'global' 等旧组名→视为 provider 名保留）；
    2) 旧两层 {group:{model:cap}}（onboarding 产物）→ group 作 harness、provider 记 'legacy'；
    3) 旧扁平 {pattern:cap} → 迁到 global 组（harness='global'，pattern 直接作键，
       与 capability.py 现有 caps['global'][model] 两层读法兼容）。
    返回 (caps, legacy_flat_seen)。"""
    caps: dict[str, dict] = {}
    legacy_flat = False
    for k, v in raw.items():
        if isinstance(v, str):  # 旧扁平 pattern -> cap
            legacy_flat = True
            caps.setdefault("global", {})[k] = _normalize_cap(v)
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                if isinstance(v2, str):  # 旧两层 group -> model
                    if k == "global":
                        # global 组吸收为两层 {pattern:cap}（与旧扁平迁移同形）：load→save→load 幂等固定点
                        caps.setdefault(k, {})[k2] = _normalize_cap(v2)
                    else:
                        caps.setdefault(k, {}).setdefault("legacy", {})[k2] = _normalize_cap(v2)
                elif isinstance(v2, dict):  # 新三层 harness -> provider -> model
                    bucket = caps.setdefault(k, {}).setdefault(k2, {})
                    for k3, v3 in v2.items():
                        bucket[k3] = _normalize_cap(v3)
                else:
                    raise ConfigError(f"model_capabilities[{k}][{k2}]: expected str or object")
        else:
            raise ConfigError(f"model_capabilities[{k}]: expected str or object")
    return caps, legacy_flat


def _parse_relays(raw: object) -> list[RelayConfig]:
    """逐条解析 relays；任一配置非法时抛带定位信息的 ConfigError（而非静默回退）。"""
    if not isinstance(raw, list):
        raise ConfigError(f"relays: expected a list, got {type(raw).__name__}")
    relays: list[RelayConfig] = []
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            raise ConfigError(f"relays[{i}]: expected an object, got {type(r).__name__}")
        name = r.get("name", "<unnamed>")
        try:
            relays.append(RelayConfig(**r))
        except ConfigError as exc:
            raise ConfigError(f"relays[{i}]: {exc}") from exc
        except TypeError as exc:
            raise ConfigError(f"relays[{i}] ({name}): {exc}") from exc
    return relays


def default_config() -> ProxyConfig:
    return ProxyConfig.from_dict({})


def _apply_env(cfg: ProxyConfig) -> ProxyConfig:
    """Env overrides (VISION_RELAY_*; legacy QWEN_MM_PROXY_* still honored, with a warning)."""
    if v := get_env("VISION_RELAY_BIND_PORT", legacy="QWEN_MM_PROXY_BIND_PORT"):
        cfg.bind_port = int(v)
    if v := get_env("VISION_RELAY_VLM_MODEL", legacy="QWEN_MM_PROXY_VLM_MODEL"):
        cfg.vlm.model = v
    if v := get_env("VISION_RELAY_VLM_BASE_URL", legacy="QWEN_MM_PROXY_VLM_BASE_URL"):
        cfg.vlm.base_url = v
    # API key 用 is not None 而非真值判断：环境变量显式设为空串也必须清掉配置里的 key。
    # 否则 walrus 写法会把空串 '' 当 falsy 跳过，导致 T7 这类"拔 VLM key 测 fail-open"永远失效
    # （proxy.json 里的 key 原样保留，VLM 照常被调用）。
    vlm_key = get_env("VISION_RELAY_VLM_API_KEY", legacy="QWEN_MM_PROXY_VLM_API_KEY")
    if vlm_key is not None:
        cfg.vlm.api_key = vlm_key
    if v := get_env("VISION_RELAY_VLM_FORMAT", legacy="QWEN_MM_PROXY_VLM_FORMAT"):
        if v in VLM_FORMATS:
            cfg.vlm.format = v
    return cfg


def save_config(cfg: ProxyConfig, path: str | None = None) -> str:
    """把 cfg 原子写回 proxy.json（0600）。onboarding/生命周期写入用它持久化。"""
    if path is None:
        from .env_util import config_dir

        path = os.path.join(config_dir(), "proxy.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def _default_config_path() -> str:
    from .env_util import config_dir

    return os.path.join(config_dir(), "proxy.json")


def _legacy_config_path() -> str:
    from .env_util import legacy_config_dir

    return os.path.join(legacy_config_dir(), "proxy.json")


def load_config(path: str | None = None) -> ProxyConfig:
    if path is None:
        path = _default_config_path()
        if not os.path.exists(path):
            legacy = _legacy_config_path()
            if os.path.exists(legacy):
                path = legacy
                print(
                    f"note: using legacy config {legacy}; it will be copied to {_default_config_path()} on next save (legacy file retained)",
                    file=sys.stderr,
                )
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        # 首次运行无配置文件：回退默认（合法），由 check 提示未配置。
        return default_config()
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read proxy.json: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"proxy.json: expected a JSON object at top level, got {type(raw).__name__}")
    try:
        cfg = ProxyConfig.from_dict(raw)
    except ConfigError:
        raise
    except (ValueError, TypeError, AttributeError) as exc:
        raise ConfigError(f"invalid proxy.json: {exc}") from exc
    return _apply_env(cfg)
