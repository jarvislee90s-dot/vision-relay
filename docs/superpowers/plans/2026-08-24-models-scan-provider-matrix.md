# 模型矩阵扫描(models-scan 数据源重构)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** models-scan 的供应商×模型矩阵改从 cc-switch SQLite 与 Codex++ settings.json 只读读取;两工具均未安装时(直连态)从 harness 配置的 base_url 域名推导供应商名;统一能力标注的读写键并迁移 legacy/`?` 影子桶;探针增加直连上游候选并对"无目标/无结论"给出可见反馈。

**Architecture:** 新增 `vision_relay/model_sources.py` 作为工具档案的唯一读取层(只读、best-effort、密钥不落地),`verbs._scan_triples` 与 probe 动词改从该层取 (provider × model) 矩阵;`onboarding` 的正则扫描降级为直连兜底并修复字符集/去重问题;GUI 仅增加反馈弹窗,不改表格结构。

**Tech Stack:** Python ≥3.10(标准库 sqlite3/json/tomllib-可选)、pytest;GUI 为 React 18 + vitest + @testing-library/react。

## Global Constraints

- **fail-open 铁律**:任何扫描/探测内层失败不得让 verb 崩溃或返回 4xx——reader 抛错一律降级为空矩阵→直连兜底。
- **工具档案只读**:绝不写 `~/.cc-switch/cc-switch.db` 与 `~/.codex-session-delete/settings.json`;sqlite 一律 `file:...?mode=ro` 打开。
- **密钥不落地**:两个工具库里存在明文 token(settings_config.env、relayApiKey、authContents)。只允许读取白名单键(模型名、base_url、供应商名、is_current);**任何 api key / token / auth 字段一律不读、不进 envelope、不进日志**。
- **禁止使用 Codex++ 的 `modelVlm` 字段**(用户裁决 2026-08-24:该配置不准,不作为模态信息来源)。只从两工具取"供应商+模型+工具"组合。
- 代理必须是第一跳、`log_json` 脱密等既有不变量不动;本计划不触碰 `ir.py`/`pipeline.py`/`server.py`。
- 命名遵循 AGENTS.md 表格(`vision-relay`/`vision_relay`/`~/.vision-relay/`);harness 适配器名 `cc-switch`/`codex-plus` 保持原样。
- Python 下限 3.10:`tomllib` 必须 `try/except ImportError` 导入,失败退正则解析 TOML 片段。
- 测试隔离:任何**进程内**单测不得读真机 `~/.cc-switch` 或 `~/.codex-session-delete`(monkeypatch `vision_relay.model_sources.CCSWITCH_DB` / `CODEXPP_SETTINGS`);子进程 e2e 用假 HOME 天然隔离。
- 每任务收尾:`ruff format` 该任务文件 + 相关测试绿灯后才 commit;行为变更同 PR 更新 spec(Task 8)。
- 工作区已有无关未跟踪文件(`gui/app-icon.png`、`gui/pnpm-workspace.yaml`、`gui/src/pages/Settings.custom-image.test.tsx`、`tests/test_proxy_vlm_test_image.py`)——commit 时只 `git add` 本计划点名的文件。

---

### Task 1: `model_sources.py` — cc-switch SQLite 读取器

**Files:**
- Create: `vision_relay/model_sources.py`
- Test: `tests/test_proxy_model_sources.py`

**Interfaces:**
- Consumes: `vision_relay.tools.CCSWITCH_DB`(常量 `~/.cc-switch/cc-switch.db`)
- Produces:
  - `@dataclass(frozen=True) class ProviderRow: tool: str; harness: str; provider: str; base_url: str; is_current: bool; models: list[str]`
  - `def ccswitch_matrix() -> dict[str, list[ProviderRow]]` — 键为 `app_type`("claude"/"codex"),值按 `is_current DESC, sort_index` 排序;DB 不存在/任何异常 → `{}`

- [ ] **Step 1: 写失败测试**

```python
"""model_sources: 工具档案只读层(spec §5 模型矩阵来源)。密钥绝不读取。"""

import json
import sqlite3

from vision_relay import model_sources as ms


def _mk_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE providers (
            id TEXT NOT NULL, app_type TEXT NOT NULL, name TEXT NOT NULL,
            settings_config TEXT NOT NULL, is_current INTEGER,
            sort_index INTEGER, PRIMARY KEY (id, app_type))"""
    )
    conn.executemany("INSERT INTO providers VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_ccswitch_matrix_claude_env_models(tmp_path, monkeypatch):
    db = tmp_path / "cc-switch.db"
    _mk_db(
        db,
        [
            (
                "a1",
                "claude",
                "火山Ark",
                json.dumps(
                    {
                        "env": {
                            "ANTHROPIC_BASE_URL": "https://ark.cn-beijing.volces.com/api/coding",
                            "ANTHROPIC_AUTH_TOKEN": "ark-secret",  # 必须被忽略
                            "ANTHROPIC_DEFAULT_SONNET_MODEL": "MiniMax-M3",
                            "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "MiniMax-M3",  # 同模型去重
                            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "Kimi-K2.7-Code",
                            "ANTHROPIC_MODEL": "deepseek-v4-flash",
                        },
                        "model": "fable",
                    }
                ),
                1,
                0,
            )
        ],
    )
    monkeypatch.setattr(ms, "CCSWITCH_DB", str(db))
    rows = ms.ccswitch_matrix()["claude"]
    assert len(rows) == 1
    r = rows[0]
    assert r.provider == "火山Ark" and r.is_current is True
    assert r.base_url == "https://ark.cn-beijing.volces.com/api/coding"
    assert r.models == ["fable", "MiniMax-M3", "Kimi-K2.7-Code", "deepseek-v4-flash"]  # 去重、保序
    assert not any("secret" in m or "ark-" in m for m in r.models)


def test_ccswitch_matrix_codex_toml_and_catalog(tmp_path, monkeypatch):
    db = tmp_path / "cc-switch.db"
    _mk_db(
        db,
        [
            (
                "c1",
                "codex",
                "Kimi For Coding",
                json.dumps(
                    {
                        "config": 'model_provider = "custom"\nmodel = "kimi-for-coding"\n\n'
                        '[model_providers.custom]\nname = "kimi_coding"\n'
                        'base_url = "https://api.kimi.com/coding/v1"\nwire_api = "responses"\n',
                        "modelCatalog": {"models": [{"model": "kimi-for-coding"}, {"model": "k2.7-code"}]},
                    }
                ),
                0,
                1,
            )
        ],
    )
    monkeypatch.setattr(ms, "CCSWITCH_DB", str(db))
    rows = ms.ccswitch_matrix()["codex"]
    r = rows[0]
    assert r.models == ["kimi-for-coding", "k2.7-code"]  # config.model + modelCatalog,去重
    assert r.base_url == "https://api.kimi.com/coding/v1"


def test_ccswitch_matrix_missing_or_corrupt_db_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "CCSWITCH_DB", str(tmp_path / "nope.db"))
    assert ms.ccswitch_matrix() == {}
    bad = tmp_path / "bad.db"
    bad.write_text("not a sqlite file")
    monkeypatch.setattr(ms, "CCSWITCH_DB", str(bad))
    assert ms.ccswitch_matrix() == {}


def test_ccswitch_matrix_ignores_other_app_types(tmp_path, monkeypatch):
    db = tmp_path / "cc-switch.db"
    _mk_db(db, [("g1", "gemini", "Google", "{}", 1, 0), ("h1", "hermes", "x", "{}", 0, 1)])
    monkeypatch.setattr(ms, "CCSWITCH_DB", str(db))
    assert ms.ccswitch_matrix() == {}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/jarvis/Documents/vision-relay && .venv/bin/python -m pytest tests/test_proxy_model_sources.py -q`
Expected: FAIL,`ModuleNotFoundError: No module named 'vision_relay.model_sources'`

- [ ] **Step 3: 最小实现**

创建 `vision_relay/model_sources.py`:

