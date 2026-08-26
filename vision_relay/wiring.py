"""start/stop 自动接线与回滚：把三处 harness 的 base_url 指到本代理(第一跳，有备份)，并在 stop 还原。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from . import snapshot
from .config import RelayConfig, save_config
from .tools import _TEMPLATES, TOOL_DOSSIERS

BAK_SUFFIX = ".vision-relay.bak"
LEGACY_BAK_SUFFIX = ".qwen-mm-proxy.bak"
# 测试可 monkeypatch 此值以隔离（不触碰真实 ~）。
HOME = os.path.expanduser("~")


@dataclass(frozen=True)
class _Harness:
    kind: str  # json | toml | env
    rel_path: str
    key: str


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

# qwen-code ≥0.22.0：模型选中 modelProviders 条目时，请求端点取条目自身 baseUrl
# （解析优先级第二层），model.baseUrl 只是 /model 选择器的消歧提示、不在解析链内。
# 因此接管必须条目级改写；authType 即协议族，仅改写本代理支持的协议。
_QWEN_AUTH_PROTO = {"openai": "chat", "anthropic": "anthropic"}
_QWEN_RELAY_PREFIX = "qwen-"
# modalities 原值哨兵：接管前该字段不存在（还原时删除字段而非写回字符串）
_MOD_ABSENT = "~absent~"


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


def _json_save_atomic(path: str, data: dict) -> bool:
    """JSON 原子写（tmp + replace，失败清 tmp）；qwen settings 与 codex catalog 共用。"""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        return False


def _modalities_open(entry: dict) -> bool:
    gc = entry.get("generationConfig")
    mod = gc.get("modalities") if isinstance(gc, dict) else None
    return isinstance(mod, dict) and mod.get("image") is True


def _open_modalities(entry: dict) -> object:
    """打开准入门，返回原值（哨兵 ~absent~ 表示原本没有 modalities 字段）。

    qwen-code 的 inputModalities 准入门不开，Read/粘贴的图片根本不会进请求体
    （本代理转写无从谈起）。接管后"所有模型都识图"（文本模型由本代理转写），
    故门一律代开；stop 按原值还原，避免直连态下图片被塞给纯文本上游报错。"""
    gc = entry.setdefault("generationConfig", {})
    if not isinstance(gc, dict):
        return _MOD_ABSENT
    mod = gc.get("modalities", _MOD_ABSENT)
    if isinstance(mod, dict) and mod.get("image") is True:
        return _MOD_ABSENT  # 已开着：不产生变更记录（幂等，还原不动它）
    original = mod
    gc["modalities"] = {"image": True}
    return original


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


def ensure_qwen_relays(cfg) -> list[str]:
    """按接管快照 + 当前 settings.json 维护 qwen 一层直连 relay（spec §5：qwen-code
    无路由工具，永远一层直连）。缺失则建（prepend，精确 id 先于通配 "*" 命中），
    原值已不存在于快照的旧 qwen relay 移除。models 为条目 id 精确匹配；api_key 留空
    ——转发时透传客户端自己的鉴权头（统一鉴权链）。返回新增 name 列表。"""
    p = _path(HOME, "qwen-code")
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


def reconcile_qwen_providers(cfg) -> dict | None:
    """接管态下校正 qwen provider 条目漂移：非本代理的条目重指本代理、被关的
    modalities 门重开，新原值吸收进快照（对应 absorb 语义，spec §5 始终接管）。
    返回 None 或摘要。"""
    p = _path(HOME, "qwen-code")
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


def _path(home: str, harness: str) -> str:
    rel = HARNESS_CFG[harness].rel_path
    return os.path.join(home, *rel) if isinstance(rel, tuple) else os.path.join(home, rel)


def _find_bak(p: str) -> str | None:
    new = p + BAK_SUFFIX
    if os.path.exists(new):
        return new
    old = p + LEGACY_BAK_SUFFIX
    if os.path.exists(old):
        return old
    return None


def read_base_url(path: str, h: _Harness) -> str | None:
    try:
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
        if h.kind == "json":
            d = json.load(open(path, encoding="utf-8"))
            node = d
            for part in h.key.split("."):
                node = node.get(part) if isinstance(node, dict) else None
                if node is None:
                    break
            return node if isinstance(node, str) else None
        if h.kind == "env":
            for line in open(path, encoding="utf-8"):
                m = re.match(rf"^{h.key}=(.*)$", line.strip())
                if m:
                    return m.group(1).strip()
            return None
        # toml
        m = re.search(r'base_url\s*=\s*"([^"]*)"', open(path, encoding="utf-8").read())
        return m.group(1) if m else None
    except (OSError, json.JSONDecodeError):
        return None


def write_base_url(path: str, h: _Harness, url: str) -> bool:
    tmp = path + ".tmp"
    try:
        if h.kind == "json":
            d = json.load(open(path, encoding="utf-8"))
            parts = h.key.split(".")
            node = d
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = url
            data = json.dumps(d, ensure_ascii=False, indent=2) + "\n"
        elif h.kind == "env":
            lines = []
            hit = False
            for line in open(path, encoding="utf-8"):
                if re.match(rf"^{h.key}=", line.strip()):
                    lines.append(f"{h.key}={url}\n")
                    hit = True
                else:
                    lines.append(line)
            if not hit:
                lines.append(f"{h.key}={url}\n")
            data = "".join(lines)
        else:  # toml
            raw = open(path, encoding="utf-8").read()
            if re.search(r'base_url\s*=\s*"[^"]*"', raw):
                data = re.sub(r'base_url\s*=\s*"[^"]*"', f'base_url = "{url}"', raw)
            else:
                data = raw + f'\nbase_url = "{url}"\n'
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
        return True
    except (OSError, json.JSONDecodeError):
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        return False


def _codex_catalog_path(config_path: str) -> str | None:
    """config.toml 引用的 model catalog 路径（相对路径按 config 所在目录解析）；无引用 None。"""
    try:
        raw = open(config_path, encoding="utf-8").read()
    except OSError:
        return None
    m = re.search(r'model_catalog_json\s*=\s*"([^"]*)"', raw)
    if not m or not m.group(1).strip():
        return None
    v = m.group(1).strip()
    return v if v.startswith("/") else os.path.join(os.path.dirname(config_path), v)


def _patch_codex_catalog_modalities(config_path: str) -> str | None:
    """目录模态补丁（接管配套）：Codex 按 catalog 的 input_modalities 放行 view_image/
    贴图——纯文本标注会把图片挡在请求之外，代理转写永远收不到图。给 models[] 每条
    补 "image"（已含者不动，缺字段/非列表设 ["text","image"]）；首次改动前整文件备份
    （与 harness 配置同后缀、同"已有备份不覆盖"语义），重复接管幂等。读/解析失败
    静默跳过，不打断接线。"""
    cat = _codex_catalog_path(config_path)
    if cat is None or not os.path.exists(cat):
        return None
    try:
        d = json.load(open(cat, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    models = d.get("models") if isinstance(d, dict) else None
    if not isinstance(models, list):
        return None
    patched = 0
    for m in models:
        if not isinstance(m, dict):
            continue
        mods = m.get("input_modalities")
        if isinstance(mods, list):
            if "image" in mods:
                continue
            mods.append("image")
        else:
            m["input_modalities"] = ["text", "image"]
        patched += 1
    if not patched:
        return None
    if not os.path.exists(cat + BAK_SUFFIX):
        try:
            import shutil

            shutil.copyfile(cat, cat + BAK_SUFFIX)
        except OSError:
            pass
    if not _json_save_atomic(cat, d):
        return None
    return f"catalog {os.path.basename(cat)}: +image modalities ({patched} models)"


def _restore_codex_catalog(config_path: str) -> str | None:
    """目录补丁的对称还原：备份存在则整文件还原并删备份（base_url 守卫由调用方负责）。"""
    cat = _codex_catalog_path(config_path)
    if cat is None:
        return None
    bak = cat + BAK_SUFFIX
    if not os.path.exists(bak):
        return None
    try:
        import shutil

        shutil.copyfile(bak, cat)
        os.unlink(bak)
    except OSError:
        return None
    return f"catalog {os.path.basename(cat)}: restored"


def wiring_backup_and_rewrite(cfg) -> list[str]:
    """为启用的 harness 备份(不重复)并把 base_url 指到本代理；接管前记录组合快照。

    qwen-code 额外做条目级接管(modelProviders[].baseUrl)：qwen 0.22.0 起条目 baseUrl
    优先于 model.baseUrl，只改全局字段等于没接管。"""
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
        if _find_bak(p) is None:  # 已有备份（含旧后缀）不覆盖，防止把代理地址存成"原始值"
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                import shutil

                shutil.copyfile(p, p + BAK_SUFFIX)
            except OSError:
                pass
        # qwen 条目级改写（必须在备份之后；快照需要合并条目原值映射）
        qwen_new, qwen_mod, qwen_skipped, qwen_rewritten, qwen_gated = (None, None, 0, 0, 0)
        if name == "qwen-code":
            qwen_new, qwen_mod, qwen_skipped, qwen_rewritten, qwen_gated = _rewrite_qwen_providers(p, proxy_url)
        base_changed = bool(original) and classify_base_url(original, cfg.bind_port) != "ours"
        if base_changed or qwen_new or qwen_mod:
            # 接管前组合快照：base_url + key 位置 + 模型 + 第二跳归属（spec §5）；
            # 条目映射与既有快照合并（重复 start 时已指本代理的条目保留首次记录）
            second_hop = classify_base_url(original, cfg.bind_port) if original else None
            second_hop = second_hop if second_hop in TOOL_DOSSIERS else None
            model = _first_model(p)  # 内部自吞读取失败（返回空串），无需再包
            old = snapshot.load().get(name)
            merged = dict(old.provider_urls or {}) if old and old.provider_urls else {}
            merged.update(qwen_new or {})
            merged_mod = dict(old.provider_modalities or {}) if old and old.provider_modalities else {}
            merged_mod.update(qwen_mod or {})
            try:
                snapshot.save(
                    name,
                    snapshot.Snapshot(
                        base_url=original if base_changed else (old.base_url if old else (original or "")),
                        key_ref=snapshot.key_ref_for(name),
                        model=model,
                        second_hop=second_hop,
                        provider_urls=merged or None,
                        provider_modalities=merged_mod or None,
                    ),
                )
            except Exception:  # 快照尽力而为，绝不打断接管（保护面不依赖 snapshot 内部实现）
                pass
        ok = write_base_url(p, h, proxy_url)
        msg = f"{name}: base_url -> {proxy_url} ({'ok' if ok else 'FAIL'})"
        if name == "qwen-code":
            msg += (
                f"; providers {qwen_rewritten} entries -> proxy"
                + (f", {qwen_gated} modalities gate opened" if qwen_gated else "")
                + f", {qwen_skipped} skipped"
            )
        changed.append(msg)
        # base 已指本代理的重复接管也补（捕获 Codex++ 运行期重新生成的目录）
        if name == "codex":
            cat_msg = _patch_codex_catalog_modalities(p)
            if cat_msg:
                changed.append(f"codex: {cat_msg}")
    if any(h == "qwen-code" for h in cfg.routing.harnesses):
        for n in ensure_qwen_relays(cfg):
            changed.append(f"qwen-code: relay {n} added (一层直连, 鉴权透传客户端头)")
    return changed


def _first_model(path: str) -> str:
    """从 harness 配置尽力抽一个模型名（快照记录用；失败返回空串）。"""
    try:
        txt = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    m = re.search(r'(?i)(?:model|name)["\']?\s*[:=]\s*["\']([\w@.\-/]+)["\']', txt)
    return m.group(1) if m else ""


def wiring_restore(cfg) -> list[str]:
    """从整文件备份还原（start 的对称动作；仅当前 base_url 指向本代理时执行）。

    与 wiring_restore_by_snapshot 的分工：本函数按"第一次接管前的原始文件"还原，
    供正常 stop 使用；崩溃/漂移后的修复走 wiring_restore_by_snapshot（组合快照）。
    """
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    home = HOME
    restored: list[str] = []
    for name in cfg.routing.harnesses:
        h = HARNESS_CFG[name]
        p = _path(home, name)
        bak = _find_bak(p)
        if bak is None:
            continue
        cur = read_base_url(p, h)
        # 精确匹配（或带路径前缀）才视为本代理：startswith(proxy_url) 会把 :87870 误判为 :8787。
        if cur is not None and cur != proxy_url and not cur.startswith(proxy_url + "/"):
            restored.append(f"{name}: 当前 base_url={cur!r} 非本代理，未还原(保留备份)")
            continue
        if name == "codex":  # 目录还原须在 config 整文件换回前（当前 config 仍引用被补丁目录）
            cat_msg = _restore_codex_catalog(p)
            if cat_msg:
                restored.append(f"{name}: {cat_msg}")
        try:
            import shutil

            shutil.copyfile(bak, p)
            os.unlink(bak)
            restored.append(f"{name}: restored")
        except OSError as exc:
            restored.append(f"{name}: restore FAIL {exc}")
    return restored


def relays_activate(cfg) -> list[str]:
    """把 routing.relay_templates 合入 relays(name 去重，冲突不覆盖)，记录 activated_relays 并落盘。"""
    msgs: list[str] = []
    for name, spec in cfg.routing.relay_templates.items():
        existing = next((r for r in cfg.relays if r.name == name), None)
        if existing is not None:
            msgs.append(f"relay {name}: 已存在，跳过")
            continue
        try:
            cfg.relays.append(RelayConfig(name=name, **spec))
        except TypeError as exc:  # 模板字段非法
            msgs.append(f"relay {name}: 模板非法，跳过（{exc}）")
            continue
        if name not in cfg.routing.activated_relays:
            cfg.routing.activated_relays.append(name)
        msgs.append(f"relay {name} 已激活")
    save_config(cfg)
    return msgs


def relays_restore(cfg) -> list[str]:
    """移除 start 激活的 relay(仅移除与模板匹配者；qwen 一层直连 relay 按
    activated_relays + 名字前缀识别)，清空 activated_relays 并落盘。"""
    msgs: list[str] = []
    templates = cfg.routing.relay_templates
    remaining = []
    for r in cfg.relays:
        if r.name.startswith(_QWEN_RELAY_PREFIX) and r.name in cfg.routing.activated_relays:
            msgs.append(f"relay {r.name} 已还原移除")
        elif (
            r.name in cfg.routing.activated_relays
            and r.name in templates
            and r.__dict__ == RelayConfig(name=r.name, **templates[r.name]).__dict__
        ):
            msgs.append(f"relay {r.name} 已还原移除")
        else:
            remaining.append(r)
    cfg.relays = remaining
    cfg.routing.activated_relays = []
    save_config(cfg)
    return msgs


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
    return {
        "total": total,
        "eligible": len(items),
        "wired": wired,
        "gated": gated,
        "skipped_nokey": nokey,
        "skipped_kind": badkind,
    }


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


def wiring_report(cfg) -> list[dict]:
    """三处 harness 当前 base_url 归属。qwen-code 另附条目级统计（0.22.0 条目
    baseUrl 优先，全局字段 wired 不代表真接管；门不开图片进不了请求，故 wired
    要求 URL 指向本代理且门全开）。"""
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    home = HOME
    out = []
    for name in HARNESS_CFG:
        p = _path(home, name)
        cur = read_base_url(p, HARNESS_CFG[name]) if os.path.exists(p) else None
        row = {
            "harness": name,
            "path": p,
            "base_url": cur,
            "wired": bool(cur and (cur == proxy_url or cur.startswith(proxy_url + "/"))),
            "has_backup": _find_bak(p) is not None,
        }
        if name == "qwen-code" and os.path.exists(p):
            stats = _qwen_provider_stats(p, proxy_url)
            row["providers"] = stats
            row["wired"] = row["wired"] and stats["eligible"] == stats["wired"] and stats["eligible"] == stats["gated"]
        out.append(row)
    return out


def classify_base_url(url: str | None, bind_port: int) -> str:
    """base_url 归属：ours | cc-switch | codex-plus | other | none（spec §5 观测信号①）。"""
    if not url:
        return "none"
    if url == f"http://127.0.0.1:{bind_port}" or url.startswith(f"http://127.0.0.1:{bind_port}/"):
        return "ours"
    m = re.search(r":(\d+)", url)
    if not m:
        return "other"
    port = int(m.group(1))
    for name, d in TOOL_DOSSIERS.items():
        if port == d.port:
            return name
    return "other"


def wiring_restore_by_snapshot(cfg) -> list[str]:
    """按接管组合快照还原（spec §5 修复：路由关-崩溃路径）。仅当前指向本代理时执行。

    与 wiring_restore 的分工：本函数按"接管前正确组合"还原（防外部工具档案污染），
    供对账/修复使用；正常 stop 的整文件备份还原走 wiring_restore。还原成功后删掉
    该 harness 的 .bak——防止后续 stop 走 wiring_restore 时用过期整文件备份覆盖掉
    已按快照还原的状态。
    """
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
        if name == "codex":
            cat_msg = _restore_codex_catalog(p)
            if cat_msg:
                restored.append(f"{name}: {cat_msg}")
        ok = write_base_url(p, HARNESS_CFG[name], snap.base_url)
        if ok and name == "qwen-code" and snap.provider_urls:
            n = _restore_qwen_providers(p, proxy_url, snap.provider_urls, snap.provider_modalities)
            restored.append(f"{name}: providers restored ({n} entries)")
        if ok:
            bak = _find_bak(p)
            if bak is not None:  # 整文件备份已过期（快照才是真相），删除防误还原
                try:
                    os.unlink(bak)
                except OSError:
                    pass
        restored.append(f"{name}: restored to {snap.base_url} ({'ok' if ok else 'FAIL'})")
    return restored


def ensure_tool_relays(cfg, tool_states) -> list[str]:
    """在线工具档案 → 自动 relay（name 去重不覆盖；离线不加；spec §5/§12）。
    返回新增 relay name 列表。"""
    added: list[str] = []
    online_names = {s.name for s in tool_states if s.online}
    for (tool_name, harness), tpl in _TEMPLATES.items():
        if tool_name not in online_names:
            continue
        if harness not in cfg.routing.harnesses:
            continue
        name = _relay_name(tool_name, harness, tpl)
        if any(r.name == name for r in cfg.relays):
            continue
        if name in cfg.routing.suppressed_relays or (
            tool_name == "cc-switch" and f"cc-{harness}" in cfg.routing.suppressed_relays
        ):
            continue  # 用户显式停用 > 自动探测（spec §7.5；兼容规范名 cc-<harness>）
        try:
            cfg.relays.append(RelayConfig(name=name, **dict(tpl)))
            if name not in cfg.routing.activated_relays:
                cfg.routing.activated_relays.append(name)
            added.append(name)
        except Exception:  # 模板非法跳过（§12.3）
            continue
    if added:  # 无新增不落盘（幂等：无漂移不写文件）
        save_config(cfg)
    return added


def _relay_name(tool_name: str, harness: str, tpl: dict) -> str:
    if tool_name == "cc-switch":
        return "cc-anthropic" if tpl["protocol"] == "anthropic" else "cc-codex"
    return "codex-plus"


def wiring_restore_on_stop(cfg) -> list[str]:
    """stop 的统一还原（spec §5 + 2026-08-23 决策）：按最新接管组合快照；快照缺失的
    harness 退回第一次接管前的整文件 .bak 兜底。

    每 harness 独立决策：有快照 → 只写回 base_url（运行期间用户对配置文件的其他
    修改原样保留），并删除已过期的 .bak；无快照 → .bak 整文件还原（含 key 位置等
    完整原始状态）。两者都要求当前 base_url 指向本代理才动文件（与 wiring_restore
    同守卫）。崩溃修复路径不走这里（reconcile 仍用 wiring_restore_by_snapshot）。
    """
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    snaps = snapshot.load()
    msgs: list[str] = []
    for name in cfg.routing.harnesses:
        h = HARNESS_CFG[name]
        p = _path(HOME, name)
        if not os.path.exists(p):
            continue
        cur = read_base_url(p, h)
        if cur is None or (cur != proxy_url and not cur.startswith(proxy_url + "/")):
            msgs.append(f"{name}: 当前 base_url={cur!r} 非本代理，跳过还原")
            continue
        if name == "codex":
            cat_msg = _restore_codex_catalog(p)
            if cat_msg:
                msgs.append(f"{name}: {cat_msg}")
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
            continue
        bak = _find_bak(p)
        if bak is None:
            msgs.append(f"{name}: 无快照且无备份，跳过")
            continue
        try:
            import shutil

            shutil.copyfile(bak, p)
            os.unlink(bak)
            msgs.append(f"{name}: bak restored")
        except OSError as exc:
            msgs.append(f"{name}: restore FAIL {exc}")
    return msgs
