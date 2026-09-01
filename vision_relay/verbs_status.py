"""只读观测动词：status / refresh / diagnose / config / tools / events / visionlog。

status 一次拿全（观测+relay 打码视图+快照+vlm 概要+setup_state+zcode 运行态）；
refresh/diagnose 走对账；config 全量配置打码（明文 key 绝不出被动输出）；
tools/events/visionlog 分别是工具档案、事件 tail、识图记录查询。被测试
monkeypatch 的观测依赖（_observe_for_status/_reconcile/_probe_tools/_tail_events/
_vl_query）经 facade（verbs.*）调用时解析。
"""

from __future__ import annotations

import os

from . import route_fallback
from .config import ProxyConfig
from .verbs_contract import envelope


def status(cfg: ProxyConfig) -> dict:
    """总览一次拿全：观测 + relay 视图（打码）+ 快照 + vlm 概要 + setup_state（向导触发）。"""
    from . import verbs
    from .config import default_config_path
    from .snapshot import load as load_snapshots

    obs = verbs._observe_for_status(cfg)
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
    # zcode 重启交互支撑（spec §7.2）：进程在跑且其启动早于本代理最后一次改写 → 待重启
    from . import wiring, zcode_proc

    obs["zcode_runtime"] = {
        "running": bool(zcode_proc.find_zcode_processes()),
        "needs_restart": zcode_proc.zcode_needs_restart(wiring.zcode_rewrite_ts()),
    }
    # spec §6 向导触发：无配置 / 首次确认未置位 / VLM 未配（第①步必填）
    obs["first_run"] = (not has_config) or (not cfg.routing.capability_confirmed) or (not cfg.vlm.api_key)
    return envelope(True, obs)


def refresh(cfg: ProxyConfig) -> dict:
    from . import verbs

    report = verbs._reconcile(cfg, trigger="manual")
    return envelope(True, report)


def diagnose(cfg: ProxyConfig) -> dict:
    from . import verbs

    report = verbs._reconcile(cfg, trigger="diagnose")
    return envelope(not report["needs_you"], report)


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
    # auth_hints 是密钥指纹：spec §3 明文不进日志/GUI——输出层逐条剥离该键。
    relays_out = []
    for r in data.get("relays", []):
        r = {k: v for k, v in r.items() if k != "auth_hints"}
        if r.get("api_key"):
            r = {**r, "api_key": "●●●●"}
        relays_out.append(r)
    data["relays"] = relays_out
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
    from . import verbs

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
            for s in verbs._probe_tools()
        ],
    )


def events(cfg: ProxyConfig, limit: int = 50) -> dict:
    from . import verbs

    return envelope(True, verbs._tail_events(limit))


def visionlog(cfg: ProxyConfig, harness: str | None = None, session: str | None = None) -> dict:
    from . import verbs

    return envelope(True, verbs._vl_query(harness=harness, session=session))
