"""--json management verbs (spec §4 通信契约): one envelope, contract_version pinned.

GUI（M2）只消费这些动词的输出；结构变更必须升 contract_version 并在 spec 记录。
"""

from __future__ import annotations

import json
import sys

import httpx

from . import route_fallback
from .config import ProxyConfig, save_config
from .locking import config_lock
from .reconcile import observe as _observe_impl
from .reconcile import reconcile as _reconcile_impl
from .reconcile import tail_events as _tail_events_impl
from .tools import TOOL_DOSSIERS
from .tools import probe_tools as _probe_tools_impl
from .visionlog import query as _vl_query_impl

CONTRACT_VERSION = 1


def envelope(ok: bool, data) -> dict:
    return {"contract_version": CONTRACT_VERSION, "ok": ok, "data": data}


def _stdin_json(kind: str):
    """写动词共用的 stdin 读取+顶层类型校验。返回 (payload, None) 或 (None, 错误 envelope)。"""
    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        return None, envelope(False, {"error": f"invalid stdin json: {exc}"})
    expected = {"array": list, "object": dict}[kind]
    if not isinstance(payload, expected):
        return None, envelope(False, {"error": f"expected a JSON {kind}"})
    return payload, None


def _locked_save(cfg: ProxyConfig) -> None:
    """写动词共用的落盘段：自方写者经文件锁串行（spec §4）。"""
    with config_lock():
        save_config(cfg)


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
                        "is_current": pr.is_current,  # GUI 折叠非当前供应商行 + 前端批量探测筛候选
                    }
                )
    return rows


def _provider_hint(harness: str, states: list | None = None) -> str:
    """harness -> 当前 provider 名：在线路由工具的激活供应商，其次快照第二跳，未知 '?'。

    states 由调用方注入（_scan_triples 一次探测全组复用）；None 时自行探测。"""
    from . import snapshot

    for s in states if states is not None else _probe_tools():
        d = TOOL_DOSSIERS.get(s.name)
        if d and harness in d.harnesses and s.online and s.active_provider:
            return s.active_provider
    snap = snapshot.load().get(harness)
    if snap is not None and snap.second_hop:
        return snap.second_hop
    return "?"


def status(cfg: ProxyConfig) -> dict:
    """总览一次拿全：观测 + relay 视图（打码）+ 快照 + vlm 概要 + setup_state（向导触发）。"""
    import os

    from .config import default_config_path
    from .snapshot import load as load_snapshots

    obs = _observe_for_status(cfg)
    tool_online = {t.get("name"): t.get("online") for t in obs.get("tools", [])}
    relays = []
    for r in cfg.relays:
        # upstream_effective（spec §5 离线回落）：两层经工具=relay 地址；工具离线=档案
        # 当前供应商真实地址（仅取 base_url，档案 key 绝不进 status 输出）。
        eff = r.base_url
        if getattr(r, "via", None) and not tool_online.get(r.via):
            direct = route_fallback.archive_direct(r.via, r)
            eff = direct.base_url if direct else None
        relays.append(
            {
                "name": r.name,
                "protocol": r.protocol,
                "base_url": r.base_url,
                "via": r.via,
                "models": r.models,
                "suppressed": r.name in cfg.routing.suppressed_relays,
                "has_key": bool(r.api_key),
                "upstream_effective": eff,
            }
        )
    snaps = load_snapshots()
    obs["relays"] = relays
    obs["snapshots"] = {
        h: {
            "base_url": s.base_url,
            "key_ref": s.key_ref,
            "model": s.model,
            "second_hop": s.second_hop,
            "ts": s.ts,
        }
        for h, s in snaps.items()
    }
    obs["vlm"] = {
        "model": cfg.vlm.model,
        "base_url": cfg.vlm.base_url,
        "format": cfg.vlm.format,
        "custom_prompts": bool(cfg.vlm.custom_tier1 or cfg.vlm.custom_tier2),
        "groups": sorted(cfg.vlm_by_harness.keys()),
    }
    obs["vlm"]["configured"] = bool(cfg.vlm.api_key)
    has_config = os.path.exists(default_config_path())
    obs["setup_state"] = {
        "has_config": has_config,
        "capability_confirmed": cfg.routing.capability_confirmed,
        "vlm_configured": bool(cfg.vlm.api_key),
    }
    # spec §6 向导触发：无配置 / 首次确认未置位 / VLM 未配（第①步必填）
    obs["first_run"] = (not has_config) or (not cfg.routing.capability_confirmed) or (not cfg.vlm.api_key)
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
    """全量配置（打码）：明文 key 绝不出现在被动输出里（工程宪法）；
    刻意豁免仅 vlm-secret 动词（GUI「显示」按钮显式请求）。拷贝后打码，不改调用方的 cfg。"""

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


def events(cfg: ProxyConfig, limit: int = 50) -> dict:
    return envelope(True, _tail_events(limit))


def visionlog(cfg: ProxyConfig, harness: str | None = None, session: str | None = None) -> dict:
    return envelope(True, _vl_query(harness=harness, session=session))


