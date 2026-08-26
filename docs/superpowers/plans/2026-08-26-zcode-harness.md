# Zcode Harness 接入 + 路由范围勾选 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 zcode 接为第四个受管 harness（直连接管 + 模态门 + 指纹选路 + 重启交互），并交付适用于全部四个工具的「路由范围勾选」。

**Architecture:** 镜像 qwen 条目级接管的既有模式——zcode 接管/还原/relay 生命周期长在 `wiring.py`，快照复用 `provider_urls`/`provider_modalities` 字段（键 `pid::kind`），对账走 `reconcile()` 单分支；密钥指纹（前4+后4+长度）做成 `fingerprint.py` 通用件并挂进 `_select_relay` 第一优先级；GUI 三选弹窗 + 待重启提示条由 `status` 新字段 `zcode_runtime` 驱动。Spec: `docs/superpowers/specs/2026-08-26-zcode-harness-design.md`。

**Tech Stack:** Python 3.10+（仅标准库 + httpx，无新依赖）、pytest、React + TypeScript + Tauri（GUI）、vitest。

**全局约束（每个任务都要遵守）：**
- TDD：先写测试看到失败，再实现看到通过；断言不许放宽。
- `python -m pytest -q` 全绿、`ruff format --check .`、`ruff check .` 全绿后才 commit。
- 日志/快照绝不出现 key 明文；指纹（`963b…9NVz@36` 形态）只存 proxy.json 的 relay 上，不进日志。
- 不 push；每个任务单独 commit；commit 信息用中文（对齐仓库惯例）。

---

## 文件结构总览

| 文件 | 动作 | 职责 |
|---|---|---|
| `vision_relay/config.py` | 修改 | HARNESSES 加 zcode、旧默认迁移、RelayConfig 加 `auth_hints`/`provider_id` |
| `vision_relay/fingerprint.py` | 新建 | 密钥指纹 + 从请求头取指纹（通用件） |
| `vision_relay/wiring.py` | 修改 | zcode 接管/还原/relay/统计/改写标记 + 模态门共用原语 + 单 harness 还原抽取 |
| `vision_relay/snapshot.py` | 修改 | `_KEY_FIELDS` 加 zcode、`key_ref_for` 特判 |
| `vision_relay/reconcile.py` | 修改 | reconcile() zcode 分支 + ensure_zcode_relays 挂点 |
| `vision_relay/server.py` | 修改 | `_select_relay` 指纹层、harness 归属、VLM 客户端四 harness |
| `vision_relay/model_sources.py` | 修改 | `zcode_matrix`/`zcode_probe_target`、harness_matrix 分支 |
| `vision_relay/verbs.py` | 修改 | probe_target_for zcode 分支、settings-set harnesses、status zcode_runtime、zcode_restart verb |
| `vision_relay/cli.py` | 修改 | `_JSON_MAP`/子命令加 `zcode-restart` |
| `vision_relay/zcode_proc.py` | 新建 | zcode 进程检测/重启（best-effort，无 psutil） |
| `tests/test_proxy_zcode.py` | 新建 | zcode 全部后端单测 |
| `tests/test_proxy_wiring.py` 等 | 修改 | 既有文件补对应用例 |
| `gui/src/lib/chain.ts` | 修改 | zcode 标签 |
| `gui/src/shell/useStatus.ts` | 修改 | `zcode_runtime` 类型 |
| `gui/src/shell/ZcodeDialog.tsx` | 新建 | 三选弹窗组件 |
| `gui/src/shell/RoutingToggle.tsx` | 修改 | 开/关路由三选弹窗 |
| `gui/src/pages/Settings.tsx` | 修改 | 路由范围勾选 + VLM 分组派生 + 取消勾选弹窗 |
| `gui/src/pages/Overview.tsx` | 修改 | 待重启提示条 |
| `gui/src/i18n.ts` | 修改 | 新文案 |
| `gui/src/**/*.test.tsx` | 修改 | GUI 用例 |

---

### Task 1: config.py — zcode 注册、存量迁移、RelayConfig 新字段

**Files:**
- Modify: `vision_relay/config.py`
- Test: `tests/test_proxy_config.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_proxy_config.py` 末尾追加：

```python
class TestZcodeHarnessRegistration:
    def test_harnesses_include_zcode(self):
        from vision_relay.config import HARNESSES, RoutingConfig

        assert "zcode" in HARNESSES
        assert RoutingConfig().harnesses == list(HARNESSES)  # 默认=全量（含 zcode）

    def test_legacy_default_harnesses_upgraded(self):
        """旧默认全集（三工具）自动升级为含 zcode 的全集。"""
        from vision_relay.config import ProxyConfig

        cfg = ProxyConfig.from_dict({"routing": {"harnesses": ["claude", "codex", "qwen-code"]}})
        assert "zcode" in cfg.routing.harnesses

    def test_explicit_exclusion_not_upgraded(self):
        """曾显式排除过某工具（≠旧默认全集）→ 不自动加 zcode。"""
        from vision_relay.config import ProxyConfig

        cfg = ProxyConfig.from_dict({"routing": {"harnesses": ["claude", "codex"]}})
        assert cfg.routing.harnesses == ["claude", "codex"]

    def test_relay_new_fields_roundtrip(self):
        from vision_relay.config import ProxyConfig, RelayConfig

        cfg = ProxyConfig.from_dict(
            {
                "relays": [
                    {
                        "name": "zcode-builtin-bigmodel",
                        "protocol": "anthropic",
                        "base_url": "https://open.bigmodel.cn/api/anthropic",
                        "models": ["GLM-5.3"],
                        "provider_id": "builtin:bigmodel",
                        "auth_hints": ["963b…9NVz@36"],
                    }
                ]
            }
        )
        r = cfg.relays[0]
        assert r.provider_id == "builtin:bigmodel"
        assert r.auth_hints == ["963b…9NVz@36"]
        out = cfg.to_dict()
        assert out["relays"][0]["provider_id"] == "builtin:bigmodel"

    def test_relay_new_fields_default_empty(self):
        from vision_relay.config import RelayConfig

        r = RelayConfig(name="x", protocol="chat", base_url="https://a")
        assert r.auth_hints == []
        assert r.provider_id is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_proxy_config.py::TestZcodeHarnessRegistration -q`
Expected: FAIL（`"zcode" not in HARNESSES` / TypeError unexpected keyword `provider_id`）

- [ ] **Step 3: 实现**

`vision_relay/config.py` 三处改动：

① `HARNESSES` 常量与迁移常量（替换现有 `HARNESSES = ("claude", "codex", "qwen-code")`）：

```python
# start/stop 自动接线覆盖的 harness（第一跳 base_url→本代理）。
HARNESSES = ("claude", "codex", "qwen-code", "zcode")
# 2026-08-26 前的默认全集：恰等于它的存量配置升级为含 zcode 的全集；
# 不等于（用户曾显式排除）则视为有意选择、不自动加（spec §8 迁移规则）。
_LEGACY_DEFAULT_HARNESSES = ["claude", "codex", "qwen-code"]
```

② `ProxyConfig.from_dict` 内，在 `routing = dict(data.get("routing", {}))` 之后、`if routing.get("unknown_default") == "vision":` 之前插入：

```python
    if routing.get("harnesses") == _LEGACY_DEFAULT_HARNESSES:
        routing["harnesses"] = list(HARNESSES)
```

③ `RelayConfig` 字段（加在 `via` 字段之后）：

```python
    auth_hints: list[str] = field(default_factory=list)  # 客户端密钥指纹（前4+后4+长度，非密钥值）；选路消歧（spec §6）
    provider_id: str | None = None  # 一层 relay 的供应商身份（zcode 供应商 ID；能力/探针键与矩阵同键，spec §6.4）
```

（`to_dict` 走 `r.__dict__`、`_parse_relays` 走 `RelayConfig(**r)`，dataclass 默认值字段两端自动兼容，无需再改。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_proxy_config.py -q`
Expected: PASS（全部）

- [ ] **Step 5: 全量回归 + commit**

```bash
python -m pytest -q && ruff format --check . && ruff check .
git add vision_relay/config.py tests/test_proxy_config.py
git commit -m "feat(config): HARNESSES 注册 zcode——旧默认全集自动升级/显式排除不动;RelayConfig 加 auth_hints 指纹与 provider_id(选路消歧与能力键同源)"
```

---

### Task 2: fingerprint.py — 密钥指纹通用件

**Files:**
- Create: `vision_relay/fingerprint.py`
- Test: `tests/test_proxy_zcode.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_proxy_zcode.py`：

```python
"""zcode harness: fingerprint / wiring / relay / matrix / verbs（spec 2026-08-26）。"""

import json
import os

from vision_relay import wiring
from vision_relay.config import ProxyConfig


