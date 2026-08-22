# vision-relay 二期 M2（GUI）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **上游 spec（必须尊重，冲突时以 spec 为准）:** [`docs/superpowers/specs/2026-08-21-vision-relay-phase2-control-plane-design.md`](../specs/2026-08-21-vision-relay-phase2-control-plane-design.md) —— 重点 §3（自动决策框架：6 个用户决策点）、§4（GUI=薄壳，经 CLI JSON 契约，不解析任何配置文件）、§6（五页 IA/交互细节/保存语义/向导/托盘）、§7（配置变更）、§10（验收剧本 1–8）、§11（M2 范围）。
> **视觉基准（布局与措辞的唯一参照）:** [`docs/superpowers/specs/gui-mockups/index.html`](../specs/gui-mockups/index.html) —— 顶部标签是文档目录不是软件界面；真实软件只有左侧 5 项导航。
> **M1 事实（已核对）:** `vision_relay/verbs.py` 已有只读动词 `status/refresh/diagnose/models_scan/config_get/tools/events/visionlog`（`CONTRACT_VERSION=1`，envelope `{contract_version,ok,data}`）；CLI 子命令见 `vision_relay/cli.py`（`start --detach/stop/status/refresh/diagnose/tools/probe/events/visionlog/config/models-scan`，多数带 `--json`）；`python -m pytest -q` 基线 **330 passed**。

**Goal:** 交付 Tauri 桌面 GUI（Windows 优先，代码跨平台）：5 页 + 两步首次向导 + 托盘与关闭确认，端到端跑通 spec §10 剧本 1–8。

**Architecture:** Phase A 先补 GUI 必需的**写动词**（models-set / vlm-set / settings-set / relay-set / vlm-test / models-fetch / probe --json / status 增强），全部长在 Python 核心（pytest 覆盖）；Phase B Tauri 壳（Rust 只做三件事：找 core、跑子进程、托盘/窗口事件）；Phase C 页面只消费 verbs，**不解析任何配置文件**（spec §4 硬规则）。

**Tech Stack:** Tauri 2 + React 18 + TypeScript + Vite（pnpm）；Rust 标准库 + tauri 2；前端测试 vitest（纯逻辑单测）；Python 侧延续 pytest。**不引入** UI 组件库/状态库/React Query（YAGNI；样式手写，视觉对齐 mockup）。

**M2 范围内不做（YAGNI，归 M3/后期）:** 安装包打包与三平台 CI、自动监听开关 UI 化（M1 后端已具备，GUI 只展示状态）、冻结 sidecar 分发、自动更新、macOS/Linux 实机回归（代码跨平台，实机验证 M3 打包后补）。

**全局约定:**
- 契约：所有新增动词沿用 envelope `{contract_version:1, ok, data}`；**结构变更必须在本计划内记录**，不许静默改。
- 写动词输入一律走 **stdin 传 JSON**（避免 Windows 命令行引号地狱）；`--json` 走 stdout。
- 密钥铁律：任何动词输出不出现明文 key（打码 `●●●●`）；GUI 永不回显 key，编辑时留空=不修改。
- GUI 决策点只有 6 个（spec §3）：路由总开关 / 首次填 VLM / 首次过目模型能力 / 缺 key 补填 / 关 GUI 选择（记住）/ 稀有停用转发。其余一律只读展示或被动通知。
- 保存语义（spec §6）：输入类控件统一页底「保存」；按钮类动作（刷新/诊断/路由开关/探测/测试）即时。
- 每 Task TDD + `ruff format vision_relay tests && ruff check vision_relay tests`（Python）/ `pnpm -C gui test`（前端）+ commit。

---

## Phase A —— Python 写动词（GUI 的全部后端）

### Task 1: models-set（三元组写入，source=user；null=清除为未标注）

**Files:**
- Modify: `vision_relay/verbs.py`, `vision_relay/cli.py`
- Test: `tests/test_proxy_verbs.py`（追加）

- [ ] **Step 1: 写失败测试（追加）**

```python
class TestModelsSet:
    def _stdin(self, monkeypatch, payload):
        import io as _io

        monkeypatch.setattr("sys.stdin", _io.StringIO(json.dumps(payload)))

    def test_write_user_override_and_clear(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        self._stdin(monkeypatch, [
            {"harness": "claude", "provider": "bigmodel", "model": "m1", "value": "image"},
            {"harness": "claude", "provider": "bigmodel", "model": "m2", "value": None},
        ])
        cfg.model_capabilities.setdefault("claude", {}).setdefault("bigmodel", {})["m2"] = "text_only"
        cfg.capability_sources.setdefault("claude", {}).setdefault("bigmodel", {})["m2"] = "probe"
        out = verbs.models_set(cfg)
        assert out["ok"] is True
        assert cfg.model_capabilities["claude"]["bigmodel"]["m1"] == "image"
        assert cfg.capability_sources["claude"]["bigmodel"]["m1"] == "user"
        assert "m2" not in cfg.model_capabilities["claude"]["bigmodel"]  # null = 清除 -> 未标注
        assert "m2" not in cfg.capability_sources["claude"]["bigmodel"]

    def test_invalid_value_rejected_without_partial_write(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        self._stdin(monkeypatch, [{"harness": "c", "provider": "p", "model": "m", "value": "movie"}])
        out = verbs.models_set(cfg)
        assert out["ok"] is False and "movie" in json.dumps(out)
        assert cfg.model_capabilities == {}  # 校验失败不落盘

    def test_bad_json_rejected(self, tmp_path, monkeypatch):
        import io as _io

        monkeypatch.setenv("sys.stdin", _io.StringIO("not-json"))
        out = verbs.models_set(ProxyConfig())
        assert out["ok"] is False
```

- [ ] **Step 2: 跑确认失败** → `python -m pytest tests/test_proxy_verbs.py -q -k ModelsSet` FAIL

- [ ] **Step 3: verbs.py 追加实现**

```python
def models_set(cfg: ProxyConfig) -> dict:
    """stdin: [{"harness","provider","model","value"}]；value ∈ image|text_only|null。
    全量校验通过才写（不部分落盘）；value=null 清除条目=未标注。写路径走文件锁。"""
    import json
    import sys

    from .locking import config_lock

    try:
        rows = json.load(sys.stdin)
    except ValueError as exc:
        return envelope(False, {"error": f"invalid stdin json: {exc}"})
    if not isinstance(rows, list):
        return envelope(False, {"error": "expected a JSON array"})
    for r in rows:
        if not isinstance(r, dict) or not all(k in r for k in ("harness", "provider", "model")):
            return envelope(False, {"error": f"row missing keys: {r!r}"})
        v = r.get("value")
        if v not in ("image", "text_only", None):
            return envelope(False, {"error": f"value must be image|text_only|null, got {v!r}"})
    with config_lock():
        for r in rows:
            h, p, m, v = r["harness"], r["provider"], r["model"], r.get("value")
            cap = cfg.model_capabilities.setdefault(h, {}).setdefault(p, {})
            src = cfg.capability_sources.setdefault(h, {}).setdefault(p, {})
            if v is None:
                cap.pop(m, None)
                src.pop(m, None)
            else:
                cap[m] = v
                src[m] = "user"
        from .config import save_config

        save_config(cfg)
    return envelope(True, {"updated": len(rows)})
```

cli：`sub.add_parser("models-set", parents=[common])`，`_JSON_MAP` 加 `"models-set": verbs.models_set`（stdin 读取在 verb 内完成，`--json` 正常输出 envelope）。

- [ ] **Step 4: 跑通过 + 全量回归 + lint + commit**

```bash
python -m pytest -q && ruff format vision_relay tests && ruff check vision_relay tests
git add vision_relay/verbs.py vision_relay/cli.py tests/test_proxy_verbs.py
git commit -m "feat(verbs): models-set — user overrides via stdin JSON, null clears to unannotated"
```

---

### Task 2: vlm-set（全局/分组/自定义提示词）+ vlm.py 提示词生效 + vlm-test

**Files:**
- Modify: `vision_relay/config.py`（VLMConfig 加 custom_tier1/custom_tier2: str|None）、`vision_relay/vlm.py`（describe 支持 prompt_override 与自定义提示词）、`vision_relay/verbs.py`、`vision_relay/cli.py`
- Test: `tests/test_proxy_verbs.py`、`tests/test_proxy_vlm.py`

- [ ] **Step 1: 写失败测试（追加）**

```python
class TestVlmSet:
    def _stdin(self, monkeypatch, payload):
        import io as _io

        monkeypatch.setattr("sys.stdin", _io.StringIO(json.dumps(payload)))

    def test_set_global_and_group_and_prompts(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        self._stdin(monkeypatch, {
            "vlm": {"model": "qwen3.5-omni-plus", "base_url": "https://x/v1"},
            "vlm_by_harness": {"claude": {"model": "m-c", "api_key": "sk-new"}},
            "custom_tier1": "自定义 T1",
            "custom_tier2": None,
        })
        out = verbs.vlm_set(cfg)
        assert out["ok"] is True
        assert cfg.vlm.model == "qwen3.5-omni-plus"
        assert cfg.vlm_by_harness["claude"]["model"] == "m-c"
        assert cfg.vlm.custom_tier1 == "自定义 T1"
        assert cfg.vlm.custom_tier2 is None  # null = 恢复默认

    def test_absent_fields_unchanged_and_api_key_blank_keeps_old(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.vlm.api_key = "sk-old"
        self._stdin(monkeypatch, {"vlm": {"model": "m2"}, "vlm_by_harness": {"codex": {"api_key": ""}}})
        verbs.vlm_set(cfg)
        assert cfg.vlm.api_key == "sk-old"  # 未提供/空串 = 不修改（GUI 看不到 key，无法回显）

    def test_masked_placeholder_rejected_as_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        self._stdin(monkeypatch, {"vlm": {"api_key": "●●●●"}})
        out = verbs.vlm_set(cfg)
        assert out["ok"] is False


class TestVlmTest:
    def test_four_modes_use_overrides(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        import base64 as _b64

        calls = []

        class FakeClient:
            def __init__(self, vlm_cfg):
                self.cfg = vlm_cfg

            def describe(self, image, question=None, tier=1, detail=None, prompt_override=None):
                calls.append({"tier": tier, "q": question, "ov": prompt_override, "model": self.cfg.model})
                if detail is not None:
                    detail["prompt"] = prompt_override or "default-prompt"
                    detail["raw"] = "RAW"
                return "红色"

        monkeypatch.setattr(verbs, "_VLMClient", FakeClient)
        payload = {"mode": "tier2", "question": "图里几个字", "custom_prompt": "我的自定义提示词", "harness": "claude"}
        out = verbs.vlm_test(ProxyConfig(), payload=payload)
        assert out["ok"] is True and out["data"]["desc"] == "红色"
        assert calls[0]["ov"] == "我的自定义提示词"
        assert calls[0]["q"] == "图里几个字" and calls[0]["tier"] == 2
```

`tests/test_proxy_vlm.py` 追加：

```python
class TestCustomPrompts:
    def test_custom_tier1_used_when_set(self):
        from vision_relay.config import VLMConfig
        from vision_relay.ir import ImageBlock
        from vision_relay.vlm import VLMClient

        cfg = VLMConfig(api_key="k", custom_tier1="CT1")
        client = VLMClient(cfg)
        detail = {}
        monkey = None
        # 直接验证 _prompt 选择逻辑（不起 HTTP）
        assert client._prompt(None, 1) == "CT1"
        assert client._prompt("q", 2).startswith("Answer the question")  # tier2 未自定义走默认

    def test_prompt_override_wins_all(self):
        from vision_relay.config import VLMConfig
        from vision_relay.vlm import VLMClient

        client = VLMClient(VLMConfig(custom_tier1="CT1"))
        assert client._prompt(None, 1, override="OV") == "OV"
```

- [ ] **Step 2: 跑确认失败** → FAIL

- [ ] **Step 3: 实现**

`config.py` `VLMConfig` 追加两个字段（序列化自动随 `__dict__`）：

```python
    custom_tier1: str | None = None  # 自定义 Tier1 提示词（None=默认；spec §7.4）
    custom_tier2: str | None = None
```

`vlm.py`：`_prompt` 与 `describe` 加 override：

```python
    def _prompt(self, question: str | None, tier: int, override: str | None = None) -> str:
        if override:
            return override
        if tier == 2 and question:
            return self.cfg.custom_tier2 or TIER2_PROMPT.format(q=question)
        return self.cfg.custom_tier1 or TIER1_PROMPT
```

