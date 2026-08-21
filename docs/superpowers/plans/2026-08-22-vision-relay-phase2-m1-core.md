# vision-relay 二期 M1（核心控制面）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **上游 spec（必须尊重，冲突时以 spec 为准）:** [`docs/superpowers/specs/2026-08-21-vision-relay-phase2-control-plane-design.md`](../specs/2026-08-21-vision-relay-phase2-control-plane-design.md) — 重点 §3（自动决策框架/图片术语）、§4（架构）、§5（对账矩阵/快照/修复/工具档案/模型能力标注）、§7（配置变更）、§10（测试与验收）、§11（M1 范围）。
> **AGENTS.md 是工程宪法**：协议解析只进 ir.py、fail-open 不可破坏、不写路由工具的配置、日志不带 key、测试先行、行为变更同步 spec。

**Goal:** 落地二期 M1 —— 命令行层面刷新 / 诊断 / 自动修复 / 模态探测全部可用，为 M2 GUI 提供全套 `--json` 管理动词。

**Architecture:** 管理逻辑全部长在 Python 核心（新模块 locking/tools/snapshot/probe/reconcile/visionlog/verbs），数据面（server/pipeline/ir）只做最小增量（按 harness 选 VLM、会话标识、识图留痕钩子）。对账引擎是唯一接线真相：所有触发源（start/stop/refresh/diagnose/自动修复）走 `reconcile.reconcile()`，自方写者经文件锁串行。

**Tech Stack:** Python 3.10+（仅标准库 + 已有 httpx）；pytest；无新第三方依赖。

**M1 范围内不做（YAGNI）:** GUI/Tauri、周期自动监听（M3）、安装包（M3）、自定义提示词编辑（M2 设置层）、relay 手工管理 UI、视频/音频探测、外部目录的在线更新（内置静态建议名单即可）。

**全局约定（所有任务遵守）:**
- 能力值内部表示：`"image" | "text_only"`；**缺失 = 未标注**。读入时 `"vision"` 归一化为 `"image"`（spec §3）。
- 三元组存储：`model_capabilities: {harness: {provider: {model: value}}}`；并行 `capability_sources: {harness: {provider: {model: "user"|"probe"|"catalog"}}}`；探针缓存 `probe_results: {provider: {model: {"result": …, "ts": …}}}`（spec §5/§7）。
- 运行时状态（非用户配置）放 `~/.vision-relay/state.json`（`routing_on`）；事件流水放 `~/.vision-relay/events.jsonl`。
- JSON 契约：所有 `--json` 输出形如 `{"contract_version": 1, "ok": bool, "data": …}`（spec §8）。
- 每个任务 TDD：先写失败测试 → 跑确认失败 → 实现 → 跑通过 → `ruff format vision_relay tests && ruff check vision_relay tests` → commit。

---

### Task 1: config —— 三态能力存储、迁移、VLM 分组、移除 ui_port

**Files:**
- Modify: `vision_relay/config.py`
- Test: `tests/test_proxy_config.py`

- [ ] **Step 1: 写失败测试（追加到 `tests/test_proxy_config.py` 末尾）**

```python
# ---- Phase2 M1: tri-state capabilities / migration / vlm_by_harness / ui_port ----


class TestTriStateCapabilities:
    def test_legacy_vision_normalized_to_image(self):
        cfg = ProxyConfig.from_dict(
            {"model_capabilities": {"claude": {"prov": {"m1": "vision", "m2": "text_only"}}}}
        )
        assert cfg.model_capabilities["claude"]["prov"]["m1"] == "image"

    def test_legacy_flat_migrated_to_global_bucket(self):
        cfg = ProxyConfig.from_dict({"model_capabilities": {"deepseek/*": "text_only"}})
        assert cfg.model_capabilities["global"]["deepseek/*"] == "text_only"

    def test_legacy_grouped_kept(self):
        cfg = ProxyConfig.from_dict({"model_capabilities": {"claude": {"m1": "vision"}}})
        assert cfg.model_capabilities["claude"]["legacy"]["m1"] == "image"  # 旧两层→provider 记 'legacy'

    def test_absent_means_unannotated(self):
        cfg = ProxyConfig.from_dict({})
        assert "claude" not in cfg.model_capabilities  # 未标注 = 键缺失

    def test_invalid_value_rejected(self):
        import pytest

        with pytest.raises(ConfigError):
            ProxyConfig.from_dict({"model_capabilities": {"claude": {"p": {"m": "movie"}}}})

    def test_sources_roundtrip(self):
        cfg = ProxyConfig.from_dict(
            {"capability_sources": {"claude": {"prov": {"m1": "probe"}}}}
        )
        assert cfg.capability_sources["claude"]["prov"]["m1"] == "probe"
        assert cfg.to_dict()["capability_sources"] == {"claude": {"prov": {"m1": "probe"}}}

    def test_probe_results_roundtrip(self):
        cfg = ProxyConfig.from_dict(
            {"probe_results": {"prov": {"m": {"result": "image", "ts": 1}}}}
        )
        assert cfg.probe_results["prov"]["m"]["result"] == "image"


class TestUnknownDefault:
    def test_accepts_image_alias(self):
        assert ProxyConfig.from_dict({"routing": {"unknown_default": "image"}}).routing.unknown_default == "image"

    def test_accepts_legacy_vision(self):
        assert ProxyConfig.from_dict({"routing": {"unknown_default": "vision"}}).routing.unknown_default == "image"

    def test_rejects_other(self):
        import pytest

        with pytest.raises(ConfigError):
            ProxyConfig.from_dict({"routing": {"unknown_default": "movie"}})


class TestUiPortRemoved:
    def test_to_dict_has_no_ui_port(self):
        assert "ui_port" not in ProxyConfig.from_dict({}).to_dict()["server"]

    def test_from_dict_ignores_ui_port(self):
        cfg = ProxyConfig.from_dict({"server": {"ui_port": 8788}})
        assert not hasattr(cfg, "ui_port")


class TestVlmByHarness:
    def test_default_empty_and_roundtrip(self):
        cfg = ProxyConfig.from_dict({})
        assert cfg.vlm_by_harness == {}
        cfg.vlm_by_harness["claude"] = {"model": "qwen3.5-omni-plus", "api_key": "k"}
        assert cfg.to_dict()["vlm_by_harness"]["claude"]["model"] == "qwen3.5-omni-plus"

    def test_partial_fields_fill_from_defaults(self):
        cfg = ProxyConfig.from_dict({"vlm_by_harness": {"codex": {"model": "m"}}})
        merged = cfg.vlm_for("codex")
        assert merged.model == "m"
        assert merged.base_url == cfg.vlm.base_url  # 未填回落全局


class TestVisionLogConfig:
    def test_defaults(self):
        cfg = ProxyConfig.from_dict({})
        assert cfg.vision_log.enabled is True
        assert cfg.vision_log.retention_days == 7

    def test_roundtrip(self):
        cfg = ProxyConfig.from_dict({"vision_log": {"enabled": False, "retention_days": 3}})
        assert cfg.to_dict()["vision_log"] == {"enabled": False, "retention_days": 3}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_proxy_config.py -q -k "TriState or UnknownDefault or UiPort or VlmByHarness or VisionLogConfig"`
Expected: FAIL（`capability_sources`/`probe_results`/`vlm_by_harness`/`vision_log` 属性不存在、ui_port 仍在 to_dict）

- [ ] **Step 3: 修改 `vision_relay/config.py`**

3a. `RoutingConfig.unknown_default` 校验放宽 + 新增两个顶层配置。在 `RoutingConfig.__post_init__` 里，把

```python
        if self.unknown_default not in ("text_only", "vision"):
            raise ConfigError(f"routing.unknown_default must be 'text_only' or 'vision', got {self.unknown_default!r}")
```

改为（注意：dataclass 默认值 `"text_only"` 保持，归一化放在 `ProxyConfig.from_dict`）：

```python
        if self.unknown_default not in ("text_only", "image", "vision"):
            raise ConfigError(f"routing.unknown_default must be 'text_only' or 'image', got {self.unknown_default!r}")
```

3b. 删除 `ProxyConfig` 的 `ui_port` 字段；新增字段与解析。`ProxyConfig` 改为：

```python
CAPABILITY_VALUES = ("image", "text_only")


@dataclass
class VisionLogConfig:
    enabled: bool = True
    retention_days: int = 7


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
```

`from_dict` 改为（能力归一化 + 三种旧结构迁移 + unknown_default 归一化 + ui_port 忽略）：

```python
    @classmethod
    def from_dict(cls, data: dict) -> "ProxyConfig":
        server = data.get("server", {})
        vlm = data.get("vlm", {})
        routing = dict(data.get("routing", {}))
        if routing.get("unknown_default") == "vision":
            routing["unknown_default"] = "image"
        caps, sources, legacy_flat = _parse_capabilities(data.get("model_capabilities", {}))
        vlh = data.get("vlm_by_harness", {})
        if not isinstance(vlh, dict):
            raise ConfigError(f"vlm_by_harness: expected an object, got {type(vlh).__name__}")
        probe_results = data.get("probe_results", {})
        if not isinstance(probe_results, dict):
            raise ConfigError(f"probe_results: expected an object, got {type(probe_results).__name__}")
        return cls(
            bind_host=server.get("bind_host", "127.0.0.1"),
            bind_port=int(server.get("bind_port", 8787)),
            relays=_parse_relays(data.get("relays", [])),
            vlm=VLMConfig(**{k: v for k, v in vlm.items() if k in VLMConfig.__dataclass_fields__}),
            vlm_by_harness=vlh,
            model_capabilities=caps,
            capability_sources=data.get("capability_sources", {}),
            probe_results=probe_results,
            vision_log=VisionLogConfig(
                **{k: v for k, v in data.get("vision_log", {}).items() if k in VisionLogConfig.__dataclass_fields__}
            ),
            model_capabilities_legacy_flat_seen=legacy_flat,
            routing=RoutingConfig(**{k: v for k, v in routing.items() if k in RoutingConfig.__dataclass_fields__}),
        )
```

新增模块级函数（放在 `_parse_relays` 之前）：

```python
def _normalize_cap(value: object) -> str:
    if value == "vision":
        return "image"
    if value in CAPABILITY_VALUES:
        return str(value)
    raise ConfigError(f"model_capabilities: value must be one of {CAPABILITY_VALUES} (or legacy 'vision'), got {value!r}")


def _parse_capabilities(raw: dict) -> tuple[dict, bool]:
    """三种历史形态归一为 {harness:{provider:{model:cap}}}：
    1) 新三层嵌套（provider 层可能是 'global' 等旧组名→视为 provider 名保留）；
    2) 旧两层 {group:{model:cap}}（onboarding 产物）→ group 作 harness、provider 记 'legacy'；
    3) 旧扁平 {pattern:cap} → 迁到 global 组（harness='global', provider='legacy'）。
    返回 (caps, legacy_flat_seen)。"""
    caps: dict[str, dict] = {}
    legacy_flat = False
    for k, v in raw.items():
        if isinstance(v, str):  # 旧扁平 pattern -> cap
            legacy_flat = True
            caps.setdefault("global", {}).setdefault("legacy", {})[k] = _normalize_cap(v)
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                if isinstance(v2, str):  # 旧两层 group -> model
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
```

`to_dict` 改为（ui_port 移除、新键落盘）：

```python
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
```

新增 VLM 分组解析方法（`ProxyConfig` 成员）：

```python
    def vlm_for(self, harness: str | None) -> VLMConfig:
        """按 harness 合成生效 VLM 配置：显式覆盖字段 > 全局默认（spec §7.1）。"""
        override = self.vlm_by_harness.get(harness or "", {})
        merged = {**self.vlm.__dict__, **{k: v for k, v in override.items() if v}}
        return VLMConfig(**{k: v for k, v in merged.items() if k in VLMConfig.__dataclass_fields__})
```

- [ ] **Step 4: 修复既有测试中被本任务破坏的断言**

Run: `python -m pytest tests/test_proxy_config.py -q`
对失败用例逐一处理：所有引用 `cfg.ui_port` / `to_dict()["server"]["ui_port"]` 的旧断言删除或改为 `bind_port`；旧用例里 `"unknown_default": "vision"` 若断言回显 `"vision"`，改为断言 `"image"`。**不许放宽新测试。**

- [ ] **Step 5: 全量跑 + lint + commit**

Run: `python -m pytest -q`（记录通过数，后续任务不得低于此数）；`ruff format vision_relay tests && ruff check vision_relay tests`

```bash
git add vision_relay/config.py tests/test_proxy_config.py
git commit -m "feat(config): tri-state capability store (harness/provider/model) with legacy migration, vlm_by_harness, vision_log settings; drop ui_port"
```

---

### Task 2: capability —— 三元组判定（用户 > 探针 > 建议 > 内置 > 未标注）

