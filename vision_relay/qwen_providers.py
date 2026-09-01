"""qwen-code 条目级接线：modelProviders 的改写/还原/relay 维护/统计/对账。

qwen-code ≥0.22.0 起请求端点取 modelProviders 条目自身 baseUrl（优先于
model.baseUrl），故接管必须条目级改写并代开 image 准入门。本模块负责该 harness
的条目收集、快照键计算、改写与对称还原、一层直连 relay 的增删、统计与漂移对账。
所有入口函数接收显式 home，不读全局 HOME（由 wiring  facade 注入测试隔离点）。
"""

from __future__ import annotations

import json
import os

from . import snapshot
from .config import RelayConfig, save_config
from .harness_io import _first_model, _json_save_atomic
from .harness_spec import _MOD_ABSENT, _QWEN_AUTH_PROTO, _QWEN_RELAY_PREFIX, _path, classify_base_url
from .modalities import _modalities_open, _open_modalities
from .tools import TOOL_DOSSIERS


def _qwen_provider_items_from(data: dict) -> tuple[list[tuple[str, int, dict]], int]:
    """在已解析的 settings.json dict 上收集可改写条目（同一份 dict，改动可落回）。"""
    mp = data.get("modelProviders")
    if not isinstance(mp, dict):
        return [], 0
    items: list[tuple[str, int, dict]] = []
    skipped = 0
    for auth_type, val in mp.items():
        if auth_type not in _QWEN_AUTH_PROTO:
            if isinstance(val, list):
                skipped += sum(
                    1 for e in val if isinstance(e, dict) and isinstance(e.get("baseUrl"), str) and e.get("baseUrl")
                )
            elif isinstance(val, dict):
                skipped += 1
            continue
        if not isinstance(val, list):  # wrapped 旧形态
            skipped += 1
            continue
        for i, e in enumerate(val):
            if isinstance(e, dict) and isinstance(e.get("baseUrl"), str) and e.get("baseUrl"):
                items.append((auth_type, i, e))
            else:
                skipped += 1
    return items, skipped


def _qwen_provider_items(path: str) -> tuple[list[tuple[str, int, dict]], int]:
    """读 modelProviders 的可改写条目（authType→协议已知 + 裸数组形态 + baseUrl 非空）。

    返回 ([(authType, index, entry)], skipped)。skipped 计 gemini/vertex/custom 等
    不支持协议与 wrapped 旧形态（{protocol, models}，qwen 0.22.0 已忽略该形态）——
    它们原样保留、不接管（无协议外壳可转写）。"""
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], 0
    return _qwen_provider_items_from(d)


def _qwen_entry_keys(items: list[tuple[str, int, dict]]) -> list[str]:
    """改写侧快照键：envKey 名（位置引用，非 key 值）。同 envKey 多条目共用一个键
    （同供应商多模型）；同 envKey 但原始 URL 不同者以 #id 消歧。无 envKey 用
    authType|id|index。键只依赖 (envKey,id) 等稳定量——改写后 baseUrl 全变代理地址，
    键计算不得依赖它。"""
    first_url: dict[str, str] = {}
    keys = []
    for auth, idx, e in items:
        env = str(e.get("envKey") or "")
        eid = str(e.get("id") or idx)
        url = str(e.get("baseUrl") or "")
        if not env:
            keys.append(f"{auth}|{eid}|{idx}")
        elif env not in first_url:
            first_url[env] = url
            keys.append(env)
        elif url == first_url[env]:
            keys.append(env)
        else:
            keys.append(f"{env}#{eid}")
    return keys


def _qwen_resolve_key(auth: str, idx: int, entry: dict, provider_urls: dict[str, str]) -> str | None:
    """还原侧键解析（与 _qwen_entry_keys 语义互逆）：#id 消歧键优先（更具体），
    裸 envKey 兜底；无对应记录返回 None（该条目不动）。"""
    env = str(entry.get("envKey") or "")
    eid = str(entry.get("id") or idx)
    if env:
        specific = f"{env}#{eid}"
        if specific in provider_urls:
            return specific
        if env in provider_urls:
            return env
        return None
    key = f"{auth}|{eid}|{idx}"
    return key if key in provider_urls else None