`describe(..., prompt_override: str | None = None)`：`prompt = self._prompt(question, tier, override=prompt_override)`（其余不变，detail 里照记 prompt）。

`verbs.py` 追加：

```python
def vlm_set(cfg: ProxyConfig) -> dict:
    """stdin: {"vlm":{...}, "vlm_by_harness":{h:{...}}, "custom_tier1":str|null, "custom_tier2":str|null}
    规则：缺省字段不修改；api_key 空串/打码占位 = 不修改；custom_tierX null = 恢复默认。"""
    import json
    import sys

    from .locking import config_lock

    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        return envelope(False, {"error": f"invalid stdin json: {exc}"})
    if not isinstance(payload, dict):
        return envelope(False, {"error": "expected a JSON object"})
    MASK = "●●●●"

    def apply(target: dict, updates: dict) -> str | None:
        for k, v in updates.items():
            if k == "api_key" and (v == "" or v == MASK):
                continue  # 不修改
            if v == MASK:
                return f"masked placeholder not allowed for {k}"
            if k in ("custom_tier1", "custom_tier2"):
                continue
            target[k] = v
        return None

    err = apply(cfg.vlm.__dict__, payload.get("vlm") or {})
    if err is None:
        for k in ("custom_tier1", "custom_tier2"):
            if k in payload:
                setattr(cfg.vlm, k, payload[k] or None)
    if err is None:
        for h, over in (payload.get("vlm_by_harness") or {}).items():
            if over is None:
                cfg.vlm_by_harness.pop(h, None)  # null = 改回跟随全局
                continue
            if not isinstance(over, dict):
                err = f"vlm_by_harness[{h}] must be object or null"
                break
            bucket = cfg.vlm_by_harness.setdefault(h, {})
            err = apply(bucket, over) or None
            if err:
                break
    if err is not None:
        return envelope(False, {"error": err})
    from .config import save_config

    with config_lock():
        save_config(cfg)
    return envelope(True, {"saved": True})


def _VLMClient(vlm_cfg):
    from .vlm import VLMClient

    return VLMClient(vlm_cfg)


def vlm_test(cfg: ProxyConfig, payload: dict | None = None) -> dict:
    """与生产同一调用路径的连通测试（spec §6 设置·VLM）。
    payload: {mode: tier1|tier2, question?, custom_prompt?, harness?, image_base64?, media_type?}"""
    import base64
    import time

    payload = payload or {}
    mode = payload.get("mode", "tier1")
    if mode not in ("tier1", "tier2"):
        return envelope(False, {"error": "mode must be tier1|tier2"})
    harness = payload.get("harness")
    merged = cfg.vlm_for(harness) if harness else cfg.vlm
    client = _VLMClient(merged)
    from .ir import ImageBlock

    img_b64 = payload.get("image_base64")
    img = (
        ImageBlock(base64=img_b64, media_type=payload.get("media_type") or "image/png")
        if img_b64
        else ImageBlock(base64=base64.b64encode(b"i").decode(), media_type="image/png")
    )
    detail: dict = {}
    started = time.time()
    try:
        desc = client.describe(
            img,
            question=payload.get("question"),
            tier=2 if mode == "tier2" else 1,
            detail=detail,
            prompt_override=payload.get("custom_prompt"),
        )
    except Exception as exc:  # noqa: BLE001
        reason = getattr(exc, "reason", type(exc).__name__)
        return envelope(False, {"error": str(exc), "reason": reason})
    return envelope(
        True,
        {"desc": desc, "prompt_used": detail.get("prompt"), "model": merged.model, "duration_ms": int((time.time() - started) * 1000)},
    )
```

cli：`vlm-set`（stdin JSON→`verbs.vlm_set`）、`vlm-test`（stdin JSON→`verbs.vlm_test`）两个子命令（`parents=[common]`）+ `_JSON_MAP` 注册。

- [ ] **Step 4: 跑通过 + 回归 + lint + commit**

```bash
python -m pytest -q
git add vision_relay/config.py vision_relay/vlm.py vision_relay/verbs.py vision_relay/cli.py tests/test_proxy_verbs.py tests/test_proxy_vlm.py
git commit -m "feat(verbs): vlm-set (global/groups/custom prompts) + vlm-test sharing production path"
```

---

### Task 3: settings-set / relay-set（停用压制 + 补 key）/ probe --json / models-fetch

**Files:**
- Modify: `vision_relay/config.py`（routing 加 `suppressed_relays: list[str]`）、`vision_relay/reconcile.py`（ensure_tool_relays 跳过压制）、`vision_relay/verbs.py`、`vision_relay/cli.py`
- Test: `tests/test_proxy_verbs.py`

- [ ] **Step 1: 写失败测试（追加）**

```python
class TestSettingsSet:
    def _stdin(self, monkeypatch, payload):
        import io as _io

        monkeypatch.setattr("sys.stdin", _io.StringIO(json.dumps(payload)))

    def test_set_unknown_default_and_vision_log(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        self._stdin(monkeypatch, {"routing": {"unknown_default": "image"}, "vision_log": {"enabled": False, "retention_days": 3}})
        out = verbs.settings_set(cfg)
        assert out["ok"] is True
        assert cfg.routing.unknown_default == "image"
        assert cfg.vision_log.enabled is False and cfg.vision_log.retention_days == 3

    def test_rejects_bad_values(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        self._stdin(monkeypatch, {"routing": {"unknown_default": "movie"}})
        assert verbs.settings_set(ProxyConfig())["ok"] is False


class TestRelaySet:
    def test_suppress_and_unsuppress(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(RelayConfig(name="cc-claude", protocol="anthropic", base_url="http://127.0.0.1:15721", via="cc-switch", models=["*"]))
        self._stdin(monkeypatch, {"name": "cc-claude", "suppressed": True})
        assert verbs.relay_set(cfg)["ok"] is True
        assert cfg.routing.suppressed_relays == ["cc-claude"]
        self._stdin(monkeypatch, {"name": "cc-claude", "suppressed": False})
        verbs.relay_set(cfg)
        assert cfg.routing.suppressed_relays == []

    def test_fill_key_on_direct_relay(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(RelayConfig(name="direct-claude", protocol="anthropic", base_url="https://x", models=["*"]))
        self._stdin(monkeypatch, {"name": "direct-claude", "api_key": "sk-fill"})
        assert verbs.relay_set(cfg)["ok"] is True
        assert cfg.relays[0].api_key == "sk-fill"

    def test_unknown_relay_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        self._stdin(monkeypatch, {"name": "ghost", "suppressed": True})
        assert verbs.relay_set(ProxyConfig())["ok"] is False

    def test_suppressed_relay_not_re_added(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay import wiring
        from vision_relay.tools import ToolState

        cfg = ProxyConfig()
        cfg.routing.suppressed_relays = ["cc-claude"]
        added = wiring.ensure_tool_relays(cfg, [ToolState("cc-switch", 15721, True)])
        assert "cc-claude" not in added and added == ["cc-codex"]


class TestProbeJson:
    def test_probe_verb_envelope(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(verbs, "_run_probe", lambda cfg, h, p, m: "image")
        out = verbs.probe_one(ProxyConfig(), harness="claude", provider="bigmodel", model="m1")
        assert out == {"contract_version": 1, "ok": True, "data": {"result": "image"}}


class TestModelsFetch:
    def test_fetch_lists_model_ids(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(RelayConfig(name="qwen-direct", protocol="chat", base_url="https://dash.example/v1", models=["qwen-*"]))

        class _Resp:
            status_code = 200

            def json(self):
                return {"data": [{"id": "qwen3-coder"}, {"id": "qwen-vl-max"}]}

        monkeypatch.setattr(verbs.httpx, "get", lambda url, **k: _Resp())
        out = verbs.models_fetch(cfg)
        assert out["ok"] is True
        assert out["data"]["providers"]["qwen-direct"] == ["qwen3-coder", "qwen-vl-max"]

    def test_unreachable_provider_reported_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(RelayConfig(name="r", protocol="chat", base_url="https://down.example/v1", models=["*"]))
        import httpx as _hx

        def boom(url, **k):
            raise _hx.ConnectError("down")

        monkeypatch.setattr(verbs.httpx, "get", boom)
        out = verbs.models_fetch(cfg)
        assert out["ok"] is True and out["data"]["providers"]["r"] == []
        assert out["data"]["errors"]["r"]
```

（文件头若缺 `httpx` / `RelayConfig` 导入需补。）

- [ ] **Step 2: 跑确认失败** → FAIL

- [ ] **Step 3: 实现**

`config.py` `RoutingConfig` 追加字段（放 `activated_relays` 之后）：

```python
    suppressed_relays: list[str] = field(default_factory=list)  # 用户停用的自动 relay（压制名单，spec §7.5）
```

`reconcile.py` 的 `ensure_tool_relays` 循环内、`if any(r.name == name for r in cfg.relays): continue` 之后加一行：

```python
        if name in cfg.routing.suppressed_relays:
            continue  # 用户显式停用 > 自动探测（spec §7.5 压制名单）
```

`verbs.py` 追加（顶部补 `import httpx`）：

```python
def settings_set(cfg: ProxyConfig) -> dict:
    """stdin: {"routing": {...白名单键...}, "vision_log": {...}}。白名单外键拒绝。"""
    import json
    import sys

    from .locking import config_lock

    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        return envelope(False, {"error": f"invalid stdin json: {exc}"})
    routing_ok = {"unknown_default"}
    log_ok = {"enabled", "retention_days"}
    r = payload.get("routing") or {}
    v = payload.get("vision_log") or {}
    if not set(r).issubset(routing_ok) or not set(v).issubset(log_ok):
        return envelope(False, {"error": "unsupported settings key"})
    if "unknown_default" in r and r["unknown_default"] not in ("text_only", "image"):
        return envelope(False, {"error": "unknown_default must be text_only|image"})
    if "retention_days" in v and (not isinstance(v["retention_days"], int) or v["retention_days"] < 0):
        return envelope(False, {"error": "retention_days must be a non-negative int"})
    cfg.routing.unknown_default = r.get("unknown_default", cfg.routing.unknown_default)
    if "enabled" in v:
        cfg.vision_log.enabled = bool(v["enabled"])
    if "retention_days" in v:
        cfg.vision_log.retention_days = v["retention_days"]
    from .config import save_config

    with config_lock():
        save_config(cfg)
    return envelope(True, {"saved": True})


def relay_set(cfg: ProxyConfig) -> dict:
    """stdin: {"name", "suppressed": bool} 或 {"name", "api_key": str}（补 key，spec §6 需要你区唯一动作）。"""
    import json
    import sys

    from .locking import config_lock

    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        return envelope(False, {"error": f"invalid stdin json: {exc}"})
    name = payload.get("name")
    relay = next((r for r in cfg.relays if r.name == name), None)
    if relay is None:
        return envelope(False, {"error": f"unknown relay {name!r}"})
    if "suppressed" in payload:
        if payload["suppressed"]:
            if name not in cfg.routing.suppressed_relays:
                cfg.routing.suppressed_relays.append(name)
        else:
            cfg.routing.suppressed_relays = [n for n in cfg.routing.suppressed_relays if n != name]
    if "api_key" in payload:
        key = payload["api_key"]
        if not isinstance(key, str) or not key or key == "●●●●":
            return envelope(False, {"error": "api_key must be a non-empty string"})
        relay.api_key = key
    from .config import save_config

    with config_lock():
        save_config(cfg)
    return envelope(True, {"name": name})


def _run_probe(cfg: ProxyConfig, harness: str, provider: str, model: str) -> str | None:
    from .annotate import run_probe as _rp
    from .cli import _probe_target_for

    base, key, proto = _probe_target_for(cfg, harness, provider, {})
    return _rp(cfg, harness, provider, model, base, key, proto)


def probe_one(cfg: ProxyConfig, harness: str, provider: str, model: str) -> dict:
    result = _run_probe(cfg, harness, provider, model)
    return envelope(result is not None, {"result": result})


def models_fetch(cfg: ProxyConfig) -> dict:
    """可选：从上游 /v1/models 拉模型 ID 清单（spec §5；只补清单，能力以探针/目录为准）。"""
    providers: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    for r in cfg.relays:
        if r.name in cfg.routing.suppressed_relays or not r.base_url or r.base_url.startswith("http://127.0.0.1"):
            continue  # 工具端口两层不拉（清单在工具自己界面上）；只拉直连上游
        url = r.base_url.rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {r.api_key}"} if r.api_key else {}
        try:
            resp = httpx.get(url, headers=headers, timeout=8.0, trust_env=False)
            data = resp.json() if resp.status_code == 200 else {}
            ids = [m.get("id") for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
            if not ids and isinstance(data, list):
                ids = [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]
            providers[r.name] = ids
        except Exception as exc:  # noqa: BLE001 - 单个上游失败不致命
            providers[r.name] = []
            errors[r.name] = str(exc)[:120]
    return envelope(True, {"providers": providers, "errors": errors})
```

