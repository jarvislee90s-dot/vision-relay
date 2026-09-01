"""relay 维护：routing 模板激活/还原与在线工具档案的自动 relay 增删。

start 把 relay_templates 合入 relays 并记录 activated_relays，stop 按模板与
qwen/zcode 一层直连前缀识别并移除；在线工具（cc-switch / codex-plus）档案自动
建 relay。纯内存 + 落盘编排，不触碰 harness 配置文件，故无需 home。
"""

from __future__ import annotations

from .config import RelayConfig, save_config
from .harness_spec import _QWEN_RELAY_PREFIX, _ZCODE_RELAY_PREFIX
from .tools import _TEMPLATES


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
        if (
            r.name.startswith(_QWEN_RELAY_PREFIX) or r.name.startswith(_ZCODE_RELAY_PREFIX)
        ) and r.name in cfg.routing.activated_relays:
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