def _rewrite_qwen_providers(path: str, proxy_url: str) -> tuple[dict[str, str], dict[str, object], int, int, int]:
    """把全部可改写条目 baseUrl 指到本代理并代开 modalities 准入门。

    返回 (URL 新原值映射, modalities 原值映射, skipped, URL 改写条数, 开门条数)。
    两类映射都只含本次实际变更的条目（已就位者不产生记录，防把代理地址/开门态
    存成"原始值"；同 envKey 多条目共用一键，故映射数 ≤ 条数）。"""
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}, 0, 0, 0
    items, skipped = _qwen_provider_items_from(d)
    url_originals: dict[str, str] = {}
    mod_originals: dict[str, object] = {}
    rewritten = gated = 0
    for (auth, _idx, e), key in zip(items, _qwen_entry_keys(items)):
        if not (e["baseUrl"] == proxy_url or e["baseUrl"].startswith(proxy_url + "/")):
            url_originals[key] = e["baseUrl"]
            e["baseUrl"] = proxy_url
            rewritten += 1
        if not _modalities_open(e):
            mod_originals[key] = _open_modalities(e)
            gated += 1
    if (url_originals or mod_originals) and not _json_save_atomic(path, d):
        return {}, {}, skipped, 0, 0
    return url_originals, mod_originals, skipped, rewritten, gated


def _restore_qwen_providers(
    path: str, proxy_url: str, provider_urls: dict[str, str], provider_modalities: dict[str, object] | None
) -> int:
    """按快照映射逐条写回原值（URL + modalities 门）。守卫：仅当前 baseUrl 仍指向
    本代理才动（用户运行期改走别处的条目原样保留）。返回还原条数。"""
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    items, _ = _qwen_provider_items_from(d)
    provider_modalities = provider_modalities or {}
    restored = 0
    for auth, idx, e in items:
        cur = e.get("baseUrl")
        if not (cur == proxy_url or (isinstance(cur, str) and cur.startswith(proxy_url + "/"))):
            continue
        key = _qwen_resolve_key(auth, idx, e, provider_urls)
        touched = False
        if key is not None and key in provider_urls:
            e["baseUrl"] = provider_urls[key]
            touched = True
        if key is not None and key in provider_modalities:
            gc = e.get("generationConfig")
            if isinstance(gc, dict):
                orig = provider_modalities[key]
                if orig == _MOD_ABSENT:
                    gc.pop("modalities", None)
                else:
                    gc["modalities"] = orig
                touched = True
        if touched:
            restored += 1
    if restored:
        _json_save_atomic(path, d)
    return restored


def _qwen_relay_name(base_url: str, taken: set[str]) -> str:
    import re as _re
    from urllib.parse import urlparse

    host = (urlparse(base_url).hostname or "upstream").lower()
    name = _QWEN_RELAY_PREFIX + _re.sub(r"[^a-z0-9.-]+", "-", host)
    if name not in taken:
        return name
    i = 2
    while f"{name}-{i}" in taken:
        i += 1
    return f"{name}-{i}"


def _qwen_relay_groups(
    path: str, provider_urls: dict[str, str] | None, bind_port: int
) -> dict[tuple[str, str], list[str]]:
    """(协议, 原始 baseUrl) → 条目 id 列表。工具端口原值（两层语义，由既有 relay
    服务）与已指本代理者不建 relay。"""
    if not provider_urls:
        return {}
    items, _ = _qwen_provider_items(path)
    groups: dict[tuple[str, str], list[str]] = {}
    for auth, idx, e in items:
        # 改写后条目 baseUrl 全为代理地址，键只能按解析规则取（#id 优先、裸键兜底），
        # 不能重算——冲突检测依赖的原始 URL 信号已被改写抹掉
        key = _qwen_resolve_key(auth, idx, e, provider_urls)
        if key is None:
            continue
        orig = provider_urls[key]
        owner = classify_base_url(orig, bind_port)
        if owner == "ours" or owner in TOOL_DOSSIERS:
            continue
        groups.setdefault((_QWEN_AUTH_PROTO[auth], orig), []).append(str(e.get("id") or idx))
    return groups


