"""Auto annotation (spec §5 模型能力标注): scan -> probe/catalog -> write triple store.

写规则：source=user 绝不被自动标注覆盖；probe 结果覆盖 probe/catalog 来源值并
写 probe_results 缓存；目录建议（内置名单命中）写 source=catalog；都不中=不落键（未标注）。
"""

from __future__ import annotations

import fnmatch
import time

from .capability import BUILTIN_CAPABILITIES
from .config import ProxyConfig, save_config
from .probe import probe_modality
from .reconcile import append_event


def _probe_one(provider: str, model: str, base_url: str = "", api_key: str = "", protocol: str = "chat") -> str | None:
    """单一入口便于测试替换；无 base_url 时直接视为不可探测。"""
    if not base_url:
        return None
    return probe_modality(base_url, api_key, model, protocol)


def _set_value(cfg: ProxyConfig, harness: str, provider: str, model: str, value: str, source: str) -> None:
    cfg.model_capabilities.setdefault(harness, {}).setdefault(provider, {})[model] = value
    cfg.capability_sources.setdefault(harness, {}).setdefault(provider, {})[model] = source


def _source_of(cfg: ProxyConfig, harness: str, provider: str, model: str) -> str | None:
    return cfg.capability_sources.get(harness, {}).get(provider, {}).get(model)


def run_probe(
    cfg: ProxyConfig,
    harness: str,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    protocol: str,
) -> str | None:
    """执行探针：结果永远写 probe_results；仅当现有值非 user 来源时更新标注值。"""
    result = _probe_one(provider, model, base_url, api_key, protocol)
    if result is None:
        return None
    cfg.probe_results.setdefault(provider, {})[model] = {"result": result, "ts": time.time()}
    current_source = _source_of(cfg, harness, provider, model)
    effective = result
    if current_source != "user":
        _set_value(cfg, harness, provider, model, result, "probe")
    else:
        stored = cfg.model_capabilities.get(harness, {}).get(provider, {}).get(model)
        effective = stored if stored in ("image", "text_only") else result
    save_config(cfg)
    append_event("auto_annotate", harness, {"provider": provider, "model": model, "by": "probe", "result": result})
    return effective


def _catalog_suggest(model: str) -> str | None:
    for pattern, cap in BUILTIN_CAPABILITIES.items():
        if fnmatch.fnmatch(model, pattern):
            return cap
    return None


def auto_annotate(
    cfg: ProxyConfig,
    scan: list[dict],
    probe_targets: dict[str, tuple[str, str, str]],
) -> list[dict]:
    """对扫描到的 (harness, provider, model) 自动标注。
    probe_targets: {model: (base_url, api_key, protocol)} 可探测目标（两层=工具端口无 key 也可探）。
    返回逐模型报告（result None = 未标注）。"""
    report: list[dict] = []
    changed = False
    for row in scan:
        harness, provider, model = row["harness"], row.get("provider") or "?", row["model"]
        entry = {"harness": harness, "provider": provider, "model": model, "result": None, "by": None}
        if _source_of(cfg, harness, provider, model) == "user":
            # sources 标 user 而 caps 缺值的脏数据不让扫描崩掉：result 保持 None（未标注）
            stored = cfg.model_capabilities.get(harness, {}).get(provider, {}).get(model)
            if stored in ("image", "text_only"):
                entry["result"] = stored
                entry["by"] = "user"
            report.append(entry)
            continue
        cached = cfg.probe_results.get(provider, {}).get(model, {}).get("result")
        if cached in ("image", "text_only"):
            _set_value(cfg, harness, provider, model, cached, "probe")
            entry.update(result=cached, by="probe-cache")
            report.append(entry)
            changed = True
            continue
        target = probe_targets.get(model)
        result = _probe_one(provider, model, *target) if target else None
        if result in ("image", "text_only"):
            cfg.probe_results.setdefault(provider, {})[model] = {"result": result, "ts": time.time()}
            _set_value(cfg, harness, provider, model, result, "probe")
            entry.update(result=result, by="probe")
            changed = True
        else:
            suggestion = _catalog_suggest(model)
            if suggestion:
                _set_value(cfg, harness, provider, model, suggestion, "catalog")
                entry.update(result=suggestion, by="catalog")
                changed = True
            # 都不中：不落键 = 未标注（运行按 unknown_default 开关）
        report.append(entry)
    if changed:
        save_config(cfg)
        append_event("auto_annotate", None, {"count": len(report)})
    return report
