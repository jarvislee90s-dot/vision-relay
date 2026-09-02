"""模型矩阵来源层(spec §5):cc-switch / Codex++ 工具档案只读读取 + 直连兜底。

铁律:绝不写工具的任何文件;sqlite 只以 mode=ro 打开;只读白名单键
(模型名、base_url、供应商名、is_current)——任何密钥字段不读、不外传、不落日志。
读取 best-effort:任何失败返回空,由调用方降级到直连扫描。
禁止读取 Codex++ 的 modelVlm(用户裁决 2026-08-24:该配置不准)。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from urllib.parse import urlparse

try:  # Python ≥3.11 才有 tomllib;3.10 退正则
    import tomllib
except ImportError:  # pragma: no cover - 取决于解释器版本
    tomllib = None

from .tools import CCSWITCH_DB as _CCSWITCH_DB_DEFAULT
from .tools import CODEXPP_SETTINGS as _CODEXPP_DEFAULT

# 模块级常量:测试 monkeypatch 的挂点(默认指向真机路径)
CCSWITCH_DB = _CCSWITCH_DB_DEFAULT
CODEXPP_SETTINGS = _CODEXPP_DEFAULT


@dataclass(frozen=True)
class ProviderRow:
    tool: str  # cc-switch | codex-plus | direct
    harness: str  # claude | codex | qwen-code
    provider: str  # 工具档案里的供应商显示名;直连态为域名推导名或 "?"
    base_url: str
    is_current: bool
    models: list[str]


# claude settings_config 里视作"模型值"的 env 键(白名单:不含任何 token 键)。
# 只认 *_MODEL（传给 claude 的真实模型字段）；*_MODEL_NAME 是展示名，混进收集
# 会把 GLM-5.3[1M]/GLM-5.3 这类成对条目拆成两个"模型"（2026-09-02 回归）。
_CLAUDE_MODEL_KEY = re.compile(r"^ANTHROPIC_(?:DEFAULT_\w+_)?MODEL$")


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
        models.insert(0, top)  # 顶层 model 是主模型,列首位;env 模型随后(测试断言该顺序)
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
                tool="cc-switch",
                harness=app_type,
                provider=name,
                base_url=base if isinstance(base, str) else "",
                is_current=bool(is_current),
                models=models,
            )
        )
    return out


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
                tool="codex-plus",
                harness="codex",
                provider=str(p.get("name") or p.get("id") or "?"),
                base_url=base if isinstance(base, str) else "",
                is_current=p.get("id") == active,
                models=models,
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
    """按 snapshot 的 key 位置描述取真实 key 值(仅进程内使用,绝不进 envelope/日志)。

    HOME 用 wiring.HOME(测试 monkeypatch 挂点),绝不读真机 ~。"""
    if not key_ref:
        return ""
    import pathlib

    from . import snapshot as snap_mod
    from . import wiring

    path: pathlib.Path | None = None
    file_parts = snap_mod._KEY_FIELDS.get(harness, ((None,), ()))[0]
    if file_parts and file_parts[0]:
        cand = pathlib.Path(wiring.HOME).joinpath(*file_parts)
        path = cand if cand.exists() else None
    try:
        if key_ref.startswith("env.") and path is not None:
            return str(json.loads(path.read_text(encoding="utf-8")).get("env", {}).get(key_ref[4:], "")) or ""
        if key_ref == "model.apiKey" and path is not None:
            return str(json.loads(path.read_text(encoding="utf-8")).get("model", {}).get("apiKey", "")) or ""
        if key_ref.endswith("auth.json"):
            auth = pathlib.Path(wiring.HOME) / ".codex" / "auth.json"
            return str(json.loads(auth.read_text(encoding="utf-8")).get("OPENAI_API_KEY", "")) or ""
    except (OSError, ValueError):
        return ""
    return os.environ.get(key_ref, "")


def _direct_rows(cfg, harness: str) -> list[ProviderRow]:
    """直连兜底行:模型来自 live 配置正则扫描;供应商由 base_url 域名推导,未知 → "?"。

    即使无模型也产一行(provider 语义"直连未知"),让 current_provider 恒有结论;
    空 models 不会产生任何 GUI 行(_scan_triples 按 models 展开)。"""
    from .onboarding import scan_model_groups

    models: list[str] = []
    for g in scan_model_groups(cfg):
        if g.group == harness:
            models = _dedup_keep_order([e.model for e in g.entries])
    url = direct_provider_url(harness)
    provider = provider_from_url(url) if url else None
    return [
        ProviderRow(
            tool="direct",
            harness=harness,
            provider=provider or "?",
            base_url=url or "",
            is_current=True,
            models=models,
        )
    ]


def _ccswitch_installed() -> bool:
    return os.path.exists(CCSWITCH_DB)


def _codexpp_installed() -> bool:
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
        elif harness == "zcode":
            rows = zcode_matrix(cfg)
        elif harness == "codex":
            s = snap.get("codex")
            tool = s.second_hop if s is not None and s.second_hop else None
            if tool == "cc-switch" and _ccswitch_installed():
                rows = ccswitch_matrix().get("codex", [])
            elif _codexpp_installed():
                rows = codexpp_matrix()
            elif _ccswitch_installed():
                rows = ccswitch_matrix().get("codex", [])
        if not rows and harness != "zcode":  # zcode 矩阵真相=config.json，文件缺失=空矩阵，不走直连兜底
            rows = _direct_rows(cfg, harness)
        out[harness] = rows
    return out


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


def current_provider(cfg, harness: str) -> str:
    for row in harness_matrix(cfg).get(harness, []):
        if row.is_current:
            return row.provider
    from . import snapshot

    s = snapshot.load().get(harness)
    return s.second_hop if s is not None and s.second_hop else "?"