def ensure_qwen_relays(cfg, home: str) -> list[str]:
    """按接管快照 + 当前 settings.json 维护 qwen 一层直连 relay（spec §5：qwen-code
    无路由工具，永远一层直连）。缺失则建（prepend，精确 id 先于通配 "*" 命中），
    原值已不存在于快照的旧 qwen relay 移除。models 为条目 id 精确匹配；api_key 留空
    ——转发时透传客户端自己的鉴权头（统一鉴权链）。返回新增 name 列表。"""
    p = _path(home, "qwen-code")
    if not os.path.exists(p):
        return []
    snap = snapshot.load().get("qwen-code")
    groups = _qwen_relay_groups(p, getattr(snap, "provider_urls", None), cfg.bind_port)
    changed = False
    # 清理：activated 里的 qwen relay 若其 (协议, 原始地址) 已不在当前分组 → 移除
    keep = []
    for r in cfg.relays:
        if (
            r.name.startswith(_QWEN_RELAY_PREFIX)
            and r.name in cfg.routing.activated_relays
            and (r.protocol, r.base_url) not in groups
        ):
            changed = True
            continue
        keep.append(r)
    cfg.relays = keep
    added = []
    taken = {r.name for r in cfg.relays}
    for (proto, orig), ids in groups.items():
        if any(r.protocol == proto and r.base_url == orig for r in cfg.relays):
            continue
        name = _qwen_relay_name(orig, taken)
        taken.add(name)
        cfg.relays.insert(0, RelayConfig(name=name, protocol=proto, base_url=orig, models=list(ids)))
        if name not in cfg.routing.activated_relays:
            cfg.routing.activated_relays.append(name)
        added.append(name)
        changed = True
    if changed:
        save_config(cfg)
    return added


def reconcile_qwen_providers(cfg, home: str) -> dict | None:
    """接管态下校正 qwen provider 条目漂移：非本代理的条目重指本代理、被关的
    modalities 门重开，新原值吸收进快照（对应 absorb 语义，spec §5 始终接管）。
    返回 None 或摘要。"""
    p = _path(home, "qwen-code")
    if not os.path.exists(p):
        return None
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    new_urls, new_mods, _, _, _ = _rewrite_qwen_providers(p, proxy_url)
    if not new_urls and not new_mods:
        return None
    old = snapshot.load().get("qwen-code")
    merged = dict(old.provider_urls or {}) if old and old.provider_urls else {}
    merged.update(new_urls)
    merged_mod = dict(old.provider_modalities or {}) if old and old.provider_modalities else {}
    merged_mod.update(new_mods)
    try:
        snapshot.save(
            "qwen-code",
            snapshot.Snapshot(
                base_url=old.base_url if old else proxy_url,
                key_ref=snapshot.key_ref_for("qwen-code"),
                model=old.model if old else _first_model(p),
                second_hop=old.second_hop if old else None,
                provider_urls=merged or None,
                provider_modalities=merged_mod or None,
            ),
        )
    except Exception:  # 快照尽力而为，不打断重接管
        pass
    return {"rewritten": len(new_urls), "gated": len(new_mods)}


def _qwen_provider_stats(path: str, proxy_url: str) -> dict[str, int]:
    """qwen modelProviders 条目统计：total=带 baseUrl 的条目数，eligible=可改写
    （协议已知+裸数组），wired=已指本代理，gated=modalities 准入门已开（门不开
    图片根本进不了请求），skipped=不支持协议/旧形态。"""
    empty = {"total": 0, "eligible": 0, "wired": 0, "gated": 0, "skipped": 0}
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    mp = d.get("modelProviders")
    if not isinstance(mp, dict):
        return empty
    total = eligible = wired = gated = 0
    for auth_type, val in mp.items():
        entries = val if isinstance(val, list) else []
        for e in entries:
            if not (isinstance(e, dict) and isinstance(e.get("baseUrl"), str) and e.get("baseUrl")):
                continue
            total += 1
            if auth_type in _QWEN_AUTH_PROTO:
                eligible += 1
                if e["baseUrl"] == proxy_url or e["baseUrl"].startswith(proxy_url + "/"):
                    wired += 1
                if _modalities_open(e):
                    gated += 1
    return {"total": total, "eligible": eligible, "wired": wired, "gated": gated, "skipped": total - eligible}