**Files:**
- Modify: `vision_relay/capability.py`
- Test: `tests/test_proxy_capability.py`

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---- Phase2 M1: triple-key tri-state resolution ----
from vision_relay.config import ProxyConfig


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
        cfg = _cfg(caps={"claude": {"bigmodel": {"m1": "image"}}}, probe={"bigmodel": {"m1": {"result": "text_only", "ts": 1}}})
        assert CapabilityTable().judge("m1", cfg, "claude", "bigmodel") == "image"

    def test_probe_beats_catalog_written(self):
        cfg = _cfg(caps={"claude": {"bigmodel": {"m1": "text_only"}}},
                   probe={"bigmodel": {"m1": {"result": "image", "ts": 1}}})
        # caps 值来自 catalog 自动标注（sources 可证），probe 更新——判定取 probe
        cfg.capability_sources = {"claude": {"bigmodel": {"m1": "catalog"}}}
        assert CapabilityTable().judge("m1", cfg, "claude", "bigmodel") == "image"

    def test_user_beats_probe_when_source_user(self):
        cfg = _cfg(caps={"claude": {"bigmodel": {"m1": "text_only"}}},
                   probe={"bigmodel": {"m1": {"result": "image", "ts": 1}}})
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
```

- [ ] **Step 2: 跑确认失败**

Run: `python -m pytest tests/test_proxy_capability.py -q -k Triple`
Expected: FAIL —— `judge()` 不接受 provider 参数

- [ ] **Step 3: 重写 `vision_relay/capability.py`**

```python
"""Model capability: triple-key tri-state resolution (spec §5 模型能力标注).

判定优先级：用户覆盖（capability_sources=user）> 实测探针（probe_results）>
目录/建议写入值（capability_sources=probe|catalog）> 内置模式名单 > 未标注（空，
运行行为由 routing.unknown_default 开关决定：默认 text_only=走识图，安全侧）。
内部值只有 "image" | "text_only"（"vision" 在 config 层已归一为 "image"）。
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


class CapabilityTable:
    def __init__(self) -> None:
        self._cache: dict[tuple, str] = {}

    def judge(
        self, model: str, cfg: ProxyConfig, harness: str | None = None, provider: str | None = None
    ) -> str:
        key = (harness, provider, model)
        if key in self._cache:
            return self._cache[key]
        capability = self._resolve(model, cfg, harness, provider)
        self._cache[key] = capability
        return capability

    @staticmethod
    def _resolve(model: str, cfg: ProxyConfig, harness: str | None, provider: str | None) -> str:
        caps = cfg.model_capabilities
        sources = cfg.capability_sources

        def stored(h: str, p: str) -> str | None:
            if caps.get(h, {}).get(p, {}).get(model) is None:
                return None
            src = (sources.get(h, {}).get(p, {}) or {}).get(model, "user")
            return (model, caps[h][p][model], src)  # type: ignore[return-value]

        # 1) 用户覆盖：显式 user 来源（任何 harness/provider 组合，含 global 兜底组）
        for h in (harness or "", "global"):
            for p in (provider or "", "legacy"):
                hit = stored(h, p)
                if hit and hit[2] == "user":
                    return hit[1]
        # 2) 实测探针（provider 维度缓存，spec §5）
        if provider and model in cfg.probe_results.get(provider, {}):
            result = cfg.probe_results[provider][model].get("result")
            if result in ("image", "text_only"):
                return result
        # 3) 非 user 来源的存储值（probe/catalog 自动标注产物）
        for h in (harness or "", "global"):
            for p in (provider or "", "legacy"):
                hit = stored(h, p)
                if hit:
                    return hit[1]
        # 4) 内置建议名单（模式匹配）
        for pattern, cap in BUILTIN_CAPABILITIES.items():
            if fnmatch.fnmatch(model, pattern):
                return cap
        # 5) 未标注 -> 开关（默认 text_only 安全侧）
        return cfg.routing.unknown_default
```

注意 `stored()` 返回三元组供来源判断；保持类型简单（内部元组）。

- [ ] **Step 4: 修 pipeline 调用点（本任务只改签名透传，不改行为）**

`vision_relay/pipeline.py` 的 `Pipeline.process`：

```python
    def process(self, ir: IRRequest, cfg: ProxyConfig, harness: str | None = None, provider: str | None = None) -> ProcessResult:
        if self.table.judge(ir.model, cfg, harness, provider) == "image":
            return ProcessResult(ir=ir)  # image model: zero overhead passthrough
```

`vision_relay/server.py` 的 `do_POST` 里调用处（先只透传 `provider=None`，Task 12 再补真实 provider）：

```python
            result = self._pipeline.process(ir, self._cfg, _HARNESS_BY_PROTO.get(proto), None)
```

- [ ] **Step 5: 全量测试 + 既有断言修复**

Run: `python -m pytest -q`
旧测试中 `judge(model, cfg, harness) == "vision"` 的断言改为 `== "image"`；`BUILTIN_CAPABILITIES` 值断言 `"vision"`→`"image"`。pipeline 直通用例同理。

- [ ] **Step 6: lint + commit**

```bash
git add vision_relay/capability.py vision_relay/pipeline.py vision_relay/server.py tests/test_proxy_capability.py tests/test_proxy_pipeline.py
git commit -m "feat(capability): triple-key tri-state resolution (user > probe > suggested > builtin > switch)"
```

---

### Task 3: locking —— 自方写者跨进程文件锁

**Files:**
- Create: `vision_relay/locking.py`
- Test: `tests/test_proxy_locking.py`

- [ ] **Step 1: 写失败测试 `tests/test_proxy_locking.py`（新文件）**

```python
"""config_lock: 自方多进程/多线程写者互斥（spec §4 多写者对策 2）。"""

import os
import threading

import pytest

from vision_relay import locking


def test_lock_is_reentrant_per_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    with locking.config_lock():
        with locking.config_lock():  # 同线程可重入
            pass


def test_lock_blocks_second_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    acquired = []

    def try_lock():
        got = locking.try_config_lock()
        if got is not None:
            acquired.append(True)
            got.__exit__(None, None, None)

    with locking.config_lock():
        t = threading.Thread(target=try_lock)
        t.start()
        t.join(timeout=5)
    assert acquired == [], "持锁期间第二个写者必须拿不到锁"


def test_lock_released_after_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    with locking.config_lock():
        pass
    with locking.try_config_lock() as lk:
        assert lk is not None


def test_lock_file_created_in_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    with locking.config_lock():
        assert os.path.exists(str(tmp_path / "relay.lock"))
```

- [ ] **Step 2: 跑确认失败**

Run: `python -m pytest tests/test_proxy_locking.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `vision_relay/locking.py`**

```python
"""Cross-process advisory lock for vision-relay's own writers (spec §4).

同一进程内同线程可重入（threading.RLock + 计数）；跨进程用 OS 文件锁
（Windows msvcrt.locking / Unix fcntl.flock）。外部工具不受此锁约束——
它们靠对账收敛（spec §5），本锁只排队“我们自己人”。
"""

from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path

_reentrant = threading.local()


def _lock_path() -> Path:
    from .env_util import config_dir

    return Path(config_dir()) / "relay.lock"


@contextlib.contextmanager
def config_lock(timeout_s: float | None = None):
    """阻塞获取（默认）；同线程重入直接通过。"""
    token = getattr(_reentrant, "depth", 0)
    if token > 0:
        _reentrant.depth = token + 1
        try:
            yield
        finally:
            _reentrant.depth -= 1
        return
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _os_lock(fd, blocking=True)
        _reentrant.depth = 1
        yield
    finally:
        if getattr(_reentrant, "depth", 0) > 0:
            _reentrant.depth = 0
            _os_unlock(fd)
        os.close(fd)


def try_config_lock():
    """非阻塞尝试；拿不到返回 None（不抛异常，方便轮询方使用）。"""
    if getattr(_reentrant, "depth", 0) > 0:
        return contextlib.nullcontext()
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    if not _os_lock(fd, blocking=False):
        os.close(fd)
        return None

    @contextlib.contextmanager
    def _held():
        try:
            yield
        finally:
            _os_unlock(fd)
            os.close(fd)

    return _held()


def _os_lock(fd: int, blocking: bool) -> bool:
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK if not blocking else msvcrt.LK_LOCK, 1)
            return True
        except OSError:
            if not blocking:
                return False
            # msvcrt.LK_LOCK 自身重试 10 次；再不行人工小睡重试
            import time

            deadline = time.time() + 30
            while time.time() < deadline:
                time.sleep(0.2)
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    return True
                except OSError:
                    continue
            return False
    import fcntl

    flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fcntl.flock(fd, flags)
        return True
    except OSError:
        return False


def _os_unlock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
```

- [ ] **Step 4: 跑通过 + lint + commit**

Run: `python -m pytest tests/test_proxy_locking.py -q` → PASS

```bash
git add vision_relay/locking.py tests/test_proxy_locking.py
git commit -m "feat(locking): cross-process advisory lock for vision-relay writers"
```

---

### Task 4: snapshot —— 接管组合快照（不存密钥值）

**Files:**
- Create: `vision_relay/snapshot.py`
- Test: `tests/test_proxy_snapshot.py`

- [ ] **Step 1: 写失败测试（新文件）**

```python
"""takeover combo snapshots (spec §5 备份、快照与回退语义)."""

import json
import time

from vision_relay import snapshot


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    snap = snapshot.Snapshot(
        base_url="https://open.bigmodel.cn/api/anthropic",
        key_ref="env.ANTHROPIC_AUTH_TOKEN",
        model="glm-5-air",
        second_hop="cc-switch",
    )
    snapshot.save("claude", snap)
    loaded = snapshot.load()["claude"]
    assert loaded.base_url == snap.base_url
    assert loaded.key_ref == "env.ANTHROPIC_AUTH_TOKEN"  # 只存位置，绝不存 key 值
    assert loaded.second_hop == "cc-switch"
    assert loaded.ts > 0


def test_latest_only_no_history(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    snapshot.save("codex", snapshot.Snapshot(base_url="http://a", key_ref="k", model="m"))
    time.sleep(0.01)
    snapshot.save("codex", snapshot.Snapshot(base_url="http://b", key_ref="k", model="m"))
    assert snapshot.load()["codex"].base_url == "http://b"  # 每 harness 只存最新一条
    raw = json.loads((tmp_path / "snapshots.json").read_text(encoding="utf-8"))
    assert len(raw["codex"]) == 1 or isinstance(raw["codex"], dict)


def test_missing_harness_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    assert "claude" not in snapshot.load()


def test_never_stores_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    snapshot.save("claude", snapshot.Snapshot(base_url="u", key_ref="env.ANTHROPIC_AUTH_TOKEN", model="m"))
    text = (tmp_path / "snapshots.json").read_text(encoding="utf-8")
    assert "sk-" not in text and "api_key" not in text


def test_key_ref_probe_claude(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "sk-secret"}}), encoding="utf-8"
    )
    monkeypatch.setattr(snapshot, "HOME", str(home))
    assert "ANTHROPIC_AUTH_TOKEN" in snapshot.key_ref_for("claude")
    assert "sk-secret" not in snapshot.key_ref_for("claude")
```

- [ ] **Step 2: 跑确认失败** → `python -m pytest tests/test_proxy_snapshot.py -q` FAIL（模块不存在）

- [ ] **Step 3: 实现 `vision_relay/snapshot.py`**

```python
"""Takeover combo snapshots (spec §5): base_url + key LOCATION + model per harness.

防外部工具档案污染（CC Switch 会把 live 文件回读进它的供应商档案）：本代理
始终持有“接管前正确组合”的真相；任何还原按组合写回。每 harness 只存最新一条。
**绝不存密钥值**，只存 key 所在配置位置描述。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass

# 测试可 monkeypatch。
HOME = os.path.expanduser("~")

_KEY_FIELDS = {
    "claude": ((".claude", "settings.json"), ("env.ANTHROPIC_AUTH_TOKEN", "env.ANTHROPIC_API_KEY")),
    "codex": ((".codex", "auth.json"), ("OPENAI_API_KEY",)),
    "qwen-code": ((".qwen", "settings.json"), ("model.apiKey",)),
}


@dataclass
class Snapshot:
    base_url: str
    key_ref: str  # key 所在位置描述（非值）
    model: str
    second_hop: str | None = None  # 接管时该 harness 的第二跳工具名（cc-switch / codex-plus）
    ts: float = 0.0

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = time.time()


def _path() -> str:
    from .env_util import config_dir

    return os.path.join(config_dir(), "snapshots.json")