cli：`settings-set` / `relay-set` / `models-fetch`（stdin 型，注册 `_JSON_MAP`）；`probe` 子命令补 `parents=[common]`，`--json` 时输出 `verbs.probe_one`（args 透传），非 `--json` 保持现有人类输出。

- [ ] **Step 4: 跑通过 + 回归 + lint + commit**

```bash
python -m pytest -q
git add vision_relay/config.py vision_relay/reconcile.py vision_relay/verbs.py vision_relay/cli.py tests/test_proxy_verbs.py
git commit -m "feat(verbs): settings-set, relay-set (suppress/fill-key), probe --json, models-fetch"
```

---

### Task 4: status 增强（GUI 总览一次拿全）+ setup_state（向导触发）

**Files:**
- Modify: `vision_relay/reconcile.py`（observe 扩展）、`vision_relay/verbs.py`（status 汇总）
- Test: `tests/test_proxy_verbs.py`

- [ ] **Step 1: 写失败测试（追加）**

```python
class TestStatusRich:
    def test_payload_contains_relays_snapshots_setup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.relays.append(RelayConfig(name="cc-claude", protocol="anthropic", base_url="http://127.0.0.1:15721", via="cc-switch", models=["*"]))
        monkeypatch.setattr(verbs, "_observe_for_status", lambda c: {
            "service_alive": False, "routing_on": False,
            "harnesses": {"claude": {"base_url": None, "ownership": "none", "has_snapshot": False}},
            "tools": [],
        })
        out = verbs.status(cfg)
        d = out["data"]
        assert d["relays"][0]["name"] == "cc-claude" and d["relays"][0]["via"] == "cc-switch"
        assert d["vlm"]["configured"] is False and "api_key" not in d["vlm"]
        assert d["setup_state"] == {"has_config": False, "capability_confirmed": False, "vlm_configured": False}
        assert "first_run" in d and d["first_run"] is True

    def test_key_never_in_status(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        cfg.vlm.api_key = "sk-leak"
        monkeypatch.setattr(verbs, "_observe_for_status", lambda c: {"service_alive": False, "routing_on": False, "harnesses": {}, "tools": []})
        assert "sk-leak" not in json.dumps(verbs.status(cfg))
```

- [ ] **Step 2: 跑确认失败** → FAIL

- [ ] **Step 3: 实现**

`verbs.status` 替换为：

```python
def status(cfg: ProxyConfig) -> dict:
    """总览一次拿全：观测 + relay 视图（打码）+ 快照 + vlm 概要 + setup_state（向导触发）。"""
    import os

    from .config import _default_config_path
    from .env_util import config_dir
    from .snapshot import load as load_snapshots

    obs = _observe_for_status(cfg)
    relays = [
        {
            "name": r.name, "protocol": r.protocol, "base_url": r.base_url, "via": r.via,
            "models": r.models, "suppressed": r.name in cfg.routing.suppressed_relays,
            "has_key": bool(r.api_key),
        }
        for r in cfg.relays
    ]
    snaps = load_snapshots()
    obs["relays"] = relays
    obs["snapshots"] = {h: {"base_url": s.base_url, "key_ref": s.key_ref, "model": s.model, "second_hop": s.second_hop, "ts": s.ts} for h, s in snaps.items()}
    obs["vlm"] = {"model": cfg.vlm.model, "base_url": cfg.vlm.base_url, "format": cfg.vlm.format,
                  "custom_prompts": bool(cfg.vlm.custom_tier1 or cfg.vlm.custom_tier2),
                  "groups": sorted(cfg.vlm_by_harness.keys())}
    obs["vlm"]["configured"] = bool(cfg.vlm.api_key)
    has_config = os.path.exists(_default_config_path())
    obs["setup_state"] = {
        "has_config": has_config,
        "capability_confirmed": cfg.routing.capability_confirmed,
        "vlm_configured": bool(cfg.vlm.api_key),
    }
    # spec §6 向导触发：无配置 / 首次确认未置位 / VLM 未配（第①步必填）
    obs["first_run"] = (not has_config) or (not cfg.routing.capability_confirmed) or (not cfg.vlm.api_key)
    return envelope(True, obs)
```

（`_default_config_path` 是 config.py 模块私有——若 ruff 报导入私有，把它在 config.py 提升为 `default_config_path()` 公开函数并同步引用，两处调用点一起改。）

- [ ] **Step 4: 跑通过 + 回归 + lint + commit**

```bash
python -m pytest -q
git add vision_relay/verbs.py vision_relay/config.py tests/test_proxy_verbs.py
git commit -m "feat(verbs): rich status payload — relays/snapshots/vlm summary/setup_state"
```

---

## Phase B —— Tauri 壳

### Task 5: gui/ 脚手架（Vite + React + TS + Tauri 2）

**Files:**
- Create: `gui/`（package.json、vite.config.ts、tsconfig.json、index.html、src/main.tsx、src/App.tsx、src-tauri/ 全套）
- Modify: `.gitignore`

- [ ] **Step 1: 脚手架文件**

`gui/package.json`：

```json
{
  "name": "vision-relay-gui",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "test": "vitest run",
    "tauri": "tauri"
  },
  "dependencies": {
    "@tauri-apps/api": "^2.0.0",
    "react": "^18.3.0",
    "react-dom": "^18.3.0"
  },
  "devDependencies": {
    "@tauri-apps/cli": "^2.0.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "^5.5.0",
    "vite": "^5.4.0",
    "vitest": "^2.0.0"
  }
}
```

`gui/vite.config.ts`：

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
});
```

`gui/tsconfig.json`（含 vitest 类型）与 `gui/index.html`（`<div id="root">` + `/src/main.tsx`）按 Vite React-TS 模板标准内容；`src/main.tsx` 挂载 `<App/>`；`src/App.tsx` 暂时渲染 `<h1>vision-relay GUI</h1>`。

`gui/src-tauri/tauri.conf.json`：

```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "vision-relay",
  "version": "0.1.0",
  "identifier": "com.visionrelay.gui",
  "build": {
    "beforeDevCommand": "pnpm dev",
    "devUrl": "http://localhost:1420",
    "beforeBuildCommand": "pnpm build",
    "frontendDist": "../dist"
  },
  "app": {
    "windows": [{ "title": "vision-relay 控制台", "width": 1100, "height": 760, "minWidth": 960, "minHeight": 640 }],
    "security": { "csp": null }
  },
  "bundle": { "active": false }
}
```

`gui/src-tauri/Cargo.toml`：

```toml
[package]
name = "vision-relay-gui"
version = "0.1.0"
edition = "2021"

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
tauri = { version = "2", features = ["tray-icon"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

`build.rs` 为 `fn main() { tauri_build::build() }`；`src/main.rs` 为：

```rust
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    vision_relay_gui_lib::run()
}
```

`src/lib.rs`（命令先空实现，Task 6 填）：

```rust
use tauri::Manager;

#[tauri::command]
fn ping() -> String { "pong".into() }

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![ping])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

`.gitignore` 追加：

```
gui/node_modules/
gui/dist/
gui/src-tauri/target/
```

- [ ] **Step 2: 安装并验证启动**

```bash
cd gui && pnpm install && pnpm tauri dev
```

Expected: 桌面窗口出现「vision-relay GUI」。`pnpm test`（vitest 无测试时通过）。`pnpm build`（tsc + vite）零错误。

- [ ] **Step 3: commit**

```bash
git add gui .gitignore
git commit -m "feat(gui): Tauri 2 + React + Vite scaffold"
```

---

### Task 6: Rust 命令层（找 core / 跑子进程）+ TS invoke 层

**Files:**
- Modify: `gui/src-tauri/src/lib.rs`
- Create: `gui/src/core.ts`（invoke 层）、`gui/src/core.test.ts`
- Modify: `gui/src/App.tsx`（临时自检页）

- [ ] **Step 1: vitest 先行（纯解析逻辑）`gui/src/core.test.ts`**

```ts
import { describe, expect, it } from "vitest";
import { parseEnvelope, CONTRACT_VERSION } from "./core";

describe("parseEnvelope", () => {
  it("accepts matching contract and returns data", () => {
    expect(parseEnvelope({ contract_version: 1, ok: true, data: { x: 2 } })).toEqual({ x: 2 });
  });
  it("throws on ok:false with error text", () => {
    expect(() => parseEnvelope({ contract_version: 1, ok: false, data: { error: "bad" } })).toThrowError(/bad/);
  });
  it("throws on contract mismatch", () => {
    expect(() => parseEnvelope({ contract_version: 2, ok: true, data: {} })).toThrowError(/contract/);
  });
  it("throws on non-json stdout", () => {
    expect(() => parseEnvelope(JSON.parse('{"contract_version":1,"ok":true}'))).toThrowError(/data/);
  });
});
```

- [ ] **Step 2: 跑确认失败** → `pnpm -C gui test` FAIL（core.ts 不存在）

- [ ] **Step 3: 实现**

`gui/src-tauri/src/lib.rs` 替换为：

```rust
use std::process::{Command, Stdio};
use tauri::Manager;

#[tauri::command]
fn which_core() -> Option<String> {
    // 顺序：用户显式路径（前端传入并缓存） -> PATH 上的 vision-relay(.exe)
    let name = if cfg!(windows) { "vision-relay.exe" } else { "vision-relay" };
    if let Some(dir) = std::env::var_os("PATH") {
        for d in std::env::split_paths(&dir) {
            let cand = d.join(name);
            if cand.is_file() {
                return cand.to_str().map(|s| s.to_string());
            }
        }
    }
    None
}

fn spawn_core(core: &str, args: &[String], stdin: Option<String>) -> Result<String, String> {
    let mut cmd = Command::new(core);
    cmd.args(args)
        .stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env("PYTHONIOENCODING", "utf-8"); // Windows GBK 控制台下强制 UTF-8（spec 风险 4）
    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW | DETACHED_PROCESS);
    }
    let mut child = cmd.spawn().map_err(|e| format!("spawn {core}: {e}"))?;
    if let Some(data) = stdin {
        use std::io::Write;
        if let Some(mut si) = child.stdin.take() {
            let _ = si.write_all(data.as_bytes());
            let _ = si.flush();
        }
    }
    let out = child.wait_with_output().map_err(|e| e.to_string())?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    if stdout.trim().is_empty() {
        let stderr = String::from_utf8_lossy(&out.stderr).to_string();
        return Err(format!("core produced no output; stderr: {stderr}"));
    }
    Ok(stdout)
}

#[tauri::command]
fn run_core(core_path: String, args: Vec<String>, stdin: Option<String>) -> Result<String, String> {
    spawn_core(&core_path, &args, stdin)
}

#[tauri::command]
fn start_core_detached(core_path: String) -> Result<(), String> {
    let mut cmd = Command::new(core_path);
    cmd.args(["start", "--detach"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .env("PYTHONIOENCODING", "utf-8");
    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        unsafe { cmd.pre_exec(|| { let _ = nix_like_setsid(); Ok(()) }) };
    }
    cmd.spawn().map(|_| ()).map_err(|e| e.to_string())
}

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![which_core, run_core, start_core_detached])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

**实现注意（Windows 优先，别死在 unix 分支）**：`nix_like_setsid` 并不存在——unix 的 `pre_exec` 里应直接 `use std::os::unix::process::CommandExt; unsafe { cmd.process_group(0) }`（Rust 1.64+ 稳定 API），删除 `pre_exec` 写法。最终 unix 分支为：

```rust
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
```

`which_core` 首段的 `next_back().map(...)` 写法臃肿且有误——直接 `if let Some(dir) = std::env::var_os("PATH")` 再 split_paths 即可，实现时按此简化。

`gui/src/core.ts`：