def models_set(cfg: ProxyConfig) -> dict:
    """stdin: [{"harness","provider","model","value"}]；value ∈ image|text_only|null。
    全量校验通过才写（不部分落盘）；value=null 清除条目=未标注。写路径走文件锁。"""
    rows, err = _stdin_json("array")
    if err is not None:
        return err
    for r in rows:
        if not isinstance(r, dict) or not all(k in r for k in ("harness", "provider", "model")):
            return envelope(False, {"error": f"row missing keys: {r!r}"})
        v = r.get("value")
        if v not in ("image", "text_only", None):
            return envelope(False, {"error": f"value must be image|text_only|null, got {v!r}"})
    if not cfg.routing.capability_confirmed:
        # 任何一次成功的 models-set = 过目/确认完成（M2 plan Task 13：成功路径置位；
        # 跳过=空数组、完成=非空行，两条路都必须关掉向导，否则 first_run 永真、向导反复弹）
        cfg.routing.capability_confirmed = True
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
            for shadow in ("legacy", "?"):  # 键统一：规范桶落笔即清影子，防兜底读到旧值
                if shadow != p:
                    cfg.model_capabilities.get(h, {}).get(shadow, {}).pop(m, None)
                    cfg.capability_sources.get(h, {}).get(shadow, {}).pop(m, None)
        save_config(cfg)
    return envelope(True, {"updated": len(rows)})


def vlm_set(cfg: ProxyConfig) -> dict:
    """stdin: {"vlm":{...}, "vlm_by_harness":{h:{...}}, "custom_tier1":str|null, "custom_tier2":str|null}
    规则：缺省字段不修改；api_key 空串 = 不修改、打码占位 = 拒绝（GUI 看不到 key，无法回显）；
    custom_tierX null = 恢复默认。"""
    payload, err = _stdin_json("object")
    if err is not None:
        return err
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

    err_text = apply(cfg.vlm.__dict__, payload.get("vlm") or {})
    if err_text is None:
        for k in ("custom_tier1", "custom_tier2"):
            if k in payload:
                setattr(cfg.vlm, k, payload[k] or None)
    if err_text is None:
        for h, over in (payload.get("vlm_by_harness") or {}).items():
            if over is None:
                cfg.vlm_by_harness.pop(h, None)  # null = 改回跟随全局
                continue
            if not isinstance(over, dict):
                err_text = f"vlm_by_harness[{h}] must be object or null"
                break
            bucket = cfg.vlm_by_harness.setdefault(h, {})
            err_text = apply(bucket, over) or None
            if err_text:
                break
    if err_text is not None:
        return envelope(False, {"error": err_text})
    _locked_save(cfg)
    return envelope(True, {"saved": True})


def vlm_secret(cfg: ProxyConfig) -> dict:
    """显式请求才回明文 VLM key —— 工程宪法『输出不带 key』的唯一刻意豁免（spec §6 设置·key 显隐）。

    只在 GUI「显示」按钮点击时经 _JSON_MAP['vlm-secret'] 到达；config/status 等被动输出仍一律打码。
    纯读，不改调用方 cfg；范围仅 vlm + vlm_by_harness（relays / relay_templates 不回显）。"""
    by_h = {
        h: {"api_key": over["api_key"]}
        for h, over in cfg.vlm_by_harness.items()
        if isinstance(over, dict) and over.get("api_key")
    }
    return envelope(True, {"vlm": {"api_key": cfg.vlm.api_key}, "vlm_by_harness": by_h})


def _VLMClient(vlm_cfg):
    from .vlm import VLMClient

    return VLMClient(vlm_cfg)


def vlm_test(cfg: ProxyConfig, payload: dict | None = None) -> dict:
    """与生产同一调用路径的连通测试（spec §6 设置·VLM）。
    payload: {mode: tier1|tier2, question?, custom_prompt?, harness?, image_base64?, media_type?}
    payload 缺省时（CLI 入口）从 stdin 读 JSON。"""
    import base64
    import time

    if payload is None:
        payload, err = _stdin_json("object")
        if err is not None:
            return err
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
    payload, err = _stdin_json("object")
    if err is not None:
        return err
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
    if "retention_days" in v and (not isinstance(v["retention_days"], int) or v["retention_days"] < 1):
        # VisionLogConfig 要求 >=1：0 落盘会让下次 load_config 抛 ConfigError（关留存用 enabled=false）
        return envelope(False, {"error": "retention_days must be an int >= 1 (disable via vision_log.enabled=false)"})
    cfg.routing.unknown_default = r.get("unknown_default", cfg.routing.unknown_default)
    if "enabled" in v:
        enabled = v["enabled"]
        if not isinstance(enabled, bool):
            return envelope(False, {"error": "enabled must be a boolean"})
        cfg.vision_log.enabled = enabled
    if "retention_days" in v:
        cfg.vision_log.retention_days = v["retention_days"]
    _locked_save(cfg)
    return envelope(True, {"saved": True})


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