def save(harness: str, snap: Snapshot) -> None:
    data = {}
    if os.path.exists(_path()):
        try:
            data = json.load(open(_path(), encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    data[harness] = asdict(snap)  # 最新一条覆盖
    tmp = _path() + ".tmp"
    os.makedirs(os.path.dirname(_path()), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, _path())


def load() -> dict[str, Snapshot]:
    try:
        raw = json.load(open(_path(), encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {h: Snapshot(**v) for h, v in raw.items() if isinstance(v, dict)}


def key_ref_for(harness: str) -> str:
    """探测该 harness 配置里 key 的存在位置（只回位置名，不回值）。"""
    rel, fields = _KEY_FIELDS.get(harness, (None, ()))
    if rel is None:
        return "unknown"
    p = os.path.join(HOME, *rel)
    if not os.path.exists(p):
        return "not-found"
    if harness == "codex":
        return os.path.basename(p)  # auth.json 存在即视为 key 位置
    try:
        d = json.load(open(p, encoding="utf-8"))
        hit = [f for f in fields if _dig(d, f) not in (None, "")]
        return "|".join(hit) if hit else "not-found"
    except (OSError, ValueError):
        return "unparsable"


def _dig(d: dict, dotted: str):
    node = d
    for part in dotted.split("."):
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return None
    return node
```

- [ ] **Step 4: 跑通过 + lint + commit**

```bash
python -m pytest tests/test_proxy_snapshot.py -q
git add vision_relay/snapshot.py tests/test_proxy_snapshot.py
git commit -m "feat(snapshot): per-harness takeover combo snapshot (key location only)"
```

---

### Task 5: tools —— 路由工具档案（端口探测 + 激活供应商只读）

**Files:**
- Create: `vision_relay/tools.py`
- Test: `tests/test_proxy_tools.py`

- [ ] **Step 1: 写失败测试（新文件）**

```python
"""tool dossiers (spec §5 路由工具档案): port probe + read-only active provider."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from vision_relay import tools


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestDossiers:
    def test_registry_covers_both_tools_with_harness_matrix(self):
        assert tools.TOOL_DOSSIERS["cc-switch"].harnesses == ("claude", "codex")
        assert tools.TOOL_DOSSIERS["codex-plus"].harnesses == ("codex",)

    def test_relay_template_per_harness(self):
        assert tools.relay_template("cc-switch", "claude")["protocol"] == "anthropic"
        assert tools.relay_template("cc-switch", "codex")["protocol"] == "chat"
        assert tools.relay_template("codex-plus", "codex")["protocol"] == "responses"
        assert tools.relay_template("cc-switch", "qwen-code") is None  # 不支持的工具-harness 组合


class TestPortProbe:
    def test_online_when_listening(self):
        port = _free_port()
        srv = HTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            states = tools.probe_tools(port_overrides={"cc-switch": port})
            cc = next(s for s in states if s.name == "cc-switch")
            assert cc.online is True
            assert cc.port == port
        finally:
            srv.shutdown()

    def test_offline_when_closed(self):
        port = _free_port()  # bound then closed -> nothing listens
        states = tools.probe_tools(port_overrides={"cc-switch": port})
        cc = next(s for s in states if s.name == "cc-switch")
        assert cc.online is False
        assert cc.active_provider is None and cc.provider_base_url is None


class TestCodexPlusProvider:
    def test_reads_active_relay_profile(self, tmp_path, monkeypatch):
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "activeRelayId": "relay-b",
                    "relayProfiles": [
                        {"id": "relay-a", "name": "Old", "baseUrl": "https://a.example"},
                        {"id": "relay-b", "name": "deepseek", "baseUrl": "https://api.deepseek.com"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(tools, "CODEXPP_SETTINGS", str(settings))
        name, url = tools._codexpp_active_provider()
        assert name == "deepseek" and url == "https://api.deepseek.com"

    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tools, "CODEXPP_SETTINGS", str(tmp_path / "nope.json"))
        assert tools._codexpp_active_provider() == (None, None)


class TestCcSwitchProvider:
    def test_status_endpoint_fallback(self, monkeypatch):
        class _Resp:
            status_code = 200

            def json(self):
                return {"current_providers": {"claude": {"name": "bigmodel", "baseUrl": "https://open.bigmodel.cn"}}}

        monkeypatch.setattr(
            tools.httpx, "get", lambda *a, **k: _Resp(), raising=False
        )
        monkeypatch.setattr(tools, "_ccswitch_sqlite_provider", lambda: None)
        name, url = tools._ccswitch_active_provider(port=15721)
        assert name == "bigmodel" and url == "https://open.bigmodel.cn"

    def test_sqlite_before_http(self, monkeypatch):
        monkeypatch.setattr(tools, "_ccswitch_sqlite_provider", lambda: ("from-db", "https://db.example"))
        name, url = tools._ccswitch_active_provider(port=15721)
        assert (name, url) == ("from-db", "https://db.example")
```

- [ ] **Step 2: 跑确认失败** → `python -m pytest tests/test_proxy_tools.py -q` FAIL

- [ ] **Step 3: 实现 `vision_relay/tools.py`**

```python
"""Tool dossiers (spec §5): known local routing tools — port probe + active provider (READ-ONLY).

铁律：只读。绝不写 CC Switch / Codex++ 的任何配置或数据库。
探测=端口通断（不做内容指纹）；激活供应商读取按档案配置，全部 best-effort，
失败返回 (None, None)——真实上游显示退到“由工具决定（未知）”。
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass

import httpx

# Codex++ manager settings（若上游改路径，这里单点可改；缺失即 best-effort 失败）
CODEXPP_SETTINGS = os.path.expanduser("~/.codex-session-delete/settings.json")
CCSWITCH_DB = os.path.expanduser("~/.cc-switch/cc-switch.db")


@dataclass(frozen=True)
class ToolDossier:
    name: str
    port: int
    harnesses: tuple[str, ...]


TOOL_DOSSIERS: dict[str, ToolDossier] = {
    "cc-switch": ToolDossier("cc-switch", 15721, ("claude", "codex")),
    "codex-plus": ToolDossier("codex-plus", 57321, ("codex",)),
}

# (tool, harness) -> relay 模板（RelayConfig kwargs，不含 name；spec §12.2 + §5）
_TEMPLATES: dict[tuple[str, str], dict] = {
    ("cc-switch", "claude"): {"protocol": "anthropic", "base_url": "http://127.0.0.1:15721", "via": "cc-switch", "models": ["*"]},
    ("cc-switch", "codex"): {"protocol": "chat", "base_url": "http://127.0.0.1:15721", "via": "cc-switch", "models": ["*"]},
    ("codex-plus", "codex"): {"protocol": "responses", "base_url": "http://127.0.0.1:57321/v1", "via": "codex-plus", "models": ["*"]},
}


def relay_template(tool: str, harness: str) -> dict | None:
    tpl = _TEMPLATES.get((tool, harness))
    return dict(tpl) if tpl else None


@dataclass
class ToolState:
    name: str
    port: int
    online: bool
    active_provider: str | None = None
    provider_base_url: str | None = None


def _port_online(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def probe_tools(port_overrides: dict[str, int] | None = None) -> list[ToolState]:
    out: list[ToolState] = []
    for name, d in TOOL_DOSSIERS.items():
        port = (port_overrides or {}).get(name, d.port)
        online = _port_online(port)
        provider = base = None
        if online:
            if name == "codex-plus":
                provider, base = _codexpp_active_provider()
            elif name == "cc-switch":
                provider, base = _ccswitch_active_provider(port)
        out.append(ToolState(name=name, port=port, online=online, active_provider=provider, provider_base_url=base))
    return out


def _codexpp_active_provider() -> tuple[str | None, str | None]:
    try:
        data = json.load(open(CODEXPP_SETTINGS, encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    active = data.get("activeRelayId")
    for p in data.get("relayProfiles", []):
        if isinstance(p, dict) and p.get("id") == active:
            return p.get("name") or active, p.get("baseUrl")
    return None, None


def _ccswitch_active_provider(port: int) -> tuple[str | None, str | None]:
    hit = _ccswitch_sqlite_provider()
    if hit != (None, None):
        return hit
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/status", timeout=1.0, trust_env=False)
        if resp.status_code != 200:
            return None, None
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None, None
    # best-effort：在状态 JSON 里找 claude/codex 当前供应商字段（字段名随版本可能变化）
    pools = data.get("current_providers") or data.get("providers") or {}
    if isinstance(pools, dict):
        for v in pools.values():
            if isinstance(v, dict) and (v.get("name") or v.get("baseUrl")):
                return v.get("name"), v.get("baseUrl")
    return None, None


def _ccswitch_sqlite_provider() -> tuple[str | None, str | None]:
    """读取 cc-switch 的 SQLite 配置库（只读）。schema 不稳——任何失败静默返回 (None, None)。"""
    if not os.path.exists(CCSWITCH_DB):
        return None, None
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{CCSWITCH_DB}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT key, value FROM settings WHERE key LIKE '%current%' LIMIT 20"
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - best-effort，任何 schema/锁问题都退回
        return None, None
    for key, value in rows:
        try:
            v = json.loads(value)
        except (TypeError, ValueError):
            continue
        if isinstance(v, dict) and (v.get("name") or v.get("baseUrl")):
            return v.get("name"), v.get("baseUrl")
        if isinstance(v, str):  # 值是 provider id，查 providers 表
            try:
                conn = sqlite3.connect(f"file:{CCSWITCH_DB}?mode=ro", uri=True)
                try:
                    row = conn.execute(
                        "SELECT settings_config FROM providers WHERE id = ? LIMIT 1", (v,)
                    ).fetchone()
                finally:
                    conn.close()
                if row and row[0]:
                    d = json.loads(row[0])
                    env = d.get("env", {}) if isinstance(d, dict) else {}
                    url = env.get("ANTHROPIC_BASE_URL") or env.get("OPENAI_BASE_URL")
                    if url:
                        return v, url
            except Exception:  # noqa: BLE001
                continue
    return None, None
```

- [ ] **Step 4: 跑通过 + lint + commit**

```bash
python -m pytest tests/test_proxy_tools.py -q
git add vision_relay/tools.py tests/test_proxy_tools.py
git commit -m "feat(tools): routing-tool dossiers — port probe + read-only active provider"
```

---

### Task 6: probe —— 可验证模态探针（纯色 PNG + 三档判定）

**Files:**
- Create: `vision_relay/probe.py`
- Test: `tests/test_proxy_probe.py`

- [ ] **Step 1: 写失败测试（新文件）**

```python
"""verifiable modality probe (spec §5 模型能力标注): red-pixel PNG + three-way verdict."""

import base64
import json
import zlib

import pytest

from vision_relay import probe


class TestRedPng:
    def test_valid_png_magic_and_decompress(self):
        raw = probe.red_png()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
        # IDAT 能解压且首行滤镜字节后是红色像素
        idx = raw.find(b"IDAT")
        length = int.from_bytes(raw[idx - 4 : idx], "big")
        idat = raw[idx + 4 : idx + 4 + length]
        pixels = zlib.decompress(idat)
        assert pixels[1:4] == b"\xff\x00\x00"  # 第一行第一个像素 = RGB(255,0,0)


class TestVerdict:
    def _classify(self, status, text):
        return probe._verdict(status, text)

    def test_200_correct_color_means_image(self):
        assert self._classify(200, "红色") == "image"
        assert self._classify(200, "It is RED") == "image"

    def test_200_wrong_answer_means_text_only(self):
        assert self._classify(200, "蓝色") == "text_only"      # 200 但没读图
        assert self._classify(200, "我不知道") == "text_only"  # 吞图

    def test_modality_error_means_text_only(self):
        assert self._classify(400, json.dumps({"error": {"message": "model does not support image input"}})) == "text_only"
        assert self._classify(400, "This model does not support vision content") == "text_only"

    def test_auth_notfound_5xx_timeout_are_inconclusive(self):
        assert self._classify(401, "unauthorized") is None
        assert self._classify(403, "forbidden") is None
        assert self._classify(404, "model not found") is None
        assert self._classify(500, "oops") is None
        assert self._classify(200, None) is None  # 解析失败=含糊


class TestProbeCall:
    def test_chat_request_shape_and_result_cached(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        seen = {}

        class _Resp:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": "红色"}}]}

            text = '{"ok":1}'

        def fake_post(url, json=None, headers=None, timeout=None):
            seen["url"], seen["body"], seen["headers"] = url, json, headers
            return _Resp()

        monkeypatch.setattr(probe.httpx, "post", fake_post)
        result = probe.probe_modality("https://api.example", "sk-k", "glm-5-plus", "chat")
        assert result == "image"
        assert seen["url"].endswith("/chat/completions")
        content = seen["body"]["messages"][0]["content"]
        assert content[0]["type"] == "text" and "颜色" in content[0]["text"]
        assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert seen["headers"]["Authorization"] == "Bearer sk-k"
        # 结果已按 (provider, model) 缓存 —— 由调用方写入 probe_results；本函数只回判定
```

注：`probe_modality` 不落盘（纯函数）；写入 `probe_results` 由 Task 8 的 `run_probe` 做（单一职责）。

- [ ] **Step 2: 跑确认失败** → FAIL

- [ ] **Step 3: 实现 `vision_relay/probe.py`**

```python
"""Verifiable modality probe (spec §5): one minimal image request per (provider, model).

探针=红色 8×8 PNG + “这是什么颜色”。三档判定：
  报错且报文含模态语义 → text_only（主力信号：不识图直接报错）
  200 且答对颜色       → image（真读到图）
  200 但答错/没答      → text_only（静默吞图，防误判）
  401/403/404/5xx/超时/解析失败 → None（不下结论，回落目录建议）
结果由调用方按 (provider, model) 写入 probe_results 缓存。
"""

from __future__ import annotations

import base64
import struct
import zlib

import httpx

_QUESTION = "这张图片是什么颜色？只回答一个颜色词（例如：红色）。"
_COLOR_HINTS = ("红", "red")


def red_png() -> bytes:
    """8×8 纯红 PNG（纯标准库生成，无外部资源）。"""
    width = height = 8
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(red_png()).decode()


# 报错文本中的模态语义线索（不识图的模型报错通常带这些词）
_MODALITY_ERROR_HINTS = ("image", "vision", "multimodal", "modalit", "图片", "图像", "视觉")


def _verdict(status: int, answer: str | None) -> str | None:
    if status == 200:
        if answer is None:
            return None  # 解析失败=含糊
        low = answer.lower()
        return "image" if any(h in low for h in _COLOR_HINTS) else "text_only"
    if status in (401, 403, 404) or status >= 500:
        return None
    low = (answer or "").lower()
    if any(h in low for h in _MODALITY_ERROR_HINTS):
        return "text_only"
    return None  # 含糊报错不下结论


def _chat_body(model: str) -> dict:
    return {
        "model": model,
        "max_tokens": 24,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _QUESTION},
                    {"type": "image_url", "image_url": {"url": _data_url()}},
                ],
            }
        ],
    }


def _responses_body(model: str) -> dict:
    return {
        "model": model,
        "max_output_tokens": 24,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _QUESTION},
                    {"type": "input_image", "image_url": _data_url()},
                ],
            }
        ],
    }


def _anthropic_body(model: str) -> dict:
    return {
        "model": model,
        "max_tokens": 24,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _QUESTION},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(red_png()).decode()},
                    },
                ],
            }
        ],
    }


def _extract(protocol: str, data) -> str | None:
    try:
        if protocol == "anthropic":
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        if protocol == "responses":
            for item in data.get("output", []):
                for part in item.get("content", []):
                    if part.get("type") in ("output_text", "text"):
                        return part.get("text")
            return None
        content = data["choices"][0]["message"]["content"]
        return content if isinstance(content, str) else None
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def probe_modality(base_url: str, api_key: str, model: str, protocol: str, timeout: float = 30.0) -> str | None:
    """发一次最小带图请求并判定。返回 'image' | 'text_only' | None(不下结论)。"""
    if protocol == "anthropic":
        url = base_url.rstrip("/") + "/v1/messages"
        body = _anthropic_body(model)
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"} if api_key else {}
    elif protocol == "responses":
        url = base_url.rstrip("/") + "/responses"
        body = _responses_body(model)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    else:
        url = base_url.rstrip("/") + "/chat/completions"
        body = _chat_body(model)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=timeout, trust_env=False)
    except httpx.HTTPError:
        return None
    try:
        answer = _extract(protocol, resp.json())
    except ValueError:
        answer = None
    return _verdict(resp.status_code, answer if resp.status_code != 200 else answer or _extract_from_text(protocol, resp.text))


def _extract_from_text(protocol: str, text: str) -> str | None:
    """JSON 解析失败时退而做一次宽松文本嗅探（找颜色词）。"""
    low = text.lower()
    if any(h in low for h in _COLOR_HINTS):
        return "红"
    return None
```

- [ ] **Step 4: 跑通过 + lint + commit**

```bash
python -m pytest tests/test_proxy_probe.py -q
git add vision_relay/probe.py tests/test_proxy_probe.py
git commit -m "feat(probe): verifiable modality probe (red PNG, three-way verdict)"
```

---

### Task 7: wiring —— 归属判定、接管挂钩快照、按快照还原、工具 relay 自动生成

**Files:**
- Modify: `vision_relay/wiring.py`
- Test: `tests/test_proxy_wiring.py`（新文件）

- [ ] **Step 1: 写失败测试（新文件）**

```python
"""wiring upgrades: ownership classify, snapshot on takeover, restore-by-snapshot, tool relays."""

import json
import os

from vision_relay import snapshot, wiring
from vision_relay.config import ProxyConfig, RelayConfig, save_config


def _write_harness(home, harness, base_url):
    h = wiring.HARNESS_CFG[harness]
    p = wiring._path(home, harness)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if h.kind == "toml":
        open(p, "w", encoding="utf-8").write(f'model = "gpt-5"\nbase_url = "{base_url}"\n')
    else:
        d = {"env": {"ANTHROPIC_BASE_URL": base_url}} if harness == "claude" else {"model": {"baseUrl": base_url, "apiKey": "sk-x"}}
        open(p, "w", encoding="utf-8").write(json.dumps(d))


