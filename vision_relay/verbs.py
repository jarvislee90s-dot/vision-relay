"""--json management verbs (spec §4 通信契约): one envelope, contract_version pinned.

GUI（M2）只消费这些动词的输出；结构变更必须升 contract_version 并在 spec 记录。
"""

from __future__ import annotations

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