```ts
import { invoke } from "@tauri-apps/api/core";

export const CONTRACT_VERSION = 1;

export interface Envelope<T = unknown> {
  contract_version: number;
  ok: boolean;
  data: T;
}

export function parseEnvelope<T>(raw: unknown): T {
  if (typeof raw !== "object" || raw === null) throw new Error("core output is not JSON");
  const e = raw as Envelope;
  if (e.contract_version !== CONTRACT_VERSION)
    throw new Error(`contract mismatch: core=${e.contract_version}, gui=${CONTRACT_VERSION}（请升级另一侧）`);
  if (!e.ok) throw new Error((e.data as { error?: string })?.error ?? "core verb failed");
  if (!("data" in e)) throw new Error("envelope missing data");
  return e.data as T;
}

let corePath: string | null = null;

export async function detectCore(explicit?: string | null): Promise<string> {
  if (explicit) {
    corePath = explicit;
    return explicit;
  }
  if (corePath) return corePath;
  const found = await invoke<string | null>("which_core");
  if (!found) throw new Error("找不到 vision-relay 核心：请在设置里指定路径，或确认已 pip install 并在 PATH");
  corePath = found;
  return found;
}

export function setCorePath(p: string | null): void {
  corePath = p;
}

export async function core<T = unknown>(verb: string, opts: { args?: string[]; stdin?: unknown } = {}): Promise<T> {
  const path = await detectCore();
  const args = [verb, "--json", ...(opts.args ?? [])];
  const stdin = opts.stdin !== undefined ? JSON.stringify(opts.stdin) : null;
  const out = await invoke<string>("run_core", { corePath: path, args, stdin });
  return parseEnvelope<T>(JSON.parse(out));
}

export async function startService(): Promise<void> {
  const path = await detectCore();
  await invoke("start_core_detached", { corePath: path });
}

export async function stopService(): Promise<void> {
  // stop 是生命周期命令（不加载配置、PID 直杀），输出人类可读文本而非 envelope——直接跑子进程、忽略输出
  const path = await detectCore();
  await invoke("run_core", { corePath: path, args: ["stop"], stdin: null });
}
```

`App.tsx` 临时自检：按钮各调 `which_core`/`status`，显示结果（Task 8 重写）。

- [ ] **Step 4: 验证**：`pnpm -C gui test` PASS；`pnpm -C gui tauri dev` 里点按钮能看到 status envelope（core 在 PATH 时）或友好错误。

- [ ] **Step 5: commit**

```bash
git add gui/src-tauri/src/lib.rs gui/src/core.ts gui/src/core.test.ts gui/src/App.tsx
git commit -m "feat(gui): rust command layer (core discovery/subprocess) + typed invoke client"
```

---

### Task 7: App 壳（5 页导航 / 状态轮询 / 路由总开关 / 关闭确认 / 托盘）

**Files:**
- Create: `gui/src/shell/*`（Nav、useStatus、CloseGuard、tray 注册）、`gui/src/i18n.ts`、`gui/src/styles.css`
- Modify: `gui/src/App.tsx`、`gui/src-tauri/src/lib.rs`（托盘 + 关闭事件）

- [ ] **Step 1: `styles.css`** —— 从 `docs/superpowers/specs/gui-mockups/index.html` 的 `<style>` 整体移植（类名一一对应：`.side/.side-item/.card/.tag/.chain/.hop/.arrow/.pin/.cap/.switch…`），另加 `.app{display:flex;height:100vh}` 与滚动条样式。**视觉基准 = mockup，不许自创风格。**

- [ ] **Step 2: `gui/src/i18n.ts`**

```ts
export type Lang = "zh" | "en";
const dict = {
  zh: { overview: "总览", models: "模型能力", visionlog: "识图记录", events: "事件日志", settings: "设置",
        routingOn: "路由开启", routingOff: "路由已关", refresh: "刷新", diag: "诊断报告", /* …按页面补全 */ },
  en: { overview: "Overview", models: "Models", visionlog: "Vision Log", events: "Events", settings: "Settings",
        routingOn: "Routing ON", routingOff: "Routing OFF", refresh: "Refresh", diag: "Diagnostics", /* … */ },
} as const;

export function initialLang(): Lang {
  const saved = localStorage.getItem("vr.lang");
  if (saved === "zh" || saved === "en") return saved;
  return navigator.language.startsWith("zh") ? "zh" : "en";
}
export function t(lang: Lang, key: keyof typeof dict.zh): string {
  return dict[lang][key] ?? dict.zh[key];
}
```

（后续页面文案键随任务逐个补进 dict，两语言同步补，不许只写中文。）

- [ ] **Step 3: `useStatus` 轮询 hook（`gui/src/shell/useStatus.ts`）**

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import { core } from "../core";

export interface HarnessRow { base_url: string | null; ownership: string; has_snapshot: boolean }
export interface ToolRow { name: string; port: number; online: boolean; active_provider: string | null; provider_base_url: string | null }
export interface RelayRow { name: string; protocol: string; base_url: string; via: string | null; models: string[]; suppressed: boolean; has_key: boolean }
export interface SnapshotRow { base_url: string; key_ref: string; model: string; second_hop: string | null; ts: number }
export interface StatusData {
  service_alive: boolean; routing_on: boolean;
  harnesses: Record<string, HarnessRow>;
  tools: ToolRow[];
  relays: RelayRow[];
  snapshots: Record<string, SnapshotRow>;
  vlm: { model: string; base_url: string; format: string; configured: boolean; custom_prompts: boolean; groups: string[] };
  setup_state: { has_config: boolean; capability_confirmed: boolean; vlm_configured: boolean };
  first_run: boolean;
}

export function useStatus(intervalMs = 5000) {
  const [status, setStatus] = useState<StatusData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await core<StatusData>("status"));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    timer.current = window.setInterval(refresh, intervalMs);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [refresh, intervalMs]);

  return { status, error, refresh };
}
```

- [ ] **Step 4: 路由总开关（`gui/src/shell/RoutingToggle.tsx`）**

```tsx
import { useState } from "react";
import { startService, stopService } from "../core";