```python
"""模型矩阵来源层(spec §5):cc-switch / Codex++ 工具档案只读读取 + 直连兜底。

铁律:绝不写工具的任何文件;sqlite 只以 mode=ro 打开;只读白名单键
(模型名、base_url、供应商名、is_current)——任何密钥字段不读、不外传、不落日志。
读取 best-effort:任何失败返回空,由调用方降级到直连扫描。
禁止读取 Codex++ 的 modelVlm(用户裁决 2026-08-24:该配置不准)。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

try:  # Python ≥3.11 才有 tomllib;3.10 退正则
    import tomllib
except ImportError:  # pragma: no cover - 取决于解释器版本
    tomllib = None

from .tools import CCSWITCH_DB as _CCSWITCH_DB_DEFAULT

# 模块级常量:测试 monkeypatch 的挂点(默认指向真机路径)
CCSWITCH_DB = _CCSWITCH_DB_DEFAULT
CODEXPP_SETTINGS: str = ""  # Task 2 填真实默认值


@dataclass(frozen=True)
class ProviderRow:
    tool: str  # cc-switch | codex-plus | direct
    harness: str  # claude | codex | qwen-code
    provider: str  # 工具档案里的供应商显示名;直连态为域名推导名或 "?"
    base_url: str
    is_current: bool
    models: list[str]


# claude settings_config 里视作"模型值"的 env 键(白名单:不含任何 token 键)
_CLAUDE_MODEL_KEY = re.compile(r"^ANTHROPIC_(?:DEFAULT_\w+_)?MODEL(?:_NAME)?$")


def _dedup_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _claude_models(sc: dict) -> list[str]:
    env = sc.get("env") if isinstance(sc.get("env"), dict) else {}
    models = [v for k, v in env.items() if _CLAUDE_MODEL_KEY.match(k) and isinstance(v, str)]
    top = sc.get("model")
    if isinstance(top, str):
        models.append(top)
    return _dedup_keep_order(models)


_TOML_MODEL = re.compile(r'(?m)^model\s*=\s*"([^"]+)"')
_TOML_BASE = re.compile(r'(?m)^base_url\s*=\s*"([^"]+)"')


def _codex_models(sc: dict) -> list[str]:
    models: list[str] = []
    text = sc.get("config")
    if isinstance(text, str) and text.strip():
        if tomllib is not None:
            try:
                parsed = tomllib.loads(text)
                m = parsed.get("model")
                if isinstance(m, str):
                    models.append(m)
            except tomllib.TOMLDecodeError:
                models += _TOML_MODEL.findall(text)
        else:
            models += _TOML_MODEL.findall(text)
    cat = sc.get("modelCatalog")
    if isinstance(cat, dict):
        for m in cat.get("models") or []:
            if isinstance(m, dict) and isinstance(m.get("model"), str):
                models.append(m["model"])
    return _dedup_keep_order(models)


def _codex_base_url(sc: dict) -> str:
    text = sc.get("config")
    if isinstance(text, str) and text.strip():
        if tomllib is not None:
            try:
                for p in tomllib.loads(text).get("model_providers", {}).values():
                    if isinstance(p, dict) and isinstance(p.get("base_url"), str):
                        return p["base_url"]
            except tomllib.TOMLDecodeError:
                pass
        hit = _TOML_BASE.search(text)
        if hit:
            return hit.group(1)
    return ""


def ccswitch_matrix() -> dict[str, list[ProviderRow]]:
    """providers 表 → {app_type: [ProviderRow]}。只取 claude/codex;任何失败返回 {}。"""
    try:
        conn = sqlite3.connect(f"file:{CCSWITCH_DB}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT app_type, name, is_current, settings_config FROM providers "
                "WHERE app_type IN ('claude','codex') ORDER BY app_type, is_current DESC, sort_index"
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - schema 漂移/库缺失:best-effort 空矩阵
        return {}
    out: dict[str, list[ProviderRow]] = {}
    for app_type, name, is_current, cfg_text in rows:
        try:
            sc = json.loads(cfg_text)
        except (TypeError, ValueError):
            continue
        if app_type == "claude":
            env = sc.get("env") if isinstance(sc.get("env"), dict) else {}
            base = env.get("ANTHROPIC_BASE_URL", "")
            models = _claude_models(sc)
        else:
            base = _codex_base_url(sc)
            models = _codex_models(sc)
        if not models:
            continue  # 如 OpenAI Official 的空 config:无模型可标,不产行
        out.setdefault(app_type, []).append(
            ProviderRow(
                tool="cc-switch", harness=app_type, provider=name,
                base_url=base if isinstance(base, str) else "",
                is_current=bool(is_current), models=models,
            )
        )
    return out
```

注意 `tools.py` 里 `CODEXPP_SETTINGS` 常量已存在(`~/.codex-session-delete/settings.json`);Task 1 先占位为 `""`,Task 2 接上。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_proxy_model_sources.py -q`
Expected: 4 passed

- [ ] **Step 5: ruff + commit**

```bash
cd /Users/jarvis/Documents/vision-relay
.venv/bin/ruff format vision_relay/model_sources.py tests/test_proxy_model_sources.py
.venv/bin/ruff check vision_relay/model_sources.py tests/test_proxy_model_sources.py
git add vision_relay/model_sources.py tests/test_proxy_model_sources.py
git commit -m "feat(models): model_sources 层——cc-switch SQLite 只读读取器(供应商×模型矩阵,密钥白名单外不读)"
```

---

### Task 2: `model_sources.py` — Codex++ 读取器 + 域名推导 + 编排

**Files:**
- Modify: `vision_relay/model_sources.py`
- Test: `tests/test_proxy_model_sources.py`(追加)

**Interfaces:**
- Consumes: `vision_relay.tools.CODEXPP_SETTINGS`;`vision_relay.snapshot.load()`;`vision_relay.wiring`(HARNESS_CFG/read_base_url/_path/HOME);`vision_relay.onboarding.scan_model_groups`
- Produces:
  - `def codexpp_matrix() -> list[ProviderRow]`(全为 `harness="codex", tool="codex-plus"`;文件缺失/异常 → `[]`)
  - `def provider_from_url(url: str) -> str | None`(回环/无效 → None)
  - `def direct_provider_url(harness: str) -> str | None`(live 优先、回环则用 snapshot 原始值)
  - `def resolve_probe_key(harness: str, key_ref: str | None) -> str`(按 snapshot._KEY_FIELDS 的位置描述取 key;失败 → `""`)
  - `def harness_matrix(cfg) -> dict[str, list[ProviderRow]]`(按 `cfg.routing.harnesses` 顺序;工具矩阵为空→直连兜底)
  - `def current_provider(cfg, harness: str) -> str`(矩阵 is_current → snapshot.second_hop → `"?"`)

- [ ] **Step 1: 写失败测试(追加到 tests/test_proxy_model_sources.py)**

```python
# ── Task 2:codex++ / 域名推导 / 编排 ──────────────────────────────
import wiring_stub  # noqa: F401  (不存在——见下方说明,实际按下面写)


