"""--json management verbs (spec §4 通信契约): one envelope, contract_version pinned.

GUI（M2）只消费这些动词的输出；结构变更必须升 contract_version 并在 spec 记录。
"""

from __future__ import annotations

import httpx

from .config import ProxyConfig
from .reconcile import observe as _observe_impl
from .reconcile import reconcile as _reconcile_impl
from .reconcile import tail_events as _tail_events_impl
from .tools import probe_tools as _probe_tools_impl
from .visionlog import query as _vl_query_impl

CONTRACT_VERSION = 1


def envelope(ok: bool, data) -> dict:
    return {"contract_version": CONTRACT_VERSION, "ok": ok, "data": data}


# 依赖注入点（测试替换；生产各指向真实现）
def _observe_for_status(cfg: ProxyConfig) -> dict:
    return _observe_impl(cfg)


def _reconcile(cfg: ProxyConfig, **kw) -> dict:
    return _reconcile_impl(cfg, **kw)


def _probe_tools() -> list:
    return _probe_tools_impl()


def _tail_events(n: int = 50) -> list[dict]:
    return _tail_events_impl(n)


def _vl_query(**kw) -> list[dict]:
    return _vl_query_impl(**kw)


def _scan_triples(cfg: ProxyConfig) -> list[dict]:
    """扫描 harness 配置 -> 三元组 + 当前标注值/来源（未标注= value None）。"""
    from .onboarding import scan_model_groups

    states = _probe_tools()  # 一次探测全 scan 复用（每组一次会把端口超时放大 N 倍）
    rows: list[dict] = []
    for g in scan_model_groups(cfg):
        provider = _provider_hint(g.group, states)
        for ent in g.entries:
            value = cfg.model_capabilities.get(g.group, {}).get(provider, {}).get(ent.model)
            source = cfg.capability_sources.get(g.group, {}).get(provider, {}).get(ent.model)
            rows.append(
                {
                    "harness": g.group,
                    "provider": provider,
                    "model": ent.model,
                    "value": value,
                    "source": source,
                    "probe_cached": (cfg.probe_results.get(provider, {}).get(ent.model) or {}).get("result"),
                }
            )
    return rows


def _provider_hint(harness: str, states: list | None = None) -> str:
    """harness -> 当前 provider 名：在线路由工具的激活供应商，其次快照第二跳，未知 '?'。

    states 由调用方注入（_scan_triples 一次探测全组复用）；None 时自行探测。"""
    from . import snapshot
    from .tools import TOOL_DOSSIERS

    for s in states if states is not None else _probe_tools():
        d = TOOL_DOSSIERS.get(s.name)
        if d and harness in d.harnesses and s.online and s.active_provider:
            return s.active_provider
    snap = snapshot.load().get(harness)
    if snap is not None and snap.second_hop:
        return snap.second_hop
    return "?"


def status(cfg: ProxyConfig) -> dict:
    obs = _observe_for_status(cfg)
    return envelope(True, obs)


def refresh(cfg: ProxyConfig) -> dict:
    report = _reconcile(cfg, trigger="manual")
    return envelope(True, report)


def diagnose(cfg: ProxyConfig) -> dict:
    report = _reconcile(cfg, trigger="diagnose")
    return envelope(not report["needs_you"], report)


def models_scan(cfg: ProxyConfig) -> dict:
    return envelope(True, {"models": _scan_triples(cfg)})


def config_get(cfg: ProxyConfig) -> dict:
    """全量配置（打码）：明文 key 绝不出现在输出里（工程宪法）。拷贝后打码，不改调用方的 cfg。"""

    def mask(v):
        return "●●●●" if v else v

    data = cfg.to_dict()
    # to_dict 的 vlm/relays 子字典是实例 __dict__ 引用——必须复制后打码，
    # 否则会把调用方 ProxyConfig 里的真 key 原地抹掉。
    data["vlm"] = {**data["vlm"], "api_key": mask(data["vlm"].get("api_key", ""))}
    data["vlm_by_harness"] = {
        h: ({**over, "api_key": "●●●●"} if isinstance(over, dict) and over.get("api_key") else over)
        for h, over in data.get("vlm_by_harness", {}).items()
    }
    data["relays"] = [{**r, "api_key": "●●●●"} if r.get("api_key") else r for r in data.get("relays", [])]
    # 手编 relay_templates 的 api_key 会被 wiring 展开进 RelayConfig 真实用于上游认证——同样打码。
    data["routing"] = {
        **data["routing"],
        "relay_templates": {
            name: ({**spec, "api_key": "●●●●"} if isinstance(spec, dict) and spec.get("api_key") else spec)
            for name, spec in data["routing"].get("relay_templates", {}).items()
        },
    }
    return envelope(True, data)


