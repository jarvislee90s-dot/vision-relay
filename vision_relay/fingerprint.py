"""密钥指纹（spec 2026-08-26 §6）：前4+后4+长度，用于请求期选路消歧。

8 个字符无法还原 32+ 字符的随机 key，且 proxy.json（0600）本就允许存完整 key 的
relay——指纹严格更不敏感；仍不进日志/快照/GUI。极短 key 只露长度（不露字符）。"""

from __future__ import annotations


def key_fingerprint(key: str) -> str:
    k = str(key or "")
    if len(k) < 8:
        return f"short@{len(k)}"
    return f"{k[:4]}…{k[-4:]}@{len(k)}"


def fingerprint_from_headers(headers: dict[str, str]) -> str | None:
    """客户端入站鉴权头 → 指纹（best-effort；取不到返回 None，选路退回顺序命中）。"""
    auth = headers.get("Authorization") or headers.get("x-api-key") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else auth.strip()
    return key_fingerprint(token) if token else None