def test_codexpp_matrix_reads_profiles(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text(
        json.dumps(
            {
                "activeRelayId": "relay-mt7bt7s3",
                "relayProfiles": [
                    {
                        "id": "relay-mt7bt7s3", "name": "Openrouter",
                        "upstreamBaseUrl": "https://openrouter.ai/api/v1",
                        "modelList": "stealth/ox-alpha\ndeepseek/deepseek-v4-pro",
                        "relayApiKey": "sk-or-secret",  # 必须被忽略
                        "modelVlm": '{"stealth/ox-alpha":"vlm"}',  # 用户裁决:禁用,不读
                    },
                    {
                        "id": "relay-mq92h08y", "name": "opencode",
                        "upstreamBaseUrl": "https://opencode.ai/zen/go/v1",
                        "modelList": "deepseek-v4-flash\nminimax-m3",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ms, "CODEXPP_SETTINGS", str(f))
    rows = ms.codexpp_matrix()
    assert [(r.provider, r.is_current) for r in rows] == [("Openrouter", True), ("opencode", False)]
    assert rows[0].models == ["stealth/ox-alpha", "deepseek/deepseek-v4-pro"]
    assert rows[0].base_url == "https://openrouter.ai/api/v1"
    assert all(r.tool == "codex-plus" and r.harness == "codex" for r in rows)


def test_codexpp_matrix_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "CODEXPP_SETTINGS", str(tmp_path / "nope.json"))
    assert ms.codexpp_matrix() == []


def test_provider_from_url_known_map_and_fallback():
    assert ms.provider_from_url("https://ark.cn-beijing.volces.com/api/coding") == "volces-ark"
    assert ms.provider_from_url("https://coding.dashscope.aliyuncs.com/apps/anthropic") == "dashscope"
    assert ms.provider_from_url("https://openrouter.ai/api/v1") == "openrouter"
    assert ms.provider_from_url("https://origin.example/api") == "origin.example"  # 未知域名→主机名
    assert ms.provider_from_url("http://127.0.0.1:8787") is None
    assert ms.provider_from_url("http://localhost:15721") is None
    assert ms.provider_from_url("not a url") is None


def test_direct_provider_url_prefers_live_then_snapshot(tmp_path, monkeypatch):
    import pathlib

    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://live.example/api"}}), encoding="utf-8"
    )
    import vision_relay.wiring as W

    monkeypatch.setattr(W, "HOME", str(home))
    assert ms.direct_provider_url("claude") == "https://live.example/api"
    # live 是回环(已接线到代理)→ 退 snapshot 的接管前原始值
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8787"}}), encoding="utf-8"
    )
    import vision_relay.snapshot as S

    snap_dir = tmp_path / "cfg"
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(snap_dir))
    S._path  # noqa: B018 - 确认可导入
    (snap_dir).mkdir(exist_ok=True)
    (snap_dir / "snapshots.json").write_text(
        json.dumps({"claude": {"base_url": "https://snap.example/api", "key_ref": "env.ANTHROPIC_AUTH_TOKEN",
                                "model": "m", "second_hop": None, "ts": 1}}),
        encoding="utf-8",
    )
    assert ms.direct_provider_url("claude") == "https://snap.example/api"


def test_resolve_probe_key_env_ref(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "ark-tok-123"}}), encoding="utf-8"
    )
    import vision_relay.wiring as W

    monkeypatch.setattr(W, "HOME", str(home))
    assert ms.resolve_probe_key("claude", "env.ANTHROPIC_AUTH_TOKEN") == "ark-tok-123"
    assert ms.resolve_probe_key("claude", None) == ""
    assert ms.resolve_probe_key("claude", "env.NOPE") == ""


def test_harness_matrix_direct_fallback_and_current_provider(tmp_path, monkeypatch):
    from vision_relay.config import ProxyConfig

    monkeypatch.setattr(ms, "CCSWITCH_DB", str(tmp_path / "nope.db"))
    monkeypatch.setattr(ms, "CODEXPP_SETTINGS", str(tmp_path / "nope.json"))
    home = tmp_path / "home"
    (home / ".qwen").mkdir(parents=True)
    (home / ".qwen" / "settings.json").write_text(
        json.dumps({"model": {"baseUrl": "https://dashscope.aliyuncs.com/x", "model": "qwen3-coder"}}),
        encoding="utf-8",
    )
    import vision_relay.wiring as W

    monkeypatch.setattr(W, "HOME", str(home))
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
    cfg = ProxyConfig()
    matrix = ms.harness_matrix(cfg)
    assert matrix["qwen-code"][0].provider == "dashscope"
    assert matrix["qwen-code"][0].models == ["qwen3-coder"]
    assert matrix["qwen-code"][0].is_current is True
    assert matrix["claude"][0].provider == "?"  # 无工具、无 harness 配置 → 直连未知
    assert ms.current_provider(cfg, "claude") == "?"
```

上面第一行 `import wiring_stub` 是**故意不要写**的——直接从 `# ── Task 2` 注释行开始追加,不要引入任何 stub 模块。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_proxy_model_sources.py -q`
Expected: 新增 6 个 FAIL(`AttributeError: module 'vision_relay.model_sources' has no attribute 'codexpp_matrix'` 等),Task 1 的 4 个仍 PASS。

- [ ] **Step 3: 实现(追加到 model_sources.py)**

```python
import os
from urllib.parse import urlparse

from .tools import CODEXPP_SETTINGS as _CODEXPP_DEFAULT

CODEXPP_SETTINGS = _CODEXPP_DEFAULT  # 覆盖 Task 1 的占位 ""


def codexpp_matrix() -> list[ProviderRow]:
    """relayProfiles → codex 行。只取 id/name/upstreamBaseUrl/modelList/activeRelayId;
    relayApiKey / authContents / modelVlm 一律不读(密钥不出库;modelVlm 用户裁决禁用)。"""
    try:
        with open(CODEXPP_SETTINGS, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    active = data.get("activeRelayId")
    out: list[ProviderRow] = []
    for p in data.get("relayProfiles") or []:
        if not isinstance(p, dict):
            continue
        raw = p.get("modelList")
        models = _dedup_keep_order([ln.strip() for ln in str(raw).splitlines()]) if isinstance(raw, str) else []
        if not models:
            continue
        base = p.get("upstreamBaseUrl") or p.get("baseUrl") or ""
        out.append(
            ProviderRow(
                tool="codex-plus", harness="codex", provider=str(p.get("name") or p.get("id") or "?"),
                base_url=base if isinstance(base, str) else "",
                is_current=p.get("id") == active, models=models,
            )
        )
    return out


_KNOWN_DOMAINS: list[tuple[str, str]] = [
    ("openrouter.ai", "openrouter"),
    ("api.openai.com", "openai"),
    ("api.anthropic.com", "anthropic"),
    ("api.deepseek.com", "deepseek"),
    ("dashscope.aliyuncs.com", "dashscope"),
    ("volces.com", "volces-ark"),
    ("api.kimi.com", "kimi"),
    ("bigmodel.cn", "bigmodel"),
]


def _host(url: str) -> str | None:
    try:
        h = urlparse(url).hostname
    except ValueError:
        return None
    return (h or "").lower() or None


def _is_loopback_url(url: str) -> bool:
    return _host(url) in ("127.0.0.1", "localhost", "::1")


def provider_from_url(url: str) -> str | None:
    """直连态供应商名:已知域名映射 → 主机名兜底;回环/无效 → None。"""
    host = _host(url)
    if not host or host in ("127.0.0.1", "localhost", "::1"):
        return None
    for suffix, name in _KNOWN_DOMAINS:
        if host == suffix or host.endswith("." + suffix):
            return name
    return host


def direct_provider_url(harness: str) -> str | None:
    """harness 自身配置的上游:live 文件优先;live 是回环(接线中)→ snapshot 的接管前原始值。"""
    from . import snapshot, wiring

    h = wiring.HARNESS_CFG.get(harness)
    live = wiring.read_base_url(wiring._path(wiring.HOME, harness), h) if h else None
    if live and not _is_loopback_url(live):
        return live
    snap = snapshot.load().get(harness)
    if snap is not None and snap.base_url and not _is_loopback_url(snap.base_url):
        return snap.base_url
    return None


def resolve_probe_key(harness: str, key_ref: str | None) -> str:
    """按 snapshot 的 key 位置描述取真实 key 值(仅进程内使用,绝不进 envelope/日志)。"""
    if not key_ref:
        return ""
    import pathlib

    from . import snapshot as snap_mod

    path: pathlib.Path | None = None
    file_parts = snap_mod._KEY_FIELDS.get(harness, ((None,), ()))[0]
    if file_parts and file_parts[0]:
        cand = pathlib.Path.home().joinpath(*file_parts)
        path = cand if cand.exists() else None
    try:
        if key_ref.startswith("env.") and path is not None:
            return str(json.loads(path.read_text(encoding="utf-8")).get("env", {}).get(key_ref[4:], "")) or ""
        if key_ref == "model.apiKey" and path is not None:
            return str(json.loads(path.read_text(encoding="utf-8")).get("model", {}).get("apiKey", "")) or ""
        if key_ref.endswith("auth.json"):
            auth = pathlib.Path.home() / ".codex" / "auth.json"
            return str(json.loads(auth.read_text(encoding="utf-8")).get("OPENAI_API_KEY", "")) or ""
    except (OSError, ValueError):
        return ""
    return os.environ.get(key_ref, "")


def _direct_rows(cfg, harness: str) -> list[ProviderRow]:
    from .onboarding import scan_model_groups

    models: list[str] = []
    for g in scan_model_groups(cfg):
        if g.group == harness:
            models = _dedup_keep_order([e.model for e in g.entries])
    if not models:
        return []
    url = direct_provider_url(harness)
    provider = provider_from_url(url) if url else None
    return [
        ProviderRow(tool="direct", harness=harness, provider=provider or "?",
                    base_url=url or "", is_current=True, models=models)
    ]


def _ccswitch_installed() -> bool:
    import os

    return os.path.exists(CCSWITCH_DB)


def _codexpp_installed() -> bool:
    import os

    return os.path.exists(CODEXPP_SETTINGS)


def harness_matrix(cfg) -> dict[str, list[ProviderRow]]:
    """每 harness 的供应商×模型矩阵。工具已装(磁盘上有档案,与进程在不在线无关)
    → 工具矩阵;读取失败/为空 → 直连兜底。codex 归属哪个工具以 snapshot.second_hop
    (接管时的接线真相)为准,缺省 codex-plus,再缺省 cc-switch。"""
    from . import snapshot

    snap = snapshot.load()
    out: dict[str, list[ProviderRow]] = {}
    for harness in cfg.routing.harnesses:
        rows: list[ProviderRow] = []
        if harness == "claude" and _ccswitch_installed():
            rows = ccswitch_matrix().get("claude", [])
        elif harness == "codex":
            s = snap.get("codex")
            tool = s.second_hop if s is not None and s.second_hop else None
            if tool == "cc-switch" and _ccswitch_installed():
                rows = ccswitch_matrix().get("codex", [])
            elif _codexpp_installed():
                rows = codexpp_matrix()
            elif _ccswitch_installed():
                rows = ccswitch_matrix().get("codex", [])
        if not rows:
            rows = _direct_rows(cfg, harness)
        out[harness] = rows
    return out


def current_provider(cfg, harness: str) -> str:
    for row in harness_matrix(cfg).get(harness, []):
        if row.is_current:
            return row.provider
    from . import snapshot

    s = snapshot.load().get(harness)
    return s.second_hop if s is not None and s.second_hop else "?"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_proxy_model_sources.py -q`
