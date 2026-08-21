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
}


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
                snapshot.save(
                    name,
                    snapshot.Snapshot(
                        base_url=original, key_ref=snapshot.key_ref_for(name), model=model, second_hop=second_hop
                    ),
                )
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
    try:
        txt = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    m = re.search(r'(?i)(?:model|name)["\']?\s*[:=]\s*["\']([\w@.\-/]+)["\']', txt)
    return m.group(1) if m else ""


def wiring_restore(cfg) -> list[str]:
    """仅在当前 base_url 指向本代理时，从备份还原；返回改动描述。"""
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
    """移除 start 激活的 relay(仅移除与模板匹配者)，清空 activated_relays 并落盘。"""
    msgs: list[str] = []
    templates = cfg.routing.relay_templates
    remaining = []
    for r in cfg.relays:
        if (
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


def wiring_report(cfg) -> list[dict]:
    """三处 harness 当前 base_url 归属。"""
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    home = HOME
    out = []
    for name in HARNESS_CFG:
        p = _path(home, name)
        cur = read_base_url(p, HARNESS_CFG[name]) if os.path.exists(p) else None
        out.append(
            {
                "harness": name,
                "path": p,
                "base_url": cur,
                "wired": bool(cur and (cur == proxy_url or cur.startswith(proxy_url + "/"))),
                "has_backup": _find_bak(p) is not None,
            }
        )
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
        try:
            cfg.relays.append(RelayConfig(name=name, **dict(tpl)))
            if name not in cfg.routing.activated_relays:
                cfg.routing.activated_relays.append(name)
            added.append(name)
        except Exception:  # 模板非法跳过（§12.3）
            continue
    save_config(cfg)
    return added


def _relay_name(tool_name: str, harness: str, tpl: dict) -> str:
    if tool_name == "cc-switch":
        return "cc-anthropic" if tpl["protocol"] == "anthropic" else "cc-codex"
    return "codex-plus"
