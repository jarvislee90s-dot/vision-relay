"""VLM 动词：vlm-set / vlm-secret / vlm-test（VLM 全局/分组配置、明文回显、连通测试）。

vlm-set 写 VLM 配置（空串=不改、打码占位=拒绝、custom_tierX null=恢复默认）；
vlm-secret 是「输出不带 key」工程宪法的唯一刻意豁免（GUI「显示」按钮）；
vlm-test 走与生产同一 VLMClient 调用路径做连通测试。被测试 monkeypatch 的
_VLMClient 经 facade（verbs._VLMClient）调用时解析。
"""

from __future__ import annotations

from .config import ProxyConfig
from .verbs_contract import _locked_save, _stdin_json, envelope

MASK = "●●●●"


def _VLMClient(vlm_cfg):
    from .vlm import VLMClient

    return VLMClient(vlm_cfg)


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


def vlm_test(cfg: ProxyConfig, payload: dict | None = None) -> dict:
    """与生产同一调用路径的连通测试（spec §6 设置·VLM）。
    payload: {mode: tier1|tier2, question?, custom_prompt?, harness?, image_base64?, media_type?}
    payload 缺省时（CLI 入口）从 stdin 读 JSON。"""
    import base64
    import time

    from . import verbs

    if payload is None:
        payload, err = _stdin_json("object")
        if err is not None:
            return err
    mode = payload.get("mode", "tier1")
    if mode not in ("tier1", "tier2"):
        return envelope(False, {"error": "mode must be tier1|tier2"})
    harness = payload.get("harness")
    merged = cfg.vlm_for(harness) if harness else cfg.vlm
    client = verbs._VLMClient(merged)
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