Expected: 10 passed

- [ ] **Step 5: ruff + commit**

```bash
.venv/bin/ruff format vision_relay/model_sources.py tests/test_proxy_model_sources.py
.venv/bin/ruff check vision_relay/model_sources.py tests/test_proxy_model_sources.py
git add vision_relay/model_sources.py tests/test_proxy_model_sources.py
git commit -m "feat(models): codex++ 只读读取器 + 域名推导供应商 + harness_matrix 编排(直连兜底、密钥与 modelVlm 均不读)"
```

---

### Task 3: `verbs._scan_triples` 改用矩阵 + 影子桶(legacy/`?`)读兜底

**Files:**
- Modify: `vision_relay/verbs.py:68-105`(`_scan_triples`/`_provider_hint`)
- Test: `tests/test_proxy_verbs.py`(改 2 个、增 2 个)

**Interfaces:**
- Consumes: `model_sources.harness_matrix(cfg)`
- Produces: `_scan_triples(cfg)` 返回行结构与现状完全一致(`harness/provider/model/value/source/probe_cached`);`_lookup_cap(cfg, h, p, m) -> tuple[str | None, str | None]`(精确 → `"legacy"` → `"?"`);`_lookup_probe(cfg, p, m) -> str | None`(精确 → `"?"`)

- [ ] **Step 1: 写失败测试**

在 `tests/test_proxy_verbs.py` 中:

(a) **替换** `test_models_scan_probes_tools_once_per_scan`(约 95-118 行)为:

```python
def test_models_scan_reads_matrix_without_port_probing(cfg, monkeypatch):
    """矩阵来自工具档案(磁盘),models-scan 不再探测端口——离线/加速两得。"""
    from vision_relay.model_sources import ProviderRow

    calls = []
    monkeypatch.setattr(verbs, "_probe_tools", lambda: calls.append(1) or [])
    monkeypatch.setattr(
        "vision_relay.model_sources.harness_matrix",
        lambda c: {
            "claude": [ProviderRow("cc-switch", "claude", "火山Ark", "https://x", True, ["m1", "m2"])],
            "codex": [ProviderRow("codex-plus", "codex", "Openrouter", "https://y", False, ["gpt-5"])],
        },
    )
    data = verbs.models_scan(cfg)
    rows = data["data"]["models"]
    assert [(r["provider"], r["model"]) for r in rows] == [("火山Ark", "m1"), ("火山Ark", "m2"), ("Openrouter", "gpt-5")]
    assert calls == []  # 不探端口
```

(b) **新增**:

```python
def test_scan_triples_shadow_bucket_fallback(cfg, monkeypatch):
    """存量标注挂在 legacy / "?" 影子桶下时,按精确→legacy→"?" 兜底显示(键统一的读侧)。"""
    from vision_relay.model_sources import ProviderRow

    monkeypatch.setattr(
        "vision_relay.model_sources.harness_matrix",
        lambda c: {"claude": [ProviderRow("cc-switch", "claude", "火山Ark", "https://x", True, ["m1", "m2"])]},
    )
    cfg.model_capabilities["claude"] = {
        "legacy": {"m1": "image"},
        "?": {"m2": "text_only"},
    }
    cfg.capability_sources["claude"] = {"legacy": {"m1": "user"}}
    rows = verbs._scan_triples(cfg)
    by_model = {r["model"]: r for r in rows}
    assert (by_model["m1"]["value"], by_model["m1"]["source"]) == ("image", "user")
    assert by_model["m2"]["value"] == "text_only"  # "?" 桶兜底


def test_scan_triples_probe_cached_shadow_fallback(cfg, monkeypatch):
    from vision_relay.model_sources import ProviderRow

    monkeypatch.setattr(
        "vision_relay.model_sources.harness_matrix",
        lambda c: {"claude": [ProviderRow("cc-switch", "claude", "p1", "https://x", True, ["m1"])]},
    )
    cfg.probe_results["?"] = {"m1": {"result": "image", "ts": 1}}
    assert verbs._scan_triples(cfg)[0]["probe_cached"] == "image"
```

注意:这两个测试必须 monkeypatch `vision_relay.model_sources.harness_matrix`,否则会读真机 `~/.cc-switch`(违反测试隔离约束)。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_proxy_verbs.py -q`
Expected: 3 个新/改测试 FAIL(当前 `_scan_triples` 走 scan_model_groups + `_provider_hint`);其余 PASS。

- [ ] **Step 3: 实现**

`verbs.py` 中替换 `_scan_triples` 与 `_provider_hint`(保留 `_provider_hint` 函数——`cli.py` 与其他调用方还在用,但 `_scan_triples` 不再调它):

```python
def _lookup_cap(cfg: ProxyConfig, harness: str, provider: str, model: str) -> tuple[str | None, str | None]:
    """能力读取:精确供应商桶 → legacy 影子桶 → "?" 影子桶(键统一迁移的读侧兜底)。"""
    for p in (provider, "legacy", "?"):
        v = cfg.model_capabilities.get(harness, {}).get(p, {}).get(model)
        if v is not None:
            s = cfg.capability_sources.get(harness, {}).get(p, {}).get(model)
            return v, s
    return None, None


def _lookup_probe(cfg: ProxyConfig, provider: str, model: str) -> str | None:
    for p in (provider, "?"):
        hit = (cfg.probe_results.get(p, {}).get(model) or {}).get("result")
        if hit is not None:
            return hit
    return None