export function RoutingToggle(props: { on: boolean; onChangeDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const toggle = async () => {
    setBusy(true);
    try {
      if (props.on) await stopService();
      else await startService();
      setTimeout(props.onChangeDone, 1200); // 分离启动后给核心一点起服务时间再刷新
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="switch">
      <span className="dim">路由关闭</span>
      <div className={"track" + (props.on ? "" : " off")} onClick={busy ? undefined : toggle}>
        <div className="knob" />
      </div>
      <b style={{ color: props.on ? "#059669" : "#6b7280" }}>{props.on ? "路由开启" : "路由已关"}</b>
    </div>
  );
}
```

- [ ] **Step 5: 关闭确认（`gui/src/shell/CloseGuard.tsx`）**

```tsx
import { useEffect, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";

export function CloseGuard() {
  const [asking, setAsking] = useState(false);
  const [remember, setRemember] = useState(localStorage.getItem("vr.close") === "stop");

  useEffect(() => {
    const unlisten = getCurrentWindow().onCloseRequested(async (event) => {
      const mode = localStorage.getItem("vr.close");
      if (mode === "ui") return; // 记住了"仅关界面"：直接放行
      event.preventDefault();
      if (mode === "stop") { await import("../core").then((m) => m.stopService().catch(() => {})); return; }
      setAsking(true);
    });
    return () => { unlisten.then((f) => f()); };
  }, []);

  if (!asking) return null;
  const decide = async (stopServiceToo: boolean) => {
    if (remember) localStorage.setItem("vr.close", stopServiceToo ? "stop" : "ui");
    if (stopServiceToo) await import("../core").then((m) => m.stopService().catch(() => {}));
    await getCurrentWindow().destroy();
  };
  return (
    <div className="modal show">
      <div className="modal-box">
        <b>关闭 vision-relay 控制台？</b>
        <div className="dim" style={{ margin: "10px 0" }}>服务在后台继续运行，可从托盘或本界面重新打开。</div>
        <label><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> 记住我的选择</label>
        <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
          <button className="btn" onClick={() => decide(false)}>仅关闭界面（服务继续）</button>
          <button className="btn red" onClick={() => decide(true)}>关闭界面并停止服务</button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: 托盘（`gui/src-tauri/src/lib.rs` 的 `run()` 扩展）**

```rust
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;

fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let open = MenuItem::with_id(app, "open", "打开主界面", true, None::<&str>)?;
            let toggle = MenuItem::with_id(app, "toggle", "路由：开/关", true, None::<&str>)?;
            let diag = MenuItem::with_id(app, "diag", "诊断报告", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出（停止服务）", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &toggle, &diag, &quit])?;
            TrayIconBuilder::with_id("main")
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .on_menu_event(|app, event| {
                    match event.id().as_ref() {
                        "open" => { if let Some(w) = app.get_webview_window("main") { let _ = w.show(); let _ = w.set_focus(); } }
                        "toggle" | "diag" => { if let Some(w) = app.get_webview_window("main") { let _ = w.emit("tray", event.id().as_ref()); let _ = w.show(); } }
                        "quit" => { let _ = app.handle().plugin(std::collections::HashMap::<(),()>::new()); app.exit(0); }
                        _ => {}
                    }
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close(); // 关闭语义交给前端 CloseGuard 决定（隐藏 or 退出）
                let _ = window.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![which_core, run_core, start_core_detached])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

**实现注意**：Tauri 2 的 tray/menu API 在小版本间有签名差异——以 `cargo doc`/编译器提示为准做等价调整（例如 `MenuItem::with_id` 的泛型参数、`emit` 需要 `use tauri::Emitter`）。`quit` 分支那行 `app.handle().plugin(...)` 是错的：正确做法是先经 `run_core` 语义跑 `vision-relay stop`（在 Rust 里直接 `spawn_core(core, &["stop".into(), "--json".into()], None)`，core 路径从前端启动时经 `tauri::State` 传入或存 `app.manage(core_path)`），然后 `app.exit(0)`。**托盘退出必须真的停服务**（spec：退出=停止服务）。

- [ ] **Step 7: `App.tsx` 壳（路由 + 导航 + 托盘事件转发）**

```tsx
import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { initialLang, t, Lang } from "./i18n";
import { useStatus } from "./shell/useStatus";
import { RoutingToggle } from "./shell/RoutingToggle";
import { CloseGuard } from "./shell/CloseGuard";
import { Overview } from "./pages/Overview";
import { ModelsPage } from "./pages/Models";
import { VisionLogPage } from "./pages/VisionLog";
import { EventsPage } from "./pages/Events";
import { SettingsPage } from "./pages/Settings";

export default function App() {
  const [page, setPage] = useState("overview");
  const [lang, setLang] = useState<Lang>(initialLang());
  const { status, error, refresh } = useStatus();
  const [showDiag, setShowDiag] = useState(false);

  useEffect(() => listen("tray", (e) => {
    if (e.payload === "toggle") setPage("overview");
    if (e.payload === "diag") setShowDiag(true);
  }).then((f) => f), []);

  const nav: [string, string][] = [["overview", "overview"], ["models", "models"], ["visionlog", "visionlog"], ["events", "events"], ["settings", "settings"]];
  return (
    <div className="app">
      <div className="side">
        <div className="logo">👁 vision-relay</div>
        {nav.map(([id, key]) => (
          <div key={id} className={"side-item" + (page === id ? " active" : "")} onClick={() => setPage(id)}>
            {t(lang, key as never)}
          </div>
        ))}
      </div>
      <div className="main">
        {error && <div className="alert-err">核心不可用：{error}</div>}
        {page === "overview" && <Overview status={status} refresh={refresh} lang={lang} showDiag={showDiag} setShowDiag={setShowDiag} />}
        {page === "models" && <ModelsPage lang={lang} refresh={refresh} />}
        {page === "visionlog" && <VisionLogPage lang={lang} />}
        {page === "events" && <EventsPage lang={lang} />}
        {page === "settings" && <SettingsPage lang={lang} status={status} refresh={refresh} setLang={setLang} />}
      </div>
      <RoutingTogglePlaceholder /> {/* 见下：实际 RoutingToggle 放 Overview 状态横幅内，此处移除 */}
      <CloseGuard />
    </div>
  );
}
```

（`RoutingTogglePlaceholder` 是组装提示——**实现时删除**，总开关只出现在总览状态横幅，spec §6。）

页面文件先建空壳（`export function Overview(props: any) { return <div className="card">TODO in later task</div>; }` 之类），后续任务逐个替换——空壳里的 `TODO` 仅限本 Task 的临时占位，Task 8–13 必须全部替换掉。

- [ ] **Step 8: 验证 + commit**：`pnpm -C gui tauri dev`：五页可切换、轮询状态、关闭弹确认且记住生效、托盘四项可用（退出真的停服务）。

```bash
git add gui/src gui/src-tauri
git commit -m "feat(gui): app shell — nav, polling, routing toggle, close guard, tray"
```

---

## Phase C —— 五页 + 向导

### Task 8: 总览页（状态横幅 / 拓扑卡动态链 / 详情抽屉 / 自动处理·需要你 / 诊断报告弹层）

**Files:**
- Create: `gui/src/pages/Overview.tsx`、`gui/src/lib/chain.ts`、`gui/src/lib/chain.test.ts`

- [ ] **Step 1: vitest 先行（链路状态机纯函数）`gui/src/lib/chain.test.ts`**

```ts
import { describe, expect, it } from "vitest";
import { chainHops } from "./chain";

const tool = { name: "cc-switch", port: 15721, online: true, active_provider: "bigmodel", provider_base_url: "https://open.example" };

describe("chainHops", () => {
  it("two-hop when routing on and tool online", () => {
    const hops = chainHops({ base_url: "http://127.0.0.1:8787", ownership: "ours", has_snapshot: true }, "claude", tool, true, 8787);
    expect(hops.map((h) => h.bypass)).toEqual([false, false, false]);
    expect(hops[1].label).toContain("8787");
    expect(hops[2].label).toContain("cc-switch");
  });
  it("relay bypassed when routing off, tool still active", () => {
    const hops = chainHops({ base_url: null, ownership: "none", has_snapshot: false }, "claude", tool, false, 8787);
    expect(hops[0].bypass).toBe(false);       // harness
    expect(hops[1].bypass).toBe(true);        // vision-relay 置灰
    expect(hops[2].bypass).toBe(false);       // 工具仍在线
    expect(hops[2].arrow).toContain("直连");
  });
  it("both bypassed when routing off and tool offline", () => {
    const hops = chainHops({ base_url: null, ownership: "none", has_snapshot: false }, "claude", { ...tool, online: false }, false, 8787);
    expect(hops[1].bypass).toBe(true);
    expect(hops[2].bypass).toBe(true);
    expect(hops[3].arrow).toContain("真实上游");
  });
  it("no tool harness is single-hop direct", () => {
    const hops = chainHops({ base_url: "http://127.0.0.1:8787", ownership: "ours", has_snapshot: true }, "qwen-code", null, true, 8787);
    expect(hops).toHaveLength(3); // qwen -> relay -> upstream
  });
});
```

- [ ] **Step 2: 跑确认失败** → FAIL

- [ ] **Step 3: `gui/src/lib/chain.ts`**

```ts
import type { HarnessRow, ToolRow } from "../shell/useStatus";

export interface Hop { label: string; arrow: string; bypass: boolean; sub?: string }

export function chainHops(row: HarnessRow, harness: string, tool: ToolRow | null, routingOn: boolean, port: number): Hop[] {
  const relayHop: Hop = { label: `👁 vision-relay :${port}`, arrow: "↓ base_url", bypass: !routingOn };
  if (!tool) {
    return [
      { label: harnessLabel(harness), arrow: "", bypass: false },
      relayHop,
      { label: "☁️ 直连真实上游", arrow: routingOn ? "↓ relay（一层直连）" : "↓ 直连真实上游", bypass: false },
    ];
  }
  const toolHop: Hop = {
    label: `🔀 ${tool.name} :${tool.port}`,
    arrow: "",
    bypass: !routingOn || !tool.online,
    sub: tool.active_provider ? `供应商 ${tool.active_provider}（${tool.provider_base_url ?? "未知"}）` : undefined,
  };
  const lastArrow = routingOn
    ? (tool.online ? "↓ relay（两层）" : "↓ relay（回落一层直连）")
    : (tool.online ? `↓ 直连 :${tool.port}` : "↓ 直连真实上游");
  return [
    { label: harnessLabel(harness), arrow: "", bypass: false },
    relayHop,
    toolHop,
    { label: "☁️ 真实上游", arrow: lastArrow, bypass: false, sub: tool.provider_base_url ?? "由工具决定（未知）" },
  ];
}

export function harnessLabel(h: string): string {
  return { claude: "🤖 Claude Code", codex: "💻 Codex", "qwen-code": "❓ Qwen Code" }[h] ?? h;
}

export function toolFor(harness: string, tools: ToolRow[]): ToolRow | null {
  return tools.find((t) => (t.name === "cc-switch" ? ["claude", "codex"] : ["codex"]).includes(harness) && t.online) ?? null;
}
```

- [ ] **Step 4: `Overview.tsx`**（结构对齐 mockup p1：状态横幅[点+标题+RoutingToggle+刷新+诊断入口] / 三卡[chain + 详情抽屉] / 自动处理区[events 过滤 reclaim|absorb|auto_fix|auto_annotate] / 需要你区[diagnose needs_you] / 诊断报告 modal[只读：已自动修复 actions + 正常项 + 需你项]）

```tsx
import { useEffect, useMemo, useState } from "react";
import { core } from "../core";
import { RoutingToggle } from "../shell/RoutingToggle";
import { chainHops, toolFor } from "../lib/chain";
import type { StatusData } from "../shell/useStatus";

interface EventRow { ts: number; type: string; harness: string | null; [k: string]: unknown }
interface DiagReport { actions: { type: string; harness?: string; fix?: string; ok?: boolean }[]; needs_you: { type: string; harness?: string; hint?: string }[]; observed: StatusData }

export function Overview(p: { status: StatusData | null; refresh: () => void; lang: string; showDiag: boolean; setShowDiag: (b: boolean) => void }) {
  const [events, setEvents] = useState<EventRow[]>([]);
  const [diag, setDiag] = useState<DiagReport | null>(null);
  const [drawer, setDrawer] = useState<string | null>(null);

  const loadEvents = async () => { try { const rows = await core<EventRow[]>("events"); setEvents(rows.slice(-20).reverse()); } catch { /* 被动 */ } };
  useEffect(() => { loadEvents(); const t = setInterval(loadEvents, 8000); return () => clearInterval(t); }, []);
  useEffect(() => { if (p.showDiag) runDiag(); }, [p.showDiag]);

  const runDiag = async () => {
    const d = await core<DiagReport>("diagnose");
    setDiag(d); p.refresh(); loadEvents();
  };
  const auto = useMemo(() => events.filter((e) => ["reclaim", "absorb", "auto_fix", "auto_annotate"].includes(e.type)), [events]);
  const needsYou = diag?.needs_you ?? autoNeedsYou(p.status);

  if (!p.status) return <div className="card">加载中…（核心不可用时见顶部错误）</div>;
  const s = p.status;
  return (
    <>
      <div className="card row between">
        <div>
          <div className="row">
            <span className={"dot " + (s.service_alive ? "g" : "r")} />
            <b style={{ fontSize: 15 }}>{s.service_alive ? "服务运行中" : "服务已停止"}</b>
            <span className="dim">127.0.0.1:8787 · 自动对账中</span>
          </div>
        </div>
        <RoutingToggle on={s.routing_on && s.service_alive} onChangeDone={p.refresh} />
        <button className="btn lg" onClick={p.refresh}>🔄 刷新（便捷动作）</button>
        <button className="btn lg" onClick={() => p.setShowDiag(true)}>📋 诊断报告</button>
      </div>

      <div className="cols3">
        {Object.keys(s.harnesses).map((h) => {
          const tool = toolFor(h, s.tools);
          const hops = chainHops(s.harnesses[h], h, tool, s.routing_on && s.service_alive, 8787);
          const snap = s.snapshots[h];
          return (
            <div className="card" key={h}>
              <div className="row between"><b>{h}</b><span className={"tag " + (s.harnesses[h].ownership === "ours" ? "ok" : "gray")}>{s.harnesses[h].ownership === "ours" ? "✓ 已接管" : s.harnesses[h].ownership}</span></div>
              <div className="chain">
                {hops.map((hop, i) => (
                  <div key={i}>
                    {hop.arrow && <div className="arrow">{hop.arrow}</div>}
                    <div className={"hop" + (hop.bypass ? " bypass" : "")}>{hop.label}{hop.bypass ? <span className="bz">已旁路</span> : null}</div>
                    {hop.sub && <div className="small dim">{hop.sub}</div>}
                  </div>
                ))}
              </div>
              <a href="#" className="small dim" onClick={(e) => { e.preventDefault(); setDrawer(drawer === h ? null : h); }}>
                {drawer === h ? "▾" : "▸"} 详情：快照 · relay · 停用
              </a>
              {drawer === h && (
                <table>
                  <tbody>
                    <tr><td className="dim small">接管快照</td><td className="mono small">{snap ? `${snap.base_url} · ${snap.key_ref} · ${snap.model}` : "无"}</td></tr>
                    {s.relays.filter((r) => r.via || r.name.startsWith("direct-")).slice(0, 4).map((r) => (
                      <tr key={r.name}><td className="dim small">relay</td><td className="small">
                        {r.name} → {r.base_url} {r.suppressed ? <span className="tag gray">已停用</span> : null}
                        {!r.has_key && r.name.startsWith("direct-") ? <button className="btn" onClick={() => fillKey(r.name)}>🔑 补填 key</button> : null}
                        <button className="btn" onClick={() => toggleRelay(r.name, r.suppressed)}>{r.suppressed ? "恢复" : "停用转发"}</button>
                      </td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          );
        })}
      </div>

      <div className="card">
        <h3>自动处理（无需你操作）</h3>
        {auto.length === 0 && <div className="dim small" style={{ padding: 4 }}>最近无自动动作</div>}
        {auto.slice(0, 5).map((e, i) => (
          <div className="alert-ok row between" key={i}>
            <span>✅ {new Date(e.ts * 1000).toLocaleTimeString()} {e.harness ?? ""} {eventText(e)}</span>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>⚠ 需要你</h3>
        {needsYou.length === 0 && <div className="dim small" style={{ padding: 4 }}>无待办</div>}
        {needsYou.map((n, i) => (
          <div className="alert-err row between" key={i}><span>🔑 {n.harness ?? ""} {n.hint ?? ""}</span></div>
        ))}
      </div>

      {p.showDiag && diag && (
        <div className="modal show" onClick={() => p.setShowDiag(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="row between"><b style={{ fontSize: 15 }}>📋 诊断报告（自动运行，只读）</b><button className="btn" onClick={() => p.setShowDiag(false)}>✕</button></div>
            <div className="alert-ok">✅ 已自动修复：{diag.actions.map((a) => `${a.harness ?? ""} ${a.type}${a.fix ? `(${a.fix})` : ""}`).join(" · ") || "（本次无）"}</div>
            <table><tbody>
              <tr><td>✓ 服务进程 / 端口</td><td style={{ textAlign: "right", color: "#059669" }}>{diag.observed.service_alive ? "运行中" : "未运行"}</td></tr>
              {diag.observed.tools.map((t) => (
                <tr key={t.name}><td>✓ 工具 {t.name} :{t.port}</td><td style={{ textAlign: "right", color: t.online ? "#059669" : "#b91c1c" }}>{t.online ? `在线${t.active_provider ? " · " + t.active_provider : ""}` : "离线"}</td></tr>
              ))}
            </tbody></table>
            {diag.needs_you.map((n, i) => <div className="alert-err" key={i}>⚠ {n.harness} {n.hint}</div>)}
          </div>
        </div>
      )}
    </>
  );
}

function autoNeedsYou(s: StatusData | null) {
  if (!s) return [];
  return s.relays.filter((r) => r.name.startsWith("direct-") && !r.has_key)
    .map((r) => ({ type: "missing_key", harness: r.name, hint: "直连上游缺 API key" }));
}
function eventText(e: EventRow): string {
  const m: Record<string, string> = { reclaim: "漂移已自动抢回", absorb: "新上游已吸收接管", auto_fix: "已自动修复", auto_annotate: "新模型已自动标注", relay_added: "已生成工具转发" };
  return m[e.type] ?? e.type;
}
async function fillKey(name: string): Promise<void> {
  const key = window.prompt(`为 ${name} 粘贴 API key（不会回显）：`);
  if (!key) return;
  await core("relay-set", { stdin: { name, api_key: key } });
}
async function toggleRelay(name: string, suppressed: boolean): Promise<void> {
  await core("relay-set", { stdin: { name, suppressed: !suppressed } });
}
```

- [ ] **Step 5: 验证 + commit**：`pnpm -C gui test` 通过（chain 单测）；dev 里开关路由、切换工具在线态（真实开关 CC Switch）、抽屉、诊断弹层可用。

```bash
git add gui/src/pages/Overview.tsx gui/src/lib
git commit -m "feat(gui): overview — status banner, dynamic chains, drawers, auto/needs-you areas, diag modal"
```

---

### Task 9: 模型能力页（三元组表 + 切换/保存 + 行内重测 + 探测全部 + 拉清单）

**Files:**
- Create: `gui/src/pages/Models.tsx`

- [ ] **Step 1: 实现**（表格列：harness / provider / 模型 / 当前标注[三态 chip] / 依据[source+probe_cached] / 实测[probe_cached 或未测] / 操作[切换+重测]；顶部提示条 + 「🔍 探测全部未测」「⬇ 从上游拉取模型清单」；页底「保存修改」——本地 dirty 集合，保存时一次 `models-set` stdin 数组）

```tsx
import { useEffect, useState } from "react";
import { core } from "../core";

interface Triple { harness: string; provider: string; model: string; value: string | null; source: string | null; probe_cached: string | null }
type Draft = Record<string, string | null>; // key "h|p|m" -> value(null=未标注)

export function ModelsPage(p: { lang: string; refresh: () => void }) {
  const [rows, setRows] = useState<Triple[]>([]);
  const [draft, setDraft] = useState<Draft>({});
  const [busy, setBusy] = useState("");

  const load = async () => { setRows((await core<{ models: Triple[] }>("models-scan")).models); setDraft({}); };
  useEffect(() => { load(); }, []);

  const key = (r: Triple) => `${r.harness}|${r.provider}|${r.model}`;
  const effective = (r: Triple) => (key(r) in draft ? draft[key(r)] : r.value);
  const dirty = rows.filter((r) => key(r) in draft && draft[key(r)] !== r.value);

  const save = async () => {
    await core("models-set", { stdin: dirty.map((r) => ({ harness: r.harness, provider: r.provider, model: r.model, value: draft[key(r)] })) });
    await load(); p.refresh();
  };
  const retest = async (r: Triple) => {
    setBusy(r.model);
    try { await core("probe", { args: ["--harness", r.harness, "--provider", r.provider, "--model", r.model] }); await load(); } finally { setBusy(""); }
  };
  const probeAll = async () => { setBusy("all"); try { await core("probe", { args: ["--all-untested"] }); await load(); } finally { setBusy(""); } };
  const fetchList = async () => {
    const d = await core<{ providers: Record<string, string[]>; errors: Record<string, string> }>("models-fetch");
    const ids = Object.values(d.providers).flat();
    window.alert(ids.length ? `拉到 ${ids.length} 个模型 ID（能力以探针/目录为准）：\n${ids.slice(0, 30).join("\n")}${ids.length > 30 ? "\n…" : ""}` : "未拉到清单：" + JSON.stringify(d.errors));
  };
  const cycle = (v: string | null) => (v === null ? "text_only" : v === "text_only" ? "image" : null); // 三态循环：未标注→纯文本→图片→未标注

  return (
    <>
      <div className="alert-ok">
        ✅ 按 (harness · provider · 模型) 三元组标注，记录只有输入模态一个字段（纯文本 / 支持图片 / 未标注）。只在猜错时改；未标注模型运行时按设置开关（默认走识图）。
      </div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <button className="btn" disabled={!!busy} onClick={probeAll}>{busy === "all" ? "探测中…" : "🔍 探测全部未测"}</button>
        <button className="btn" onClick={fetchList}>⬇ 从上游拉取模型清单（可选）</button>
      </div>
      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>harness</th><th>provider</th><th>模型</th><th>当前标注</th><th>依据</th><th>实测</th><th>操作</th></tr></thead>
          <tbody>
            {rows.map((r, i) => {
              const v = effective(r);
              return (
                <tr key={i} style={{ background: key(r) in draft ? "#fffbeb" : undefined }}>
                  <td>{r.harness}</td><td>{r.provider}</td><td>{r.model}</td>
                  <td>{v === null ? <span className="tag gray">未标注（空）</span> : v === "image" ? <span className="cap image">支持图片</span> : <span className="cap text">纯文本</span>}</td>
                  <td className="dim small">{r.source ?? "—"}{r.probe_cached ? `（缓存 ${r.probe_cached}）` : ""}</td>
                  <td className="small">{r.probe_cached === "image" ? "✓ 答对" : r.probe_cached === "text_only" ? "✗ 报错/吞图" : <span className="tag gray">未测</span>}</td>
                  <td>
                    <a href="#" onClick={(e) => { e.preventDefault(); setDraft({ ...draft, [key(r)]: cycle(v) }); }}>切换</a>
                    {" · "}<a href="#" onClick={(e) => { e.preventDefault(); retest(r); }}>{busy === r.model ? "探测中…" : "重测"}</a>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <span className="dim small">{dirty.length ? `${dirty.length} 处未保存` : "无未保存修改"}</span>
        <button className="btn primary" disabled={!dirty.length} onClick={save}>保存修改</button>
      </div>
    </>
  );
}
```

- [ ] **Step 2: 验证 + commit**：dev 里改两行保存 → `vision-relay models-scan --json` 复核 value/source=user；重测按钮更新 probe_cached。

```bash
git add gui/src/pages/Models.tsx
git commit -m "feat(gui): models page — tri-state table, save, per-row retest, probe-all, fetch list"
```

---

### Task 10: 识图记录页（harness→会话分组 + 三段明细）

**Files:**
- Create: `gui/src/pages/VisionLog.tsx`、`gui/src/lib/grouping.ts`、`gui/src/lib/grouping.test.ts`

- [ ] **Step 1: vitest 先行 `grouping.test.ts`**

```ts
import { describe, expect, it } from "vitest";
import { groupRecords } from "./grouping";

const rec = (h: string, session: string | null, ts: number) => ({ ts, harness: h, session, tier: 1, prompt: "p", raw: "r", injected: "i", duration_ms: 1, cache_hit: false, image_hash: "x", vlm_model: "m", question: null });

describe("groupRecords", () => {
  it("groups by harness then session, null session last, desc by ts", () => {
    const g = groupRecords([rec("claude", "s1", 3), rec("claude", null, 1), rec("codex", "s2", 2), rec("claude", "s1", 4)]);
    expect(g.map((x) => x.harness)).toEqual(["claude", "codex"]);
    expect(g[0].sessions.map((s) => s.session)).toEqual(["s1", null]);
    expect(g[0].sessions[0].records.map((r) => r.ts)).toEqual([4, 3]);
  });
});
```

- [ ] **Step 2: 实现 `grouping.ts`**（`groupRecords(rows): {harness, sessions: {session, records}[]}[]`，session null 显示「未识别会话」）

```ts
export interface Rec { ts: number; harness: string; session: string | null; [k: string]: unknown }
export interface Group { harness: string; sessions: { session: string; records: Rec[] }[] }

export function groupRecords(rows: Rec[]): Group[] {
  const byH = new Map<string, Map<string, Rec[]>>();
  for (const r of [...rows].sort((a, b) => b.ts - a.ts)) {
    const sessions = byH.get(r.harness) ?? new Map();
    const k = r.session ?? "";
    (sessions.get(k) ?? sessions.set(k, []).get(k)!).push(r);
    byH.set(r.harness, sessions);
  }
  return [...byH.entries()].map(([harness, sessions]) => ({
    harness,
    sessions: [...sessions.entries()]
      .sort((a, b) => (a[0] === "" ? 1 : b[0] === "" ? -1 : b[1][0].ts - a[1][0].ts))
      .map(([session, records]) => ({ session: session || "未识别会话", records })),
  })).sort((a, b) => b.sessions[0].records[0].ts - a.sessions[0].records[0].ts);
}
```

- [ ] **Step 3: `VisionLog.tsx`**：左侧分组树（harness → 会话，含计数），右侧记录表（时间/层级/提示词[默认|自定义]/缓存/耗时/VLM）+ 选中行下方三段明细卡（①提示词 ②VLM 原始返回 ③实际注入文本，颜色区分同 mockup p6：蓝/紫/绿）；数据来自 `core("visionlog", {args:["--json"]})`（M1 的 visionlog verb 返回 records 数组）；空态显示「暂无识图记录（留痕可在设置关闭）」。

```tsx
import { useEffect, useState } from "react";
import { core } from "../core";
import { groupRecords } from "../lib/grouping";
import type { Rec } from "../lib/grouping";

export function VisionLogPage(p: { lang: string }) {
  const [rows, setRows] = useState<Rec[]>([]);
  const [sel, setSel] = useState<Rec | null>(null);
  useEffect(() => { core<Rec[]>("visionlog").then(setRows).catch(() => {}); }, []);
  const groups = groupRecords(rows);
  return (
    <div style={{ display: "flex", gap: 10 }}>
      <div style={{ width: 200, flexShrink: 0 }}>
        <div className="card" style={{ padding: 10 }}>
          <b className="small">按 harness → 会话</b>
          {groups.map((g) => (
            <div key={g.harness} style={{ marginTop: 6, fontSize: 12 }}>
              <div style={{ padding: "5px 8px", background: "#eef2ff", borderRadius: 6, fontWeight: 600 }}>{g.harness} <span className="dim">{g.sessions.reduce((n, s) => n + s.records.length, 0)}</span></div>
              {g.sessions.map((s) => (
                <div key={s.session} className="dim" style={{ padding: "3px 8px 3px 20px", cursor: "pointer" }} onClick={() => setSel(s.records[0])}>
                  └ {s.session === "未识别会话" ? "未识别会话" : `${s.session.slice(0, 8)}…`} <span className="dim">{s.records.length}</span>
                </div>
              ))}
            </div>
          ))}
          {groups.length === 0 && <div className="dim small" style={{ marginTop: 8 }}>暂无识图记录</div>}
        </div>
      </div>
      <div style={{ flex: 1 }}>
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>时间</th><th>层级</th><th>提示词</th><th>缓存</th><th>耗时</th><th>VLM</th></tr></thead>
            <tbody>
              {rows.slice(0, 50).map((r, i) => (
                <tr key={i} style={{ background: sel === r ? "#f0f9ff" : undefined, cursor: "pointer" }} onClick={() => setSel(r)}>
                  <td>{new Date((r.ts as number) * 1000).toLocaleTimeString()}</td>
                  <td>Tier{r.tier as number}</td>
                  <td className="dim">{r.prompt ? "默认" : "—"}</td>
                  <td>{r.cache_hit ? "命中" : "未命中"}</td>
                  <td>{r.duration_ms as number}ms</td>
                  <td className="dim">{String(r.vlm_model ?? "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {sel && (
          <div className="card">
            <b className="small">▸ 三段明细</b>
            <div style={{ marginTop: 8 }}>
              <div className="small" style={{ color: "#2563eb", fontWeight: 700 }}>① 发给 VLM 的提示词</div>
              <div className="mono codebox">{String(sel.prompt ?? "—")}</div>
              <div className="small" style={{ color: "#7c3aed", fontWeight: 700, margin: "6px 0 3px" }}>② VLM 原始返回</div>
              <div className="mono codebox" style={{ background: "#faf5ff", borderColor: "#e9d5ff" }}>{String(sel.raw ?? "—")}</div>
              <div className="small" style={{ color: "#059669", fontWeight: 700, margin: "6px 0 3px" }}>③ 实际注入对话的文本</div>
              <div className="mono codebox" style={{ background: "#ecfdf5", borderColor: "#a7f3d0", color: "#064e3b" }}>{String(sel.injected ?? "—")}</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: `pnpm -C gui test` + commit**

```bash
git add gui/src/pages/VisionLog.tsx gui/src/lib/grouping.ts gui/src/lib/grouping.test.ts
git commit -m "feat(gui): vision log — harness/session grouping, three-segment detail"
```

---

### Task 11: 事件日志页

**Files:**
- Create: `gui/src/pages/Events.tsx`

- [ ] **Step 1: 实现**（表：时间 / harness / 类型 chip / 内容 / 结果；类型筛选下拉 全部|reclaim|absorb|auto_fix|auto_annotate|relay_added|takeover|restore；8s 轮询；导出=下载 JSONL blob——纯前端，不涉密钥）

```tsx
import { useEffect, useMemo, useState } from "react";
import { core } from "../core";

interface EventRow { ts: number; type: string; harness: string | null; [k: string]: unknown }
const TYPES = ["all", "reclaim", "absorb", "auto_fix", "auto_annotate", "relay_added", "takeover", "restore"];

export function EventsPage(p: { lang: string }) {
  const [rows, setRows] = useState<EventRow[]>([]);
  const [filter, setFilter] = useState("all");
  useEffect(() => {
    const load = () => core<EventRow[]>("events").then((rows) => setRows(rows.slice().reverse())).catch(() => {});
    load(); const t = setInterval(load, 8000); return () => clearInterval(t);
  }, []);
  const shown = useMemo(() => (filter === "all" ? rows : rows.filter((r) => r.type === filter)), [rows, filter]);
  const label: Record<string, string> = { reclaim: "自动抢回", absorb: "自动吸收", auto_fix: "自动修复", auto_annotate: "自动标注", relay_added: "生成转发", takeover: "接管", restore: "还原" };
  return (
    <div className="card" style={{ padding: 0 }}>
      <div className="row between" style={{ padding: "12px 14px 0" }}>
        <h3>自动动作全程留痕</h3>
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          {TYPES.map((t) => <option key={t} value={t}>{t === "all" ? "全部" : label[t] ?? t}</option>)}
        </select>
      </div>
      <table>
        <thead><tr><th>时间</th><th>harness</th><th>类型</th><th>内容</th></tr></thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={i}>
              <td className="mono">{new Date(r.ts * 1000).toLocaleString()}</td>
              <td>{r.harness ?? "—"}</td>
              <td><span className="tag warn">{label[r.type] ?? r.type}</span></td>
              <td className="small dim">{JSON.stringify({ ...r, ts: undefined, type: undefined, harness: undefined })}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: 验证 + commit**

```bash
git add gui/src/pages/Events.tsx
git commit -m "feat(gui): events page — filterable auto-action timeline"
```

---

### Task 12: 设置页（VLM + 分组 + 测试四模式 + 提示词折叠 + 外观 + 服务默认折叠 + 统一保存）

**Files:**
- Create: `gui/src/pages/Settings.tsx`

- [ ] **Step 1: 实现**（区块自上而下：VLM 全局卡[模型名称/base URL/API key(留空不改)/协议] → 按 harness 分组状态表[跟随全局✓/自定义 + 展开四件套 + null=改回跟随] → 外观[语言三选] → `<details>` 识图提示词[Tier1/Tier2 textarea + 保存自定义 + 恢复默认] → `<details>` 服务与高级[只读默认值 + 未标注默认开关 radio + 留存天数 + 留痕开关] → 页底统一保存条[未保存计数 + 放弃 + 保存]）

```tsx
import { useEffect, useState } from "react";
import { core, setCorePath } from "../core";
import type { StatusData } from "../shell/useStatus";
import { Lang } from "../i18n";

interface VlmForm { model: string; base_url: string; api_key: string; format: string }
const EMPTY: VlmForm = { model: "", base_url: "", api_key: "", format: "chat" };
const HARNESSES = ["claude", "codex", "qwen-code"];

export function SettingsPage(p: { lang: string; status: StatusData | null; refresh: () => void; setLang: (l: Lang) => void }) {
  const [vlm, setVlm] = useState<VlmForm>(EMPTY);
  const [groups, setGroups] = useState<Record<string, VlmForm | null>>({});
  const [prompts, setPrompts] = useState<{ t1: string; t2: string }>({ t1: "", t2: "" });
  const [unknownDefault, setUnknownDefault] = useState("text_only");
  const [logCfg, setLogCfg] = useState({ enabled: true, retention_days: 7 });
  const [corePath, setCorePathInput] = useState("");
  const [dirtyCount, setDirtyCount] = useState(0);
  const [testOut, setTestOut] = useState<string | null>(null);
  const [testBusy, setTestBusy] = useState(false);
  const [testMode, setTestMode] = useState("tier1");
  const [testCustom, setTestCustom] = useState("");
  const [testQ, setTestQ] = useState("");

  useEffect(() => {
    core<Record<string, unknown>>("config").then((c) => {
      const v = c.vlm as Record<string, string>;
      setVlm({ model: v.model ?? "", base_url: v.base_url ?? "", api_key: "", format: v.format ?? "chat" });
      const g: Record<string, VlmForm | null> = {};
      for (const h of HARNESSES) g[h] = null;
      for (const [h, over] of Object.entries((c.vlm_by_harness ?? {}) as Record<string, Record<string, string>>)) {
        g[h] = { model: over.model ?? "", base_url: over.base_url ?? "", api_key: "", format: over.format ?? "" };
      }
      setGroups(g);
      setPrompts({ t1: v.custom_tier1 ?? "", t2: v.custom_tier2 ?? "" });
      const r = c.routing as Record<string, unknown>;
      setUnknownDefault((r.unknown_default as string) ?? "text_only");
      const vl = c.vision_log as Record<string, unknown>;
      setLogCfg({ enabled: vl.enabled !== false, retention_days: (vl.retention_days as number) ?? 7 });
    }).catch(() => {});
  }, []);

  const touch = () => setDirtyCount((n) => n + 1);

  const save = async () => {
    const payload: Record<string, unknown> = {
      vlm: { model: vlm.model, base_url: vlm.base_url, format: vlm.format, ...(vlm.api_key ? { api_key: vlm.api_key } : {}) },
      vlm_by_harness: Object.fromEntries(
        HARNESSES.filter((h) => groups[h]).map((h) => {
          const g = groups[h]!;
          return [h, g ? { model: g.model, base_url: g.base_url, ...(g.api_key ? { api_key: g.api_key } : {}) } : null];
        }),
      ),
    };
    if (prompts.t1) payload.custom_tier1 = prompts.t1; else payload.custom_tier1 = null;
    if (prompts.t2) payload.custom_tier2 = prompts.t2; else payload.custom_tier2 = null;
    await core("vlm-set", { stdin: payload });
    await core("settings-set", { stdin: { routing: { unknown_default: unknownDefault }, vision_log: logCfg } });
    if (corePath) setCorePath(corePath);
    setDirtyCount(0); p.refresh();
  };

  const runTest = async () => {
    setTestBusy(true); setTestOut(null);
    try {
      const d = await core<{ desc: string; duration_ms: number; model: string }>("vlm-test", {
        stdin: { mode: testMode, question: testQ || null, custom_prompt: testCustom || null },
      });
      setTestOut(`✅ ${d.model} · ${d.duration_ms}ms\n${d.desc}`);
    } catch (e) {
      setTestOut(`❌ ${String(e)}`);
    } finally { setTestBusy(false); }
  };

  const field = (label: string, value: string, onChange: (v: string) => void, type = "text", width = 320) => (
    <div className="field" key={label}>
      <label>{label}</label>
      <input className="input" type={type} value={value} onChange={(e) => { onChange(e.target.value); touch(); }} style={{ minWidth: width }} />
    </div>
  );

  return (
    <>
      <div className="card">
        <h3>🔍 VLM（唯一必配）{p.status?.vlm.configured ? <span className="tag ok">已配置</span> : <span className="tag err">未配置</span>}</h3>
        {field("模型名称", vlm.model, (v) => setVlm({ ...vlm, model: v }))}
        {field("base URL", vlm.base_url, (v) => setVlm({ ...vlm, base_url: v }))}
        {field("API key", vlm.api_key, (v) => setVlm({ ...vlm, api_key: v }), "password", 160)}
        <div className="dim small" style={{ marginLeft: 92 }}>留空 = 不修改（已保存的 key 不回显）</div>
      </div>

      <div className="card">
        <h3>按 harness 分组</h3>
        <table><tbody>
          {HARNESSES.map((h) => (
            <tr key={h}>
              <td>{h}</td>
              <td>{groups[h] ? <span className="tag gray">自定义</span> : <span className="tag ok">跟随全局 ✓</span>}</td>
              <td style={{ textAlign: "right" }}>
                <a href="#" onClick={(e) => { e.preventDefault(); setGroups({ ...groups, [h]: groups[h] ? null : { ...EMPTY } }); touch(); }}>
                  {groups[h] ? "改回跟随全局" : "单独配置"}
                </a>
              </td>
            </tr>
          ))}
        </tbody></table>
        {HARNESSES.filter((h) => groups[h]).map((h) => (
          <div className="card" key={h} style={{ margin: "8px 0 0" }}>
            {field(`${h} · 模型`, groups[h]!.model, (v) => setGroups({ ...groups, [h]: { ...groups[h]!, model: v } }))}
            {field(`${h} · base URL`, groups[h]!.base_url, (v) => setGroups({ ...groups, [h]: { ...groups[h]!, base_url: v } }))}
            {field(`${h} · API key`, groups[h]!.api_key, (v) => setGroups({ ...groups, [h]: { ...groups[h]!, api_key: v } }), "password", 160)}
          </div>
        ))}
      </div>

      <div className="card">
        <h3>外观</h3>
        <div className="field">
          <label>语言</label>
          {(["system", "zh", "en"] as const).map((l) => (
            <span key={l} className={"tag" + ((localStorage.getItem("vr.lang") ?? "system") === l ? " info" : " gray")} style={{ cursor: "pointer" }}
              onClick={() => { localStorage.setItem("vr.lang", l === "system" ? "" : l); p.setLang(l === "system" ? (navigator.language.startsWith("zh") ? "zh" : "en") : (l as Lang)); touch(); }}>
              {l === "system" ? "跟随系统" : l === "zh" ? "中文" : "English"}
            </span>
          ))}
        </div>
        <div className="field">
          <label>核心路径</label>
          <input className="input" value={corePath} placeholder="（自动探测 PATH；失败时手动指定）" onChange={(e) => { setCorePathInput(e.target.value); touch(); }} style={{ minWidth: 320 }} />
        </div>
      </div>

      <details className="card">
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>📝 识图提示词（高级，默认内置）▸</summary>
        <div className="field" style={{ alignItems: "flex-start", marginTop: 10 }}>
          <label>Tier1 全面</label>
          <textarea className="prompt" value={prompts.t1} onChange={(e) => { setPrompts({ ...prompts, t1: e.target.value }); touch(); }} />
        </div>
        <div className="field" style={{ alignItems: "flex-start" }}>
          <label>Tier2 聚焦</label>
          <textarea className="prompt" value={prompts.t2} onChange={(e) => { setPrompts({ ...prompts, t2: e.target.value }); touch(); }} />
        </div>
        <div className="row">
          <button className="btn" onClick={() => { setPrompts({ t1: "", t2: "" }); touch(); }}>↩ 恢复默认</button>
          <span className="dim small">当前：{prompts.t1 || prompts.t2 ? "自定义" : "默认"}</span>
        </div>
      </details>

      <details className="card">
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>服务与高级（默认值）▸</summary>
        <table style={{ marginTop: 8 }}><tbody>
          <tr><td className="dim" style={{ width: 130 }}>监听地址</td><td>127.0.0.1:8787（默认）</td></tr>
          <tr><td className="dim">管理的 harness</td><td>自动检测已安装者</td></tr>
          <tr><td className="dim">未标注模型默认</td>
            <td>
              <label><input type="radio" checked={unknownDefault === "text_only"} onChange={() => { setUnknownDefault("text_only"); touch(); }} /> 按纯文本走 VLM 转述（安全，默认）</label>
              <label style={{ marginLeft: 12 }}><input type="radio" checked={unknownDefault === "image"} onChange={() => { setUnknownDefault("image"); touch(); }} /> 直通（省 token，真纯文本会报错）</label>
            </td></tr>
          <tr><td className="dim">识图留痕</td>
            <td>
              <label><input type="checkbox" checked={logCfg.enabled} onChange={(e) => { setLogCfg({ ...logCfg, enabled: e.target.checked }); touch(); }} /> 记录</label>
              　留存 <input className="input" value={logCfg.retention_days} onChange={(e) => { setLogCfg({ ...logCfg, retention_days: Number(e.target.value) || 0 }); touch(); }} style={{ width: 56 }} /> 天 · 仅存本机
            </td></tr>
        </tbody></table>
      </details>

      <div className="card">
        <h3>🧪 VLM 测试（与生产同一调用路径）</h3>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <select value={testMode} onChange={(e) => setTestMode(e.target.value)}>
            <option value="tier1">Tier1 · 默认提示词</option>
            <option value="tier1c">Tier1 · 自选提示词</option>
            <option value="tier2">Tier2 · 默认＋问题</option>
            <option value="tier2c">Tier2 · 自选提示词</option>
          </select>
          {testMode.startsWith("tier2") && <input className="input" placeholder="聚焦问题…" value={testQ} onChange={(e) => setTestQ(e.target.value)} />}
          {testMode.endsWith("c") && <input className="input" placeholder="自选提示词…" value={testCustom} onChange={(e) => setTestCustom(e.target.value)} />}
          <button className="btn green" disabled={testBusy} onClick={runTest}>{testBusy ? "测试中…" : "开始测试"}</button>
        </div>
        {testOut && <div className="mono codebox" style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>{testOut}</div>}
        <div className="dim small" style={{ marginTop: 4 }}>测试使用 1×1 最小图（不带用户图片，验证连通与提示词）。</div>
      </div>

      <div className="card row between" style={{ position: "sticky", bottom: 0 }}>
        <span className="dim small">{dirtyCount ? `● ${dirtyCount} 处未保存修改` : "无未保存修改"}——输入类修改统一由这里保存生效</span>
        <div className="row">
          <button className="btn" onClick={() => window.location.reload()}>放弃修改</button>
          <button className="btn primary" disabled={!dirtyCount} onClick={save}>💾 保存设置</button>
        </div>
      </div>
    </>
  );
}
```

**注意**：`vlm-test` 的 `mode` 取值是 `tier1|tier2`（Task 2 契约），前端下拉的 `tier1c/tier2c` 在 `runTest` 里映射：`mode = testMode.startsWith("tier1") ? "tier1" : "tier2"`，自选提示词走 `custom_prompt`（已如此实现则无需改；实现时核对一遍）。

- [ ] **Step 2: 验证 + commit**：改全局 VLM 保存 → `config --json` 复核（key 仍打码）；四模式测试跑通一种；恢复默认提示词后 `config --json` 的 custom_tier1 为 null。

```bash
git add gui/src/pages/Settings.tsx
git commit -m "feat(gui): settings — vlm global/groups, prompts editor, four-mode test, unified save"
```

---

### Task 13: 首次向导（两步弹层，可跳过第②步）

**Files:**
- Create: `gui/src/wizard/Wizard.tsx`
- Modify: `gui/src/App.tsx`（`status.first_run` 时挂载）

- [ ] **Step 1: 实现**（步骤条 ①配置 VLM（必填，调 vlm-set）→ ②过目模型能力（models-scan 只读展示 + 跳过按钮）→ 完成；完成条件 = vlm_configured && capability_confirmed；「完成」后 `models-set` 空数组把 capability_confirmed 置位？——不对：确认标志由写入动作置位。**约定**：向导第②步无论跳过还是过目，都调一次 `models-set` stdin `[]`（空数组合法，不写任何条目），并在 verbs.models_set 成功路径补一行 `cfg.routing.capability_confirmed = True`（若尚未置位）——这是最小改动，spec 语义「完成向导=首次确认完成」。此改动落在本 Task 的 Python 侧并补测试。）

`tests/test_proxy_verbs.py` 追加：

```python
class TestWizardConfirm:
    def test_empty_models_set_marks_confirmed(self, tmp_path, monkeypatch):
        import io as _io

        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("sys.stdin", _io.StringIO("[]"))
        cfg = ProxyConfig()
        assert cfg.routing.capability_confirmed is False
        verbs.models_set(cfg)
        assert cfg.routing.capability_confirmed is True
```

`verbs.models_set` 校验通过后（写入前）加：

```python
    if rows == [] and not cfg.routing.capability_confirmed:
        cfg.routing.capability_confirmed = True  # 向导完成/跳过 = 首次确认完成（spec §6）
```

`gui/src/wizard/Wizard.tsx`：

```tsx
import { useEffect, useState } from "react";
import { core } from "../core";
import type { StatusData } from "../shell/useStatus";

interface Triple { harness: string; provider: string; model: string; value: string | null; source: string | null; probe_cached: string | null }

export function Wizard(p: { onDone: () => void }) {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState({ model: "qwen-vl-max", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", api_key: "" });
  const [rows, setRows] = useState<Triple[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { if (step === 2) core<{ models: Triple[] }>("models-scan").then((d) => setRows(d.models)).catch(() => {}); }, [step]);

  const saveVlm = async () => {
    setErr(null);
    try {
      await core("vlm-set", { stdin: { vlm: { model: form.model, base_url: form.base_url, ...(form.api_key ? { api_key: form.api_key } : {}) } } });
      setStep(2);
    } catch (e) { setErr(String(e)); }
  };
  const finish = async (reviewed: boolean) => {
    if (reviewed) {
      // 过目后把用户看到的最终态原样写回（source=user）——表格里的切换在 rows 本地维护
      await core("models-set", { stdin: rows.map((r) => ({ harness: r.harness, provider: r.provider, model: r.model, value: r.value })) });
    } else {
      await core("models-set", { stdin: [] }); // 跳过 = 仅置首次确认标志
    }
    p.onDone();
  };

  return (
    <div className="modal show">
      <div className="modal-box" style={{ width: 680 }}>
        <div className="steps">
          <div className={"step " + (step === 1 ? "on" : "done")}>{step === 1 ? "① 配置 VLM（必填）" : "① 配置 VLM ✓"}</div>
          <div className={"step " + (step === 2 ? "on" : "")}>② 过目模型能力（可跳过）</div>
        </div>
        {step === 1 && (
          <div className="card">
            <div className="field"><label>模型名称</label><input className="input" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} /></div>
            <div className="field"><label>base URL</label><input className="input" style={{ minWidth: 380 }} value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} /></div>
            <div className="field"><label>API key</label><input className="input" type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} /></div>
            {err && <div className="alert-err">{err}</div>}
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button className="btn primary lg" disabled={!form.api_key || !form.model} onClick={saveVlm}>下一步：过目模型能力 →</button>
            </div>
          </div>
        )}
        {step === 2 && (
          <div className="card">
            <div className="alert-warn">⚠ 标错两个方向的代价：视觉模型标成纯文本 → 每张图都白走 VLM 转述（费 token + 降质）；纯文本标成视觉 → 请求报错。</div>
            <table>
              <thead><tr><th>harness · provider</th><th>模型</th><th>标注</th><th>实测</th></tr></thead>
              <tbody>
                {rows.slice(0, 12).map((r, i) => (
                  <tr key={i}>
                    <td>{r.harness} · {r.provider}</td><td>{r.model}</td>
                    <td>{r.value === "image" ? "支持图片" : r.value === "text_only" ? "纯文本" : "未标注"}</td>
                    <td>{r.probe_cached ?? "未测"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="row" style={{ justifyContent: "space-between", marginTop: 10 }}>
              <button className="btn" onClick={() => setStep(1)}>← 上一步</button>
              <div className="row">
                <button className="btn" onClick={() => finish(false)}>跳过（按默认）</button>
                <button className="btn primary lg" onClick={() => finish(true)}>完成 → 开启路由</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
```

`App.tsx`：`{status?.first_run && <Wizard onDone={refresh} />}`（first_run 由 Task 4 的 status 提供；向导完成触发 refresh 后 first_run 变 false 自动消失）。

- [ ] **Step 2: 验证 + commit**：删 `~/.vision-relay/proxy.json` 后启动 GUI → 向导出现 → 走完两步 → 自动消失、`capability_confirmed` 置位；「跳过」路径同样置位。

```bash
git add vision_relay/verbs.py tests/test_proxy_verbs.py gui/src/wizard gui/src/App.tsx
git commit -m "feat(gui,verbs): two-step first-run wizard (skip marks confirmed)"
```

---

### Task 14: 收尾（i18n 补全 / 全量回归 / 无 TODO 残留）

**Files:**
- Modify: `gui/src/i18n.ts`、各页面的硬编码中文核对

- [ ] **Step 1**: `grep -rn "TODO" gui/src` —— 只允许零命中（Task 7 的临时空壳必须已全部替换）。
- [ ] **Step 2**: i18n dict 补全两语言全部页面文案键（做不到逐键也至少保证导航/开关/按钮级文案双语，正文表格数据本身是核心返回值不翻译）。
- [ ] **Step 3**: `python -m pytest -q`（不低于基线）+ `ruff format --check . && ruff check .` + `pnpm -C gui test` + `pnpm -C gui build` 全绿。
- [ ] **Step 4**: commit

```bash
git add -A gui vision_relay tests
git commit -m "chore(gui): i18n completion, remove scaffolding leftovers"
```

---

### Task 15: 文档 + 手动测试手册

**Files:**
- Modify: `README.md`、`README.zh.md`、`CHANGELOG.md`
- Create: `docs/superpowers/plans/2026-08-22-vision-relay-phase2-m2-manual-test.md`

- [ ] **Step 1**: README 双语加「GUI（预览）」小节：`pnpm -C gui install && pnpm -C gui tauri dev`；说明 M2 阶段 GUI 需要 PATH 上有 `vision-relay`（打包随 M3）。
- [ ] **Step 2**: CHANGELOG Unreleased 追加 M2 条目。
- [ ] **Step 3**: 手动测试手册 = 下方附录 A 全文粘贴。
- [ ] **Step 4**: commit

```bash
git add README.md README.zh.md CHANGELOG.md docs/superpowers/plans/2026-08-22-vision-relay-phase2-m2-manual-test.md
git commit -m "docs: M2 GUI preview docs and manual test checklist"
```

---

## 附录 A：M2 手动测试手册（GUI 验收 = spec §10 剧本 1–8 GUI 化 + GUI 专项）

> 环境：Windows 本机；`vision-relay` 已 pip 安装且在 PATH；CC Switch / Codex++ 至少一个可用；VLM key 可用。macOS/Linux 实机回归留 M3 打包后。

| # | 场景 | 步骤 | 预期 |
|---|---|---|---|
| G1 | 首次向导 | 删 `~/.vision-relay/proxy.json` → 启动 GUI | 向导弹层（①VLM 必填：key 空时「下一步」禁用）；②步展示三元组+实测列；「跳过」与「完成」都能关闭向导且 `models-set` 后 `capability_confirmed=true` |
| G2 | 路由开关 | 总览滑动开关 → 开 | 服务启动（状态点变绿、拓扑链 relay 节点激活）；开关 → 关 | 服务停止、relay 节点置灰、连线右移（工具在线则直连工具，工具离线直连上游） |
| G3 | 刷新与抢回 | 服务运行中手改 codex base_url 为 :57321 → 点「刷新」 | 动作后拓扑卡回到已接管；「自动处理」区出现抢回绿条；事件日志页有 reclaim |
| G4 | 诊断报告 | 强杀服务进程（任务管理器）→ 点「诊断报告」 | routing_on=true 时自动重启（状态恢复运行中，报告含 auto_fix/restart）；手动把 routing 意图关掉再杀 → 自动 restore 回快照原值 |
| G5 | 模型能力 | 模型能力页切换某模型三态 → 保存 | `vision-relay models-scan --json` 里该行 source=user、value 与界面一致；「重测」更新实测列；「探测全部未测」批量跑完刷新 |
| G6 | VLM 设置与测试 | 设置页改全局模型 + 为 claude 组自定义 + 四模式测试 | 保存后 `config --json` 复核（key 打码、分组正确）；tier2+自选提示词的返回 desc 非空、prompt_used=自选文本 |
| G7 | 提示词编辑 | 设置折叠区编辑 Tier1 → 保存 → 发一张图过代理 | 识图记录 ①段的 prompt = 自定义文本；「恢复默认」后回到内置 |
| G8 | 识图记录 | 发带图请求 → 识图记录页 | harness→会话分组正确（无会话标识的归「未识别会话」）；三段明细齐全且 ③ 以 [图片描述] 开头 |
| G9 | 事件日志 | 触发 G3/G4 后查看 | 类型筛选可用、时间倒序、内容 JSON 完整 |
| G10 | 关闭与托盘 | 关闭 GUI 选「仅关界面」并记住 → 再关 | 直接隐藏不弹；托盘「路由：开/关」可用；「退出（停止服务）」后 `vision-relay status` 显示 not running |
| G11 | 保存语义 | 设置页改多个字段不保存直接切页再回来 | 脏计数与字段保持（组件内）；点「放弃修改」回到已保存态；总览的按钮（刷新/诊断/开关）全部即时生效不出现保存条 |
| G12 | 未标注开关 | 服务折叠区切「直通」保存 | 用一个未标注模型发带图请求：请求直通上游（报错或成功都不经 VLM）；切回「走识图」后同请求走 VLM 转述 |
| G13 | 找不到核心 | PATH 移除 vision-relay 启动 GUI | 顶部红条「核心不可用」+ 设置页可手填核心路径后恢复 |

## 附录 B：执行纪律（给子代理）

1. 严格 TDD / 先测后码；每个 checkbox 真跑过命令才打勾；每 Task 一 commit。
2. **尊重 spec 与 mockup**：与 spec 冲突停下来问用户；GUI 布局/措辞以 `gui-mockups/index.html` 为准（顶部标签不是软件界面）；决策点不许超过 6 个（§3）。
3. Python 侧全绿门禁：`python -m pytest -q`（≥ 基线 330）+ `ruff format --check .` + `ruff check .`；前端门禁：`pnpm -C gui test` + `pnpm -C gui build`。
4. 密钥铁律：任何 GUI 显示/日志/导出不出现明文 key；`config --json` 输出必须打码。
5. 计划中的 Rust/TS 代码贴合 Tauri 2 与 React 18，但小版本 API 有出入时以编译器为准做等价调整（Task 6/7 已标注两处），不许改变行为语义。
6. M2 不做：打包、自动监听 UI、三平台 CI、冻结分发。