def _write_zcode_config(home, providers: dict) -> str:
    p = os.path.join(str(home), ".zcode", "v2", "config.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"provider": providers}, f, ensure_ascii=False, indent=2)
    return p


def _provider(url="https://open.bigmodel.cn/api/anthropic", key="k-1234567890abcdef", kind="anthropic", enabled=True, models=None):
    return {
        "name": "P",
        "kind": kind,
        "options": {"apiKey": key, "baseURL": url},
        "enabled": enabled,
        "models": models if models is not None else {},
    }


class TestFingerprint:
    def test_shape(self):
        from vision_relay.fingerprint import key_fingerprint

        fp = key_fingerprint("963b01ba64764f1f86e4f53223f0df2c.4bGDCSCynWRi9NVz")
        assert fp.startswith("963b") and fp.endswith("9NVz@51")
        assert "…" in fp

    def test_short_key_hides_chars(self):
        from vision_relay.fingerprint import key_fingerprint

        assert key_fingerprint("abc") == "short@3"
        assert key_fingerprint("") == "short@0"

    def test_from_headers_bearer_and_xapikey(self):
        from vision_relay.fingerprint import fingerprint_from_headers, key_fingerprint

        assert fingerprint_from_headers({"Authorization": "Bearer sk-abcdefgh1234"}) == key_fingerprint("sk-abcdefgh1234")
        assert fingerprint_from_headers({"x-api-key": "sk-abcdefgh1234"}) == key_fingerprint("sk-abcdefgh1234")
        assert fingerprint_from_headers({}) is None
        assert fingerprint_from_headers({"Authorization": ""}) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_proxy_zcode.py::TestFingerprint -q`
Expected: FAIL（`ModuleNotFoundError: vision_relay.fingerprint`）

- [ ] **Step 3: 实现**

新建 `vision_relay/fingerprint.py`：

```python
"""密钥指纹（spec 2026-08-26 §6）：前4+后4+长度，用于请求期选路消歧。

8 个字符无法还原 32+ 字符的随机 key，且 proxy.json（0600）本就允许存完整 key 的
relay——指纹严格更不敏感；仍不进日志/快照/GUI。极短 key 只露长度（不露字符）。"""

from __future__ import annotations


def key_fingerprint(key: str) -> str:
    k = str(key or "")
    if len(k) < 8:
        return f"short@{len(k)}"
    return f"{k[:4]}…{k[-4:]}@{len(k)}"


def fingerprint_from_headers(headers: dict[str, str]) -> str | None:
    """客户端入站鉴权头 → 指纹（best-effort；取不到返回 None，选路退回顺序命中）。"""
    auth = headers.get("Authorization") or headers.get("x-api-key") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else auth.strip()
    return key_fingerprint(token) if token else None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_proxy_zcode.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
python -m pytest -q && ruff format --check . && ruff check .
git add vision_relay/fingerprint.py tests/test_proxy_zcode.py
git commit -m "feat(fingerprint): 密钥指纹通用件——前4+后4+长度(极短只露长度)+请求头取 token;选路消歧与 qwen 未来复用"
```

---

### Task 3: wiring.py — zcode 注册、条目收集、统计、snapshot 键位

**Files:**
- Modify: `vision_relay/wiring.py`、`vision_relay/snapshot.py`
- Test: `tests/test_proxy_zcode.py`

- [ ] **Step 1: 写失败测试**

`tests/test_proxy_zcode.py` 追加：

```python
class TestZcodeRegistration:
    def test_harness_cfg_registered(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        p = _write_zcode_config(tmp_path, {"builtin:bigmodel": _provider(enabled=True)})
        h = wiring.HARNESS_CFG["zcode"]
        assert h.kind == "zcode-v2"
        assert wiring._path(str(tmp_path), "zcode") == p

    def test_read_base_url_returns_enabled_provider(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        _write_zcode_config(
            tmp_path,
            {
                "a": _provider(url="https://a.example", enabled=False),
                "b": _provider(url="https://b.example", enabled=True),
            },
        )
        p = wiring._path(str(tmp_path), "zcode")
        assert wiring.read_base_url(p, wiring.HARNESS_CFG["zcode"]) == "https://b.example"

    def test_entries_admission(self):
        d = {
            "provider": {
                "k": _provider(),                                            # 可接管
                "nokey": _provider(key=""),                                  # 空 key 跳过
                "badkind": _provider(kind="gemini"),                         # 未知 kind 跳过
                "nourl": _provider(url=""),                                  # 无 URL 跳过
            }
        }
        items, nokey, badkind = wiring._zcode_entries(d)
        assert [pid for pid, _k, _e in items] == ["k"]
        assert nokey == 1 and badkind == 1

    def test_stats_counts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        proxy = "http://127.0.0.1:8787"
        _write_zcode_config(
            tmp_path,
            {
                "wired": _provider(url=proxy, models={"m": {"modalities": {"input": ["text", "image"], "output": ["text"]}}}),
                "nowired": _provider(url="https://x.example"),
                "nokey": _provider(key="", url="https://y.example"),
            },
        )
        stats = wiring._zcode_provider_stats(wiring._path(str(tmp_path), "zcode"), proxy)
        assert stats == {"total": 3, "eligible": 2, "wired": 1, "gated": 1, "skipped_nokey": 1, "skipped_kind": 0}

    def test_snapshot_key_ref_for_zcode(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        from vision_relay import snapshot

        assert snapshot.key_ref_for("zcode") == "not-found"
        _write_zcode_config(tmp_path, {"k": _provider()})
        assert snapshot.key_ref_for("zcode") == "provider[].options.apiKey"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_proxy_zcode.py::TestZcodeRegistration -q`
Expected: FAIL（`KeyError: 'zcode'`）

- [ ] **Step 3: 实现**

① `wiring.py` 顶部 import 加 `import time`；`HARNESS_CFG` 加条目与常量：

```python
HARNESS_CFG: dict[str, _Harness] = {
    # qwen-code 实际配置在 ~/.qwen/settings.json 的 model.baseUrl（不是旧路径 ~/.qwen-code/.env）
    "claude": _Harness("json", (".claude", "settings.json"), "env.ANTHROPIC_BASE_URL"),
    "codex": _Harness("toml", (".codex", "config.toml"), "base_url"),
    "qwen-code": _Harness("json", (".qwen", "settings.json"), "model.baseUrl"),
    # zcode 供应商配置在 ~/.zcode/v2/config.json 的 provider.<id>.options.baseURL（纯条目级，
    # 无全局 base_url；key 字段仅作路径占位，read_base_url 特判返回激活供应商地址）
    "zcode": _Harness("zcode-v2", (".zcode", "v2", "config.json"), "provider"),
}

# zcode provider.kind → relay 协议（spec §4；未知 kind 不接管）
_ZCODE_PROTO = {"anthropic": "anthropic", "openai": "chat", "openai-compatible": "chat"}
_ZCODE_RELAY_PREFIX = "zcode-"
```

② `read_base_url` 在 `if h.kind == "json":` 分支前插入：

```python
        if h.kind == "zcode-v2":
            d = json.load(open(path, encoding="utf-8"))
            provs = d.get("provider")
            if isinstance(provs, dict):
                for e in provs.values():
                    if isinstance(e, dict) and e.get("enabled") is True:
                        opts = e.get("options")
                        if isinstance(opts, dict) and isinstance(opts.get("baseURL"), str) and opts["baseURL"]:
                            return opts["baseURL"]
            return None
```

③ 条目收集与统计（放在 `_qwen_provider_stats` 之前）：

```python
def _zcode_key(pid: str, kind: str) -> str:
    """快照身份键：供应商 ID + 接口格式（kind 变更=身份变更→还原不命中，交给对账吸收）。"""
    return f"{pid}::{kind}"


def _zcode_entries(d: dict) -> tuple[list[tuple[str, str, dict]], int, int]:
    """收集可接管条目 (pid, kind, entry)：baseURL/apiKey 均非空 + kind 已知。
    返回 (items, skipped_nokey, skipped_kind)——空 key 预设供应商不接管（spec §5.1）。"""
    provs = d.get("provider")
    if not isinstance(provs, dict):
        return [], 0, 0
    items: list[tuple[str, str, dict]] = []
    nokey = badkind = 0
    for pid, e in provs.items():
        if not isinstance(e, dict) or not isinstance(e.get("options"), dict):
            continue
        opts = e["options"]
        url, key = opts.get("baseURL"), opts.get("apiKey")
        if not (isinstance(url, str) and url):
            continue
        if not (isinstance(key, str) and key):
            nokey += 1
            continue
        kind = e.get("kind")
        if kind not in _ZCODE_PROTO:
            badkind += 1
            continue
        items.append((str(pid), kind, e))
    return items, nokey, badkind


def _zcode_provider_gated(entry: dict) -> bool:
    """该供应商全部模型的图片门都已开（无模型视为 True；input 形态不认识不计）。"""
    models = entry.get("models")
    if not isinstance(models, dict) or not models:
        return True
    for m in models.values():
        if isinstance(m, dict):
            mods = m.get("modalities")
            inp = mods.get("input") if isinstance(mods, dict) else None
            if isinstance(inp, list) and "image" not in inp:
                return False
    return True


def _zcode_provider_stats(path: str, proxy_url: str) -> dict:
    """zcode 条目统计（wiring_report/observe 用）：wired 要求 URL 指本代理，gated 要求门全开。"""
    empty = {"total": 0, "eligible": 0, "wired": 0, "gated": 0, "skipped_nokey": 0, "skipped_kind": 0}
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    items, nokey, badkind = _zcode_entries(d)
    wired = gated = total = 0
    provs = d.get("provider")
    if isinstance(provs, dict):
        total = sum(
            1
            for e in provs.values()
            if isinstance(e, dict) and isinstance(e.get("options"), dict) and e["options"].get("baseURL")
        )
    for _pid, _kind, e in items:
        url = e["options"]["baseURL"]
        if url == proxy_url or url.startswith(proxy_url + "/"):
            wired += 1
        if _zcode_provider_gated(e):
            gated += 1
    return {"total": total, "eligible": len(items), "wired": wired, "gated": gated, "skipped_nokey": nokey, "skipped_kind": badkind}
```

④ `snapshot.py` 的 `_KEY_FIELDS` 加条目：

```python
    "zcode": ((".zcode", "v2", "config.json"), ("provider[].options.apiKey",)),
```

`key_ref_for` 在 `if harness == "codex":` 分支后加：

```python
    if harness == "zcode":  # key 位置是条目级通配描述，非可 dig 的点路径
        return "provider[].options.apiKey"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_proxy_zcode.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归 + commit**

```bash
python -m pytest -q && ruff format --check . && ruff check .
git add vision_relay/wiring.py vision_relay/snapshot.py tests/test_proxy_zcode.py
git commit -m "feat(wiring): zcode 注册与条目层——HARNESS_CFG zcode-v2/read_base_url 激活供应商特判/准入收集(空 key·未知 kind 跳过)/统计口径/snapshot 键位"
```

---

### Task 4: 模态门共用原语 + `_rewrite_zcode_providers` + 改写标记

**Files:**
- Modify: `vision_relay/wiring.py`
- Test: `tests/test_proxy_zcode.py`

- [ ] **Step 1: 写失败测试**

`tests/test_proxy_zcode.py` 追加：

```python
def _text_model():
    return {"limit": {"context": 1000000}, "modalities": {"input": ["text"], "output": ["text"]}, "zcode": {"modified": False}}


def _vision_model():
    return {"modalities": {"input": ["text", "image"], "output": ["text"]}}


class TestZcodeRewrite:
    def test_rewrite_takes_over_and_gates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        p = _write_zcode_config(
            tmp_path,
            {
                "k": _provider(
                    models={"GLM-5.2": _text_model(), "kimi": _vision_model()},
                ),
                "nokey": _provider(key="", models={"m": _text_model()}),
            },
        )
        proxy = "http://127.0.0.1:8787"
        urls, mods, stats = wiring._rewrite_zcode_providers(p, proxy)
        d = json.load(open(p, encoding="utf-8"))
        assert d["provider"]["k"]["options"]["baseURL"] == proxy
        assert d["provider"]["nokey"]["options"]["baseURL"] == "https://open.bigmodel.cn/api/anthropic"  # 空 key 不动
        glm = d["provider"]["k"]["models"]["GLM-5.2"]
        assert "image" in glm["modalities"]["input"]  # 纯文本模型开门
        assert glm["zcode"]["modalitiesConfigured"] is True  # 置位簿记标志
        kimi = d["provider"]["k"]["models"]["kimi"]
        assert "video" not in kimi["modalities"]["input"]  # 已开门的模型不动
        assert urls == {"k::anthropic": "https://open.bigmodel.cn/api/anthropic"}
        assert mods["k::anthropic::GLM-5.2"] == {"input": ["text"], "flag": False}
        assert "k::anthropic::kimi" not in mods  # 幂等：已开门不产生记录
        assert stats["skipped_nokey"] == 1 and stats["gated"] == 1

    def test_rewrite_idempotent_no_new_records(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        p = _write_zcode_config(tmp_path, {"k": _provider(models={"m": _text_model()})})
        proxy = "http://127.0.0.1:8787"
        wiring._rewrite_zcode_providers(p, proxy)
        urls2, mods2, _ = wiring._rewrite_zcode_providers(p, proxy)
        assert urls2 == {} and mods2 == {}  # 二次接管不产生新记录

    def test_rewrite_marks_timestamp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        p = _write_zcode_config(tmp_path, {"k": _provider()})
        wiring._rewrite_zcode_providers(p, "http://127.0.0.1:8787")
        assert wiring.zcode_rewrite_ts() > 0.0

    def test_missing_modality_input_skips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        p = _write_zcode_config(tmp_path, {"k": _provider(models={"m": {"limit": {}}})})
        _urls, _mods, stats = wiring._rewrite_zcode_providers(p, "http://127.0.0.1:8787")
        assert stats["skipped_mod"] == 1  # 形态不认识不硬造（spec §5.1）
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_proxy_zcode.py::TestZcodeRewrite -q`
Expected: FAIL（`AttributeError: _rewrite_zcode_providers`）

- [ ] **Step 3: 实现**

`wiring.py` 追加（放在 `_open_modalities` 之后）：

```python
def _mod_input(model: dict) -> list | None:
    """zcode 模型 → modalities.input 列表；形态不认识返回 None（不硬造，spec §5.1）。"""
    mods = model.get("modalities")
    inp = mods.get("input") if isinstance(mods, dict) else None
    return inp if isinstance(inp, list) else None


def _ensure_image(mods_list: list) -> bool:
    """输入模态列表没有 "image" 则追加（模态门共用原语：zcode 与 codex 目录补丁共用）。"""
    if "image" in mods_list:
        return False
    mods_list.append("image")
    return True


def _zcode_marker_path() -> str:
    from .env_util import config_dir

    return os.path.join(config_dir(), "zcode.rewrite.json")


def _mark_zcode_rewrite() -> None:
    """记录本代理最后一次改写 zcode config.json 的时间（§7.2 待重启判定：进程启动须晚于它）。"""
    try:
        os.makedirs(os.path.dirname(_zcode_marker_path()), exist_ok=True)
        tmp = _zcode_marker_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time()}, f)
        os.replace(tmp, _zcode_marker_path())
    except OSError:
        pass


def zcode_rewrite_ts() -> float:
    """本代理最后一次改写 zcode config.json 的时刻（无记录=0）。"""
    try:
        with open(_zcode_marker_path(), encoding="utf-8") as f:
            return float(json.load(f).get("ts") or 0.0)
    except (OSError, ValueError):
        return 0.0


def _rewrite_zcode_providers(path: str, proxy_url: str) -> tuple[dict[str, str], dict[str, dict], dict]:
    """接管改写（spec §5.1）：可接管条目 baseURL→本代理 + 纯文本模型补 image 门与
    modalitiesConfigured。返回 (url 原值映射, 模态门原值映射, 统计)；键 pid::kind /
    pid::kind::model。已就位者不产生记录（幂等）；空 key 供应商完全不碰。"""
    empty_stats = {"rewritten": 0, "gated": 0, "skipped_nokey": 0, "skipped_kind": 0, "skipped_mod": 0}
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}, empty_stats
    items, nokey, badkind = _zcode_entries(d)
    url_originals: dict[str, str] = {}
    mod_originals: dict[str, dict] = {}
    stats = {"rewritten": 0, "gated": 0, "skipped_nokey": nokey, "skipped_kind": badkind, "skipped_mod": 0}
    for pid, kind, e in items:
        key = _zcode_key(pid, kind)
        opts = e["options"]
        if not (opts["baseURL"] == proxy_url or opts["baseURL"].startswith(proxy_url + "/")):
            url_originals[key] = opts["baseURL"]
            opts["baseURL"] = proxy_url
            stats["rewritten"] += 1
        models = e.get("models")
        if not isinstance(models, dict):
            continue
        for mid, m in models.items():
            if not isinstance(m, dict):
                continue
            inp = _mod_input(m)
            if inp is None:
                stats["skipped_mod"] += 1
                continue
            if "image" in inp:
                continue  # 已开门（用户自配）：不动、不记录（幂等）
            zc = m.get("zcode")
            zc = zc if isinstance(zc, dict) else None
            flag_orig = zc.get("modalitiesConfigured", _MOD_ABSENT) if zc else _MOD_ABSENT
            mod_originals[f"{key}::{mid}"] = {"input": list(inp), "flag": flag_orig}
            _ensure_image(inp)
            if zc is None:
                zc = m.setdefault("zcode", {})
            zc["modalitiesConfigured"] = True
            stats["gated"] += 1
    if not (url_originals or mod_originals):
        return {}, {}, stats
    if not _json_save_atomic(path, d):
        return {}, {}, {**stats, "rewritten": 0, "gated": 0}
    _mark_zcode_rewrite()
    return url_originals, mod_originals, stats
```

同时把 `_patch_codex_catalog_modalities` 内层循环改用共用原语（行为等价，收敛一处）：

```python
    for m in models:
        if not isinstance(m, dict):
            continue
        mods = m.get("input_modalities")
        if isinstance(mods, list):
            if _ensure_image(mods):
                patched += 1
        else:
            m["input_modalities"] = ["text", "image"]
            patched += 1
```

- [ ] **Step 4: 跑测试确认通过（含 codex 既有补丁回归）**

Run: `python -m pytest tests/test_proxy_zcode.py tests/test_proxy_wiring.py -q`
Expected: PASS（codex 目录补丁既有用例不回归）

- [ ] **Step 5: Commit**

```bash
python -m pytest -q && ruff format --check . && ruff check .
git add vision_relay/wiring.py tests/test_proxy_zcode.py
git commit -m "feat(wiring): zcode 接管改写——URL 指代理+纯文本模型补 image 门并置位 modalitiesConfigured(原值哨兵记录,空 key 不碰,幂等);模态门列表增删提为共用原语(codex 目录补丁同用);改写时间戳标记"
```

---

### Task 5: `_restore_zcode_providers` + 单 harness 还原抽取 + 停止/修复路径

**Files:**
- Modify: `vision_relay/wiring.py`
- Test: `tests/test_proxy_zcode.py`、`tests/test_proxy_wiring.py`

- [ ] **Step 1: 写失败测试**

`tests/test_proxy_zcode.py` 追加：

```python
class TestZcodeRestore:
    def _taken_over(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        p = _write_zcode_config(tmp_path, {"k": _provider(models={"GLM-5.2": _text_model()})})
        proxy = "http://127.0.0.1:8787"
        urls, mods, _ = wiring._rewrite_zcode_providers(p, proxy)
        return p, proxy, urls, mods

    def test_restore_reverts_url_and_gate(self, tmp_path, monkeypatch):
        p, proxy, urls, mods = self._taken_over(tmp_path, monkeypatch)
        n = wiring._restore_zcode_providers(p, proxy, urls, mods)
        d = json.load(open(p, encoding="utf-8"))
        glm = d["provider"]["k"]["models"]["GLM-5.2"]
        assert d["provider"]["k"]["options"]["baseURL"] == "https://open.bigmodel.cn/api/anthropic"
        assert glm["modalities"]["input"] == ["text"]
        assert "modalitiesConfigured" not in glm["zcode"]  # flag 原值 False → 回写后按 False；本例 zcode 子对象存在
        assert n == 1

    def test_restore_skips_kind_changed_entry(self, tmp_path, monkeypatch):
        p, proxy, urls, mods = self._taken_over(tmp_path, monkeypatch)
        d = json.load(open(p, encoding="utf-8"))
        d["provider"]["k"]["kind"] = "openai"  # zcode 更新换了协议族 → 身份键不命中
        json.dump(d, open(p, "w", encoding="utf-8"))
        n = wiring._restore_zcode_providers(p, proxy, urls, mods)
        d2 = json.load(open(p, encoding="utf-8"))
        assert d2["provider"]["k"]["options"]["baseURL"] == proxy  # 原样保留，交给对账吸收
        assert n == 0

    def test_restore_skips_user_repointed_entry(self, tmp_path, monkeypatch):
        p, proxy, urls, mods = self._taken_over(tmp_path, monkeypatch)
        d = json.load(open(p, encoding="utf-8"))
        d["provider"]["k"]["options"]["baseURL"] = "https://user-changed.example"
        json.dump(d, open(p, "w", encoding="utf-8"))
        wiring._restore_zcode_providers(p, proxy, urls, mods)
        d2 = json.load(open(p, encoding="utf-8"))
        assert d2["provider"]["k"]["options"]["baseURL"] == "https://user-changed.example"

    def test_flag_true_restored(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        m = _text_model()
        m["zcode"]["modalitiesConfigured"] = True  # 原本就配置过
        p = _write_zcode_config(tmp_path, {"k": _provider(models={"m": m})})
        proxy = "http://127.0.0.1:8787"
        _urls, mods, _ = wiring._rewrite_zcode_providers(p, proxy)
        wiring._restore_zcode_providers(p, proxy, _urls, mods)
        d = json.load(open(p, encoding="utf-8"))
        assert d["provider"]["k"]["models"]["m"]["zcode"]["modalitiesConfigured"] is True

    def test_wiring_restore_harness_single(self, tmp_path, monkeypatch):
        """取消勾选单 harness 还原：与 stop 同一路径，只动指定 harness。"""
        p, proxy, urls, mods = self._taken_over(tmp_path, monkeypatch)
        from vision_relay import snapshot

        snapshot.save(
            "zcode",
            snapshot.Snapshot(base_url="https://open.bigmodel.cn/api/anthropic", key_ref="provider[].options.apiKey", model="", provider_urls=urls, provider_modalities=mods),
        )
        cfg = ProxyConfig()
        msgs = wiring.wiring_restore_harness(cfg, "zcode")
        d = json.load(open(p, encoding="utf-8"))
        assert d["provider"]["k"]["options"]["baseURL"] != proxy
        assert any("providers restored" in m for m in msgs)

    def test_stop_restores_zcode(self, tmp_path, monkeypatch):
        p, proxy, urls, mods = self._taken_over(tmp_path, monkeypatch)
        from vision_relay import snapshot

        snapshot.save(
            "zcode",
            snapshot.Snapshot(base_url="https://open.bigmodel.cn/api/anthropic", key_ref="provider[].options.apiKey", model="", provider_urls=urls, provider_modalities=mods),
        )
        cfg = ProxyConfig()
        cfg.routing.harnesses = ["zcode"]
        msgs = wiring.wiring_restore_on_stop(cfg)
        d = json.load(open(p, encoding="utf-8"))
        assert d["provider"]["k"]["options"]["baseURL"] == "https://open.bigmodel.cn/api/anthropic"
        assert any("providers restored" in m for m in msgs)
```

`tests/test_proxy_wiring.py` 追加（验证重构等价——既有三个 harness 的 stop 语义不变）：

```python
class TestRestoreOnStopAfterExtraction:
    def test_generic_harness_still_guarded(self, tmp_path, monkeypatch):
        """非本代理指向时不还原（抽取后守卫仍在）。"""
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_harness(tmp_path, "claude", "https://elsewhere.example")
        msgs = wiring.wiring_restore_on_stop(ProxyConfig())
        assert any("非本代理" in m for m in msgs)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_proxy_zcode.py::TestZcodeRestore -q`
Expected: FAIL（`AttributeError: _restore_zcode_providers`）

- [ ] **Step 3: 实现**

① `_rewrite_zcode_providers` 之后追加还原函数：

```python
def _restore_zcode_providers(
    path: str, proxy_url: str, provider_urls: dict[str, str], provider_modalities: dict[str, object] | None
) -> int:
    """按快照还原（spec §5.2）。守卫：当前 baseURL 仍指本代理才动（用户改走别处不动）；
    身份键 pid::kind 现场重算，kind 变更不命中→跳过（对账吸收新值）。返回还原条数。"""
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    provs = d.get("provider")
    if not isinstance(provs, dict):
        return 0
    provider_modalities = provider_modalities or {}
    restored = 0
    for pid, e in provs.items():
        if not isinstance(e, dict) or not isinstance(e.get("options"), dict):
            continue
        opts = e["options"]
        cur = opts.get("baseURL")
        if not (isinstance(cur, str) and (cur == proxy_url or cur.startswith(proxy_url + "/"))):
            continue
        key = _zcode_key(str(pid), str(e.get("kind") or ""))
        touched = False
        if key in provider_urls:
            opts["baseURL"] = provider_urls[key]
            touched = True
        models = e.get("models")
        if isinstance(models, dict):
            for mid, m in models.items():
                rec = provider_modalities.get(f"{key}::{mid}")
                if not (isinstance(m, dict) and isinstance(rec, dict)):
                    continue
                inp = _mod_input(m)
                if inp is not None and isinstance(rec.get("input"), list):
                    m["modalities"]["input"] = list(rec["input"])  # 整列表写回原值（原值必不含 image——只记录过缺 image 的模型）
                zc = m.get("zcode")
                if isinstance(zc, dict) and "flag" in rec:
                    if rec["flag"] == _MOD_ABSENT:
                        zc.pop("modalitiesConfigured", None)
                    else:
                        zc["modalitiesConfigured"] = rec["flag"]
                touched = True
        if touched:
            restored += 1
    if restored and _json_save_atomic(path, d):
        _mark_zcode_rewrite()
    return restored
```

② 把 `wiring_restore_on_stop` 的循环体抽成 `_restore_harness_on_stop(cfg, snaps, name) -> list[str]`（既有 claude/codex/qwen-code 逻辑逐字搬入，签名从循环变量改为参数），然后：

```python
def _restore_harness_on_stop(cfg, snaps: dict, name: str) -> list[str]:
    """stop 的单 harness 还原步骤（wiring_restore_on_stop 与「取消勾选即还原」共用，spec §8）。"""
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    h = HARNESS_CFG[name]
    p = _path(HOME, name)
    if not os.path.exists(p):
        return []
    if name == "zcode":
        snap = snaps.get(name)
        if snap is not None and (snap.provider_urls or snap.provider_modalities):
            n = _restore_zcode_providers(p, proxy_url, snap.provider_urls or {}, snap.provider_modalities)
            bak = _find_bak(p)
            if bak is not None:
                try:
                    os.unlink(bak)
                except OSError:
                    pass
            return [f"{name}: providers restored ({n} entries)"]
        stats = _zcode_provider_stats(p, proxy_url)
        if stats["wired"] == 0:
            return []  # 无本代理痕迹：不动
        bak = _find_bak(p)
        if bak is None:
            return [f"{name}: 无快照且无备份，跳过"]
        try:
            import shutil

            shutil.copyfile(bak, p)
            os.unlink(bak)
            _mark_zcode_rewrite()
            return [f"{name}: bak restored"]
        except OSError as exc:
            return [f"{name}: restore FAIL {exc}"]
    # ---- 以下为既有 claude/codex/qwen-code 逻辑（逐字保留）----
    cur = read_base_url(p, h)
    if cur is None or (cur != proxy_url and not cur.startswith(proxy_url + "/")):
        return [f"{name}: 当前 base_url={cur!r} 非本代理，跳过还原"]
    if name == "codex":  # 目录还原须在 config 整文件换回前（当前 config 仍引用被补丁目录）
        cat_msg = _restore_codex_catalog(p)
        if cat_msg:
            return [f"{name}: {cat_msg}"] + _generic_snapshot_or_bak_restore(cfg, snaps, name, p, h, cur, proxy_url)
    return _generic_snapshot_or_bak_restore(cfg, snaps, name, p, h, cur, proxy_url)


def _generic_snapshot_or_bak_restore(cfg, snaps, name, p, h, cur, proxy_url):
    """既有通用还原尾段（快照优先 → .bak 兜底），从原 wiring_restore_on_stop 循环体原样抽出。"""
    msgs: list[str] = []
    snap = snaps.get(name)
    if snap is not None:
        ok = write_base_url(p, h, snap.base_url)
        if ok and name == "qwen-code" and snap.provider_urls:
            n = _restore_qwen_providers(p, proxy_url, snap.provider_urls, snap.provider_modalities)
            msgs.append(f"{name}: providers restored ({n} entries)")
        if ok:
            bak = _find_bak(p)
            if bak is not None:
                try:
                    os.unlink(bak)
                except OSError:
                    pass
        msgs.append(f"{name}: snapshot restored to {snap.base_url} ({'ok' if ok else 'FAIL'})")
        return msgs
    bak = _find_bak(p)
    if bak is None:
        return [f"{name}: 无快照且无备份，跳过"]
    try:
        import shutil

        shutil.copyfile(bak, p)
        os.unlink(bak)
        return [f"{name}: bak restored"]
    except OSError as exc:
        return [f"{name}: restore FAIL {exc}"]


def wiring_restore_harness(cfg, name: str) -> list[str]:
    """单 harness 还原（「路由范围取消勾选」用；与 stop 同一还原步骤，spec §8）。"""
    if name not in HARNESS_CFG:
        return [f"{name}: unknown harness"]
    return _restore_harness_on_stop(cfg, snapshot.load(), name)


def wiring_restore_on_stop(cfg) -> list[str]:
    """stop 的统一还原（既有语义不变）：按最新接管组合快照；快照缺失退回整文件 .bak 兜底。"""
    snaps = snapshot.load()
    msgs: list[str] = []
    for name in cfg.routing.harnesses:
        msgs.extend(_restore_harness_on_stop(cfg, snaps, name))
    return msgs
```

③ `wiring_restore_by_snapshot` 的循环内、全局守卫 `cur = read_base_url(...)` / `if cur is None or ...` **之前**插入 zcode 分支（条目级守卫在还原函数内逐条做，激活供应商非代理不能挡住其他条目还原）：

```python
        if name == "zcode":
            if snap.provider_urls or snap.provider_modalities:
                n = _restore_zcode_providers(p, proxy_url, snap.provider_urls or {}, snap.provider_modalities)
                restored.append(f"{name}: providers restored ({n} entries)")
            bak = _find_bak(p)
            if bak is not None:  # 整文件备份已过期（快照才是真相），删除防误还原
                try:
                    os.unlink(bak)
                except OSError:
                    pass
            continue
```

- [ ] **Step 4: 跑测试确认通过（重点盯既有 stop/reconcile 用例不回归）**

Run: `python -m pytest tests/test_proxy_zcode.py tests/test_proxy_wiring.py tests/test_proxy_reconcile.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
python -m pytest -q && ruff format --check . && ruff check .
git add vision_relay/wiring.py tests/test_proxy_zcode.py tests/test_proxy_wiring.py
git commit -m "feat(wiring): zcode 守卫还原(身份键现场重算,kind 变更/用户改走不动)+单 harness 还原步骤抽取(wiring_restore_harness,取消勾选与 stop 共用);快照路径 zcode 条目级分支"
```

---

### Task 6: `ensure_zcode_relays` + 接管主路径 + 对账分支

**Files:**
- Modify: `vision_relay/wiring.py`、`vision_relay/reconcile.py`
- Test: `tests/test_proxy_zcode.py`

- [ ] **Step 1: 写失败测试**

`tests/test_proxy_zcode.py` 追加：

```python
class TestZcodeRelays:
    def test_one_relay_per_provider_with_fingerprint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_zcode_config(
            tmp_path,
            {
                "builtin:bigmodel": _provider(enabled=True, models={"GLM-5-Turbo": {"name": "glm-5-turbo", "modalities": {"input": ["text"]}}}),
                "ark": _provider(url="https://ark.cn-beijing.volces.com/api/coding/v3", key="ark-xyz1234567890", kind="openai", enabled=False, models={"DeepSeek-V4-Flash": {"name": "deepseek-v4-flash"}}),
            },
        )
        cfg = ProxyConfig()
        added = wiring.ensure_zcode_relays(cfg)
        zs = [r for r in cfg.relays if r.provider_id]
        assert len(zs) == 2
        assert zs[0].provider_id == "builtin:bigmodel"  # 激活供应商排最前
        ark = next(r for r in zs if r.provider_id == "ark")
        assert ark.protocol == "chat"  # openai kind → chat
        assert zs[0].protocol == "anthropic"
        assert set(zs[0].models) == {"GLM-5-Turbo", "glm-5-turbo"}  # 双名收录
        assert ark.base_url == "https://ark.cn-beijing.volces.com/api/coding/v3"
        assert len(zs[0].auth_hints) == 1 and zs[0].auth_hints[0].startswith("k-12")  # 指纹形态
        assert zs[0].api_key == ""  # 鉴权透传
        assert set(added) == {r.name for r in zs}

    def test_loopback_original_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_zcode_config(tmp_path, {"k": _provider(url="http://127.0.0.1:15721")})  # cc-switch 端口
        cfg = ProxyConfig()
        assert wiring.ensure_zcode_relays(cfg) == []
        assert not [r for r in cfg.relays if r.provider_id]

    def test_reorder_enabled_first_and_cleanup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_zcode_config(
            tmp_path,
            {
                "a": _provider(url="https://a.example", enabled=False),
                "b": _provider(url="https://b.example", enabled=True),
            },
        )
        cfg = ProxyConfig()
        wiring.ensure_zcode_relays(cfg)
        _write_zcode_config(  # 用户切换激活供应商 a
            tmp_path,
            {
                "a": _provider(url="https://a.example", enabled=True),
                "b": _provider(url="https://b.example", enabled=False),
            },
        )
        wiring.ensure_zcode_relays(cfg)
        zs = [r for r in cfg.relays if r.provider_id]
        assert [r.provider_id for r in zs] == ["a", "b"]  # 激活优先重排序
        _write_zcode_config(tmp_path, {"a": _provider(url="https://a.example", enabled=True)})  # b 消失
        wiring.ensure_zcode_relays(cfg)
        assert [r.provider_id for r in cfg.relays if r.provider_id] == ["a"]  # 清理

    def test_backup_and_rewrite_wires_zcode_and_snapshots(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        p = _write_zcode_config(tmp_path, {"k": _provider(models={"m": _text_model()})})
        cfg = ProxyConfig()
        cfg.routing.harnesses = ["zcode"]
        msgs = wiring.wiring_backup_and_rewrite(cfg)
        d = json.load(open(p, encoding="utf-8"))
        assert d["provider"]["k"]["options"]["baseURL"] == "http://127.0.0.1:8787"
        from vision_relay import snapshot

        snap = snapshot.load()["zcode"]
        assert snap.provider_urls == {"k::anthropic": "https://open.bigmodel.cn/api/anthropic"}
        assert snap.second_hop is None
        assert any("zcode: providers" in m for m in msgs)
        assert any(r.provider_id == "k" for r in cfg.relays)  # 接管顺带建 relay

    def test_reconcile_zcode_providers_absorbs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        p = _write_zcode_config(tmp_path, {"k": _provider()})
        cfg = ProxyConfig()
        cfg.routing.harnesses = ["zcode"]
        wiring.wiring_backup_and_rewrite(cfg)
        # 模拟 zcode 运行期回写：改走新上游
        d = json.load(open(p, encoding="utf-8"))
        d["provider"]["k"]["options"]["baseURL"] = "https://new.example/api"
        json.dump(d, open(p, "w", encoding="utf-8"))
        res = wiring.reconcile_zcode_providers(cfg)
        assert res and res["rewritten"] == 1
        from vision_relay import snapshot

        merged = snapshot.load()["zcode"].provider_urls
        assert merged["k::anthropic"] == "https://new.example/api"  # 新原值吸收
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_proxy_zcode.py::TestZcodeRelays -q`
Expected: FAIL（`AttributeError: ensure_zcode_relays`）

- [ ] **Step 3: 实现**

① `wiring.py` 顶部加 `from . import fingerprint`（与 `from . import snapshot` 并列）；追加 relay 维护（放在 `ensure_qwen_relays` 之后）：

```python
def _zcode_slug(pid: str) -> str:
    return re.sub(r"[^a-z0-9.-]+", "-", pid.lower()).strip("-") or "provider"


def _zcode_relay_desired(d: dict, provider_urls: dict[str, str] | None, proxy_url: str, bind_port: int) -> list[RelayConfig]:
    """期望 zcode relay 列表（有序：激活供应商最前）。一供应商一条；原值=现场非代理地址
    优先、现场指代理时取快照原值；ours/工具端口不建（防回环）。名称待 ensure 侧消歧。"""
    items, _nokey, _bad = _zcode_entries(d)
    provider_urls = provider_urls or {}
    ordered = [t for t in items if t[2].get("enabled") is True] + [t for t in items if t[2].get("enabled") is not True]
    out: list[RelayConfig] = []
    for pid, kind, e in ordered:
        key = _zcode_key(pid, kind)
        live = e["options"]["baseURL"]
        orig = live
        if live == proxy_url or live.startswith(proxy_url + "/"):
            orig = provider_urls.get(key) or live
        owner = classify_base_url(orig, bind_port)
        if owner == "ours" or owner in TOOL_DOSSIERS:
            continue
        names: list[str] = []
        models = e.get("models")
        if isinstance(models, dict):
            for mid, m in models.items():
                if isinstance(m, dict):
                    names.append(mid)
                    api = m.get("name")
                    if isinstance(api, str) and api and api != mid:
                        names.append(api)  # 双名收录（spec §6.3）
        out.append(
            RelayConfig(
                name=_ZCODE_RELAY_PREFIX + _zcode_slug(pid),  # 暂定名，ensure 侧查重
                protocol=_ZCODE_PROTO[kind],
                base_url=orig,
                models=names,
                provider_id=pid,
                auth_hints=[fingerprint.key_fingerprint(e["options"]["apiKey"])],
            )
        )
    return out


def ensure_zcode_relays(cfg) -> list[str]:
    """按现场 config.json + 快照维护 zcode 一层直连 relay（spec §6）：一供应商一条、激活优先、
    指纹随行。现状（成员/字段/顺序）与期望不一致 → 整块重建；返回新增 name 列表。"""
    p = _path(HOME, "zcode")
    if not os.path.exists(p):
        return []
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    snap = snapshot.load().get("zcode")
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    desired = _zcode_relay_desired(d, getattr(snap, "provider_urls", None), proxy_url, cfg.bind_port)
    current = [r for r in cfg.relays if getattr(r, "provider_id", None) and r.name.startswith(_ZCODE_RELAY_PREFIX)]
    same = [r.provider_id for r in current] == [r.provider_id for r in desired] and all(
        c.protocol == w.protocol and c.base_url == w.base_url and list(c.models) == list(w.models)
        for c, w in zip(current, desired)
    )
    if same:
        return []
    names_before = {r.name for r in current}
    taken = {r.name for r in cfg.relays if not (getattr(r, "provider_id", None) and r.name.startswith(_ZCODE_RELAY_PREFIX))}
    cfg.relays = [r for r in cfg.relays if not (getattr(r, "provider_id", None) and r.name.startswith(_ZCODE_RELAY_PREFIX))]
    added: list[str] = []
    for i, r in enumerate(desired):
        name = r.name
        n = 2
        while name in taken:  # slug 撞名消歧（与 qwen _qwen_relay_name 同风格）
            name = f"{r.name}-{n}"
            n += 1
        taken.add(name)
        r.name = name
        cfg.relays.insert(i, r)  # 整块插头部：先于通配 "*" 既有 relay（prepend 语义同 qwen）
        if name not in cfg.routing.activated_relays:
            cfg.routing.activated_relays.append(name)
        if name not in names_before:
            added.append(name)
    save_config(cfg)
    return added


def reconcile_zcode_providers(cfg) -> dict | None:
    """接管态校正 zcode 条目漂移（spec §7.1）：非本代理条目重指、关门重开、新原值吸收进
    快照合并映射。返回 None 或摘要 {rewritten, gated}。"""
    p = _path(HOME, "zcode")
    if not os.path.exists(p):
        return None
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    new_urls, new_mods, _stats = _rewrite_zcode_providers(p, proxy_url)
    if not new_urls and not new_mods:
        return None
    old = snapshot.load().get("zcode")
    merged = dict(old.provider_urls or {}) if old and old.provider_urls else {}
    merged.update(new_urls)
    merged_mod = dict(old.provider_modalities or {}) if old and old.provider_modalities else {}
    merged_mod.update(new_mods)
    try:
        snapshot.save(
            "zcode",
            snapshot.Snapshot(
                base_url=old.base_url if old else proxy_url,
                key_ref=snapshot.key_ref_for("zcode"),
                model=old.model if old else "",
                second_hop=None,
                provider_urls=merged or None,
                provider_modalities=merged_mod or None,
            ),
        )
    except Exception:  # 快照尽力而为，不打断重接管
        pass
    return {"rewritten": len(new_urls), "gated": len(new_mods)}
```

② `relays_restore` 的移除条件加 zcode 前缀（改第一个 if）：

```python
        if (
            (r.name.startswith(_QWEN_RELAY_PREFIX) or r.name.startswith(_ZCODE_RELAY_PREFIX))
            and r.name in cfg.routing.activated_relays
        ):
```

③ `wiring_backup_and_rewrite`：qwen 特判旁加 zcode（改写后存快照、不写全局 base_url、消息、建 relay）。在 `qwen_new, qwen_mod, ... = (None, None, 0, 0, 0)` 行改为同时初始化 zcode 变量，并在 `if name == "qwen-code":` 后加：

```python
        zcode_new: dict = {}
        zcode_mod: dict = {}
        zcode_stats: dict = {}
        if name == "qwen-code":
            qwen_new, qwen_mod, qwen_skipped, qwen_rewritten, qwen_gated = _rewrite_qwen_providers(p, proxy_url)
        elif name == "zcode":
            zcode_new, zcode_mod, zcode_stats = _rewrite_zcode_providers(p, proxy_url)
```

快照合并段把 `merged.update(qwen_new or {})` 改为 `merged.update(qwen_new or zcode_new or {})`、`merged_mod.update(qwen_mod or zcode_mod or {})`；`base_changed or qwen_new or qwen_mod` 条件改为 `base_changed or qwen_new or qwen_mod or zcode_new or zcode_mod`。

结尾写 base_url 与消息段改为：

```python
        if name == "zcode":
            changed.append(
                f"zcode: providers {zcode_stats.get('rewritten', 0)} -> proxy"
                + (f", {zcode_stats.get('gated', 0)} modalities gated" if zcode_stats.get("gated") else "")
                + f", {zcode_stats.get('skipped_nokey', 0)} nokey skipped, {zcode_stats.get('skipped_kind', 0)} unknown-kind skipped"
            )
        else:
            ok = write_base_url(p, h, proxy_url)
            msg = f"{name}: base_url -> {proxy_url} ({'ok' if ok else 'FAIL'})"
            if name == "qwen-code":
                msg += (
                    f"; providers {qwen_rewritten} entries -> proxy"
                    + (f", {qwen_gated} modalities gate opened" if qwen_gated else "")
                    + f", {qwen_skipped} skipped"
                )
            changed.append(msg)
```

codex 目录补丁段保持 `if name == "codex":` 不变；函数末尾 qwen relay 段后加：

```python
    if any(h == "zcode" for h in cfg.routing.harnesses):
        for n in ensure_zcode_relays(cfg):
            changed.append(f"zcode: relay {n} added (一层直连, 鉴权透传, 指纹选路)")
```

④ `wiring_report` 循环内加：

```python
        if name == "zcode" and os.path.exists(p):
            stats = _zcode_provider_stats(p, proxy_url)
            row["providers"] = stats
            # zcode 纯条目级：wired 只看 eligible 全覆盖+门全开（激活供应商可能是空 key 未接管者，
            # 其直连地址不代表接管失败）
            row["wired"] = stats["eligible"] > 0 and stats["eligible"] == stats["wired"] == stats["gated"]
```

⑤ `reconcile.py` 的 `reconcile()`：harness 循环里 `if obs["service_alive"] and name in expected_wired:` 的第一个子分支前插入 zcode（跳过 ours/other/absorb 全部分支）：

```python
            if obs["service_alive"] and name in expected_wired:
                if name == "zcode":
                    # zcode 纯条目级：无全局 base_url 归属分支，任何漂移由重写+吸收收敛（spec §7.1）
                    res = wiring.reconcile_zcode_providers(cfg)
                    if res:
                        actions.append(
                            {"type": "provider_absorb", "harness": name, "rewritten": res["rewritten"], "gated": res["gated"]}
                        )
                elif owner == "ours":
                    ...（既有分支，elif 化）
```

（把原 `if owner == "ours":` 改为 `elif owner == "ours":`，其余分支缩进不动。）循环后 qwen relay 维护段旁加：

```python
        if obs["service_alive"] and "zcode" in expected_wired:
            for n in wiring.ensure_zcode_relays(cfg):
                actions.append({"type": "relay_added", "name": n})
                append_event("relay_added", None, {"name": n})
```

- [ ] **Step 4: 跑测试确认通过（含 reconcile 既有回归）**

Run: `python -m pytest tests/test_proxy_zcode.py tests/test_proxy_wiring.py tests/test_proxy_reconcile.py tests/test_e2e_g2_routing.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
python -m pytest -q && ruff format --check . && ruff check .
git add vision_relay/wiring.py vision_relay/reconcile.py tests/test_proxy_zcode.py
git commit -m "feat(wiring): zcode relay 维护(一供应商一条/双名/指纹随行/激活优先重排/整块重建)+接管主路径与快照落盘+reconcile 单分支(重写+吸收,无全局归属分支)"
```

---

### Task 7: server.py — 指纹选路层 + harness 归属 + VLM 客户端

**Files:**
- Modify: `vision_relay/server.py`
- Test: `tests/test_proxy_server.py`

- [ ] **Step 1: 写失败测试**

`tests/test_proxy_server.py` 追加：

```python
class TestSelectRelayAuthHints:
    def test_fingerprint_hit_wins_over_order(self):
        from vision_relay.config import ProxyConfig, RelayConfig
        from vision_relay.server import _select_relay

        cfg = ProxyConfig()
        cfg.relays = [
            RelayConfig(name="zcode-a", protocol="anthropic", base_url="https://a.example", models=["GLM-5.3"], provider_id="a", auth_hints=["aaaa…zzzz@20"]),
            RelayConfig(name="zcode-b", protocol="anthropic", base_url="https://b.example", models=["GLM-5.3"], provider_id="b", auth_hints=["bbbb…yyyy@20"]),
        ]
        r = _select_relay(cfg, "anthropic", "GLM-5.3", "bbbb…yyyy@20")
        assert r.name == "zcode-b"  # 顺序命中会选 a，指纹命中必须赢

    def test_no_fingerprint_falls_back_to_order(self):
        from vision_relay.config import ProxyConfig, RelayConfig
        from vision_relay.server import _select_relay

        cfg = ProxyConfig()
        cfg.relays = [RelayConfig(name="zcode-a", protocol="anthropic", base_url="https://a.example", models=["*"])]
        assert _select_relay(cfg, "anthropic", "m", None).name == "zcode-a"
        assert _select_relay(cfg, "anthropic", "m", "unknown…fp@10").name == "zcode-a"

    def test_fingerprint_requires_model_match(self):
        from vision_relay.config import ProxyConfig, RelayConfig
        from vision_relay.server import _select_relay

        cfg = ProxyConfig()
        cfg.relays = [RelayConfig(name="zcode-a", protocol="anthropic", base_url="https://a.example", models=["other-*"], auth_hints=["fp@10"])]
        assert _select_relay(cfg, "anthropic", "GLM-5.3", "fp@10").name == "default"  # 模型不匹配不得命中


class TestHarnessAttribution:
    def test_zcode_relay_attributes_zcode(self):
        from vision_relay.config import ProxyConfig, RelayConfig
        from vision_relay.server import _HARNESS_BY_PROTO

        relay = RelayConfig(name="zcode-k", protocol="anthropic", base_url="https://x", provider_id="k")
        harness = "zcode" if getattr(relay, "provider_id", None) else _HARNESS_BY_PROTO.get("anthropic")
        assert harness == "zcode"

    def test_resolve_provider_prefers_provider_id(self):
        from vision_relay.config import RelayConfig
        from vision_relay.server import _resolve_provider

        r = RelayConfig(name="zcode-open.bigmodel.cn", protocol="anthropic", base_url="https://x", provider_id="builtin:bigmodel")
        assert _resolve_provider(ProxyConfig(), r, {}) == "builtin:bigmodel"  # 能力/探针键=供应商 ID（spec §6.4）
        plain = RelayConfig(name="qwen-open.bigmodel.cn", protocol="chat", base_url="https://x")
        assert _resolve_provider(ProxyConfig(), plain, {}) == "qwen-open.bigmodel.cn"  # 非 zcode relay 语义不变

    def test_build_vlm_clients_includes_zcode(self):
        from vision_relay.config import ProxyConfig
        from vision_relay.server import build_vlm_clients

        assert "zcode" in build_vlm_clients(ProxyConfig())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_proxy_server.py::TestSelectRelayAuthHints -q`
Expected: FAIL（`_select_relay() takes 3 positional arguments but 4 were given`）

- [ ] **Step 3: 实现**

① `_select_relay` 加指纹第一优先级层：

```python
def _select_relay(cfg: ProxyConfig, inbound_proto: str, model: str = "", auth_fp: str | None = None) -> RelayConfig:
    """按 spec §6.3 选 relay：①(模型,协议,密钥指纹)精确 → ②(模型,协议)顺序 → ③仅协议 → 默认。
    指纹层为 zcode 同名模型消歧主路径（spec 2026-08-26 §6）；无指纹/未命中退回顺序命中。"""
    import fnmatch

    if auth_fp:
        for relay in cfg.relays:
            if (
                relay.protocol == inbound_proto
                and getattr(relay, "auth_hints", None)
                and auth_fp in relay.auth_hints
                and any(fnmatch.fnmatch(model, p) for p in relay.models)
            ):
                return relay
    for relay in cfg.relays:
        if relay.protocol == inbound_proto and any(fnmatch.fnmatch(model, p) for p in relay.models):
            return relay
    for relay in cfg.relays:
        if relay.protocol == inbound_proto:
            return relay
    return RelayConfig(name="default", protocol=inbound_proto, base_url="", api_key="")
```

② `do_POST` 内 relay 选择与 harness 归属（改两行）：

```python
        from .fingerprint import fingerprint_from_headers  # 文件顶部 import 区

            relay = _select_relay(self._cfg, proto, ir.model, fingerprint_from_headers(client_auth))
            harness = "zcode" if getattr(relay, "provider_id", None) else _HARNESS_BY_PROTO.get(proto)
```

③ `build_vlm_clients` 循环改：

```python
    for harness in ("claude", "codex", "qwen-code", "zcode"):
```

④ `_resolve_provider` 的返回段（一层分支）改为优先回供应商身份：

```python
def _resolve_provider(cfg: ProxyConfig, relay: RelayConfig, tool_states_cache: dict) -> str | None:
    """请求期 provider 解析（尽力而为）：两层=工具激活供应商；一层=relay 名。
    zcode 一层 relay 带 provider_id（供应商 ID）——能力/探针键与矩阵标注同键（spec §6.4）。"""
    if getattr(relay, "via", None):
        return tool_states_cache.get(relay.via) or relay.via  # 激活供应商名未取到时退工具名
    if getattr(relay, "provider_id", None):
        return relay.provider_id
    return relay.name if relay.name != "default" else None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_proxy_server.py tests/test_proxy_stream.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
python -m pytest -q && ruff format --check . && ruff check .
git add vision_relay/server.py tests/test_proxy_server.py
git commit -m "feat(server): 选路加密钥指纹第一优先级层((模型,协议,指纹)精确命中,未命中退顺序);_resolve_provider 优先回 provider_id(能力/探针键与矩阵同源);zcode relay 请求期归属 harness=zcode;VLM 客户端四 harness"
```

---

### Task 8: model_sources — zcode 矩阵与探测目标

**Files:**
- Modify: `vision_relay/model_sources.py`、`vision_relay/verbs.py`
- Test: `tests/test_proxy_model_sources.py`

- [ ] **Step 1: 写失败测试**

`tests/test_proxy_model_sources.py` 追加：

```python
class TestZcodeMatrix:
    def _setup(self, tmp_path, monkeypatch, providers):
        from vision_relay import wiring

        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        p = tmp_path / ".zcode" / "v2" / "config.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"provider": providers}), encoding="utf-8")
        return wiring

    def test_matrix_rows_and_empty_key_excluded(self, tmp_path, monkeypatch):
        wiring = self._setup(tmp_path, monkeypatch, {
            "builtin:bigmodel": {
                "name": "B", "kind": "anthropic", "options": {"apiKey": "k-1234567890", "baseURL": "https://b.example"}, "enabled": True,
                "models": {"GLM-5-Turbo": {"name": "glm-5-turbo"}, "GLM-5.3": {}},
            },
            "nokey": {"name": "N", "kind": "anthropic", "options": {"apiKey": "", "baseURL": "https://n.example"}, "enabled": False, "models": {"m": {}}},
        })
        from vision_relay import model_sources
        from vision_relay.config import ProxyConfig

        rows = model_sources.zcode_matrix(ProxyConfig())
        assert len(rows) == 1  # 空 key 供应商整行不产
        r = rows[0]
        assert r.provider == "builtin:bigmodel" and r.is_current is True
        assert r.models == ["glm-5-turbo", "GLM-5.3"]  # API 名（name 优先）
        assert r.tool == "zcode" and r.harness == "zcode"

    def test_harness_matrix_has_zcode_no_direct_fallback(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, {"k": {"kind": "anthropic", "options": {"apiKey": "k", "baseURL": "https://x"}, "models": {"m": {}}}})
        from vision_relay import model_sources
        from vision_relay.config import ProxyConfig

        cfg = ProxyConfig()
        cfg.routing.harnesses = ["zcode"]
        out = model_sources.harness_matrix(cfg)
        assert len(out["zcode"]) == 1 and out["zcode"][0].tool == "zcode"

    def test_probe_target_uses_snapshot_original(self, tmp_path, monkeypatch):
        wiring = self._setup(tmp_path, monkeypatch, {"k": {"kind": "openai", "options": {"apiKey": "sk-abcdefgh", "baseURL": "http://127.0.0.1:8787"}, "models": {}}})
        from vision_relay import model_sources, snapshot
        from vision_relay.config import ProxyConfig

        snapshot.save("zcode", snapshot.Snapshot(base_url="x", key_ref="provider[].options.apiKey", model="", provider_urls={"k::openai": "https://real.example/v1"}))
        base, key, proto = model_sources.zcode_probe_target(ProxyConfig(), "k")
        assert (base, key, proto) == ("https://real.example/v1", "sk-abcdefgh", "chat")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_proxy_model_sources.py::TestZcodeMatrix -q`
Expected: FAIL（`AttributeError: zcode_matrix`）

- [ ] **Step 3: 实现**

① `model_sources.py` 追加（文件末尾、`current_provider` 之前）：

```python
def _zcode_config_path() -> str:
    from . import wiring

    return wiring._path(wiring.HOME, "zcode")


def _zcode_load() -> dict:
    try:
        with open(_zcode_config_path(), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def zcode_matrix(cfg) -> list[ProviderRow]:
    """zcode config.json → ProviderRow（spec §9）：可接管供应商一行（空 key/未知 kind 整行
    不产），provider=供应商 ID（唯一可反查、与请求期能力键同键），enabled 即当前，
    models=API 名（name 优先）。现场地址指代理时显示快照原值。"""
    from . import snapshot, wiring

    d = _zcode_load()
    items, _nokey, _bad = wiring._zcode_entries(d)
    snap = snapshot.load().get("zcode")
    snap_urls = (snap.provider_urls if snap is not None and snap.provider_urls else {}) or {}
    rows: list[ProviderRow] = []
    for pid, kind, e in items:
        models: list[str] = []
        models_obj = e.get("models")
        if isinstance(models_obj, dict):
            for mid, m in models_obj.items():
                if isinstance(m, dict):
                    api = m.get("name")
                    models.append(api if isinstance(api, str) and api else mid)
        url = e["options"]["baseURL"]
        if wiring.classify_base_url(url, cfg.bind_port) == "ours":
            url = snap_urls.get(wiring._zcode_key(pid, kind)) or url
        rows.append(
            ProviderRow(
                tool="zcode",
                harness="zcode",
                provider=pid,
                base_url=url,
                is_current=e.get("enabled") is True,
                models=_dedup_keep_order(models),
            )
        )
    return rows


def zcode_probe_target(cfg, provider: str) -> tuple[str, str, str]:
    """(base, key, proto)：原始上游=现场（非代理）→快照原值；key 仅进程内使用（spec §9）。"""
    from . import snapshot, wiring

    d = _zcode_load()
    provs = d.get("provider")
    if isinstance(provs, dict):
        e = provs.get(provider)
        if isinstance(e, dict) and isinstance(e.get("options"), dict):
            kind = e.get("kind")
            opts = e["options"]
            if kind in wiring._ZCODE_PROTO:
                base = opts.get("baseURL")
                if isinstance(base, str) and wiring.classify_base_url(base, cfg.bind_port) == "ours":
                    snap = snapshot.load().get("zcode")
                    base = (snap.provider_urls or {}).get(wiring._zcode_key(provider, str(kind))) if snap else None
                key = opts.get("apiKey")
                return (
                    base if isinstance(base, str) else "",
                    key if isinstance(key, str) else "",
                    wiring._ZCODE_PROTO[kind],
                )
    return "", "", "chat"
```

② `harness_matrix` 的分支链加 zcode 并排除直连兜底：

```python
        elif harness == "zcode":
            rows = zcode_matrix(cfg)
        if not rows and harness != "zcode":  # zcode 矩阵真相=config.json，文件缺失=空矩阵，不走直连兜底
            rows = _direct_rows(cfg, harness)
```

③ `verbs.py` 的 `probe_target_for` 函数体最前面（`proto = {...}` 行之前）插入：

```python
    if harness == "zcode":  # zcode 探测目标=该供应商原始上游+自带 key（spec §9）
        from . import model_sources

        return model_sources.zcode_probe_target(cfg, provider)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_proxy_model_sources.py tests/test_proxy_verbs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
python -m pytest -q && ruff format --check . && ruff check .
git add vision_relay/model_sources.py vision_relay/verbs.py tests/test_proxy_model_sources.py
git commit -m "feat(models): zcode 供应商矩阵(空 key 不产行/provider=ID 与能力键同源/API 名)+探测目标(快照原值+自带 key,kind 定协议);probe_target_for zcode 分支"
```

---

### Task 9: zcode_proc.py — 进程检测/重启 + status 透出 + zcode-restart 动词

**Files:**
- Create: `vision_relay/zcode_proc.py`
- Modify: `vision_relay/verbs.py`、`vision_relay/cli.py`
- Test: `tests/test_proxy_zcode.py`

- [ ] **Step 1: 写失败测试**

`tests/test_proxy_zcode.py` 追加（monkeypatch 子进程层，不真跑系统命令）：

```python
class TestZcodeProc:
    def test_find_parses_tasklist(self, monkeypatch):
        from vision_relay import zcode_proc

        monkeypatch.setattr(zcode_proc, "_run", lambda cmd, timeout=3.0: '"zcode.exe","4242","Console","1","1,234 K"\r\n')
        monkeypatch.setattr(zcode_proc, "_win_start_ts", lambda pid: 100.0)
        monkeypatch.setattr(zcode_proc, "_win_exe", lambda pid: "C:/zcode.exe")
        procs = zcode_proc.find_zcode_processes(force=True)
        assert procs == [{"pid": 4242, "start_ts": 100.0, "exe": "C:/zcode.exe"}]

    def test_needs_restart_logic(self, monkeypatch):
        from vision_relay import zcode_proc

        monkeypatch.setattr(zcode_proc, "find_zcode_processes", lambda force=False: [{"pid": 1, "start_ts": 100.0, "exe": "x"}])
        assert zcode_proc.zcode_needs_restart(200.0) is True  # 启动早于改写
        assert zcode_proc.zcode_needs_restart(50.0) is False  # 改写后已重启
        assert zcode_proc.zcode_needs_restart(0.0) is False  # 无改写记录

    def test_needs_restart_not_running(self, monkeypatch):
        from vision_relay import zcode_proc

        monkeypatch.setattr(zcode_proc, "find_zcode_processes", lambda force=False: [])
        assert zcode_proc.zcode_needs_restart(999.0) is False

    def test_restart_kills_and_relaunches(self, monkeypatch, tmp_path):
        from vision_relay import zcode_proc

        exe = tmp_path / "zcode.exe"
        exe.write_bytes(b"x")
        launched: list[list[str]] = []
        monkeypatch.setattr(
            zcode_proc, "find_zcode_processes",
            lambda force=False: [{"pid": 7, "start_ts": 0.0, "exe": str(exe)}],
        )
        monkeypatch.setattr(zcode_proc, "_run", lambda cmd, timeout=3.0: launched.append(cmd) or "")
        monkeypatch.setattr(
            zcode_proc.subprocess, "Popen",
            lambda argv, **kw: launched.append(argv),
        )
        assert zcode_proc.restart_zcode() is True
        assert any("kill" in " ".join(map(str, c)).lower() or "taskkill" in " ".join(map(str, c)).lower() for c in launched)
        assert launched[-1] == [str(exe)]  # 最后拉起 exe

    def test_restart_no_process_returns_false(self, monkeypatch):
        from vision_relay import zcode_proc

        monkeypatch.setattr(zcode_proc, "find_zcode_processes", lambda force=False: [])
        assert zcode_proc.restart_zcode() is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_proxy_zcode.py::TestZcodeProc -q`
Expected: FAIL（`ModuleNotFoundError: vision_relay.zcode_proc`）

- [ ] **Step 3: 实现**

新建 `vision_relay/zcode_proc.py`：

```python
"""zcode 进程检测与重启（spec §7.2/§10）：平台 best-effort，绝不抛出、检测不到=空。

无 psutil 依赖（工程约束），Windows 用 tasklist+PowerShell、unix 用 ps。全部子进程
短超时；结果短 TTL 缓存（status 每 5s 轮询，避免每次都枚举进程）。"""

from __future__ import annotations

import os
import subprocess
import time

_TTL = 5.0
_cache: dict = {"ts": 0.0, "procs": []}


def _run(cmd: list[str], timeout: float = 3.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _win_start_ts(pid: int) -> float:
    out = _run(
        ["powershell", "-NoProfile", "-c",
         f"[int64]((Get-Process -Id {pid}).StartTime.ToUniversalTime() - [datetime]::new(1970,1,1)).TotalSeconds"],
        timeout=5.0,
    )
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def _win_exe(pid: int) -> str:
    out = _run(["powershell", "-NoProfile", "-c", f"(Get-Process -Id {pid}).Path"], timeout=5.0)
    return out.strip().strip('"')


def find_zcode_processes(force: bool = False) -> list[dict]:
    """[{pid, start_ts, exe}]——best-effort。误报防护：跳过本代理自身（vision-relay/zcode-relay）。"""
    now = time.time()
    if not force and now - _cache["ts"] < _TTL:
        return _cache["procs"]
    procs: list[dict] = []
    if os.name == "nt":
        out = _run(["tasklist", "/FO", "CSV", "/NH"])
        for line in out.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[0].lower().startswith("zcode") and parts[0].lower() != "zcode-relay.exe":
                try:
                    pid = int(parts[1])
                except ValueError:
                    continue
                procs.append({"pid": pid, "start_ts": _win_start_ts(pid), "exe": _win_exe(pid)})
    else:
        out = _run(["ps", "-eo", "pid,comm"])
        for line in out.splitlines():
            low = line.lower()
            if "zcode" in low and "vision-relay" not in low and "zcode-relay" not in low and "zcode_proc" not in low:
                fields = line.split()
                if fields and fields[0].isdigit():
                    procs.append({"pid": int(fields[0]), "start_ts": 0.0, "exe": ""})
    _cache.update(ts=now, procs=procs)
    return procs


def zcode_needs_restart(rewrite_ts: float) -> bool:
    """运行中的 zcode 进程启动时间早于本代理最后一次改写 → 需重启才能吃到新配置。"""
    if rewrite_ts <= 0:
        return False
    procs = find_zcode_processes()
    if not procs:
        return False
    return all(p["start_ts"] < rewrite_ts for p in procs)  # start_ts 未知(0.0)视为待重启


def restart_zcode() -> bool:
    """结束 zcode 全部进程并按探测到的 exe 分离重启（best-effort；无进程/无 exe 返回 False）。"""
    procs = find_zcode_processes(force=True)
    if not procs:
        return False
    exe = next((p["exe"] for p in procs if p.get("exe")), "")
    for p in procs:
        if os.name == "nt":
            _run(["taskkill", "/PID", str(p["pid"]), "/T", "/F"])
        else:
            _run(["kill", str(p["pid"])])
    if not exe or not os.path.exists(exe):
        return False
    kwargs: dict = {"creationflags": 0x00000008} if os.name == "nt" else {"start_new_session": True}
    try:
        subprocess.Popen(
            [exe], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs
        )
        return True
    except OSError:
        return False
```

`verbs.py`：`status()` 里 `obs["first_run"] = ...` 行之前插入：

```python
    # zcode 重启交互支撑（spec §7.2）：进程在跑且其启动早于本代理最后一次改写 → 待重启
    from . import wiring, zcode_proc

    obs["zcode_runtime"] = {
        "running": bool(zcode_proc.find_zcode_processes()),
        "needs_restart": zcode_proc.zcode_needs_restart(wiring.zcode_rewrite_ts()),
    }
```

`verbs.py` 追加动词（`relay_set` 之后）：

```python
def zcode_restart(cfg: ProxyConfig) -> dict:
    """立即重启 zcode（弹窗选项①/提示条按钮共用）；best-effort，结果进事件日志。"""
    from . import zcode_proc
    from .reconcile import append_event

    ok = zcode_proc.restart_zcode()
    append_event("zcode_restart", "zcode", {"ok": ok})
    return envelope(ok, {"restarted": ok})
```

`cli.py`：`_JSON_MAP` 加 `"zcode-restart": verbs.zcode_restart,`；`parse_args` 的 settings-set 注册行之后加 `sub.add_parser("zcode-restart", parents=[common])  # zcode 待重启提示条/弹窗选项①共用`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_proxy_zcode.py tests/test_proxy_cli.py tests/test_proxy_verbs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
python -m pytest -q && ruff format --check . && ruff check .
git add vision_relay/zcode_proc.py vision_relay/verbs.py vision_relay/cli.py tests/test_proxy_zcode.py
git commit -m "feat(proc): zcode 进程检测/重启(无 psutil,tasklist+PowerShell/ps,best-effort,TTL 缓存,防误报自身);status 透出 zcode_runtime;zcode-restart 动词"
```

---

### Task 10: settings-set — harnesses 编辑 + 取消勾选即还原

**Files:**
- Modify: `vision_relay/verbs.py`
- Test: `tests/test_proxy_zcode.py`

- [ ] **Step 1: 写失败测试**

`tests/test_proxy_zcode.py` 追加：

```python
class TestSettingsSetHarnesses:
    def _stdin(self, monkeypatch, payload):
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    def test_harnesses_update_and_uncheck_restores(self, tmp_path, monkeypatch):
        from vision_relay import snapshot, verbs

        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        p = _write_zcode_config(tmp_path, {"k": _provider(models={"m": _text_model()})})
        cfg = ProxyConfig()
        cfg.routing.harnesses = ["zcode"]
        wiring.wiring_backup_and_rewrite(cfg)
        assert json.load(open(p, encoding="utf-8"))["provider"]["k"]["options"]["baseURL"] == "http://127.0.0.1:8787"
        snap = snapshot.load()["zcode"]

        self._stdin(monkeypatch, {"routing": {"harnesses": ["claude", "codex", "qwen-code"]}})
        out = verbs.settings_set(cfg)
        assert out["ok"] is True
        d = json.load(open(p, encoding="utf-8"))
        assert d["provider"]["k"]["options"]["baseURL"] == "https://open.bigmodel.cn/api/anthropic"  # 取消即还原
        assert "restored" in out["data"] and any("zcode" in m for m in out["data"]["restored"])
        assert cfg.routing.harnesses == ["claude", "codex", "qwen-code"]

    def test_unknown_harness_rejected(self, monkeypatch):
        from vision_relay import verbs
        from vision_relay.config import ProxyConfig

        cfg = ProxyConfig()
        self._stdin(monkeypatch, {"routing": {"harnesses": ["claude", "nope"]}})
        out = verbs.settings_set(cfg)
        assert out["ok"] is False and "unknown" in out["data"]["error"]

    def test_empty_or_duplicate_rejected(self, monkeypatch):
        from vision_relay import verbs
        from vision_relay.config import ProxyConfig

        cfg = ProxyConfig()
        self._stdin(monkeypatch, {"routing": {"harnesses": []}})
        assert verbs.settings_set(cfg)["ok"] is False
        self._stdin(monkeypatch, {"routing": {"harnesses": ["claude", "claude"]}})
        assert verbs.settings_set(cfg)["ok"] is False

    def test_zcode_uncheck_reports_needs_restart(self, tmp_path, monkeypatch):
        from vision_relay import verbs, zcode_proc

        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        _write_zcode_config(tmp_path, {"k": _provider()})
        cfg = ProxyConfig()
        cfg.routing.harnesses = ["zcode", "claude"]
        monkeypatch.setattr(zcode_proc, "find_zcode_processes", lambda force=False: [{"pid": 1, "start_ts": 0.0, "exe": ""}])
        self._stdin(monkeypatch, {"routing": {"harnesses": ["claude"]}})
        out = verbs.settings_set(cfg)
        assert out["data"].get("needs_zcode_restart") is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_proxy_zcode.py::TestSettingsSetHarnesses -q`
Expected: FAIL（`unsupported settings key`——harnesses 还不在白名单）

- [ ] **Step 3: 实现**

`settings_set` 改造（完整替换函数体）：

```python
def settings_set(cfg: ProxyConfig) -> dict:
    """stdin: {"routing": {...白名单键...}, "vision_log": {...}}。白名单外键拒绝。
    routing.harnesses = 路由范围勾选（spec §8）：被移除的 harness 立即单 harness 还原
    （取消即还原），zcode 在跑时附 needs_zcode_restart 供 GUI 弹窗/提示条。"""
    payload, err = _stdin_json("object")
    if err is not None:
        return err
    routing_ok = {"unknown_default", "harnesses"}
    log_ok = {"enabled", "retention_days"}
    r = payload.get("routing") or {}
    v = payload.get("vision_log") or {}
    if not isinstance(payload.get("routing", {}), dict) or not isinstance(payload.get("vision_log", {}), dict):
        return envelope(False, {"error": "routing/vision_log must be objects"})
    if not set(r).issubset(routing_ok) or not set(v).issubset(log_ok):
        return envelope(False, {"error": "unsupported settings key"})
    if "unknown_default" in r and r["unknown_default"] not in ("text_only", "image"):
        return envelope(False, {"error": "unknown_default must be text_only|image"})
    if "retention_days" in v and (not isinstance(v["retention_days"], int) or v["retention_days"] < 1):
        return envelope(False, {"error": "retention_days must be an int >= 1 (disable via vision_log.enabled=false)"})
    removed: list[str] = []
    if "harnesses" in r:
        hs = r["harnesses"]
        if not isinstance(hs, list) or not hs or not all(isinstance(x, str) for x in hs):
            return envelope(False, {"error": "harnesses must be a non-empty list of harness names"})
        from .config import HARNESSES

        if [x for x in hs if x not in HARNESSES] or len(set(hs)) != len(hs):
            return envelope(False, {"error": f"unknown/duplicate harness in {hs!r}; must be in {list(HARNESSES)}"})
        removed = [h for h in cfg.routing.harnesses if h not in hs]
    if "enabled" in v:
        enabled = v["enabled"]
        if not isinstance(enabled, bool):
            return envelope(False, {"error": "enabled must be a boolean"})
        cfg.vision_log.enabled = enabled
    if "retention_days" in v:
        cfg.vision_log.retention_days = v["retention_days"]
    cfg.routing.unknown_default = r.get("unknown_default", cfg.routing.unknown_default)
    restore_msgs: list[str] = []
    if "harnesses" in r:
        cfg.routing.harnesses = list(r["harnesses"])
        if removed:  # 取消勾选即还原（spec §8）：与 stop 同一单 harness 还原步骤
            from . import wiring
            from .reconcile import append_event

            for h in removed:
                for msg in wiring.wiring_restore_harness(cfg, h):
                    restore_msgs.append(msg)
                    append_event("uncheck_restore", h, {"detail": msg})
    _locked_save(cfg)
    data: dict = {"saved": True}
    if restore_msgs:
        data["restored"] = restore_msgs
    if "zcode" in removed:
        from . import zcode_proc

        if zcode_proc.find_zcode_processes():  # 进程在跑 → 还原待重启（GUI 弹窗三选/提示条）
            data["needs_zcode_restart"] = True
    return envelope(True, data)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_proxy_zcode.py tests/test_proxy_verbs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
python -m pytest -q && ruff format --check . && ruff check .
git add vision_relay/verbs.py tests/test_proxy_zcode.py
git commit -m "feat(verbs): settings-set 路由范围勾选——harnesses 白名单校验+被移除项立即单 harness 还原(事件留痕)+zcode 在跑附 needs_zcode_restart"
```

---

### Task 11: GUI — 路由范围勾选、三选弹窗、待重启提示条

**Files:**
- Create: `gui/src/shell/ZcodeDialog.tsx`
- Modify: `gui/src/lib/chain.ts`、`gui/src/shell/useStatus.ts`、`gui/src/shell/RoutingToggle.tsx`、`gui/src/pages/Settings.tsx`、`gui/src/pages/Overview.tsx`、`gui/src/i18n.ts`
- Test: `gui/src/lib/chain.test.ts`、`gui/src/pages/Settings.test.tsx`

- [ ] **Step 1: 写失败测试**

`gui/src/lib/chain.test.ts` 追加：

```typescript
describe("zcode label", () => {
  it("labels zcode and has no routing tool", () => {
    expect(harnessLabel("zcode")).toBe("⚡ Zcode");
    expect(toolFor("zcode", [{ name: "cc-switch", port: 15721, online: true, active_provider: null, provider_base_url: null }])).toBeNull();
  });
});
```

`gui/src/pages/Settings.test.tsx` 追加（沿用该文件既有的 core mock 方式；若 mock 形态不同，以文件内既有 mock 为准对齐字段）：

```typescript
it("路由范围勾选随 managed 列表渲染并随保存提交 harnesses", async () => {
  const calls: unknown[][] = [];
  vi.mocked(core).mockImplementation(async (verb: string, opts?: { stdin?: unknown }) => {
    calls.push([verb, opts?.stdin]);
    if (verb === "config")
      return { vlm: { model: "m", base_url: "b", format: "chat" }, vlm_by_harness: {}, routing: { harnesses: ["claude", "codex", "qwen-code", "zcode"] }, vision_log: { enabled: true, retention_days: 7 } };
    return { saved: true };
  });
  render(<SettingsPage lang="zh" status={null} refresh={() => {}} setLang={() => {}} />);
  await waitFor(() => expect(screen.getByText("路由范围")).toBeTruthy());
  const boxes = screen.getAllByRole("checkbox");
  expect(boxes.length).toBeGreaterThanOrEqual(4); // 四工具勾选框
  fireEvent.click(screen.getByLabelText("zcode")); // 取消勾选 zcode（不弹窗：status=null 无 zcode 运行信号）
  fireEvent.click(screen.getByText("💾 保存设置"));
  await waitFor(() => {
    const settings = calls.find(([v, s]) => v === "settings-set" && s) as [string, { routing?: { harnesses?: string[] } }];
    expect(settings[1].routing?.harnesses).toEqual(["claude", "codex", "qwen-code"]);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pnpm -C gui run test`
Expected: FAIL（`harnessLabel("zcode")` 返回 "zcode"；找不到「路由范围」）

- [ ] **Step 3: 实现**

① `chain.ts` 标签：

```typescript
export function harnessLabel(h: string): string {
  return { claude: "🤖 Claude Code", codex: "💻 Codex", "qwen-code": "❓ Qwen Code", zcode: "⚡ Zcode" }[h] ?? h;
}
```

② `useStatus.ts` 的 `StatusData` 加字段：

```typescript
  zcode_runtime?: { running: boolean; needs_restart: boolean };
```

③ 新建 `gui/src/shell/ZcodeDialog.tsx`（三选弹窗，Settings/RoutingToggle 共用）：

```typescript
// zcode 重启三选弹窗（spec §7.2）：选项①连带重启（默认）②取消本次操作 ③稍后自行重启。
export interface ZcodeChoice { label: string; kind: "restart" | "abort" | "later" }
export function ZcodeDialog(p: { title: string; desc: string; choices: ZcodeChoice[]; onChoose: (kind: ZcodeChoice["kind"]) => void }) {
  return (
    <div className="modal-backdrop" style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.35)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50 }}>
      <div className="card" style={{ maxWidth: 460, margin: 16 }} role="dialog" aria-label={p.title}>
        <h3 style={{ marginTop: 0 }}>{p.title}</h3>
        <p className="dim">{p.desc}</p>
        <div className="row" style={{ justifyContent: "flex-end", gap: 8, flexWrap: "wrap" }}>
          {p.choices.map((c) => (
            <button key={c.kind} className={"btn" + (c.kind === "restart" ? " primary" : "")} onClick={() => p.onChoose(c.kind)}>{c.label}</button>
          ))}
        </div>
      </div>
    </div>
  );
}
```

④ `RoutingToggle.tsx` 改造（props 加 `status: StatusData | null`）：

```typescript
import { useState } from "react";
import { core, startService, stopService } from "../core";
import { t, Lang } from "../i18n";
import type { StatusData } from "./useStatus";
import { ZcodeDialog } from "./ZcodeDialog";

export function RoutingToggle(props: { on: boolean; onChangeDone: () => void; lang: Lang; status: StatusData | null }) {
  const [busy, setBusy] = useState(false);
  const [dlg, setDlg] = useState<null | "on" | "off">(null);
  // zcode 在受管清单且进程在跑 → 先弹三选（spec §7.2）；否则直接执行
  const zcodeLive = props.status?.zcode_runtime?.running && "zcode" in (props.status?.harnesses ?? {});
  const doToggle = async (restart: boolean) => {
    setBusy(true);
    try {
      if (props.on) await stopService();
      else await startService();
      if (restart && zcodeLive) await core("zcode-restart");
      setTimeout(props.onChangeDone, 1200);
    } finally { setBusy(false); }
  };
  const toggle = async () => {
    if (zcodeLive) { setDlg(props.on ? "off" : "on"); return; }
    await doToggle(false);
  };
  const act = dlg === "on" ? "开启" : "关闭";
  return (
    <>
      <div className="switch">
        <span className="dim">{t(props.lang, "routingOffLabel")}</span>
        <div className={"track" + (props.on ? "" : " off")} onClick={busy ? undefined : toggle}>
          <div className="knob" />
        </div>
        <b style={{ color: props.on ? "#059669" : "#6b7280" }}>{props.on ? t(props.lang, "routingOn") : t(props.lang, "routingOff")}</b>
      </div>
      {dlg && (
        <ZcodeDialog
          title={`⚡ ${act}路由与 zcode`}
          desc={`zcode 正在运行，配置改动需重启才能生效。${dlg === "off" ? "重启前 zcode 的请求将失败。" : ""}`}
          choices={[
            { label: `${act}路由并重启 zcode`, kind: "restart" },
            { label: `不${act}路由`, kind: "abort" },
            { label: `${act}路由，稍后自行重启`, kind: "later" },
          ]}
          onChoose={(kind) => {
            setDlg(null);
            if (kind === "abort") return;
            void doToggle(kind === "restart");
          }}
        />
      )}
    </>
  );
}
```

（调用方 `Overview.tsx` 里 `<RoutingToggle ...>` 处补传 `status={s}`。）

⑤ `Overview.tsx`：状态横幅（RoutingToggle 所在容器）之后插入提示条：

```tsx
      {s.zcode_runtime?.needs_restart && (
        <div className="alert-err row between" data-testid="zcode-restart-hint">
          <span>⚡ {t(lang, "zcodePendingRestart")}</span>
          <button className="btn" onClick={async () => { await core("zcode-restart"); refresh(); }}>{t(lang, "restartZcodeNow")}</button>
        </div>
      )}
```

（`Overview` 已有 `lang`/`refresh`/`core` 上下文变量，按该文件既有命名对齐；若无 `lang` 变量则直接用中文文案并跳过 i18n key。）

⑥ `i18n.ts` 的 dict 两语言各加：

```typescript
        zcodePendingRestart: "zcode 待重启（配置改写尚未生效）",
        restartZcodeNow: "立即重启 zcode",
```
```typescript
        zcodePendingRestart: "zcode needs restart (changes pending)",
        restartZcodeNow: "Restart zcode now",
```

⑦ `Settings.tsx`：`const HARNESSES = [...]` 行替换为全量宇宙 + managed 状态：

```typescript
const ALL_HARNESSES = ["claude", "codex", "qwen-code", "zcode"];
```

组件内加状态与加载（`useEffect` 里 config 回调中，`setUnknownDefault` 行旁）：

```typescript
  const [managed, setManaged] = useState<string[]>(ALL_HARNESSES);
  const [zcodeDlg, setZcodeDlg] = useState(false);
  // useEffect 内：setManaged(((r.harnesses as string[]) ?? ALL_HARNESSES).filter((h) => ALL_HARNESSES.includes(h)));
```

「按 harness 分组」卡的 `HARNESSES.map`/`HARNESSES.filter` 全部改用 `managed`；在其上方插入「路由范围」卡：

```tsx
      <div className="card">
        <h3>路由范围</h3>
        <div className="dim small" style={{ marginBottom: 6 }}>路由开启时对这些工具做 VLM 路由；取消勾选立即还原该工具的接线，且界面上隐藏其相关内容。</div>
        {ALL_HARNESSES.map((h) => (
          <label key={h} style={{ marginRight: 16 }}>
            <input type="checkbox" aria-label={h} checked={managed.includes(h)}
              onChange={(e) => { setManaged(e.target.checked ? [...managed, h] : managed.filter((x) => x !== h)); touch(); }} />
            {" "}{h}
          </label>
        ))}
      </div>
```

`save` 拆成实际保存函数 + zcode 弹窗闸门：

```typescript
  const doSave = async (harnesses: string[], restartZcode: boolean) => {
    try {
      const payload: Record<string, unknown> = {
        vlm: { model: vlm.model, base_url: vlm.base_url, format: vlm.format, ...(vlm.api_key ? { api_key: vlm.api_key } : {}) },
        vlm_by_harness: Object.fromEntries(
          managed.filter((h) => groups[h]).map((h) => {
            const g = groups[h]!;
            return [h, g ? { model: g.model, base_url: g.base_url, ...(g.api_key ? { api_key: g.api_key } : {}) } : null];
          }),
        ),
      };
      if (prompts.t1) payload.custom_tier1 = prompts.t1; else payload.custom_tier1 = null;
      if (prompts.t2) payload.custom_tier2 = prompts.t2; else payload.custom_tier2 = null;
      await core("vlm-set", { stdin: payload });
      await core("settings-set", { stdin: { routing: { unknown_default: unknownDefault, harnesses }, vision_log: logCfg } });
      if (restartZcode) await core("zcode-restart");
      if (corePath) setCorePath(corePath);
      setDirtyCount(0); p.refresh();
    } catch (e) {
      window.alert("保存失败：" + (e instanceof Error ? e.message : String(e)));
    }
  };
  const save = async () => {
    const removingZcode = p.status?.zcode_runtime?.running && !managed.includes("zcode") && (p.status?.harnesses ? "zcode" in p.status.harnesses : false);
    if (removingZcode) { setZcodeDlg(true); return; }  // zcode 在跑且本次取消勾选 → 先弹三选（spec §7.2）
    await doSave(managed, false);
  };
```

保存按钮卡片之后渲染弹窗（文件 return 尾部）：

```tsx
      {zcodeDlg && (
        <ZcodeDialog
          title="⚡ 取消勾选 zcode"
          desc="zcode 正在运行，取消勾选会立即还原其接线，重启 zcode 后生效。"
          choices={[
            { label: "取消勾选并重启 zcode", kind: "restart" },
            { label: "保留勾选", kind: "abort" },
            { label: "取消勾选，稍后自行重启", kind: "later" },
          ]}
          onChoose={(kind) => {
            setZcodeDlg(false);
            if (kind === "abort") return;  // 保留勾选：仅关弹窗（managed 状态保持用户改动前的语义由其自行再选）
            void doSave(kind === "restart" ? managed.filter((h) => h !== "zcode") : managed, kind === "restart");
          }}
        />
      )}
```

（`abort` 分支保持 managed 原样——用户可再勾回；`later`/`restart` 均按去掉 zcode 的列表保存。）文件顶部 import：`import { ZcodeDialog } from "../shell/ZcodeDialog";`

- [ ] **Step 4: 跑测试确认通过**

Run: `pnpm -C gui run test`
Expected: PASS（含既有 GUI 用例）

- [ ] **Step 5: Commit**

```bash
python -m pytest -q && ruff format --check . && ruff check .
git add gui/src
git commit -m "feat(gui): 路由范围勾选(取消勾选即还原+zcode 三选弹窗)+开关路由三选弹窗+待重启提示条(立即重启)+VLM 分组按 managed 派生+zcode 链路标签"
```

---

### Task 12: 端到端串联 + 全量门禁

**Files:**
- Test: `tests/test_e2e_g2_routing.py`（追加 zcode 场景）、无新源文件

- [ ] **Step 1: 追加 E2E 用例**

`tests/test_e2e_g2_routing.py` 追加（沿用该文件既有的 monkeypatch HOME/伪造服务模式；此处给最小骨架，落位时对齐文件内既有 fixture）：

```python
class TestZcodeE2E:
    def test_takeover_route_restore_cycle(self, tmp_path, monkeypatch):
        """接管→指纹选路→还原 全链路（伪造 zcode config + 内存 relay 选择）。"""
        from vision_relay import wiring
        from vision_relay.config import ProxyConfig
        from vision_relay.server import _select_relay
        from vision_relay.fingerprint import key_fingerprint

        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
        providers = {
            "a": {"kind": "anthropic", "options": {"apiKey": "k-aaaaaaaaaa1", "baseURL": "https://a.example"}, "enabled": True,
                  "models": {"GLM": {"modalities": {"input": ["text"]}}}},
            "b": {"kind": "openai", "options": {"apiKey": "k-bbbbbbbbbb2", "baseURL": "https://b.example/v1"}, "enabled": False,
                  "models": {"GLM": {"modalities": {"input": ["text"]}}}},
        }
        p = tmp_path / ".zcode" / "v2" / "config.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"provider": providers}), encoding="utf-8")
        cfg = ProxyConfig()
        cfg.routing.harnesses = ["zcode"]
        wiring.wiring_backup_and_rewrite(cfg)
        # 同名模型 GLM 双协议：按各自协议+指纹精确命中
        ra = _select_relay(cfg, "anthropic", "GLM", key_fingerprint("k-aaaaaaaaaa1"))
        rb = _select_relay(cfg, "chat", "GLM", key_fingerprint("k-bbbbbbbbbb2"))
        assert ra.provider_id == "a" and rb.provider_id == "b"
        # 停止路由：全部还原
        msgs = wiring.wiring_restore_on_stop(cfg)
        d = json.loads(p.read_text(encoding="utf-8"))
        assert d["provider"]["a"]["options"]["baseURL"] == "https://a.example"
        assert d["provider"]["b"]["options"]["baseURL"] == "https://b.example/v1"
        assert d["provider"]["a"]["models"]["GLM"]["modalities"]["input"] == ["text"]
        assert any("providers restored" in m for m in msgs)
```

- [ ] **Step 2: 跑测试确认通过**

Run: `python -m pytest tests/test_e2e_g2_routing.py -q`
Expected: PASS

- [ ] **Step 3: 全量门禁**

```bash
python -m pytest -q
ruff format --check .
ruff check .
pnpm -C gui run test
```

Expected: 四项全绿（pytest 无 skip 异常、ruff 无 diff/lint 违规、vitest 全过）。

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_g2_routing.py
git commit -m "test(e2e): zcode 接管→指纹选路(同名跨协议精确认家)→还原 全链路;门禁全绿"
```

---

## 收尾（实现完成后、手工验收前）

1. Windows 真机按 spec §11 剧本 1–10 手工验收（重点：剧本 9 沉淀 zcode 实际入站路径与模型名形态的实证，若与双名假设不符回补 relay.models 收录规则）。
2. `CHANGELOG.md` 补条目（Unreleased）。
3. spec 状态行从「设计稿」改为「已实现（M-zcode）」，PR 描述链接 spec 与本计划。