def _scan_triples(cfg: ProxyConfig) -> list[dict]:
    """模型矩阵扫描:provider×model 来自工具档案(cc-switch DB / Codex++ settings.json,
    只读);工具未装(直连态)或读取失败 → live 配置正则扫描 + base_url 域名推导供应商。"""
    from . import model_sources

    rows: list[dict] = []
    for harness, provs in model_sources.harness_matrix(cfg).items():
        for pr in provs:
            for m in pr.models:
                value, source = _lookup_cap(cfg, harness, pr.provider, m)
                rows.append(
                    {
                        "harness": harness,
                        "provider": pr.provider,
                        "model": m,
                        "value": value,
                        "source": source,
                        "probe_cached": _lookup_probe(cfg, pr.provider, m),
                    }
                )
    return rows
```

`_provider_hint` 原样保留(仍被 `verbs.probe_all_untested` 旧实现与 `cli.py` 引用;Task 6 会把调用方切到 `model_sources.current_provider` 后它只剩 cli 兜底用途)。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_proxy_verbs.py tests/test_proxy_model_sources.py tests/test_e2e_g5_models.py -q`
Expected: 全 PASS(G5 e2e 走子进程假 HOME:无工具档案 → 直连兜底,provider 变 `origin.example`;其 `models-set` 用 `"?"` 写入仍能经影子桶兜底显示,断言不破)。

- [ ] **Step 5: ruff + commit**

```bash
.venv/bin/ruff format vision_relay/verbs.py tests/test_proxy_verbs.py
.venv/bin/ruff check vision_relay/verbs.py tests/test_proxy_verbs.py
git add vision_relay/verbs.py tests/test_proxy_verbs.py
git commit -m "feat(models): _scan_triples 改用工具档案矩阵,离线不探端口;legacy/? 影子桶读兜底"
```

---

### Task 4: `onboarding` 正则兜底层修复(字符集/去重/legacy 并入)

**Files:**
- Modify: `vision_relay/onboarding.py:20`(正则)、`:38-51`(`_extract_entries`)、`:83-100`(`scan_model_groups` 第 3 步)
- Test: `tests/test_proxy_routing.py`(增 2 个)

**Interfaces:**
- Consumes: 无新依赖
- Produces: `_MODEL_ENTRY` 值字符集含 `/` 与 `:`;`name` 仅保留带引号形式 `"name"`;`_extract_entries` 按**模型值**去重;`scan_model_groups` 把 legacy 桶中未出现的模型**并入已扫到的组**

- [ ] **Step 1: 写失败测试(追加到 tests/test_proxy_routing.py)**

```python
def test_extract_entries_charset_and_dedupe(tmp_path, monkeypatch):
    """斜杠/冒号模型名不再漏;同模型多变量只留一行;TOML 裸 name 不再误抓。"""
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
    f = tmp_path / "config.toml"
    f.write_text(
        'model = "stealth/ox-alpha"\n'
        'deep_tag = "deepseek-v4-flash:0731"\n'
        '[model_providers.custom]\nname = "custom"\nbase_url = "http://x"\n',
        encoding="utf-8",
    )
    entries = onboarding._extract_entries(str(f), None)
    models = [e.model for e in entries]
    assert models == ["stealth/ox-alpha", "deepseek-v4-flash:0731"]
    assert "custom" not in models  # 供应商标签不是模型


def test_extract_entries_dedupes_same_model_across_vars(tmp_path):
    """ANTHROPIC_DEFAULT_*_MODEL 与 *_NAME 成对同值 → 一行(原 (var,model) 去重出两行)。"""
    f = tmp_path / "settings.json"
    f.write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": "MiniMax-M3",
                    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "MiniMax-M3",
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "Kimi-K2.7-Code",
                }
            }
        ),
        encoding="utf-8",
    )
    entries = onboarding._extract_entries(str(f), None)
    assert [e.model for e in entries] == ["MiniMax-M3", "Kimi-K2.7-Code"]


def test_scan_model_groups_merges_legacy_into_scanned_group(tmp_path, monkeypatch):
    """legacy 桶里独有的模型(GLM-5.3)并入已扫到的 claude 组,不再静默丢弃。"""
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text('model = "gpt-5-codex"\n', encoding="utf-8")
    import vision_relay.wiring as W

    W.HOME = str(home)
    cfg = ProxyConfig(model_capabilities={"codex": {"legacy": {"gpt-5-codex": "text_only", "GLM-5.3": "image"}}})
    groups = onboarding.scan_model_groups(cfg)
    codex_g = next(g for g in groups if g.group == "codex")
    assert {e.model for e in codex_g.entries} == {"gpt-5-codex", "GLM-5.3"}
```

(若文件顶部尚未 `import json` / `from vision_relay import onboarding` / `ProxyConfig`,按现有 import 区补齐。)

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_proxy_routing.py -q -k "extract_entries or merges_legacy"`
Expected: 3 FAIL(斜杠名漏、重复行、legacy 丢弃)

- [ ] **Step 3: 实现**

`onboarding.py:20` 正则替换为:

```python
# 捕获 (变量名, 模型名):model.xxx / 带引号 "model" "name"(JSON 键)/ 裸 model(TOML/env 后缀)。
# 值字符集含 / 与 :(路由前缀 openrouter/x、ollama 标签 x:0731);裸 name 不匹配——
# TOML [model_providers.x] 表里的 name 是供应商标签,不是模型(qwen 的 JSON "name" 由带引号分支接住)。
_MODEL_ENTRY = re.compile(
    r"""(?i)(model\.\w+|"model"|"name"|model)["']?[ \t]*[:=][ \t]*["']([\w@.\-/:]+)["']"""
)
```

`_extract_entries`(onboarding.py:38-51)去重键从 `(var, model)` 改为模型值:

```python
def _extract_entries(path: str, source_url: str | None) -> list[ModelEntry]:
    try:
        txt = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    seen: set[str] = set()
    out: list[ModelEntry] = []
    for var, model in _MODEL_ENTRY.findall(txt):
        model = model.strip()
        if model and model not in seen:  # 按模型值去重:同模型多变量(_MODEL/_NAME)只留首行
            seen.add(model)
            out.append(ModelEntry(model=model, variable=var, source_url=source_url))
```

`scan_model_groups` 第 3 步(onboarding.py:89-98 的 `elif isinstance(v, dict):` 分支)替换为:

```python
        elif isinstance(v, dict):
            if m in {g.group for g in groups}:
                # 组已扫到:legacy 桶中该组独有的模型并入(不再静默丢弃)
                g0 = next(g for g in groups if g.group == m)
                have = {e.model for e in g0.entries}
                for mm in v:
                    if mm not in have:
                        g0.entries.append(ModelEntry(mm, "model_capabilities"))
            else:
                groups.append(
                    ModelGroup(
                        group=m,
                        path="proxy.json model_capabilities",
                        entries=[ModelEntry(mm, "model_capabilities") for mm in v],
                    )
                )
