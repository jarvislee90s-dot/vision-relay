"""Takeover combo snapshots (spec §5): base_url + key LOCATION + model per harness.

防外部工具档案污染（CC Switch 会把 live 文件回读进它的供应商档案）：本代理
始终持有"接管前正确组合"的真相；任何还原按组合写回。每 harness 只存最新一条。
**绝不存密钥值**，只存 key 所在配置位置描述。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass

# 测试可 monkeypatch。
HOME = os.path.expanduser("~")

_KEY_FIELDS = {
    "claude": ((".claude", "settings.json"), ("env.ANTHROPIC_AUTH_TOKEN", "env.ANTHROPIC_API_KEY")),
    "codex": ((".codex", "auth.json"), ("OPENAI_API_KEY",)),
    "qwen-code": ((".qwen", "settings.json"), ("model.apiKey",)),
}


@dataclass
class Snapshot:
    base_url: str
    key_ref: str  # key 所在位置描述（非值）
    model: str
    second_hop: str | None = None  # 接管时该 harness 的第二跳工具名（cc-switch / codex-plus）
    ts: float = 0.0

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = time.time()


def _path() -> str:
    from .env_util import config_dir

    return os.path.join(config_dir(), "snapshots.json")


def save(harness: str, snap: Snapshot) -> None:
    data = {}
    if os.path.exists(_path()):
        try:
            with open(_path(), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
    data[harness] = asdict(snap)  # 最新一条覆盖
    tmp = _path() + ".tmp"
    os.makedirs(os.path.dirname(_path()), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, _path())


_KNOWN_FIELDS = ("base_url", "key_ref", "model", "second_hop", "ts")


def load() -> dict[str, Snapshot]:
    try:
        with open(_path(), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    out: dict[str, Snapshot] = {}
    for h, v in raw.items():
        if not isinstance(v, dict):
            continue  # 坏条目（非对象）跳过，不击穿消费方
        # 只按已知字段集重构：多余字段过滤掉；缺必填字段的条目跳过
        try:
            out[h] = Snapshot(**{k: v[k] for k in _KNOWN_FIELDS if k in v})
        except TypeError:
            continue
    return out


def key_ref_for(harness: str) -> str:
    """探测该 harness 配置里 key 的存在位置（只回位置名，不回值）。"""
    rel, fields = _KEY_FIELDS.get(harness, (None, ()))
    if rel is None:
        return "unknown"
    p = os.path.join(HOME, *rel)
    if not os.path.exists(p):
        return "not-found"
    if harness == "codex":
        return os.path.basename(p)  # auth.json 存在即视为 key 位置
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        hit = [f for f in fields if _dig(d, f) not in (None, "")]
        return "|".join(hit) if hit else "not-found"
    except (OSError, ValueError):
        return "unparsable"


def _dig(d: dict, dotted: str):
    node = d
    for part in dotted.split("."):
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return None
    return node