def probe_target_for(cfg: ProxyConfig, harness: str, provider: str, tool_by_name: dict) -> tuple[str, str, str]:
    """探测目标：两层=工具端口（无 key）；直连=harness 自身配置或对应 relay 的 base_url+key。
    （由 cli.py 上移：verbs 是更低层，原 verbs→cli 反向导入是层次倒置。）"""
    if harness == "zcode":  # zcode 探测目标=该供应商原始上游+自带 key（spec §9）
        from . import model_sources

        return model_sources.zcode_probe_target(cfg, provider)
    proto = {"claude": "anthropic", "codex": "responses", "qwen-code": "chat"}.get(harness, "chat")
    for name, d in TOOL_DOSSIERS.items():
        if harness in d.harnesses and tool_by_name.get(name, {}).get("online"):
            port = tool_by_name[name]["port"]
            base = "http://127.0.0.1:%d/v1" % port if name == "codex-plus" else "http://127.0.0.1:%d" % port
            return base, "", proto if name != "codex-plus" else "responses"
    # 直连候选:harness 自身配置(或接管前快照)指向上游时,直接探真实上游;
    # key 按 snapshot.key_ref 的位置取(仅进程内使用,绝不进 envelope/日志);无快照
    # (未接管过)时用该 harness 的默认 key 位置(_KEY_FIELDS 首个字段)。
    from . import model_sources, snapshot

    direct = model_sources.direct_provider_url(harness)
    if direct:
        snap = snapshot.load().get(harness)
        key_ref = snap.key_ref if snap is not None else None
        if not key_ref:
            fields = snapshot._KEY_FIELDS.get(harness, ((), ()))[1]
            key_ref = fields[0] if fields else None
        return direct, model_sources.resolve_probe_key(harness, key_ref), proto
    for r in cfg.relays:
        if r.protocol == proto and r.base_url and not r.base_url.startswith("http://127.0.0.1"):
            return r.base_url, r.api_key, proto
    return "", "", proto


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


def _run_probe(
    cfg: ProxyConfig, harness: str, provider: str, model: str, tool_by_name: dict | None = None
) -> str | None:
    from .annotate import run_probe as _rp

    if tool_by_name is None:  # 单模型路径：现探一次；批量路径由调用方传入（省 N 次探测）
        from .reconcile import observe

        tool_by_name = {t["name"]: t for t in observe(cfg)["tools"]}
    base, key, proto = probe_target_for(cfg, harness, provider, tool_by_name)
    return _rp(cfg, harness, provider, model, base, key, proto)


def probe_one(cfg: ProxyConfig, harness: str, provider: str, model: str) -> dict:
    tool_by_name = {t["name"]: t for t in _observe_impl(cfg)["tools"]}
    base, _key, _proto, reason = probe_target_info(cfg, harness, provider, tool_by_name)
    if not base:
        # 无结论(含无目标)= 合法三态(spec §5),不是错误;GUI 按 target_found 显示"不可达"
        return envelope(True, {"result": None, "target_found": False, "reason": reason})
    result = _run_probe(cfg, harness, provider, model, tool_by_name)
    return envelope(True, {"result": result, "target_found": True, "reason": None})


def probe_all_untested(cfg: ProxyConfig) -> dict:
    """批量探测:当前激活供应商(is_current 行)的无缓存 (provider, model) 组合。
    非当前供应商的行不经工具路由、无探测路径——不尝试、不计入 results。"""
    from . import model_sources

    matrix = model_sources.harness_matrix(cfg)
    tool_by_name = {t["name"]: t for t in _observe_impl(cfg)["tools"]}
    probed: list[dict] = []
    for harness, rows in matrix.items():
        for row in rows:
            if not row.is_current:
                continue  # 只探当前激活供应商的行
            for m in row.models:
                if _lookup_probe(cfg, row.provider, m):
                    continue  # 有缓存结论,跳过
                base, _key, _proto, reason = probe_target_info(cfg, harness, row.provider, tool_by_name)
                if not base:
                    probed.append(
                        {
                            "harness": harness,
                            "provider": row.provider,
                            "model": m,
                            "result": None,
                            "target_found": False,
                            "reason": reason,
                        }
                    )
                    continue
                result = _run_probe(cfg, harness, row.provider, m, tool_by_name)
                probed.append(
                    {
                        "harness": harness,
                        "provider": row.provider,
                        "model": m,
                        "result": result,
                        "target_found": True,
                        "reason": None,
                    }
                )
    return envelope(True, {"probed": len(probed), "results": probed})


def models_fetch(cfg: ProxyConfig) -> dict:
    """可选：从上游 /v1/models 拉模型 ID 清单（spec §5；只补清单，能力以探针/目录为准）。

    回环/被抑制 relay 不拉（工具端口两层，清单在工具自己界面上），但在 skipped 里
    透出原因——GUI 据此解释「为什么拉不到」而不是弹一个空对象。"""
    providers: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    skipped: dict[str, str] = {}
    for r in cfg.relays:
        if r.name in cfg.routing.suppressed_relays:
            skipped[r.name] = "suppressed"
            continue
        if not r.base_url or r.base_url.startswith("http://127.0.0.1"):
            skipped[r.name] = "loopback"
            continue
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
    return envelope(True, {"providers": providers, "errors": errors, "skipped": skipped})
