"""探针动词：probe（单模型/批量）与探测目标解析。

解析"探谁"：两层=工具端口（无 key），直连=harness 自身配置/快照或对应 relay 的
base_url+key；zcode 取该供应商原始上游。probe_one 返回三态（result/target_found/
reason），probe_all_untested 批量探当前激活供应商的无缓存组合。被测试 monkeypatch
的 _run_probe 经 facade（verbs._run_probe）调用时解析。
"""

from __future__ import annotations

from .config import ProxyConfig
from .reconcile import observe as _observe_impl
from .tools import TOOL_DOSSIERS
from .verbs_contract import envelope
from .verbs_models import _lookup_probe


def probe_target_for(cfg: ProxyConfig, harness: str, provider: str, tool_by_name: dict) -> tuple[str, str, str]:
    """探测目标：两层=工具端口（无 key）；直连=harness 自身配置或对应 relay 的 base_url+key。"""
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
        tool_by_name = {t["name"]: t for t in _observe_impl(cfg)["tools"]}
    base, key, proto = probe_target_for(cfg, harness, provider, tool_by_name)
    if base:
        return base, key, proto, None
    if harness == "zcode":  # M7: zcode 无路由工具，无目标=找不到该供应商的原始上游
        return base, key, proto, f"{harness}: 未找到该供应商的原始上游（供应商不存在或未接管）"
    return base, key, proto, f"{harness}: 路由工具不在线,且未配置可探测的直连上游"


def _run_probe(
    cfg: ProxyConfig, harness: str, provider: str, model: str, tool_by_name: dict | None = None
) -> str | None:
    from .annotate import run_probe as _rp

    if tool_by_name is None:  # 单模型路径：现探一次；批量路径由调用方传入（省 N 次探测）
        tool_by_name = {t["name"]: t for t in _observe_impl(cfg)["tools"]}
    base, key, proto = probe_target_for(cfg, harness, provider, tool_by_name)
    return _rp(cfg, harness, provider, model, base, key, proto)


def probe_one(cfg: ProxyConfig, harness: str, provider: str, model: str) -> dict:
    from . import verbs

    tool_by_name = {t["name"]: t for t in _observe_impl(cfg)["tools"]}
    base, _key, _proto, reason = verbs.probe_target_info(cfg, harness, provider, tool_by_name)
    if not base:
        # 无结论(含无目标)= 合法三态(spec §5),不是错误;GUI 按 target_found 显示"不可达"
        return envelope(True, {"result": None, "target_found": False, "reason": reason})
    result = verbs._run_probe(cfg, harness, provider, model, tool_by_name)
    return envelope(True, {"result": result, "target_found": True, "reason": None})


def probe_all_untested(cfg: ProxyConfig) -> dict:
    """批量探测:当前激活供应商(is_current 行)的无缓存 (provider, model) 组合。
    非当前供应商的行不经工具路由、无探测路径——不尝试、不计入 results。"""
    from . import model_sources, verbs

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
                base, _key, _proto, reason = verbs.probe_target_info(cfg, harness, row.provider, tool_by_name)
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
                result = verbs._run_probe(cfg, harness, row.provider, m, tool_by_name)
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