def tools(cfg: ProxyConfig) -> dict:
    return envelope(
        True,
        [
            {
                "name": s.name,
                "port": s.port,
                "online": s.online,
                "active_provider": s.active_provider,
                "provider_base_url": s.provider_base_url,
            }
            for s in _probe_tools()
        ],
    )


def events(cfg: ProxyConfig, tail: int = 50) -> dict:
    return envelope(True, _tail_events(tail))


def visionlog(cfg: ProxyConfig, harness: str | None = None, session: str | None = None) -> dict:
    return envelope(True, _vl_query(harness=harness, session=session))


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


def vlm_set(cfg: ProxyConfig) -> dict:
    """stdin: {"vlm":{...}, "vlm_by_harness":{h:{...}}, "custom_tier1":str|null, "custom_tier2":str|null}
    规则：缺省字段不修改；api_key 空串 = 不修改、打码占位 = 拒绝（GUI 看不到 key，无法回显）；
    custom_tierX null = 恢复默认。"""
    import json
    import sys

    from .locking import config_lock

    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        return envelope(False, {"error": f"invalid stdin json: {exc}"})
    if not isinstance(payload, dict):
        return envelope(False, {"error": "expected a JSON object"})
    for key in ("vlm", "vlm_by_harness"):
        v = payload.get(key)
        if v is not None and not isinstance(v, dict):
            return envelope(False, {"error": f"{key} must be an object"})
    MASK = "●●●●"

    def apply(target: dict, updates: dict) -> str | None:
        for k, v in updates.items():
            if k == "api_key" and v == "":
                continue  # 空串 = 不修改
            if v == MASK:
                return f"masked placeholder not allowed for {k}"
            if k in ("custom_tier1", "custom_tier2"):
                continue  # 顶层单独处理（全局字段，不走 vlm.__dict__ 直写）
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
    payload: {mode: tier1|tier2, question?, custom_prompt?, harness?, image_base64?, media_type?}
    payload 缺省时（CLI 入口）从 stdin 读 JSON。"""
    import base64
    import json
    import sys
    import time

    if payload is None:
        try:
            payload = json.load(sys.stdin)
        except ValueError as exc:
            return envelope(False, {"error": f"invalid stdin json: {exc}"})
        if not isinstance(payload, dict):
            return envelope(False, {"error": "expected a JSON object"})
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
        {
            "desc": desc,
            "prompt_used": detail.get("prompt"),
            "model": merged.model,
            "duration_ms": int((time.time() - started) * 1000),
        },
    )


def settings_set(cfg: ProxyConfig) -> dict:
    """stdin: {"routing": {...白名单键...}, "vision_log": {...}}。白名单外键拒绝。"""
    import json
    import sys

    from .locking import config_lock

    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        return envelope(False, {"error": f"invalid stdin json: {exc}"})
    if not isinstance(payload, dict):
        return envelope(False, {"error": "expected a JSON object"})
    routing_ok = {"unknown_default"}
    log_ok = {"enabled", "retention_days"}
    r = payload.get("routing") or {}
    v = payload.get("vision_log") or {}
    if not isinstance(payload.get("routing", {}), dict) or not isinstance(payload.get("vision_log", {}), dict):
        return envelope(False, {"error": "routing/vision_log must be objects"})
    if not set(r).issubset(routing_ok) or not set(v).issubset(log_ok):
        return envelope(False, {"error": "unsupported settings key"})
    if "unknown_default" in r and r["unknown_default"] not in ("text_only", "image"):
        return envelope(False, {"error": "unknown_default must be text_only|image"})
    if "retention_days" in v and (not isinstance(v["retention_days"], int) or v["retention_days"] < 0):
        return envelope(False, {"error": "retention_days must be a non-negative int"})
    cfg.routing.unknown_default = r.get("unknown_default", cfg.routing.unknown_default)
    if "enabled" in v:
        enabled = v["enabled"]
        if not isinstance(enabled, bool):
            return envelope(False, {"error": "enabled must be a boolean"})
        cfg.vision_log.enabled = enabled
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
    if not isinstance(payload, dict):
        return envelope(False, {"error": "expected a JSON object"})
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
    from .config import save_config

    with config_lock():
        save_config(cfg)
    return envelope(True, {"name": name})


def _run_probe(cfg: ProxyConfig, harness: str, provider: str, model: str) -> str | None:
    from .annotate import run_probe as _rp
    from .cli import _probe_target_for
    from .reconcile import observe

    tool_by_name = {t["name"]: t for t in observe(cfg)["tools"]}
    base, key, proto = _probe_target_for(cfg, harness, provider, tool_by_name)
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
