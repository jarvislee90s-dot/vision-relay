"""设置/relay/zcode 写动词：settings-set / relay-set / zcode-restart。

settings-set 走白名单（routing.unknown_default/harnesses、vision_log.enabled/retention_days），
harnesses 收窄即「取消勾选即还原」（与 stop 同一单 harness 还原步骤）；relay-set 做
停用压制/补 key；zcode-restart 立即重启 zcode 并记事件。全部经 stdin JSON + 文件锁落盘。
"""

from __future__ import annotations

from .config import ProxyConfig
from .verbs_contract import _locked_save, _stdin_json, envelope


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
        # VisionLogConfig 要求 >=1：0 落盘会让下次 load_config 抛 ConfigError（关留存用 enabled=false）
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
                if h == "zcode":
                    # 评审④：残留的一层直连 relay 会继续参与选路且停止跟随现场——同步移除
                    for name in wiring.remove_zcode_relays(cfg):
                        append_event("relay_removed", "zcode", {"name": name})
    _locked_save(cfg)
    data: dict = {"saved": True}
    if restore_msgs:
        data["restored"] = restore_msgs
    if "zcode" in removed:
        from . import zcode_proc

        if zcode_proc.find_zcode_processes():  # 进程在跑 → 还原待重启（GUI 弹窗三选/提示条）
            data["needs_zcode_restart"] = True
    return envelope(True, data)


def relay_set(cfg: ProxyConfig) -> dict:
    """stdin: {"name", "suppressed": bool} 或 {"name", "api_key": str}（补 key，spec §6 需要你区唯一动作）。"""
    payload, err = _stdin_json("object")
    if err is not None:
        return err
    name = payload.get("name")
    relay = next((r for r in cfg.relays if r.name == name), None)
    if relay is None:
        return envelope(False, {"error": f"unknown relay {name!r}"})
    if "suppressed" in payload:
        suppressed = payload["suppressed"]
        if not isinstance(suppressed, bool):
            return envelope(False, {"error": "suppressed must be a boolean"})
        if suppressed:
            if name not in cfg.routing.suppressed_relays:
                cfg.routing.suppressed_relays.append(name)
        else:
            cfg.routing.suppressed_relays = [n for n in cfg.routing.suppressed_relays if n != name]
    if "api_key" in payload:
        key = payload["api_key"]
        if not isinstance(key, str) or not key or key == "●●●●":
            return envelope(False, {"error": "api_key must be a non-empty string"})
        relay.api_key = key
    _locked_save(cfg)
    return envelope(True, {"name": name})


def zcode_restart(cfg: ProxyConfig) -> dict:
    """立即重启 zcode（弹窗选项①/提示条按钮共用）；best-effort，结果进事件日志。"""
    from . import zcode_proc
    from .reconcile import append_event

    ok = zcode_proc.restart_zcode()
    append_event("zcode_restart", "zcode", {"ok": ok})
    return envelope(ok, {"restarted": ok})