class TestOwnership:
    def test_classify(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        assert wiring.classify_base_url("http://127.0.0.1:8787", 8787) == "ours"
        assert wiring.classify_base_url("http://127.0.0.1:8787/v1", 8787) == "ours"
        assert wiring.classify_base_url("http://127.0.0.1:15721", 8787) == "cc-switch"
        assert wiring.classify_base_url("http://127.0.0.1:57321/v1", 8787) == "codex-plus"
        assert wiring.classify_base_url("https://api.deepseek.com", 8787) == "other"


class TestTakeoverWritesSnapshot:
    def test_backup_and_rewrite_snapshots_original(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_harness(tmp_path, "codex", "http://127.0.0.1:57321/v1")
        cfg = ProxyConfig()
        cfg.routing.relay_templates = {"codex-plus": {"protocol": "responses", "base_url": "http://127.0.0.1:57321/v1", "via": "codex-plus", "models": ["*"]}}
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
        snapshot.save("claude", snapshot.Snapshot(base_url="https://real.example/api", key_ref="env.ANTHROPIC_AUTH_TOKEN", model="glm-5-air"))
        msgs = wiring.wiring_restore_by_snapshot(ProxyConfig())
        assert "claude: restored" in msgs[0]
        assert wiring.read_base_url(wiring._path(str(tmp_path), "claude"), wiring.HARNESS_CFG["claude"]) == "https://real.example/api"


class TestToolRelays:
    def test_ensure_tool_relays_adds_missing_not_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(RelayConfig(name="cc-anthropic", **{"protocol": "anthropic", "base_url": "http://127.0.0.1:15721", "via": "cc-switch", "models": ["*"]}))
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
```

- [ ] **Step 2: 跑确认失败** → FAIL（`classify_base_url` 等不存在）

- [ ] **Step 3: 在 `vision_relay/wiring.py` 追加（不动既有函数；`wiring_backup_and_rewrite` 加快照挂钩）**

在文件 imports 区补 `from . import snapshot` 与 `from .tools import TOOL_DOSSIERS`（tools 不反向 import wiring，避免环）。

`wiring_backup_and_rewrite` 在 `ok = write_base_url(...)` 之前插入快照逻辑（整函数替换为）：

```python
def wiring_backup_and_rewrite(cfg) -> list[str]:
    """为启用的 harness 备份(不重复)并把 base_url 指到本代理；接管前记录组合快照。"""
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    home = HOME
    changed: list[str] = []
    for name in cfg.routing.harnesses:
        h = HARNESS_CFG[name]
        p = _path(home, name)
        if not os.path.exists(p):
            changed.append(f"{name}: no config file, skipped")
            continue
        original = read_base_url(p, h)
        if original and classify_base_url(original, cfg.bind_port) != "ours":
            # 接管前组合快照：base_url + key 位置 + 模型 + 第二跳归属（spec §5）
            second_hop = classify_base_url(original, cfg.bind_port)
            second_hop = second_hop if second_hop in TOOL_DOSSIERS else None
            try:
                model = _first_model(p)
            except OSError:
                model = ""
            try:
                snapshot.save(name, snapshot.Snapshot(base_url=original, key_ref=snapshot.key_ref_for(name), model=model, second_hop=second_hop))
            except OSError:
                pass
        if _find_bak(p) is None:  # 已有备份（含旧后缀）不覆盖，防止把代理地址存成"原始值"
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                import shutil

                shutil.copyfile(p, p + BAK_SUFFIX)
            except OSError:
                pass
        ok = write_base_url(p, h, proxy_url)
        changed.append(f"{name}: base_url -> {proxy_url} ({'ok' if ok else 'FAIL'})")
    return changed


def _first_model(path: str) -> str:
    """从 harness 配置尽力抽一个模型名（快照记录用；失败返回空串）。"""
    import re as _re

    try:
        txt = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    m = _re.search(r'(?i)(?:model|name)["\']?\s*[:=]\s*["\']([\w@.\-/]+)["\']', txt)
    return m.group(1) if m else ""
```

文件末尾追加：

```python
def classify_base_url(url: str | None, bind_port: int) -> str:
    """base_url 归属：ours | cc-switch | codex-plus | other | none（spec §5 观测信号①）。"""
    if not url:
        return "none"
    if url == f"http://127.0.0.1:{bind_port}" or url.startswith(f"http://127.0.0.1:{bind_port}/"):
        return "ours"
    import re

    m = re.search(r":(\d+)", url)
    if not m:
        return "other"
    port = int(m.group(1))
    for name, d in TOOL_DOSSIERS.items():
        if port == d.port:
            return name
    return "other"


def wiring_restore_by_snapshot(cfg) -> list[str]:
    """按接管组合快照还原（spec §5 修复：路由关-崩溃路径）。仅当前指向本代理时执行。"""
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    snaps = snapshot.load()
    restored: list[str] = []
    for name in cfg.routing.harnesses:
        snap = snaps.get(name)
        if snap is None:
            continue
        p = _path(HOME, name)
        cur = read_base_url(p, HARNESS_CFG[name]) if os.path.exists(p) else None
        if cur is None or (cur != proxy_url and not cur.startswith(proxy_url + "/")):
            restored.append(f"{name}: 当前 base_url={cur!r} 非本代理，跳过还原")
            continue
        ok = write_base_url(p, HARNESS_CFG[name], snap.base_url)
        restored.append(f"{name}: restored to {snap.base_url} ({'ok' if ok else 'FAIL'})")
    return restored


def ensure_tool_relays(cfg, tool_states) -> list[str]:
    """在线工具档案 → 自动 relay（name 去重不覆盖；离线不加；spec §5/§12）。
    返回新增 relay name 列表。"""
    from .tools import _TEMPLATES as all_templates

    added: list[str] = []
    online_names = {s.name for s in tool_states if s.online}
    for (tool_name, harness), tpl in all_templates.items():
        if tool_name not in online_names:
            continue
        if harness not in cfg.routing.harnesses:
            continue
        name = _relay_name(tool_name, harness, tpl)
        if any(r.name == name for r in cfg.relays):
            continue
        try:
            cfg.relays.append(RelayConfig(name=name, **dict(tpl)))
            if name not in cfg.routing.activated_relays:
                cfg.routing.activated_relays.append(name)
            added.append(name)
        except Exception:  # noqa: BLE001 - 模板非法跳过（§12.3）
            continue
    save_config(cfg)
    return added


def _relay_name(tool_name: str, harness: str, tpl: dict) -> str:
    if tool_name == "cc-switch":
        return "cc-claude" if tpl["protocol"] == "anthropic" else "cc-codex"
    return "codex-plus"
```

**实现注意**：`from .tools import TOOL_DOSSIERS, relay_template` 与 `from .tools import _TEMPLATES as _ALL_TEMPLATES`（若需要）统一放文件头 import 区；上面 `ensure_tool_relays` 内只用局部 `from .tools import _TEMPLATES as all_templates`（避免与文件头重复，二选一即可，实现时保持 ruff 干净）。

- [ ] **Step 4: 跑通过（含既有 wiring 测试不回归）**

Run: `python -m pytest tests/test_proxy_wiring.py tests/test_proxy_cli.py -q`

- [ ] **Step 5: lint + commit**

```bash
git add vision_relay/wiring.py tests/test_proxy_wiring.py
git commit -m "feat(wiring): ownership classify, snapshot on takeover, restore-by-snapshot, auto tool relays"
```

---

### Task 8: reconcile —— 状态/事件存储 + 观测 + 对账规则矩阵

**Files:**
- Create: `vision_relay/reconcile.py`
- Test: `tests/test_proxy_reconcile.py`

- [ ] **Step 1: 写失败测试（新文件；矩阵场景 = spec §5 表 + 自动修复）**

```python
"""reconcile engine (spec §5): observe -> expected -> converge, single write path under lock."""

import json
import os

import pytest

from vision_relay import reconcile, snapshot
from vision_relay.config import ProxyConfig, save_config


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    cfgdir = tmp_path / "cfg"
    cfgdir.mkdir()
    monkeypatch.setattr(reconcile, "HOME", str(home))
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(cfgdir))
    return home, cfgdir


def _write_harness(home, harness, base_url):
    import json as j

    from vision_relay import wiring

    h = wiring.HARNESS_CFG[harness]
    p = wiring._path(str(home), harness)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if h.kind == "toml":
        open(p, "w", encoding="utf-8").write(f'base_url = "{base_url}"\n')
    else:
        d = {"env": {"ANTHROPIC_BASE_URL": base_url}} if harness == "claude" else {"model": {"baseUrl": base_url}}
        open(p, "w", encoding="utf-8").write(j.dumps(d))


def _set_running(cfgdir, running: bool):
    reconcile.set_routing_on(True)
    if running:
        (cfgdir / "proxy.pid").write_text("99999999")  # 不会真实存活 -> service_alive False
        # 直接 monkeypatch service_alive 更稳：
    import vision_relay.reconcile as r

    r._service_alive = lambda cfg: running  # type: ignore[assignment]


class TestState:
    def test_routing_on_persists(self, env):
        assert reconcile.get_routing_on() is False  # 默认未开
        reconcile.set_routing_on(True)
        assert reconcile.get_routing_on() is True
        assert json.loads((env[1] / "state.json").read_text(encoding="utf-8"))["routing_on"] is True


class TestEvents:
    def test_append_and_tail(self, env):
        reconcile.append_event("reclaim", "codex", {"from": ":57321", "to": ":8787"})
        rows = reconcile.tail_events(10)
        assert rows[-1]["type"] == "reclaim" and rows[-1]["harness"] == "codex"


class TestMatrix:
    def test_noop_when_consistent(self, env):
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")
        cfg = ProxyConfig()
        _set_running(cfgdir, True)
        cfg.routing.capability_confirmed = True
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert report["actions"] == []  # 幂等：无漂移不写文件

    def test_reclaim_when_tool_stole(self, env):
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:15721")
        cfg = ProxyConfig()
        _set_running(cfgdir, True)
        from vision_relay.tools import ToolState

        report = reconcile.reconcile(cfg, tool_states=[ToolState("cc-switch", 15721, True)], expected_wired={"claude"})
        assert any(a["type"] == "reclaim" and a["harness"] == "claude" for a in report["actions"])
        from vision_relay import wiring

        cur = wiring.read_base_url(wiring._path(str(home), "claude"), wiring.HARNESS_CFG["claude"])
        assert cur == "http://127.0.0.1:8787"

    def test_absorb_stranger_address(self, env):
        home, cfgdir = env
        _write_harness(home, "claude", "https://new.upstream.example/api")
        cfg = ProxyConfig()
        _set_running(cfgdir, True)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        absorb = [a for a in report["actions"] if a["type"] == "absorb"]
        assert absorb and absorb[0]["harness"] == "claude"
        # 吸收 = 接管回 8787 + 新地址记为直连上游 + 快照更新
        assert snapshot.load()["claude"].base_url == "https://new.upstream.example/api"
        direct = [r for r in cfg.relays if r.base_url == "https://new.upstream.example/api"]
        assert direct, "吸收的新地址必须成为直连 relay"

    def test_dead_and_wired_with_routing_on_triggers_restart(self, env, monkeypatch):
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")
        cfg = ProxyConfig()
        _set_running(cfgdir, False)
        reconcile.set_routing_on(True)
        spawned = {}
        monkeypatch.setattr(reconcile, "_restart_service", lambda cfg: spawned.setdefault("n", 0) or 1)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert any(a["type"] == "auto_fix" and a["fix"] == "restart" for a in report["actions"])
        assert spawned

    def test_dead_and_wired_with_routing_off_restores_snapshot(self, env):
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")
        snapshot.save("claude", snapshot.Snapshot(base_url="https://real.example", key_ref="k", model="m"))
        cfg = ProxyConfig()
        _set_running(cfgdir, False)
        reconcile.set_routing_on(False)
        report = reconcile.reconcile(cfg, tool_states=[], expected_wired={"claude"})
        assert any(a["type"] == "auto_fix" and a["fix"] == "restore" for a in report["actions"])
        from vision_relay import wiring

        cur = wiring.read_base_url(wiring._path(str(home), "claude"), wiring.HARNESS_CFG["claude"])
        assert cur == "https://real.example"

    def test_tool_relay_generated_when_online(self, env):
        home, cfgdir = env
        _write_harness(home, "codex", "http://127.0.0.1:8787")
        cfg = ProxyConfig()
        _set_running(cfgdir, True)
        from vision_relay.tools import ToolState

        report = reconcile.reconcile(cfg, tool_states=[ToolState("codex-plus", 57321, True)], expected_wired={"codex"})
        assert any(a["type"] == "relay_added" and a["name"] == "codex-plus" for a in report["actions"])

    def test_lock_held_during_reconcile(self, env, monkeypatch):
        home, cfgdir = env
        _write_harness(home, "claude", "http://127.0.0.1:8787")
        cfg = ProxyConfig()
        _set_running(cfgdir, True)
        from vision_relay import locking

        orig = reconcile._reclaim
        flags = {}

        def spy(cfg2, name, cur):
            flags["held"] = locking.try_config_lock() is None  # 持锁中应拿不到
            return orig(cfg2, name, cur)

        monkeypatch.setattr(reconcile, "_reclaim", spy)
        _write_harness(home, "claude", "http://127.0.0.1:15721")
        from vision_relay.tools import ToolState

        reconcile.reconcile(cfg, tool_states=[ToolState("cc-switch", 15721, True)], expected_wired={"claude"})
        assert flags.get("held") is True
```

- [ ] **Step 2: 跑确认失败** → FAIL

- [ ] **Step 3: 实现 `vision_relay/reconcile.py`**

```python
"""Reconcile engine (spec §5): the single write path for wiring/relays.

所有触发源（start/stop/refresh/diagnose/自动修复）都走 reconcile()；自方写者经
config_lock 串行；无漂移不写文件（幂等）。事件流水 events.jsonl 供 GUI 事件日志页。
"""

from __future__ import annotations

import json
import os
import time

from . import snapshot, tools
from .config import ProxyConfig, save_config
from .env_util import config_dir
from .locking import config_lock

# 测试可 monkeypatch。
HOME = os.path.expanduser("~")

_service_alive_orig = None


# ---------- state.json（routing_on = 用户路由意图，崩溃后修复据此推导） ----------


def _state_path() -> str:
    return os.path.join(config_dir(), "state.json")


def get_routing_on() -> bool:
    try:
        return bool(json.load(open(_state_path(), encoding="utf-8")).get("routing_on"))
    except (OSError, ValueError):
        return False


def set_routing_on(value: bool) -> None:
    os.makedirs(config_dir(), exist_ok=True)
    data = {}
    if os.path.exists(_state_path()):
        try:
            data = json.load(open(_state_path(), encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    data["routing_on"] = value
    data["updated_ts"] = time.time()
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, _state_path())


# ---------- events.jsonl ----------


def _events_path() -> str:
    return os.path.join(config_dir(), "events.jsonl")


def append_event(type_: str, harness: str | None, detail: dict) -> None:
    row = {"ts": time.time(), "type": type_, "harness": harness, **detail}
    os.makedirs(config_dir(), exist_ok=True)
    with open(_events_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def tail_events(n: int = 50) -> list[dict]:
    try:
        lines = open(_events_path(), encoding="utf-8").readlines()
    except OSError:
        return []
    out = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# ---------- 观测 ----------


def _service_alive(cfg: ProxyConfig) -> bool:
    """PID 文件 + 端口双信号（spec §5 观测信号③）。"""
    import socket

    with socket.socket() as s:
        s.settimeout(0.3)
        if s.connect_ex(("127.0.0.1", cfg.bind_port)) == 0:
            return True
    pid_file = os.path.join(config_dir(), "proxy.pid")
    try:
        pid = int(open(pid_file, encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def observe(cfg: ProxyConfig, tool_states: list | None = None) -> dict:
    """收集三信号：接线归属 / 工具在线 / 服务存活。只读，不写。"""
    from . import wiring

    tool_states = tool_states if tool_states is not None else tools.probe_tools()
    harness_rows = {}
    for name in cfg.routing.harnesses:
        p = wiring._path(HOME, name)
        cur = wiring.read_base_url(p, wiring.HARNESS_CFG[name]) if os.path.exists(p) else None
        harness_rows[name] = {
            "base_url": cur,
            "ownership": wiring.classify_base_url(cur, cfg.bind_port),
            "has_snapshot": name in snapshot.load(),
        }
    return {
        "service_alive": _service_alive(cfg),
        "harnesses": harness_rows,
        "tools": [
            {"name": t.name, "port": t.port, "online": t.online, "active_provider": t.active_provider, "provider_base_url": t.provider_base_url}
            for t in tool_states
        ],
        "routing_on": get_routing_on(),
    }


# ---------- 对账（唯一写路径；spec §5 规则矩阵） ----------


def _expected_base(cfg: ProxyConfig) -> str:
    return f"http://127.0.0.1:{cfg.bind_port}"


def _reclaim(cfg: ProxyConfig, harness: str, cur: str) -> bool:
    from . import wiring

    p = wiring._path(HOME, harness)
    ok = wiring.write_base_url(p, wiring.HARNESS_CFG[harness], _expected_base(cfg))
    append_event("reclaim", harness, {"from": cur, "to": _expected_base(cfg), "ok": ok})
    return ok


def _absorb(cfg: ProxyConfig, harness: str, new_base: str) -> None:
    """吸收新上游（spec §5）：接管回本代理 + 新地址成为直连 relay + 快照更新。"""
    from . import wiring
    from .config import RelayConfig

    snap = snapshot.Snapshot(base_url=new_base, key_ref=snapshot.key_ref_for(harness), model="", second_hop=None)
    snapshot.save(harness, snap)
    name = f"direct-{harness}"
    cfg.relays = [r for r in cfg.relays if r.name != name]
    proto = {"claude": "anthropic", "codex": "responses", "qwen-code": "chat"}[harness]
    cfg.relays.append(RelayConfig(name=name, protocol=proto, base_url=new_base, models=["*"]))
    wiring.write_base_url(wiring._path(HOME, harness), wiring.HARNESS_CFG[harness], _expected_base(cfg))
    append_event("absorb", harness, {"new_base_url": new_base, "needs_key": True})


def _restart_service(cfg: ProxyConfig) -> bool:
    """分离进程重启服务（崩溃前路由开 -> 自动重启保持接管，spec §5 修复）。"""
    import subprocess
    import sys

    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([sys.executable, "-m", "vision_relay", "start"], **kwargs)
        return True
    except OSError:
        return False


def reconcile(
    cfg: ProxyConfig,
    tool_states: list | None = None,
    trigger: str = "manual",
    expected_wired: set[str] | None = None,
) -> dict:
    """执行对账。expected_wired：本轮应保持接管的 harness 集合（None=全部启用的）。
    返回 {"actions": [...], "needs_you": [...], "observed": {...}}（GUI/CLI 共用）。"""
    from . import wiring

    tool_states = tool_states if tool_states is not None else tools.probe_tools()
    expected_wired = expected_wired if expected_wired is not None else set(cfg.routing.harnesses)
    actions: list[dict] = []
    needs_you: list[dict] = []
    with config_lock():
        obs = observe(cfg, tool_states)
        # 0) 在线工具 → 自动 relay（name 去重不覆盖，离线不加；§12 只增不覆盖）
        added = wiring.ensure_tool_relays(cfg, tool_states)
        for n in added:
            actions.append({"type": "relay_added", "name": n})
            append_event("relay_added", None, {"name": n})
        for name, row in obs["harnesses"].items():
            cur, owner = row["base_url"], row["ownership"]
            if obs["service_alive"] and name in expected_wired:
                # 服务在跑 + 该接管：始终接管（spec §3）
                if owner == "ours":
                    pass  # 幂等：无漂移不写文件
                elif owner in tools.TOOL_DOSSIERS:
                    if _reclaim(cfg, name, cur or ""):
                        actions.append({"type": "reclaim", "harness": name, "from": cur})
                elif owner in ("other", "none"):
                    if owner == "other" and cur:
                        _absorb(cfg, name, cur)
                        actions.append({"type": "absorb", "harness": name, "new_base_url": cur})
                    else:
                        if _reclaim(cfg, name, cur or ""):
                            actions.append({"type": "reclaim", "harness": name, "from": cur})
                # 吸收/抢回后可能缺 key（被动提醒，不代填）
                if any(a.get("type") == "absorb" and a.get("harness") == name for a in actions):
                    needs_you.append({"type": "missing_key", "harness": name, "hint": f"direct-{name} 需补 API key"})
            elif not obs["service_alive"] and owner == "ours":
                # 僵尸接线：按崩溃前意图推导（spec §5 修复流程）
                if obs["routing_on"]:
                    ok = _restart_service(cfg)
                    append_event("auto_fix", name, {"fix": "restart", "ok": ok})
                    actions.append({"type": "auto_fix", "harness": name, "fix": "restart", "ok": ok})
                elif name in snapshot.load():
                    wiring.wiring_restore_by_snapshot(cfg)
                    append_event("auto_fix", name, {"fix": "restore"})
                    actions.append({"type": "auto_fix", "harness": name, "fix": "restore"})
                else:
                    needs_you.append(
                        {"type": "unresolvable", "harness": name, "hint": "快照缺失且服务未运行：需手选修复目标"}
                    )
        if actions:
            save_config(cfg)  # relay 增删/吸收落盘（持锁内）
    return {"trigger": trigger, "actions": actions, "needs_you": needs_you, "observed": obs}
```

- [ ] **Step 4: 跑通过 + 全量回归**

Run: `python -m pytest tests/test_proxy_reconcile.py -q` 然后 `python -m pytest -q`

- [ ] **Step 5: lint + commit**

```bash
git add vision_relay/reconcile.py tests/test_proxy_reconcile.py
git commit -m "feat(reconcile): single write path — observe/converge matrix, intent-based auto-fix, events"
```

---

### Task 9: visionlog —— 识图留痕（三段明细 + 留存清理）

**Files:**
- Create: `vision_relay/visionlog.py`
- Test: `tests/test_proxy_visionlog.py`

- [ ] **Step 1: 写失败测试（新文件）**

```python
"""vision call records (spec §6 识图记录 / §7.6): three segments + retention."""

import json
import os
import time

from vision_relay import visionlog


def _record(**kw):
    base = dict(
        ts=time.time(), harness="claude", session="sess-1", tier=1,
        question=None, prompt="T1 prompt", raw="desc text", injected="[图片描述] desc",
        duration_ms=120, cache_hit=False, image_hash="h1", vlm_model="qwen-vl-max",
    )
    base.update(kw)
    return base


def test_record_writes_daily_ndjson(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    visionlog.record(_record(), enabled=True, retention_days=7)
    files = os.listdir(str(tmp_path / "visionlog"))
    assert len(files) == 1 and files[0].endswith(".jsonl")
    row = json.loads((tmp_path / "visionlog" / files[0]).read_text(encoding="utf-8").splitlines()[0])
    assert row["prompt"] == "T1 prompt" and row["harness"] == "claude"


def test_disabled_records_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    visionlog.record(_record(), enabled=False, retention_days=7)
    assert not (tmp_path / "visionlog").exists()


def test_retention_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    old = tmp_path / "visionlog" / "2026-01-01.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps(_record()) + "\n", encoding="utf-8")
    removed = visionlog.cleanup(retention_days=7)
    assert removed == 1 and not old.exists()


def test_query_filters_by_harness(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    visionlog.record(_record(harness="claude"), enabled=True, retention_days=7)
    visionlog.record(_record(harness="codex", session="s2"), enabled=True, retention_days=7)
    rows = visionlog.query(harness="claude")
    assert len(rows) == 1 and rows[0]["harness"] == "claude"
    assert len(visionlog.query()) == 2


def test_record_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    visionlog.record({"bad": object()}, enabled=True, retention_days=7)  # 不可序列化也不许抛
    visionlog.record(None, enabled=True, retention_days=7)  # None 也不许抛（fail-open 铁律）
```

- [ ] **Step 2: 跑确认失败** → FAIL

- [ ] **Step 3: 实现 `vision_relay/visionlog.py`**

```python
"""Vision call records (spec §6 识图记录): per-call prompt/raw/injected, local-only.

留存默认 7 天可关闭；记录含提示词与原始返回，属敏感数据——只存本机，绝不外发。
record() 永不抛异常（fail-open：留痕失败不能影响代理转发）。
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta
from glob import glob

from .env_util import config_dir

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl$")


def _dir() -> str:
    return os.path.join(config_dir(), "visionlog")


def record(row: dict | None, enabled: bool, retention_days: int) -> None:
    if not enabled or not isinstance(row, dict):
        return
    try:
        os.makedirs(_dir(), exist_ok=True)
        path = os.path.join(_dir(), datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001 - 留痕绝不影响主链路
        pass


def cleanup(retention_days: int) -> int:
    """删除超过留存天数的日文件；返回删除数。"""
    removed = 0
    cutoff = datetime.now() - timedelta(days=retention_days)
    for path in glob(os.path.join(_dir(), "*.jsonl")):
        name = os.path.basename(path)
        if not _DATE_RE.match(name):
            continue
        try:
            if datetime.strptime(name[:10], "%Y-%m-%d") < cutoff:
                os.unlink(path)
                removed += 1
        except (ValueError, OSError):
            continue
    return removed


def query(harness: str | None = None, session: str | None = None, limit: int = 200) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(glob(os.path.join(_dir(), "*.jsonl")), reverse=True):
        try:
            lines = open(path, encoding="utf-8").readlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if harness and row.get("harness") != harness:
                continue
            if session and row.get("session") != session:
                continue
            rows.append(row)
            if len(rows) >= limit:
                return rows
    return rows
```

- [ ] **Step 4: 跑通过 + lint + commit**

```bash
python -m pytest tests/test_proxy_visionlog.py -q
git add vision_relay/visionlog.py tests/test_proxy_visionlog.py
git commit -m "feat(visionlog): local vision-call records with retention"
```

---

### Task 10: vlm —— describe_detail（提示词 + 原始返回，供留痕）

**Files:**
- Modify: `vision_relay/vlm.py`
- Test: `tests/test_proxy_vlm.py`

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---- Phase2 M1: describe_detail for vision log ----


class TestDescribeDetail:
    def test_detail_contains_prompt_and_raw(self, monkeypatch):
        from vision_relay.vlm import VLMClient
        from vision_relay.config import VLMConfig
        from vision_relay.ir import ImageBlock

        client = VLMClient(VLMConfig(api_key="k"))
        monkeypatch.setattr(
            VLMClient, "_describe_chat", lambda self, image, prompt: "红色卡车"
        )
        detail = {}
        desc = client.describe(ImageBlock(base64="aGk=", media_type="image/png"), detail=detail)
        assert desc == "红色卡车"
        assert "Describe the image" in detail["prompt"]
        assert detail["raw"] == "红色卡车"
```

- [ ] **Step 2: 跑确认失败** → FAIL（`describe() 不接受 detail`）

- [ ] **Step 3: 修改 `VLMClient.describe`（其余不动）**

```python
    def describe(self, image: ImageBlock, question: str | None = None, tier: int = 1, detail: dict | None = None) -> str:
        prompt = self._prompt(question, tier)
        desc: str
        if self._resolve_local_model() is not None:
            try:
                desc = self._describe_local(image, prompt)
            except (VLMError, httpx.HTTPError) as exc:
                self._local_model = None
                raise VLMError("TRANSPORT", f"local ollama failed: {exc}") from exc
        else:
            try:
                if self.cfg.format == "anthropic":
                    desc = self._describe_anthropic(image, prompt)
                else:
                    desc = self._describe_chat(image, prompt)
            except VLMError:
                raise
            except httpx.TimeoutException as exc:
                raise VLMError("TIMEOUT", str(exc)) from exc
            except httpx.HTTPError as exc:
                raise VLMError("TRANSPORT", str(exc)) from exc
        if detail is not None:
            detail["prompt"] = prompt
            detail["raw"] = desc  # 文本层原始返回（协议原文可后续增强，M1 记文本）
        return desc
```

- [ ] **Step 4: 跑通过 + lint + commit**

```bash
python -m pytest tests/test_proxy_vlm.py tests/test_proxy_pipeline.py -q
git add vision_relay/vlm.py tests/test_proxy_vlm.py
git commit -m "feat(vlm): describe(detail=...) exposes prompt/raw for vision log"
```

---

### Task 11: pipeline + server —— 按 harness 选 VLM、会话标识、provider 解析、留痕钩子

**Files:**
- Modify: `vision_relay/pipeline.py`, `vision_relay/server.py`
- Test: `tests/test_proxy_pipeline.py`（追加）

- [ ] **Step 1: 写失败测试（追加到 `tests/test_proxy_pipeline.py`）**

```python
# ---- Phase2 M1: per-harness VLM + session/provider + vision log hook ----


class TestVisionLogHook:
    def test_vlm_call_recorded_with_three_segments(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        import json as j

        cfg = ProxyConfig()
        cfg.vlm.api_key = "k"
        recorded = []
        monkeypatch.setattr(
            "vision_relay.pipeline.record_vision_call",
            lambda row, cfg_: recorded.append(row),
        )

        class FakeVLM:
            def describe(self, image, question=None, tier=1, detail=None):
                if detail is not None:
                    detail["prompt"] = "P"
                    detail["raw"] = "RAW"
                return "a red truck"

        pl = Pipeline(FakeVLM(), DescriptionCache())
        msg = Message(role="user", content=[ContentBlock(type="text", text="what?"), ContentBlock(type="image", image=ImageBlock(base64="aGk=", media_type="image/png"))])
        ir = IRRequest(model="deepseek-v4", messages=[msg])
        pl.process(ir, cfg, "claude", "bigmodel", session_id="sess-9")
        assert recorded, "VLM 调用必须留痕"
        row = recorded[0]
        assert row["harness"] == "claude" and row["session"] == "sess-9"
        assert row["prompt"] == "P" and row["raw"] == "RAW"
        assert row["injected"].startswith("[图片描述]")
        assert row["cache_hit"] is False


class TestPerHarnessVlmSelection:
    def test_server_builds_vlm_client_per_harness(self):
        from vision_relay.server import build_vlm_clients

        cfg = ProxyConfig.from_dict({"vlm_by_harness": {"claude": {"model": "qwen3.5-omni-plus"}}})
        clients = build_vlm_clients(cfg)
        assert clients["claude"].cfg.model == "qwen3.5-omni-plus"
        assert clients["codex"].cfg.model == cfg.vlm.model  # 未覆盖回落全局
        assert clients["_default"].cfg.model == cfg.vlm.model


class TestSessionExtraction:
    def test_anthropic_user_id_suffix(self):
        from vision_relay.server import extract_session_id

        assert extract_session_id("anthropic", {"metadata": {"user_id": "user_abc__session-77"}}) == "session-77"
        assert extract_session_id("anthropic", {"metadata": {"user_id": "plain"}}) == "plain"
        assert extract_session_id("chat", {}) is None
```

- [ ] **Step 2: 跑确认失败** → FAIL

- [ ] **Step 3: 修改 `vision_relay/pipeline.py`**

3a. 顶部 import 补：

```python
from .visionlog import record as _vl_record


def record_vision_call(row: dict, cfg: ProxyConfig) -> None:
    """留痕入口（独立函数便于测试 monkeypatch；fail-open 永不抛）。"""
    _vl_record(row, enabled=cfg.vision_log.enabled, retention_days=cfg.vision_log.retention_days)
```

3b. `Pipeline.__init__` 增加可选记录器参数（默认全局函数）：

```python
    def __init__(self, vlm, cache: DescriptionCache, semaphore: threading.Semaphore | None = None):
        self.vlm = vlm
        self.cache = cache
        self.semaphore = semaphore or threading.Semaphore(5)
        self.table = CapabilityTable()
        self._recorder = record_vision_call
```

3c. `process` 签名扩展（会话标识透传到 _handle_one）：

```python
    def process(
        self,
        ir: IRRequest,
        cfg: ProxyConfig,
        harness: str | None = None,
        provider: str | None = None,
        session_id: str | None = None,
    ) -> ProcessResult:
```

当前轮调用改为 `self._handle_one(target, result, question, cfg, harness, provider, session_id)`（黄金窗口缓存命中分支传 `cfg=None` 不留痕——只有真实 VLM 调用留痕；缓存命中由 demo 中"命中 0ms"行显示，M1 记真实调用即可，缓存命中行 M2 GUI 从 cache 统计补）。

3d. `_handle_one` 增加留痕（VLM 成功路径）：

```python
    def _handle_one(
        self,
        target: _ImageTarget,
        result: ProcessResult,
        question: str | None = None,
        cfg: ProxyConfig | None = None,
        harness: str | None = None,
        provider: str | None = None,
        session_id: str | None = None,
    ) -> str:
        key = target.key
        cached = self.cache.get(key, question)
        if cached is not None:
            self._inject(target, cached, result)
            return "injected"
        try:
            tier = 2 if question else 1
            detail: dict = {}
            started = time.time()
            desc = self._describe_with_retry(target.image, question, tier, result, detail=detail)
            self.cache.put(key, question, desc)
            if cfg is not None:
                try:
                    self._recorder(
                        {
                            "ts": time.time(),
                            "harness": harness,
                            "session": session_id,
                            "tier": tier,
                            "question": question,
                            "prompt": detail.get("prompt"),
                            "raw": detail.get("raw"),
                            "injected": self._format_desc(desc, target),
                            "duration_ms": int((time.time() - started) * 1000),
                            "cache_hit": False,
                            "image_hash": key[:64],
                            "vlm_model": getattr(getattr(self.vlm, "cfg", None), "model", None),
                        },
                        cfg,
                    )
                except Exception:  # noqa: BLE001 - 留痕绝不影响主链路
                    pass
        except Exception as exc:  # noqa: BLE001 - fail-open on ANY VLM failure
            self._strip_target(target, self._fail_open_text(exc))
            result.fail_open = getattr(exc, "reason", "VLM_FAILED")
            return "stripped"
        self._inject(target, desc, result)
        return "injected"
```

`_describe_with_retry` 增加 `detail` 透传：签名 `def _describe_with_retry(self, image, question, tier, result, detail: dict | None = None)`，调用处 `desc = self.vlm.describe(image, question=question, tier=tier, detail=detail)`。

- [ ] **Step 4: 修改 `vision_relay/server.py`**

4a. 新增模块级函数（放在 `_HARNESS_BY_PROTO` 之后）：

```python
def build_vlm_clients(cfg: ProxyConfig) -> dict:
    """按 harness 预建 VLM 客户端（vlm_for 合并覆盖；_default 供未知 harness）。"""
    from .vlm import VLMClient

    clients = {"_default": VLMClient(cfg.vlm)}
    for harness in ("claude", "codex", "qwen-code"):
        merged = cfg.vlm_for(harness)
        if merged != cfg.vlm or harness in cfg.vlm_by_harness:
            clients[harness] = VLMClient(merged)
    return clients


def extract_session_id(proto: str, body: dict) -> str | None:
    """尽力而为的会话标识（spec §6 识图记录）：anthropic metadata.user_id（常含 __sessionId 后缀）。"""
    if proto != "anthropic":
        return None
    user_id = (body.get("metadata") or {}).get("user_id") if isinstance(body.get("metadata"), dict) else None
    if not isinstance(user_id, str) or not user_id:
        return None
    return user_id.rsplit("__", 1)[-1]


def _resolve_provider(cfg: ProxyConfig, relay, tool_states_cache: dict) -> str | None:
    """请求期 provider 解析（尽力而为）：两层=工具激活供应商；一层=relay 名。"""
    if getattr(relay, "via", None):
        return tool_states_cache.get(relay.via) or relay.via  # 激活供应商名未取到时退工具名
    return relay.name if relay.name != "default" else None
```

4b. `run_server` 构建并挂载 vlm 客户端表与工具缓存：

```python
def run_server(cfg: ProxyConfig | None = None, handler_cls=ProxyHandler):
    """Build the ThreadingHTTPServer with cfg + pipeline attached; caller calls serve_forever()."""
    cfg = cfg or load_config()
    vlm = VLMClient(cfg.vlm)
    cache = DescriptionCache()
    pipeline = Pipeline(vlm, cache)
    server = http.server.ThreadingHTTPServer((cfg.bind_host, cfg.bind_port), handler_cls)
    server.cfg = cfg  # type: ignore[attr-defined]
    server.pipeline = pipeline  # type: ignore[attr-defined]
    server.vlm_clients = build_vlm_clients(cfg)  # type: ignore[attr-defined]
    server.tool_provider_cache = {}  # type: ignore[attr-defined]  # via -> active provider（reconcile 时刷新）
    return server
```

4c. `do_POST` 中 `self._pipeline.process(...)` 一行替换为（按 harness 换 VLM 客户端 + provider + session）：

```python
            harness = _HARNESS_BY_PROTO.get(proto)
            relay = _select_relay(self._cfg, proto, ir.model)
            vlm_client = getattr(self.server, "vlm_clients", {}).get(harness) or self._pipeline.vlm
            self._pipeline.vlm = vlm_client  # 按 harness 切换 VLM（spec §7.1）
            provider = _resolve_provider(self._cfg, relay, getattr(self.server, "tool_provider_cache", {}))
            result = self._pipeline.process(
                ir, self._cfg, harness, provider, session_id=extract_session_id(proto, body)
            )
            out_body = _SERIALIZERS[proto](result.ir)
            status, text = _forward(relay, out_body, ir.stream)
```

（原 `relay = _select_relay(...)` 行删除，统一上移。）

- [ ] **Step 5: 全量回归 + lint + commit**

Run: `python -m pytest -q`

```bash
git add vision_relay/pipeline.py vision_relay/server.py tests/test_proxy_pipeline.py
git commit -m "feat(pipeline,server): per-harness VLM, session/provider passthrough, vision-log hook"
```

---

### Task 12: probe 集成 —— run_probe 写缓存 + 新模型自动标注

**Files:**
- Create: `vision_relay/annotate.py`（probe 落缓存 + 扫描标注，reconcile/verbs 共用）
- Test: `tests/test_proxy_annotate.py`

- [ ] **Step 1: 写失败测试（新文件）**

```python
"""auto annotation (spec §5): scan models -> probe/catalog/annotate into triple store."""

import json
import os

from vision_relay import annotate
from vision_relay.config import ProxyConfig, save_config


class TestRunProbe:
    def test_writes_probe_results_and_never_overwrites_user(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig.from_dict(
            {"model_capabilities": {"claude": {"bigmodel": {"m1": "text_only"}}},
             "capability_sources": {"claude": {"bigmodel": {"m1": "user"}}}}
        )
        monkeypatch.setattr(annotate, "_probe_one", lambda provider, model, *a: "image")
        result = annotate.run_probe(cfg, harness="claude", provider="bigmodel", model="m1",
                                    base_url="http://x", api_key="", protocol="chat")
        assert result == "text_only"  # user 来源不被探针覆盖
        assert cfg.probe_results["bigmodel"]["m1"]["result"] == "image"  # 但缓存记录实测

    def test_overwrites_catalog_sourced(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig.from_dict(
            {"model_capabilities": {"claude": {"bigmodel": {"m1": "text_only"}}},
             "capability_sources": {"claude": {"bigmodel": {"m1": "catalog"}}}}
        )
        monkeypatch.setattr(annotate, "_probe_one", lambda provider, model, *a: "image")
        result = annotate.run_probe(cfg, harness="claude", provider="bigmodel", model="m1",
                                    base_url="http://x", api_key="", protocol="chat")
        assert result == "image"
        assert cfg.model_capabilities["claude"]["bigmodel"]["m1"] == "image"
        assert cfg.capability_sources["claude"]["bigmodel"]["m1"] == "probe"


class TestAutoAnnotate:
    def test_new_model_annotated_from_probe_or_catalog(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig.from_dict({})
        scan = [
            {"harness": "claude", "provider": "bigmodel", "model": "glm-5-air"},   # probe 结论
            {"harness": "claude", "provider": "bigmodel", "model": "zzz-unknown"}, # 无探针->目录->仍未中->不落值
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
            {"model_capabilities": {"claude": {"bigmodel": {"m1": "image"}}},
             "capability_sources": {"claude": {"bigmodel": {"m1": "user"}}}}
        )
        annotate.auto_annotate(cfg, [{"harness": "claude", "provider": "bigmodel", "model": "m1"}], probe_targets={})
        assert cfg.model_capabilities["claude"]["bigmodel"]["m1"] == "image"
```

- [ ] **Step 2: 跑确认失败** → FAIL

- [ ] **Step 3: 实现 `vision_relay/annotate.py`**

```python
"""Auto annotation (spec §5 模型能力标注): scan -> probe/catalog -> write triple store.

写规则：source=user 绝不被自动标注覆盖；probe 结果覆盖 probe/catalog 来源值并
写 probe_results 缓存；目录建议（内置名单命中）写 source=catalog；都不中=不落键（未标注）。
"""

from __future__ import annotations

import time

from .capability import BUILTIN_CAPABILITIES
from .config import ProxyConfig, save_config
from .probe import probe_modality
from .reconcile import append_event
import fnmatch


def _probe_one(provider: str, model: str, base_url: str = "", api_key: str = "", protocol: str = "chat") -> str | None:
    """单一入口便于测试替换；无 base_url 时直接视为不可探测。"""
    if not base_url:
        return None
    return probe_modality(base_url, api_key, model, protocol)


def _set_value(cfg: ProxyConfig, harness: str, provider: str, model: str, value: str, source: str) -> None:
    cfg.model_capabilities.setdefault(harness, {}).setdefault(provider, {})[model] = value
    cfg.capability_sources.setdefault(harness, {}).setdefault(provider, {})[model] = source


def _source_of(cfg: ProxyConfig, harness: str, provider: str, model: str) -> str | None:
    return cfg.capability_sources.get(harness, {}).get(provider, {}).get(model)


def run_probe(
    cfg: ProxyConfig,
    harness: str,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    protocol: str,
) -> str | None:
    """执行探针：结果永远写 probe_results；仅当现有值非 user 来源时更新标注值。"""
    result = _probe_one(provider, model, base_url, api_key, protocol)
    if result is None:
        return None
    cfg.probe_results.setdefault(provider, {})[model] = {"result": result, "ts": time.time()}
    current_source = _source_of(cfg, harness, provider, model)
    effective = result
    if current_source != "user":
        _set_value(cfg, harness, provider, model, result, "probe")
    else:
        stored = cfg.model_capabilities.get(harness, {}).get(provider, {}).get(model)
        effective = stored if stored in ("image", "text_only") else result
    save_config(cfg)
    append_event("auto_annotate", harness, {"provider": provider, "model": model, "by": "probe", "result": result})
    return effective


def _catalog_suggest(model: str) -> str | None:
    for pattern, cap in BUILTIN_CAPABILITIES.items():
        if fnmatch.fnmatch(model, pattern):
            return cap
    return None


def auto_annotate(
    cfg: ProxyConfig,
    scan: list[dict],
    probe_targets: dict[str, tuple[str, str, str]],
) -> list[dict]:
    """对扫描到的 (harness, provider, model) 自动标注。
    probe_targets: {model: (base_url, api_key, protocol)} 可探测目标（两层=工具端口无 key 也可探）。
    返回逐模型报告（result None = 未标注）。"""
    report: list[dict] = []
    changed = False
    for row in scan:
        harness, provider, model = row["harness"], row.get("provider") or "?", row["model"]
        entry = {"harness": harness, "provider": provider, "model": model, "result": None, "by": None}
        if _source_of(cfg, harness, provider, model) == "user":
            entry["result"] = cfg.model_capabilities[harness][provider][model]
            entry["by"] = "user"
            report.append(entry)
            continue
        cached = cfg.probe_results.get(provider, {}).get(model, {}).get("result")
        if cached in ("image", "text_only"):
            _set_value(cfg, harness, provider, model, cached, "probe")
            entry.update(result=cached, by="probe-cache")
            report.append(entry)
            changed = True
            continue
        target = probe_targets.get(model)
        result = _probe_one(provider, model, *target) if target else None
        if result in ("image", "text_only"):
            cfg.probe_results.setdefault(provider, {})[model] = {"result": result, "ts": time.time()}
            _set_value(cfg, harness, provider, model, result, "probe")
            entry.update(result=result, by="probe")
            changed = True
        else:
            suggestion = _catalog_suggest(model)
            if suggestion:
                _set_value(cfg, harness, provider, model, suggestion, "catalog")
                entry.update(result=suggestion, by="catalog")
                changed = True
            # 都不中：不落键 = 未标注（运行按 unknown_default 开关）
        report.append(entry)
    if changed:
        save_config(cfg)
        append_event("auto_annotate", None, {"count": len(report)})
    return report
```

- [ ] **Step 4: 跑通过 + lint + commit**

```bash
python -m pytest tests/test_proxy_annotate.py -q
git add vision_relay/annotate.py tests/test_proxy_annotate.py
git commit -m "feat(annotate): probe/catalog auto-annotation into triple store (user overrides protected)"
```

---

### Task 13: cli —— start --detach / 意图状态 / refresh / diagnose / tools / probe / events / visionlog 子命令

**Files:**
- Modify: `vision_relay/cli.py`
- Test: `tests/test_proxy_cli.py`（追加）

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---- Phase2 M1: lifecycle intent + new verbs (json tested in Task 14) ----


class TestIntentState:
    def test_start_sets_routing_on_stop_clears(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay import reconcile

        cli.cmd_start_intent(True)
        assert reconcile.get_routing_on() is True
        cli.cmd_start_intent(False)
        assert reconcile.get_routing_on() is False


class TestDetachStart:
    def test_detach_spawns_child_and_returns(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        spawned = {}
        monkeypatch.setattr(
            cli,
            "_spawn_detached",
            lambda argv: spawned.setdefault("argv", argv) or 0,
        )
        rc = cli.cmd_start_detach(None)
        assert rc == 0
        assert spawned["argv"][1:] == ["-m", "vision_relay", "start"]


class TestRefreshVerb:
    def test_refresh_calls_reconcile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.config import ProxyConfig

        called = {}
        monkeypatch.setattr(
            "vision_relay.cli.reconcile_reconcile",
            lambda cfg, **kw: called.setdefault("ok", True) or {"actions": [], "needs_you": [], "observed": {}},
        )
        rc = cli.cmd_refresh(ProxyConfig())
        assert rc == 0 and called.get("ok") is True
```

- [ ] **Step 2: 跑确认失败** → FAIL

- [ ] **Step 3: 修改 `vision_relay/cli.py`**

3a. `parse_args` 子命令与参数补充：

```python
    st = sub.add_parser("start")
    st.add_argument("--detach", action="store_true", help="分离进程启动（GUI/自动重启用）")
    sub.add_parser("stop")
    sub.add_parser("status")
    sub.add_parser("logs")
    ti = sub.add_parser("test-image")
    ti.add_argument("path")
    ti.add_argument("--question", default=None)
    sub.add_parser("check")
    sub.add_parser("refresh")  # M1: 手动对账（= 刷新按钮后端）
    sub.add_parser("diagnose")  # M1: 观测 + 自动修复 + 报告
    sub.add_parser("tools")  # M1: 工具档案探测
    pr = sub.add_parser("probe")  # M1: 模态探针
    pr.add_argument("--harness")
    pr.add_argument("--provider")
    pr.add_argument("--model")
    pr.add_argument("--all-untested", action="store_true")
    sub.add_parser("events")  # M1: 事件日志 tail
    sub.add_parser("visionlog")  # M1: 识图记录查询
    sub.add_parser("models-scan")
    sub.add_parser("models")
```

3b. 文件头 import 区补模块级导入（供测试 monkeypatch `vision_relay.cli.reconcile_reconcile`；reconcile 不反向 import cli，无环）：

```python
from .reconcile import reconcile as reconcile_reconcile
```

新增意图/分离/动词实现（放在 `cmd_stop` 之后）：

```python
def cmd_start_intent(on: bool) -> None:
    """记录用户路由意图（崩溃后自动修复按此推导，spec §5）。"""
    from .reconcile import set_routing_on

    set_routing_on(on)


def _spawn_detached(argv: list[str]) -> int:
    import subprocess
    import sys

    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([sys.executable, *argv], **kwargs)
        return 0
    except OSError as exc:
        print(f"cannot spawn {argv}: {exc}", file=sys.stderr)
        return 1


def cmd_start_detach(cfg) -> int:
    """分离进程启动：父进程立即返回，子进程跑普通 start（写 pid/意图）。"""
    rc = _spawn_detached(["-m", "vision_relay", "start"])
    print("started (detached)" if rc == 0 else "detach failed")
    return rc


def cmd_refresh(cfg) -> int:
    """手动对账 = GUI「刷新」按钮的后端（spec §5 唯一写路径）。"""
    report = reconcile_reconcile(cfg, trigger="manual")
    for a in report["actions"]:
        print(f"  [reconcile] {a}")
    for n in report["needs_you"]:
        print(f"  [需要你] {n}")
    return 0


def cmd_diagnose(cfg) -> int:
    """诊断报告（自动运行 + 自动修复 + needs_you；spec §5 修复流程）。"""
    report = reconcile_reconcile(cfg, trigger="diagnose")
    obs = report["observed"]
    print(f"服务: {'运行中' if obs['service_alive'] else '未运行'} · 路由意图: {'开' if obs['routing_on'] else '关'}")
    for t in obs["tools"]:
        prov = f" · 供应商 {t['active_provider']}" if t["active_provider"] else ""
        print(f"  工具 {t['name']} :{t['port']} {'在线' if t['online'] else '离线'}{prov}")
    for name, row in obs["harnesses"].items():
        print(f"  {name}: base_url={row['base_url'] or '(无)'} [{row['ownership']}]")
    for a in report["actions"]:
        print(f"  [已自动处理] {a}")
    for n in report["needs_you"]:
        print(f"  ⚠ 需要你: {n}")
    return 0 if not report["needs_you"] else 1


def cmd_tools(cfg) -> int:
    from .tools import probe_tools

    for s in probe_tools():
        prov = f" · 供应商 {s.active_provider} ({s.provider_base_url})" if s.active_provider else ""
        print(f"{s.name}: :{s.port} {'在线' if s.online else '离线'}{prov}")
    return 0


def cmd_probe(args, cfg) -> int:
    from .annotate import run_probe
    from .reconcile import observe

    obs = observe(cfg)
    if args.all_untested or not args.model:
        # 对所有已知 (provider, model) 且无探针缓存的组合探测
        from .onboarding import scan_model_groups

        count = 0
        tool_by_name = {t["name"]: t for t in obs["tools"]}
        for g in scan_model_groups(cfg):
            provider = _provider_for_group(cfg, g.group, tool_by_name)
            for ent in g.entries:
                if args.model and ent.model != args.model:
                    continue
                cached = cfg.probe_results.get(provider or "?", {}).get(ent.model)
                if cached and not args.all_untested:
                    continue
                base, key, proto = _probe_target_for(cfg, g.group, provider, tool_by_name)
                result = run_probe(cfg, g.group, provider or "?", ent.model, base, key, proto)
                print(f"  {ent.model}: {result}")
                count += 1
        print(f"probed {count} model(s)")
        return 0
    base, key, proto = _probe_target_for(cfg, args.harness or "", args.provider or "?", {t["name"]: t for t in obs["tools"]})
    result = run_probe(cfg, args.harness or "?", args.provider or "?", args.model, base, key, proto)
    print(f"{args.model}: {result}")
    return 0 if result else 1


def _provider_for_group(cfg, group: str, tool_by_name: dict) -> str | None:
    """harness -> 当前 provider 名（两层=工具激活供应商；直连场景名未知回 None，调用方用 '?' 占位）。"""
    from . import tools

    for name, d in tools.TOOL_DOSSIERS.items():
        if group in d.harnesses and tool_by_name.get(name, {}).get("online") and tool_by_name[name].get("active_provider"):
            return tool_by_name[name]["active_provider"]
    return None


def _probe_target_for(cfg, harness: str, provider: str, tool_by_name: dict) -> tuple[str, str, str]:
    """探测目标：两层=工具端口（无 key）；直连=对应 relay 的 base_url+key。"""
    from . import tools

    proto = {"claude": "anthropic", "codex": "responses", "qwen-code": "chat"}.get(harness, "chat")
    for name, d in tools.TOOL_DOSSIERS.items():
        if harness in d.harnesses and tool_by_name.get(name, {}).get("online"):
            port = tool_by_name[name]["port"]
            base = "http://127.0.0.1:%d/v1" % port if name == "codex-plus" else "http://127.0.0.1:%d" % port
            return base, "", proto if name != "codex-plus" else "responses"
    for r in cfg.relays:
        if r.protocol == proto and r.base_url and not r.base_url.startswith("http://127.0.0.1"):
            return r.base_url, r.api_key, proto
    return "", "", proto


def cmd_events(cfg) -> int:
    from .reconcile import tail_events

    for row in tail_events(50):
        import time as _t

        stamp = _t.strftime("%m-%d %H:%M:%S", _t.localtime(row.get("ts", 0)))
        print(f"{stamp} [{row.get('type')}] {row.get('harness') or '-'} {row.get('detail') or row.get('name') or ''}")
    return 0


def cmd_visionlog(args, cfg) -> int:
    from .visionlog import query

    for row in query(harness=getattr(args, "harness", None), limit=50):
        print(f"{row.get('ts')} {row.get('harness')} t{row.get('tier')} cache={row.get('cache_hit')} {str(row.get('injected'))[:60]}")
    return 0
```

3c. `cmd_start`：`_write_pid()` 之后、`run_server` 之前插入 `cmd_start_intent(True)`，并在 `wiring_backup_and_rewrite` 循环之后追加一次对账收敛（spec §4「所有触发源走同一套逻辑」：start 也要吸收漂移/生成工具 relay/记事件）：

```python
        for msg in wiring_backup_and_rewrite(cfg):
            print(f"  [wire] {msg}")
        for report_key in ("actions",):
            for a in reconcile_reconcile(cfg, trigger="start")["actions"]:
                print(f"  [reconcile] {a}")
```

`cmd_stop` 在还原接线后追加 `cmd_start_intent(False)`。

3d. `parse_args` 里 `visionlog` 子命令补 `--harness` 参数；`main()` 分发追加：

```python
    if args.command == "start" and getattr(args, "detach", False):
        return cmd_start_detach(cfg)
    if args.command == "refresh":
        return cmd_refresh(cfg)
    if args.command == "diagnose":
        return cmd_diagnose(cfg)
    if args.command == "tools":
        return cmd_tools(cfg)
    if args.command == "probe":
        return cmd_probe(args, cfg)
    if args.command == "events":
        return cmd_events(cfg)
    if args.command == "visionlog":
        return cmd_visionlog(args, cfg)
```

并删除 `cmd_check` 里对 `cfg.ui_port` 的引用（`for port in (cfg.bind_port, cfg.ui_port)` → `for port in (cfg.bind_port,)`）；`cmd_start` 打印行去掉 `(control)` 与 ui_port 字样。

- [ ] **Step 4: 跑通过 + 全量回归 + lint + commit**

Run: `python -m pytest -q`；`ruff format vision_relay tests && ruff check vision_relay tests`

```bash
git add vision_relay/cli.py tests/test_proxy_cli.py
git commit -m "feat(cli): start --detach + intent state + refresh/diagnose/tools/probe/events/visionlog verbs"
```

---

### Task 14: verbs —— --json 契约全套

**Files:**
- Create: `vision_relay/verbs.py`
- Modify: `vision_relay/cli.py`（每个子命令挂 `--json`）
- Test: `tests/test_proxy_verbs.py`

- [ ] **Step 1: 写失败测试（新文件）**

```python
"""--json management verbs (spec §4/§8): envelope + contract_version on every verb."""

import json

import pytest

from vision_relay import verbs
from vision_relay.config import ProxyConfig


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    return ProxyConfig()


def test_envelope_shape():
    e = verbs.envelope(True, {"x": 1})
    assert e == {"contract_version": 1, "ok": True, "data": {"x": 1}}


def test_status(cfg, monkeypatch):
    monkeypatch.setattr(verbs, "_observe_for_status", lambda c: {"service_alive": False, "harnesses": {}, "tools": [], "routing_on": False})
    data = verbs.status(cfg)
    assert data["data"]["service_alive"] is False and data["contract_version"] == 1


def test_refresh(cfg, monkeypatch):
    monkeypatch.setattr(verbs, "_reconcile", lambda c, **kw: {"actions": [{"type": "reclaim"}], "needs_you": [], "observed": {}})
    data = verbs.refresh(cfg)
    assert data["ok"] is True and data["data"]["actions"][0]["type"] == "reclaim"


def test_diagnose(cfg, monkeypatch):
    monkeypatch.setattr(verbs, "_reconcile", lambda c, **kw: {"actions": [], "needs_you": [{"type": "missing_key"}], "observed": {"service_alive": True}})
    data = verbs.diagnose(cfg)
    assert data["data"]["needs_you"][0]["type"] == "missing_key"


def test_models_scan(cfg, monkeypatch):
    monkeypatch.setattr(
        verbs,
        "_scan_triples",
        lambda c: [{"harness": "claude", "provider": "bigmodel", "model": "glm-5-air", "value": None, "source": None}],
    )
    data = verbs.models_scan(cfg)
    row = data["data"]["models"][0]
    assert row["value"] is None and row["source"] is None  # 未标注=空


def test_config_get_masks_secrets(cfg):
    cfg.vlm.api_key = "sk-secret"
    data = verbs.config_get(cfg)
    text = json.dumps(data)
    assert "sk-secret" not in text
    assert "api_key" in text and data["data"]["vlm"]["api_key"] == "●●●●"


def test_tools(cfg, monkeypatch):
    from vision_relay.tools import ToolState

    monkeypatch.setattr(verbs, "_probe_tools", lambda: [ToolState("cc-switch", 15721, True, "bigmodel", "https://x")])
    data = verbs.tools(cfg)
    assert data["data"][0]["active_provider"] == "bigmodel"


def test_events(cfg, monkeypatch):
    monkeypatch.setattr(verbs, "_tail_events", lambda n: [{"type": "reclaim", "harness": "codex"}])
    data = verbs.events(cfg)
    assert data["data"][0]["type"] == "reclaim"


def test_visionlog(cfg, monkeypatch):
    monkeypatch.setattr(verbs, "_vl_query", lambda **kw: [{"harness": "claude", "tier": 1}])
    data = verbs.visionlog(cfg, harness="claude")
    assert data["data"][0]["harness"] == "claude"


def test_every_verb_has_contract_version(cfg, monkeypatch):
    monkeypatch.setattr(verbs, "_observe_for_status", lambda c: {"service_alive": False, "harnesses": {}, "tools": [], "routing_on": False})
    monkeypatch.setattr(verbs, "_reconcile", lambda c, **kw: {"actions": [], "needs_you": [], "observed": {}})
    monkeypatch.setattr(verbs, "_scan_triples", lambda c: [])
    monkeypatch.setattr(verbs, "_probe_tools", lambda: [])
    monkeypatch.setattr(verbs, "_tail_events", lambda n: [])
    monkeypatch.setattr(verbs, "_vl_query", lambda **kw: [])
    for fn in (verbs.status, verbs.refresh, verbs.diagnose, verbs.models_scan, verbs.config_get, verbs.tools, verbs.events, verbs.visionlog):
        assert fn(cfg)["contract_version"] == verbs.CONTRACT_VERSION
```

- [ ] **Step 2: 跑确认失败** → FAIL

- [ ] **Step 3: 实现 `vision_relay/verbs.py`**

```python
"""--json management verbs (spec §4 通信契约): one envelope, contract_version pinned.

GUI（M2）只消费这些动词的输出；结构变更必须升 contract_version 并在 spec 记录。
"""

from __future__ import annotations

from .config import ProxyConfig

CONTRACT_VERSION = 1


def envelope(ok: bool, data) -> dict:
    return {"contract_version": CONTRACT_VERSION, "ok": ok, "data": data}


# 依赖注入点（测试替换；生产各指向真实现）
from .reconcile import observe as _observe_impl  # noqa: E402
from .reconcile import reconcile as _reconcile_impl  # noqa: E402
from .tools import probe_tools as _probe_tools_impl  # noqa: E402
from .visionlog import query as _vl_query_impl  # noqa: E402


def _observe_for_status(cfg: ProxyConfig) -> dict:
    return _observe_impl(cfg)


def _reconcile(cfg: ProxyConfig, **kw) -> dict:
    return _reconcile_impl(cfg, **kw)


def _probe_tools() -> list:
    return _probe_tools_impl()


def _tail_events(n: int = 50) -> list[dict]:
    from .reconcile import tail_events

    return tail_events(n)


def _vl_query(**kw) -> list[dict]:
    return _vl_query_impl(**kw)


def _scan_triples(cfg: ProxyConfig) -> list[dict]:
    """扫描 harness 配置 -> 三元组 + 当前标注值/来源（未标注= value None）。"""
    from .onboarding import scan_model_groups

    rows: list[dict] = []
    for g in scan_model_groups(cfg):
        provider = _provider_hint(cfg, g.group)
        for ent in g.entries:
            value = cfg.model_capabilities.get(g.group, {}).get(provider, {}).get(ent.model)
            source = cfg.capability_sources.get(g.group, {}).get(provider, {}).get(ent.model)
            rows.append(
                {
                    "harness": g.group,
                    "provider": provider,
                    "model": ent.model,
                    "value": value,
                    "source": source,
                    "probe_cached": (cfg.probe_results.get(provider, {}).get(ent.model) or {}).get("result"),
                }
            )
    return rows


def _provider_hint(cfg: ProxyConfig, harness: str) -> str:
    from . import snapshot
    from .tools import TOOL_DOSSIERS, probe_tools

    for s in probe_tools():
        d = TOOL_DOSSIERS.get(s.name)
        if d and harness in d.harnesses and s.online and s.active_provider:
            return s.active_provider
    snap = snapshot.load().get(harness)
    if snap is not None and snap.second_hop:
        return snap.second_hop
    return "?"


def status(cfg: ProxyConfig) -> dict:
    obs = _observe_for_status(cfg)
    return envelope(True, obs)


def refresh(cfg: ProxyConfig) -> dict:
    report = _reconcile(cfg, trigger="manual")
    return envelope(True, report)


def diagnose(cfg: ProxyConfig) -> dict:
    report = _reconcile(cfg, trigger="diagnose")
    return envelope(not report["needs_you"], report)


def models_scan(cfg: ProxyConfig) -> dict:
    return envelope(True, {"models": _scan_triples(cfg)})


def config_get(cfg: ProxyConfig) -> dict:
    def mask(v: str) -> str:
        return "●●●●" if v else v

    data = cfg.to_dict()
    data["vlm"]["api_key"] = mask(data["vlm"].get("api_key", ""))
    for h, over in list(data.get("vlm_by_harness", {}).items()):
        if isinstance(over, dict) and over.get("api_key"):
            over["api_key"] = "●●●●"
    for r in data.get("relays", []):
        if r.get("api_key"):
            r["api_key"] = "●●●●"
    return envelope(True, data)


def tools(cfg: ProxyConfig) -> dict:
    return envelope(
        True,
        [
            {"name": s.name, "port": s.port, "online": s.online, "active_provider": s.active_provider, "provider_base_url": s.provider_base_url}
            for s in _probe_tools()
        ],
    )


def events(cfg: ProxyConfig, tail: int = 50) -> dict:
    return envelope(True, {"events": _tail_events(tail)})


def visionlog(cfg: ProxyConfig, harness: str | None = None, session: str | None = None) -> dict:
    return envelope(True, {"records": _vl_query(harness=harness, session=session)})
```

- [ ] **Step 4: cli 挂 `--json`**

`parse_args` 用公共 parent parser（保证 `vision-relay status --json` 子命令后置flag 可用），并注册 `config` 子命令：

```python
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output (contract_version pinned)")
    # 每个子命令注册时传 parents=[common]，例如：
    #   sub.add_parser("status", parents=[common])
    #   sub.add_parser("refresh", parents=[common])  …（全部子命令同理）
    sub.add_parser("config", parents=[common])  # M1: config get（读视图，key 打码）
```

`main()` 分发处，对有 verbs 对应的命令：

```python
    import json as _json

    from . import verbs

    _JSON_MAP = {
        "status": verbs.status,
        "refresh": verbs.refresh,
        "diagnose": verbs.diagnose,
        "models-scan": verbs.models_scan,
        "config": verbs.config_get,
        "tools": verbs.tools,
        "events": verbs.events,
        "visionlog": verbs.visionlog,
    }
    if args.json and args.command in _JSON_MAP:
        kw = {}
        if args.command == "visionlog":
            kw["harness"] = getattr(args, "harness", None)
        print(_json.dumps(_JSON_MAP[args.command](cfg), ensure_ascii=False))
        return 0
```

（`cmd_start` 的 `--detach` 不与 `--json` 组合使用，M1 不做其 JSON 输出。）

- [ ] **Step 5: 跑通过 + 全量回归 + lint + commit**

Run: `python -m pytest -q`

```bash
git add vision_relay/verbs.py vision_relay/cli.py tests/test_proxy_verbs.py
git commit -m "feat(verbs): --json management API with pinned contract_version"
```

---

### Task 15: onboarding —— 三元组扫描（provider 列）+ 终端向导三态默认

**Files:**
- Modify: `vision_relay/onboarding.py`
- Test: `tests/test_proxy_cli.py`（`models-scan` 相关既有用例扩展）

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---- Phase2 M1: onboarding tri-state default ----


class TestOnboardingTriState:
    def test_confirm_defaults_to_text_only_and_uses_image_value(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.config import ProxyConfig
        from vision_relay.onboarding import ModelEntry, ModelGroup, confirm_models

        groups = [ModelGroup(group="claude", path="x", entries=[ModelEntry(model="qwen-vl-max")])]
        keys = iter(["enter"])  # 直接回车 = 接受默认（未勾选=纯文本）
        result = confirm_models(groups, key_source=lambda: next(keys), out=_Sink())
        assert result["claude"]["qwen-vl-max"] == "text_only"

    def test_toggle_marks_image(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay.onboarding import ModelEntry, ModelGroup, confirm_models

        groups = [ModelGroup(group="claude", path="x", entries=[ModelEntry(model="kimi-k2.7-code")])]
        keys = iter(["space", "enter"])
        result = confirm_models(groups, key_source=lambda: next(keys), out=_Sink())
        assert result["claude"]["kimi-k2.7-code"] == "image"  # 内部值 image（非 vision）


class _Sink:
    def write(self, s):
        pass

    def flush(self):
        pass
```

- [ ] **Step 2: 跑确认失败** → FAIL（当前返回 `"vision"`）

- [ ] **Step 3: 修改 `vision_relay/onboarding.py`**

- `_default_cap`：返回值 `"vision"` 全部改 `"image"`（BUILTIN 已在 Task 2 改值）。
- `confirm_models` 中 `vision[...] = (_default_cap(ent.model) == "image")`；结果映射行改：

```python
        result.setdefault(g.group, {})[ent.model] = "image" if vision.get((id(g), ent.model)) else "text_only"
```

- 界面文案 `"[x] 支持图片" / "[ ] 纯文本 "` 保持；`models_scan_report` 的 `_default_cap` 输出自动跟随。

- [ ] **Step 4: 跑通过 + 全量回归 + lint + commit**

Run: `python -m pytest -q`

```bash
git add vision_relay/onboarding.py tests/test_proxy_cli.py
git commit -m "feat(onboarding): tri-state confirm defaults to text_only, internal value 'image'"
```

---

### Task 16: 文档同步 + 手动冒烟脚本

**Files:**
- Modify: `README.md`, `README.zh.md`, `CHANGELOG.md`, `AGENTS.md`
- Create: `docs/superpowers/plans/2026-08-22-vision-relay-phase2-m1-manual-test.md`

- [ ] **Step 1: README 双语「运行命令」表补新命令**：`start --detach / refresh / diagnose / tools / probe / events / visionlog` 各一行一句话说明 + `--json` 示例一条：

```
vision-relay status --json
```

- [ ] **Step 2: CHANGELOG `Unreleased` 追加**：`Added: M1 control plane — refresh/diagnose verbs, reconcile engine with intent-based auto-repair, tool dossiers, modality probe (tri-state), takeover snapshots, file lock, vision call records, per-harness VLM, tri-state capability store (image terminology)`。

- [ ] **Step 3: AGENTS.md 命名对齐表补一行**：`能力值 image|text_only（旧 vision 只读兼容）；运行状态 state.json；事件 events.jsonl；识图留痕 visionlog/`。并在白名单里把 `capability.py 内置 "qwen-vl-*": "vision"` 改为 `"image"`。

- [ ] **Step 4: 写手动测试手册 `2026-08-22-vision-relay-phase2-m1-manual-test.md`**（内容 = 下方「附录 A」全文粘贴）。

- [ ] **Step 5: 全量验证 + commit**

Run: `python -m pytest -q && ruff format --check . && ruff check .`

```bash
git add README.md README.zh.md CHANGELOG.md AGENTS.md docs/superpowers/plans/2026-08-22-vision-relay-phase2-m1-manual-test.md
git commit -m "docs: M1 CLI verbs, AGENTS naming updates, manual test checklist"
```

---

## 附录 A：手动测试手册（M1 验收，真实环境）

> 前置：本机已装 CC Switch 或 Codex++（任一即可）；有一个可用的 OpenAI 兼容 VLM key。
> 全程在项目根目录，用 `.venv\Scripts\python -m vision_relay …`（或装好后的 `vision-relay …`）。
> 每条都写「预期」，不符即 FAIL 并记录到 issues。

### A1. 刷新优先做（核心诉求）
1. `vision-relay start` → 服务起来，三 harness 接线，`proxy.json` 出现自动 relay（工具在线时）。
   预期：输出含 `[wire] claude: base_url -> http://127.0.0.1:8787 (ok)`；`vision-relay tools` 显示在线工具与激活供应商。
2. 保持服务运行，手动把 `~/.codex/config.toml` 的 `base_url` 改成 `http://127.0.0.1:57321/v1`（模拟工具抢线）。
3. `vision-relay refresh`。
   预期：输出 `[reconcile] {'type': 'reclaim', 'harness': 'codex', ...}`；重新查看 config.toml 已回到 `:8787`；`vision-relay events` 最后一条是 `reclaim`。

### A2. 换供应商吸收
1. 服务运行中，把 `~/.claude/settings.json` 的 `env.ANTHROPIC_BASE_URL` 改成一个陌生地址（如 `https://newvendor.example/api`）。
2. `vision-relay refresh`。
   预期：`absorb` 事件；base_url 被接管回 `:8787`；`proxy.json` 出现 `direct-claude` relay 指向新地址；`~/.vision-relay/snapshots.json` 的 claude 快照 base_url 已更新；`diagnose` 提示需要补 key。

### A3. 僵尸接线自动修复（两种意图）
1. 路由开着：`vision-relay start`，然后直接 `taskkill /F /PID <pid>`（或 `kill -9`）。
   预期：pid 文件残留。运行 `vision-relay diagnose`：`routing_on=true` → 自动 `restart`，数秒后 `vision-relay status --json` 显示 `service_alive: true`，接线保持 `:8787`（事件里 `auto_fix restart`）。
2. 路由关着（模拟关路由时崩溃）：`vision-relay stop` 后手动把 claude 的 base_url 改回 `:8787` 再 `vision-relay diagnose`。
   预期：`routing_on=false` → `auto_fix restore`，base_url 恢复为快照里的原值。

### A4. 模态探针（真供应商）
1. `vision-relay probe --harness claude --provider bigmodel --model <你的文本模型>`。
   预期：文本模型返回 `text_only`（大概率报错判定）；再对视觉模型（如 qwen-vl-max，经 dashscope 直连 relay）探测，预期 `image`。
2. `vision-relay models-scan --json`。
   预期：三元组行含 `value/source/probe_cached`；未标注模型 `value: null`。

### A5. 识图留痕三段
1. 配好 VLM key，`vision-relay start`。
2. 用 curl 向代理发一条带 data URL 小图的 chat 请求（示例图任意 <100KB png base64）：
   `curl http://127.0.0.1:8787/v1/chat/completions -d '{"model":"<文本模型>","messages":[{"role":"user","content":[{"type":"text","text":"这是什么"},{"type":"image_url","image_url":{"url":"data:image/png;base64,<...>"}}]}]}'`
3. `vision-relay visionlog`。
   预期：一条记录，含 harness、tier、prompt（Tier2 带你的问题）、raw、injected（`[图片描述]` 开头）。

### A6. --json 契约抽查
1. `vision-relay status --json`、`refresh --json`、`diagnose --json`、`tools --json`、`events --json`。
   预期：每条输出都是合法 JSON 且含 `"contract_version": 1`；`config get --json`（若实现）不含明文 key（`●●●●`）。

### A7. 留存与开关
1. `proxy.json` 设 `"vision_log": {"enabled": false}` → 重启 → 发图请求 → `vision-relay visionlog` 无新记录。
2. 恢复 enabled=true，把 `retention_days` 设 0 → 重启（触发 cleanup）→ 昨日及更早的 visionlog 文件被清理。

---

## 附录 B：执行纪律（给子代理）

1. **严格 TDD 顺序**，每个 checkbox 打勾前必须真的跑过命令并核对预期输出。
2. **尊重 spec**：与 `2026-08-21-vision-relay-phase2-control-plane-design.md` 冲突时停下来问用户，不要自行发挥；AGENTS.md 的不变量（fail-open、不写工具配置、日志无 key）不可破坏。
3. **频繁提交**：每个 Task 一个 commit（计划里已给 message）；不许把多个 Task 混进一个 commit。
4. **不许跳测试**：`python -m pytest -q` 全绿 + `ruff format --check .` + `ruff check .` 零告警才算 Task 完成。
5. 计划中的代码是**贴合现状的起点**：允许在实现中发现签名/导入位置的小出入并就地修正（保持测试语义不变），但不允许改变行为语义或放宽测试。
