"""zcode 条目级接线：provider.<id>.options.baseURL 的改写/还原/relay 维护/统计/对账。

zcode 供应商配置是纯条目级（无全局 base_url），接管须把可接管供应商的 baseURL
指到本代理并给纯文本模型补 image 模态门与 modalitiesConfigured 标记。本模块负责
条目收集、快照身份键、改写与对称还原、一层直连 relay 的增删（指纹随行）、统计与
漂移对账，以及改写时间戳标记（待重启判定）。入口函数接收显式 home，不读全局 HOME。
"""

from __future__ import annotations

import json
import os
import re
import time

from . import fingerprint, snapshot
from .config import RelayConfig, save_config
from .harness_io import _json_save_atomic
from .harness_spec import _MOD_ABSENT, _ZCODE_PROTO, _ZCODE_RELAY_PREFIX, _path, classify_base_url
from .modalities import _ensure_image, _mod_input
from .tools import TOOL_DOSSIERS


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
                    m["modalities"]["input"] = list(
                        rec["input"]
                    )  # 整列表写回原值（原值必不含 image——只记录过缺 image 的模型）
                zc = m.get("zcode")
                if isinstance(zc, dict) and "flag" in rec:
                    if rec["flag"] == _MOD_ABSENT:
                        zc.pop("modalitiesConfigured", None)
                        if not zc:  # M5: 空壳（开窗时 setdefault 创建）一并移除；非空=用户数据不动
                            m.pop("zcode", None)
                    else:
                        zc["modalitiesConfigured"] = rec["flag"]
                touched = True
        if touched:
            restored += 1
    if restored and _json_save_atomic(path, d):
        _mark_zcode_rewrite()
    return restored


def _zcode_slug(pid: str) -> str:
    return re.sub(r"[^a-z0-9.-]+", "-", pid.lower()).strip("-") or "provider"


def _zcode_relay_desired(
    d: dict, provider_urls: dict[str, str] | None, proxy_url: str, bind_port: int
) -> list[RelayConfig]:
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


def _is_zcode_relay(r) -> bool:
    """zcode 自动条目判定：provider_id + 前缀双条件（手编同名前缀 relay 不误伤）。"""
    return bool(getattr(r, "provider_id", None)) and r.name.startswith(_ZCODE_RELAY_PREFIX)


def ensure_zcode_relays(cfg, home: str) -> list[str]:
    """按现场 config.json + 快照维护 zcode 一层直连 relay（spec §6）：一供应商一条、激活优先、
    指纹随行。现状（成员/字段/顺序/指纹）与期望不一致 → 整块重建；返回新增 name 列表。"""
    p = _path(home, "zcode")
    if not os.path.exists(p):
        return []
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    snap = snapshot.load().get("zcode")
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    desired = _zcode_relay_desired(d, getattr(snap, "provider_urls", None), proxy_url, cfg.bind_port)
    current = [r for r in cfg.relays if _is_zcode_relay(r)]
    same = [r.provider_id for r in current] == [r.provider_id for r in desired] and all(
        c.protocol == w.protocol
        and c.base_url == w.base_url
        and list(c.models) == list(w.models)
        and list(c.auth_hints or []) == list(w.auth_hints or [])  # 密钥轮换 → 指纹必须跟随重建（评审⑤）
        for c, w in zip(current, desired)
    )
    if same:
        return []
    names_before = {r.name for r in current}
    taken = {r.name for r in cfg.relays if not _is_zcode_relay(r)}
    cfg.relays = [r for r in cfg.relays if not _is_zcode_relay(r)]
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


def remove_zcode_relays(cfg) -> list[str]:
    """移除全部 zcode 自动 relay 并清出 activated_relays（「取消勾选 zcode」配套，评审④）：
    残留条目会继续参与选路且停止跟随现场。返回被移除的 name 列表。"""
    removed = [r.name for r in cfg.relays if _is_zcode_relay(r)]
    if not removed:
        return []
    cfg.relays = [r for r in cfg.relays if not _is_zcode_relay(r)]
    cfg.routing.activated_relays = [n for n in cfg.routing.activated_relays if n not in set(removed)]
    save_config(cfg)
    return removed


def reconcile_zcode_providers(cfg, home: str) -> dict | None:
    """接管态校正 zcode 条目漂移（spec §7.1）：非本代理条目重指、关门重开、新原值吸收进
    快照合并映射。返回 None 或摘要 {rewritten, gated}。"""
    p = _path(home, "zcode")
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
    return {
        "total": total,
        "eligible": len(items),
        "wired": wired,
        "gated": gated,
        "skipped_nokey": nokey,
        "skipped_kind": badkind,
    }
