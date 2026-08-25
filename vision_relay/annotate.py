"""Auto annotation (spec §5 模型能力标注): scan -> probe/catalog -> write triple store.

写规则：source=user 绝不被自动标注覆盖（caps 有存储值而 sources 缺键的存量数据同受
保护，镜像 capability._stored 读方语义）；probe 结果覆盖 probe/catalog 来源值并写
probe_results 缓存；目录建议（内置名单命中）写 source=catalog；都不中=不落键（未标注）。
锁纪律：网络探测（单模型最长 30s）在 config_lock 之外；写回落盘段（含 save_config
与事件）持锁，同线程可重入，外层已持锁时直接放行。
"""

from __future__ import annotations

import time

from .capability import suggest
from .config import CAPABILITY_VALUES, ProxyConfig, save_config
from .locking import config_lock
from .probe import probe_modality
from .reconcile import append_event


def _probe_one(provider: str, model: str, base_url: str = "", api_key: str = "", protocol: str = "chat") -> str | None:
    """单一入口便于测试替换（provider 形参即为 monkeypatch 签名对称而设，探测本身不使用）；
    无 base_url 时直接视为不可探测。"""
    if not base_url:
        return None
    return probe_modality(base_url=base_url, api_key=api_key, model=model, protocol=protocol)


def _stored_value(cfg: ProxyConfig, harness: str, provider: str, model: str) -> str | None:
    return cfg.model_capabilities.get(harness, {}).get(provider, {}).get(model)


def _set_value(cfg: ProxyConfig, harness: str, provider: str, model: str, value: str, source: str) -> None:
    cfg.model_capabilities.setdefault(harness, {}).setdefault(provider, {})[model] = value
    cfg.capability_sources.setdefault(harness, {}).setdefault(provider, {})[model] = source


def _apply(cfg: ProxyConfig, harness: str, provider: str, model: str, value: str, source: str) -> bool:
    """写 triple store；仅当值或来源真变时返回 True（diff-aware，防无谓重写盘）。"""
    old_value = _stored_value(cfg, harness, provider, model)
    old_source = cfg.capability_sources.get(harness, {}).get(provider, {}).get(model)
    _set_value(cfg, harness, provider, model, value, source)
    return old_value != value or old_source != source


def _source_of(cfg: ProxyConfig, harness: str, provider: str, model: str) -> str | None:
    """镜像 capability._stored 读方语义：caps 有存储值而 sources 缺键 -> "user"
    （存量数据先于一切自动标注，防用户直写三层 caps 不带 sources 被覆盖销毁）；
    无存储值 -> None（未标注，可自动标注）。"""
    if _stored_value(cfg, harness, provider, model) is None:
        return None
    return cfg.capability_sources.get(harness, {}).get(provider, {}).get(model) or "user"


def _norm_stored(stored: str | None) -> str | None:
    """内存直构造数据可能残留旧词汇 'vision'：读取处兜底归一为 'image'（对齐 capability._norm）。"""
    return "image" if stored == "vision" else stored


def run_probe(
    cfg: ProxyConfig,
    harness: str,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    protocol: str,
) -> str | None:
    """执行探针：实测结果永远写 probe_results 缓存；仅当现有值非 user 来源（含无
    source 存量值）时更新标注值。

    返回生效值：user 来源=存储值（遗留 'vision' 归一 'image'），否则=实测值；
    None=探针不下结论（什么都不写）。
    """
    result = _probe_one(provider, model, base_url, api_key, protocol)  # 锁外：不能持锁做网络探测
    if result is None:
        return None
    with config_lock():  # 写回落盘段（同线程可重入，外层已持锁时直接放行）
        cfg.probe_results.setdefault(provider, {})[model] = {"result": result, "ts": time.time()}
        applied = _source_of(cfg, harness, provider, model) != "user"
        effective = result
        if applied:
            _set_value(cfg, harness, provider, model, result, "probe")
        else:
            stored = _norm_stored(_stored_value(cfg, harness, provider, model))
            effective = stored if stored in CAPABILITY_VALUES else result
        save_config(cfg)
        append_event(
            "auto_annotate",
            harness,
            {"provider": provider, "model": model, "by": "probe", "result": result, "applied": applied},
        )
    return effective


def _catalog_suggest(model: str) -> str | None:
    """目录建议（fnmatch 精确表 + 正则启发式统一入口，capability.suggest）。"""
    return suggest(model)


def auto_annotate(
    cfg: ProxyConfig,
    scan: list[dict],
    probe_targets: dict[str, tuple[str, str, str]],
) -> list[dict]:
    """对扫描到的 (harness, provider, model) 自动标注。

    probe_targets: {model: (base_url, api_key, protocol)} 可探测目标——以 model 为键
    跨 provider 共享同一探测目标（同名模型视作同一上游能力；两层=工具端口无 key 也可探）。
    返回逐模型报告（result None = 未标注）。逐模型探测在锁外；写回落盘段持 config_lock。
    """
    report: list[dict] = []
    changed = 0
    for row in scan:
        harness, provider, model = row["harness"], row.get("provider") or "?", row["model"]
        entry = {"harness": harness, "provider": provider, "model": model, "result": None, "by": None}
        if _source_of(cfg, harness, provider, model) == "user":
            stored = _norm_stored(_stored_value(cfg, harness, provider, model))
            if stored in CAPABILITY_VALUES:  # sources 标 user 而 caps 缺值的脏数据不让扫描崩掉
                entry["result"] = stored
                entry["by"] = "user"
            report.append(entry)
            continue
        cached = cfg.probe_results.get(provider, {}).get(model, {}).get("result")
        if cached in CAPABILITY_VALUES:
            with config_lock():
                if _apply(cfg, harness, provider, model, cached, "probe"):
                    changed += 1
            entry.update(result=cached, by="probe-cache")
            report.append(entry)
            continue
        target = probe_targets.get(model)
        result = _probe_one(provider, model, *target) if target else None  # 锁外
        if result in CAPABILITY_VALUES:
            with config_lock():
                cfg.probe_results.setdefault(provider, {})[model] = {"result": result, "ts": time.time()}
                if _apply(cfg, harness, provider, model, result, "probe"):
                    changed += 1
            entry.update(result=result, by="probe")
        else:
            suggestion = _catalog_suggest(model)
            if suggestion:
                with config_lock():
                    if _apply(cfg, harness, provider, model, suggestion, "catalog"):
                        changed += 1
                entry.update(result=suggestion, by="catalog")
            # 都不中：不落键 = 未标注（运行按 unknown_default 开关）
        report.append(entry)
    if changed:
        with config_lock():
            save_config(cfg)
        append_event("auto_annotate", None, {"scanned": len(report), "changed": changed})
    return report
