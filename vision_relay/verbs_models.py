"""模型能力动词：models-scan / models-set / models-fetch 与模型矩阵扫描原语。

负责模型能力（image|text_only）的读取草稿、stdin 三元组写入（source=user，
null=清除）、从上游 /v1/models 拉模型 ID 清单。写入走文件锁、全量校验后落盘。
被测试 monkeypatch 的 _scan_triples 经 facade（verbs._scan_triples）调用时解析。
"""

from __future__ import annotations

import httpx

from .config import ProxyConfig, save_config
from .locking import config_lock
from .verbs_contract import _stdin_json, envelope


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


def models_scan(cfg: ProxyConfig) -> dict:
    from . import verbs

    return envelope(True, {"models": verbs._scan_triples(cfg)})


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