```

- [ ] **Step 4: 跑测试确认通过(含既有全量回归)**

Run: `.venv/bin/python -m pytest tests/test_proxy_routing.py tests/test_proxy_onboarding.py tests/test_proxy_verbs.py tests/test_e2e_g1_wizard.py -q`
Expected: 全 PASS。`test_scan_model_groups_partitions_by_harness` 中 qwen 断言依赖带引号 `"name"` 分支,仍成立;codex 的 `e.variable == "model"` 断言仍成立。

- [ ] **Step 5: ruff + commit**

```bash
.venv/bin/ruff format vision_relay/onboarding.py tests/test_proxy_routing.py
.venv/bin/ruff check vision_relay/onboarding.py tests/test_proxy_routing.py
git add vision_relay/onboarding.py tests/test_proxy_routing.py
git commit -m "fix(onboarding): 模型提取字符集补 /: 与按模型去重、TOML 裸 name 防误抓、legacy 桶独有模型并入扫描组"
```

---

### Task 5: `verbs.models_set` 影子桶清除(键统一的写侧)

**Files:**
- Modify: `vision_relay/verbs.py:223-252`(`models_set`)
- Test: `tests/test_proxy_verbs.py`(增 1 个)

**Interfaces:**
- Consumes: Task 3 的 `_lookup_cap` 语义(无需直接调用)
- Produces: `models-set` 写入规范桶 `(harness, provider, model)` 的同时,**清除同 `(harness, model)` 在 `legacy` 与 `?` 桶下的影子条目**(caps + sources 两张表都清;`value=null` 的清除路径同样先清影子)

- [ ] **Step 1: 写失败测试**

```python
def test_models_set_purges_shadow_buckets(cfg, monkeypatch):
    """保存到规范供应商桶时,清掉 legacy/? 影子桶的同模型旧条目——防双写后兜底读到旧值。"""
    _set_stdin(monkeypatch, [{"harness": "claude", "provider": "火山Ark", "model": "m1", "value": "image"}])
    cfg.model_capabilities["claude"] = {"legacy": {"m1": "text_only"}, "?": {"m1": "text_only"}}
    cfg.capability_sources["claude"] = {"legacy": {"m1": "user"}}
    out = verbs.models_set(cfg)
    assert out["ok"] is True
    caps = cfg.model_capabilities["claude"]
    assert caps["火山Ark"]["m1"] == "image"
    assert "m1" not in caps.get("legacy", {}) and "m1" not in caps.get("?", {})
    assert "m1" not in cfg.capability_sources["claude"].get("legacy", {})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_proxy_verbs.py -q -k purge`
Expected: FAIL(影子桶条目仍在)

- [ ] **Step 3: 实现**

`models_set` 的 `with config_lock():` 循环体(verbs.py 约 241-250 行)改为:

```python
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
            for shadow in ("legacy", "?"):  # 键统一:规范桶落笔即清影子,防兜底读到旧值
                if shadow != p:
                    cfg.model_capabilities.get(h, {}).get(shadow, {}).pop(m, None)
                    cfg.capability_sources.get(h, {}).get(shadow, {}).pop(m, None)
        save_config(cfg)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_proxy_verbs.py -q`
Expected: 全 PASS

- [ ] **Step 5: ruff + commit**

```bash
.venv/bin/ruff format vision_relay/verbs.py tests/test_proxy_verbs.py
.venv/bin/ruff check vision_relay/verbs.py tests/test_proxy_verbs.py
git add vision_relay/verbs.py tests/test_proxy_verbs.py
git commit -m "feat(models): models-set 写规范桶时清除 legacy/? 影子桶同模型条目(读写键统一收口)"
```

---

### Task 6: 探测目标直连候选 + 无目标/无结论反馈

**Files:**
- Modify: `vision_relay/verbs.py`(`probe_target_for` 约 413-425、`probe_one` 约 441-444、`probe_all_untested` 约 447-462)、`vision_relay/cli.py:307-318`(`_provider_for_group`)
- Test: `tests/test_proxy_verbs.py`(改 3 个、增 3 个)

**Interfaces:**
- Consumes: `model_sources.direct_provider_url` / `resolve_probe_key` / `current_provider` / `harness_matrix`
- Produces:
  - `probe_target_for(cfg, harness, provider, tool_by_name) -> tuple[str, str, str]` **签名不变**,解析顺序变为:在线工具端口 → **harness 自身直连上游(live 非回环 → snapshot 原始值,key 按 snapshot.key_ref 位置取)** → `cfg.relays` 非回环 → 空
  - `verbs.probe_target_info(cfg, harness, provider, tool_by_name=None) -> tuple[str, str, str, str | None]`(前三个同 `probe_target_for`,第四个为无目标原因;有目标时为 `None`)
  - `probe_one` envelope data:`{"result", "target_found": bool, "reason": str | None}`(ok 恒 True;**绝不包含 key**)
  - `probe_all_untested` envelope data:`{"probed", "results": [{harness, provider, model, result, target_found, reason}]}`;只尝试"当前激活供应商"的无缓存模型(非当前供应商的行不探测也不计入 results)
  - `cli._provider_for_group` 改走 `model_sources.current_provider`(矩阵 is_current → snapshot.second_hop → 在线工具激活供应商 → `?`),交互/非 JSON 路径与 GUI 键一致

- [ ] **Step 1: 写失败测试**

(a) `TestProbeJson` 两个既有测试(约 378-391 行)**改为**(envelope 增字段 + 隔离真机):

```python
class TestProbeJson:
    def test_probe_verb_envelope(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(verbs, "_run_probe", lambda cfg, h, p, m, tb=None: "image")
        monkeypatch.setattr(verbs, "probe_target_info", lambda cfg, h, p, tb=None: ("https://up.example", "k", "chat", None))
        out = verbs.probe_one(ProxyConfig(), harness="claude", provider="bigmodel", model="m1")
        assert out == {
            "contract_version": 1, "ok": True,
            "data": {"result": "image", "target_found": True, "reason": None},
        }

    def test_inconclusive_is_ok_with_null_result(self, tmp_path, monkeypatch):
        """含糊不下结论是合法结果(spec §5),不是错误:ok 恒 True。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(verbs, "_run_probe", lambda cfg, h, p, m, tb=None: None)
        monkeypatch.setattr(verbs, "probe_target_info", lambda cfg, h, p, tb=None: ("https://up.example", "k", "chat", None))
        out = verbs.probe_one(ProxyConfig(), harness="claude", provider="p", model="m")
        assert out["ok"] is True and out["data"]["result"] is None and out["data"]["target_found"] is True
```

(b) **新增**:

```python
def test_probe_one_no_target_reports_reason(tmp_path, monkeypatch):
    """无探测目标=ok 但 target_found=False + 原因(GUI 显示"不可达")。"""
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(verbs, "probe_target_info", lambda cfg, h, p, tb=None: ("", "", "chat", "claude: 路由工具不在线,且未配置可探测的直连上游"))
    out = verbs.probe_one(ProxyConfig(), harness="claude", provider="?", model="m")
    assert out["ok"] is True and out["data"]["result"] is None
    assert out["data"]["target_found"] is False
    assert out["data"]["reason"]  # 非空原因文案


def test_probe_target_info_direct_upstream_candidate(tmp_path, monkeypatch):
    """工具离线时,harness 自身直连上游(live→snapshot)成为探测目标,key 按 key_ref 位置取。"""
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://ark.example/api", "ANTHROPIC_AUTH_TOKEN": "tok-1"}}),
        encoding="utf-8",
    )
    import vision_relay.wiring as W

    monkeypatch.setattr(W, "HOME", str(home))
    base, key, proto, reason = verbs.probe_target_info(ProxyConfig(), "claude", "volces-ark", {})
    assert base == "https://ark.example/api" and key == "tok-1" and proto == "anthropic" and reason is None
```

(c) `TestProbeAllUntested.test_probes_only_uncached`(约 395-425 行)**改为**:

```python
class TestProbeAllUntested:
    def test_probes_only_uncached_current_provider(self, tmp_path, monkeypatch):
        """批量探测=当前激活供应商的无缓存模型;有缓存跳过;envelope 带 target_found/reason。"""
        from vision_relay.model_sources import ProviderRow

        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        monkeypatch.setattr(
            "vision_relay.model_sources.harness_matrix",
            lambda c: {
                "claude": [ProviderRow("cc-switch", "claude", "bigmodel", "https://x", True, ["m1", "m2", "m3", "m4"])],
                "codex": [ProviderRow("codex-plus", "codex", "Openrouter", "https://y", False, ["g5"])],
            },
        )
        monkeypatch.setattr(
            "vision_relay.model_sources.current_provider", lambda c, h: "bigmodel" if h == "claude" else "Openrouter"
        )
        monkeypatch.setattr(
            verbs, "probe_target_info", lambda cfg, h, p, tb=None: ("https://up.example", "", "chat", None)
        )
        called = []
        monkeypatch.setattr(verbs, "_run_probe", lambda cfg, h, p, m, tb=None: called.append((h, p, m)) or "image")
        cfg.probe_results.setdefault("bigmodel", {})["m1"] = {"result": "text_only", "ts": 1}
        cfg.probe_results.setdefault("bigmodel", {})["m2"] = {"result": "image", "ts": 1}
        out = verbs.probe_all_untested(cfg)
        assert out["ok"] is True
        assert out["data"]["probed"] == 2
        assert set(x[2] for x in called) == {"m3", "m4"}  # 只测无缓存的
        assert all(r["target_found"] is True and r["reason"] is None for r in out["data"]["results"])
```

(d) **新增** no-target 汇总:

```python
    def test_all_untested_no_target_marks_reason(self, tmp_path, monkeypatch):
        from vision_relay.model_sources import ProviderRow

        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        cfg = ProxyConfig()
        monkeypatch.setattr(
            "vision_relay.model_sources.harness_matrix",
            lambda c: {"claude": [ProviderRow("cc-switch", "claude", "bigmodel", "https://x", True, ["m9"])]},
        )
        monkeypatch.setattr("vision_relay.model_sources.current_provider", lambda c, h: "bigmodel")
        monkeypatch.setattr(
            verbs, "probe_target_info",
            lambda cfg, h, p, tb=None: ("", "", "chat", "claude: 路由工具不在线,且未配置可探测的直连上游"),
        )
        out = verbs.probe_all_untested(cfg)
        r = out["data"]["results"][0]
        assert r["result"] is None and r["target_found"] is False and r["reason"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_proxy_verbs.py -q -k "probe"`
Expected: 新增/改动 5 个 FAIL(`probe_target_info` 不存在等),旧断言因 envelope 缺字段 FAIL。

- [ ] **Step 3: 实现**

(a) `verbs.py` 新增导入(文件顶部已 `from . import ...` 区域按现状风格补):函数内懒导入即可。新增:

```python
def probe_target_info(
    cfg: ProxyConfig, harness: str, provider: str, tool_by_name: dict | None = None
) -> tuple[str, str, str, str | None]:
    """探测目标 + 无目标原因。probe_target_for 的超集(前三位相同)。"""
    if tool_by_name is None:
        from .reconcile import observe

        tool_by_name = {t["name"]: t for t in observe(cfg)["tools"]}
    base, key, proto = probe_target_for(cfg, harness, provider, tool_by_name)
    if base:
        return base, key, proto, None
    return base, key, proto, f"{harness}: 路由工具不在线,且未配置可探测的直连上游"
```

(b) `probe_target_for`(verbs.py:413-425)在工具端口循环之后、`cfg.relays` 循环**之前**插入直连候选:

```python
    # 直连候选:harness 自身配置(或接管前快照)指向上游时,直接探真实上游;
    # key 按 snapshot.key_ref 的位置描述取(仅进程内使用,绝不进 envelope/日志)。
    from . import model_sources, snapshot

    direct = model_sources.direct_provider_url(harness)
    if direct:
        snap = snapshot.load().get(harness)
        return direct, model_sources.resolve_probe_key(harness, snap.key_ref if snap is not None else None), proto
```

(c) `probe_one` 替换为:

```python
def probe_one(cfg: ProxyConfig, harness: str, provider: str, model: str) -> dict:
    tool_by_name = {t["name"]: t for t in _observe_impl(cfg)["tools"]}
    base, _key, _proto, reason = probe_target_info(cfg, harness, provider, tool_by_name)
    if not base:
        # 无结论(含无目标)= 合法三态(spec §5),不是错误;GUI 按 target_found 显示"不可达"
        return envelope(True, {"result": None, "target_found": False, "reason": reason})
    result = _run_probe(cfg, harness, provider, model, tool_by_name)
    return envelope(True, {"result": result, "target_found": True, "reason": None})
```

(d) `probe_all_untested` 替换为:

```python
def probe_all_untested(cfg: ProxyConfig) -> dict:
    """批量探测:当前激活供应商的无缓存 (provider, model) 组合。
    非当前供应商的行不经工具路由、无探测路径——不尝试、不计入 results。"""
    from . import model_sources

    matrix = model_sources.harness_matrix(cfg)
    tool_by_name = {t["name"]: t for t in _observe_impl(cfg)["tools"]}
    probed: list[dict] = []
    for harness, rows in matrix.items():
        cur = model_sources.current_provider(cfg, harness)
        for row in rows:
            if row.provider != cur:
                continue
            for m in row.models:
                if _lookup_probe(cfg, row.provider, m):
                    continue  # 有缓存结论,跳过
                base, _key, _proto, reason = probe_target_info(cfg, harness, row.provider, tool_by_name)
                if not base:
                    probed.append(
                        {"harness": harness, "provider": row.provider, "model": m,
                         "result": None, "target_found": False, "reason": reason}
                    )
                    continue
                result = _run_probe(cfg, harness, row.provider, m, tool_by_name)
                probed.append(
                    {"harness": harness, "provider": row.provider, "model": m,
                     "result": result, "target_found": True, "reason": None}
                )
    return envelope(True, {"probed": len(probed), "results": probed})
```

(e) `cli.py` `_provider_for_group`(307-318 行)替换为:

```python
def _provider_for_group(group: str, tool_by_name: dict) -> str | None:
    """harness -> 当前供应商名:工具档案矩阵 is_current 优先(磁盘真相,与工具在不在线无关);
    退在线工具激活供应商;直连场景名未知回 None,调用方用 '?' 占位。"""
    from . import model_sources, tools
    from .config import load_config

    try:
        hit = model_sources.current_provider(load_config(), group)
        if hit != "?":
            return hit
    except Exception:  # noqa: BLE001 - 档案读取失败走旧链路
        pass
    for name, d in tools.TOOL_DOSSIERS.items():
        if (
            group in d.harnesses
            and tool_by_name.get(name, {}).get("online")
            and tool_by_name[name].get("active_provider")
        ):
            return tool_by_name[name]["active_provider"]
    return None
```

- [ ] **Step 4: 跑测试确认通过(全量回归)**

Run: `.venv/bin/python -m pytest -q`
Expected: 全 PASS。若 `tests/test_integration_m1.py` / `test_integration_cross_process.py` 有断言扫描行 provider 的用例失败:直连态(假 HOME、无工具档案)下 provider 现为 `origin.example`(域名推导),按此语义更新断言。

- [ ] **Step 5: ruff + commit**

```bash
.venv/bin/ruff format vision_relay/verbs.py vision_relay/cli.py tests/test_proxy_verbs.py
.venv/bin/ruff check vision_relay/verbs.py vision_relay/cli.py tests/test_proxy_verbs.py
git add vision_relay/verbs.py vision_relay/cli.py tests/test_proxy_verbs.py
git commit -m "feat(probe): 直连上游探测候选(工具离线也能真探测);无目标/无结论透出 target_found+reason,批量探测限定当前供应商"
```

---

### Task 7: GUI 反馈(Models.tsx)+ vitest

**Files:**
- Modify: `gui/src/pages/Models.tsx:27-34`(`retest`/`probeAll`)
- Test: `gui/src/pages/Models.test.tsx`(追加 describe)

**Interfaces:**
- Consumes: Task 6 的 envelope 字段(`result` / `target_found` / `reason` / `probed` / `results`)
- Produces: 行为不变量——`result` 非空时静默刷新(现状);`result` 为 null 时必弹窗:无目标→`reason`(“不可达”),有目标→“已探测但无结论”;`probeAll` 的 `probed===0` 提示无可探测项

- [ ] **Step 1: 写失败测试(追加到 Models.test.tsx 末尾)**

```tsx
describe("ModelsPage 探测反馈", () => {
  beforeEach(() => {
    coreMock.mockReset();
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      return {};
    });
  });

  it("重测:无目标时弹 reason(不可达),不再静默", async () => {
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    coreMock.mockImplementation(async (verb: string, opts?: { args?: string[] }) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      if (verb === "probe" && opts?.args?.[0] === "--all-untested") return { probed: 0, results: [] };
      return { result: null, target_found: false, reason: "claude: 路由工具不在线,且未配置可探测的直连上游" };
    });
    render(<ModelsPage lang="zh" refresh={vi.fn()} />);
    await screen.findByText("gpt-5-codex");
    fireEvent.click(screen.getAllByText("重测")[0]);
    await waitFor(() => expect(alert).toHaveBeenCalledWith(expect.stringContaining("路由工具不在线")));
    alert.mockRestore();
  });

  it("重测:有目标但无结论时提示不下判定", async () => {
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      return { result: null, target_found: true, reason: null };
    });
    render(<ModelsPage lang="zh" refresh={vi.fn()} />);
    await screen.findByText("gpt-5-codex");
    fireEvent.click(screen.getAllByText("重测")[0]);
    await waitFor(() => expect(alert).toHaveBeenCalledWith(expect.stringContaining("无结论")));
    alert.mockRestore();
  });

  it("探测全部:全部无结论时汇总弹窗;有结论时静默", async () => {
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    coreMock.mockImplementation(async (verb: string, opts?: { args?: string[] }) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      if (verb === "probe" && opts?.args?.[0] === "--all-untested")
        return {
          probed: 2,
          results: [
            { result: null, target_found: false, reason: "claude: 不可达" },
            { result: null, target_found: true, reason: null },
          ],
        };
      return {};
    });
    render(<ModelsPage lang="zh" refresh={vi.fn()} />);
    await screen.findByText("gpt-5-codex");
    fireEvent.click(screen.getByText("🔍 探测全部未测"));
    await waitFor(() => expect(alert).toHaveBeenCalledWith(expect.stringContaining("均无结论")));
    alert.mockClear();
    // 有结论 → 静默
    coreMock.mockImplementation(async (verb: string, opts?: { args?: string[] }) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      if (verb === "probe" && opts?.args?.[0] === "--all-untested")
        return { probed: 1, results: [{ result: "image", target_found: true, reason: null }] };
      return {};
    });
    fireEvent.click(screen.getByText("🔍 探测全部未测"));
    await waitFor(() => expect(coreMock).toHaveBeenCalled());
    expect(alert).not.toHaveBeenCalled();
    alert.mockRestore();
  });
});
```

注意既有测试的 core mock 对 `probe` 返回 `{}`(`target_found` undefined):新代码不得因此弹窗(判 `=== false` / `=== null`)。若既有「重测」用例断言刷新行为,保持其通过。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gui && pnpm vitest run src/pages/Models.test.tsx`
Expected: 新 describe 3 个 FAIL(alert 未被调用)

- [ ] **Step 3: 实现(Models.tsx)**

`retest` 与 `probeAll` 替换为:

```tsx
  const retest = async (r: Triple) => {
    setBusy(r.model);
    try {
      const d = await core<{ result: string | null; target_found: boolean; reason: string | null }>("probe", { args: ["--harness", r.harness, "--provider", r.provider, "--model", r.model] });
      if (d.result === null)
        window.alert(d.target_found === false ? (d.reason ?? "探测目标不可达") : "已探测但无结论（超时/鉴权/回答含糊），不下判定");
      await refreshRows();
    } catch (e) { console.error(e); window.alert(String(e)); } finally { setBusy(""); }
  };
  const probeAll = async () => {
    setBusy("all");
    try {
      const d = await core<{ probed: number; results: { result: string | null; target_found: boolean; reason: string | null }[] }>("probe", { args: ["--all-untested"] });
      if (d.probed === 0) window.alert("没有待探测的模型（全部已有缓存结论）");
      else if (d.results.every((x) => x.result === null)) {
        const first = d.results.find((x) => x.target_found === false);
        window.alert(`已探测 ${d.probed} 个，均无结论${first ? "：" + (first.reason ?? "目标不可达") : "（超时/鉴权/回答含糊）"}`);
      }
      await refreshRows();
    } catch (e) { console.error(e); window.alert(String(e)); } finally { setBusy(""); }
  };
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd gui && pnpm test`
Expected: 全 PASS(含既有用例)

- [ ] **Step 5: commit**

```bash
cd /Users/jarvis/Documents/vision-relay/gui
git add src/pages/Models.tsx src/pages/Models.test.tsx
git commit -m "feat(gui): 模型页探测反馈——无目标弹不可达原因、无结论弹说明、批量探测汇总"
```

---

### Task 8: e2e 对齐 + spec 更新 + 全量门禁

**Files:**
- Modify: `tests/test_e2e_g5_models.py:40-46`(models-set 的 provider)
- Modify: `docs/superpowers/specs/2026-08-21-vision-relay-phase2-control-plane-design.md`(模型页行 + 动词说明)

**Interfaces:**
- Consumes: 前面全部任务
- Produces: e2e 与新键语义一致;spec 记录矩阵来源与反馈契约

- [ ] **Step 1: 更新 e2e(直连态 provider 为域名推导名)**

`test_models_page_triset_save_and_retest` 中把:

```python
        stdin=json.dumps([{"harness": "codex", "provider": "?", "model": "gpt-5-codex", "value": "image"}]),
```

改为:

```python
        stdin=json.dumps([{"harness": "codex", "provider": "origin.example", "model": "gpt-5-codex", "value": "image"}]),
```

并在初始扫描断言后追加一行(锁定新语义):

```python
    assert any(r["provider"] == "origin.example" for r in rows)  # 直连态:域名推导供应商名
```

- [ ] **Step 2: 跑 e2e 确认通过**

Run: `cd /Users/jarvis/Documents/vision-relay && .venv/bin/python -m pytest tests/test_e2e_g5_models.py -q`
Expected: PASS

- [ ] **Step 3: 更新 spec(同 PR 行为变更同步)**

在 `docs/superpowers/specs/2026-08-21-vision-relay-phase2-control-plane-design.md`:

1. 页面矩阵表中 `| 模型 ...` 行(约 158-162 行区域,grep `| 模型`)的扫描来源描述后**追加**:
   > 扫描矩阵来自工具档案只读读取（cc-switch SQLite `providers` 表 / Codex++ `settings.json` `relayProfiles`，含供应商名、base_url、模型清单、is_current/activeRelayId；密钥字段与 `modelVlm` 一律不读——后者用户裁决不准）；两工具均未装（直连态）时按 harness 配置 base_url 域名推导供应商名（已知域名映射表 + 主机名兜底）。能力标注键统一为该供应商名，legacy/`?` 影子桶读兜底、写清除。探测目标解析：在线工具端口 → harness 自身直连上游（live→snapshot，key 按 key_ref 位置取）→ relay 直连；无目标/无结论经 `target_found`+`reason` 透出，GUI 弹窗可见。
2. 若文中有 `models-scan`/`probe` 动词的字段表,同步补 `target_found`/`reason` 字段说明。

- [ ] **Step 4: 全量门禁**

```bash
cd /Users/jarvis/Documents/vision-relay
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
cd gui && pnpm test && pnpm build
```

Expected: ruff 两项零告警;pytest 全 PASS;vitest 全 PASS;`tsc --noEmit && vite build` 成功。

- [ ] **Step 5: commit**

```bash
cd /Users/jarvis/Documents/vision-relay
git add tests/test_e2e_g5_models.py docs/superpowers/specs/2026-08-21-vision-relay-phase2-control-plane-design.md
git commit -m "test,docs: G5 e2e 对齐域名推导供应商键;spec 记录模型矩阵来源与探测反馈契约"
```

---

## Self-Review 记录(计划作者已核)

1. **需求覆盖**:矩阵来源(工具档案→组合;不读 modelVlm)✓ Task 1/2;直连态域名推导 ✓ Task 2;读写键统一(legacy/? 迁移)✓ Task 3/5;探测直连候选与"不可达/无结论"反馈 ✓ Task 6/7;正则兜底修复(去重按三元组在矩阵层天然成立、兜底层按模型去重)✓ Task 4;legacy 桶模型不丢 ✓ Task 4;spec 同步 ✓ Task 8。
2. **占位符扫描**:Task 2 测试代码首行的 `import wiring_stub` 已标注为"故意不写";其余步骤均含完整代码/命令/预期。
3. **类型一致性**:`ProviderRow` 字段、`probe_target_info` 四元组、`_lookup_cap/_lookup_probe` 签名在 Task 3/5/6 间引用一致;`probe_target_for` 三元组签名未变(cli 两处调用不受影响)。
