"""harness 接线规格：四种 harness 的配置表、备份后缀、base_url 归属判定与路径解析。

本模块只持有"规格"——配置文件位置/格式、备份命名（含 legacy 迁移后缀）、
base_url 归属分类（ours / 工具 / other / none）与 home→配置路径解析；
不含任何读写副作用，供 qwen/zcode/io/orchestrate 各模块共享。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .tools import TOOL_DOSSIERS

BAK_SUFFIX = ".vision-relay.bak"
LEGACY_BAK_SUFFIX = ".qwen-mm-proxy.bak"

# zcode provider.kind → relay 协议（spec §4；未知 kind 不接管）
_ZCODE_PROTO = {"anthropic": "anthropic", "openai": "chat", "openai-compatible": "chat"}
_ZCODE_RELAY_PREFIX = "zcode-"

# qwen-code ≥0.22.0：模型选中 modelProviders 条目时，请求端点取条目自身 baseUrl
# （解析优先级第二层），model.baseUrl 只是 /model 选择器的消歧提示、不在解析链内。
# 因此接管必须条目级改写；authType 即协议族，仅改写本代理支持的协议。
_QWEN_AUTH_PROTO = {"openai": "chat", "anthropic": "anthropic"}
_QWEN_RELAY_PREFIX = "qwen-"
# modalities 原值哨兵：接管前该字段不存在（还原时删除字段而非写回字符串）
_MOD_ABSENT = "~absent~"


@dataclass(frozen=True)
class _Harness:
    kind: str  # json | toml | env | zcode-v2
    rel_path: str | tuple[str, ...]
    key: str


HARNESS_CFG: dict[str, _Harness] = {
    # qwen-code 实际配置在 ~/.qwen/settings.json 的 model.baseUrl（不是旧路径 ~/.qwen-code/.env）
    "claude": _Harness("json", (".claude", "settings.json"), "env.ANTHROPIC_BASE_URL"),
    "codex": _Harness("toml", (".codex", "config.toml"), "base_url"),
    "qwen-code": _Harness("json", (".qwen", "settings.json"), "model.baseUrl"),
    # zcode 供应商配置在 ~/.zcode/v2/config.json 的 provider.<id>.options.baseURL（纯条目级，
    # 无全局 base_url；key 字段仅作路径占位，read_base_url 特判返回激活供应商地址）
    "zcode": _Harness("zcode-v2", (".zcode", "v2", "config.json"), "provider"),
}


def _path(home: str, harness: str) -> str:
    """home 根目录 + harness 相对路径 → 配置文件绝对路径。"""
    rel = HARNESS_CFG[harness].rel_path
    return os.path.join(home, *rel) if isinstance(rel, tuple) else os.path.join(home, rel)


def _find_bak(p: str) -> str | None:
    """定位备份文件：新后缀优先，缺失时回退 legacy .qwen-mm-proxy.bak（迁移兼容）。"""
    new = p + BAK_SUFFIX
    if os.path.exists(new):
        return new
    old = p + LEGACY_BAK_SUFFIX
    if os.path.exists(old):
        return old
    return None


def classify_base_url(url: str | None, bind_port: int) -> str:
    """base_url 归属：ours | cc-switch | codex-plus | other | none（spec §5 观测信号①）。"""
    if not url:
        return "none"
    if url == f"http://127.0.0.1:{bind_port}" or url.startswith(f"http://127.0.0.1:{bind_port}/"):
        return "ours"
    m = re.search(r":(\d+)", url)
    if not m:
        return "other"
    port = int(m.group(1))
    for name, d in TOOL_DOSSIERS.items():
        if port == d.port:
            return name
    return "other"


def relay_harness(name: str) -> str | None:
    """relay name → 所属 harness；无法判定（用户手编模板等）返回 None。

    判定即本仓库各创建点的命名约定：direct-<harness>（_absorb 吸收直连）、
    zcode-/qwen- 一层直连前缀（ensure_zcode/qwen_relays）、cc-anthropic /
    cc-codex / codex-plus（ensure_tool_relays）。GUI 详情抽屉据此把 relay
    只挂在相关 harness 卡片下（2026-09-02：此前每个工具下都显示全部 relay）。"""
    if name.startswith("direct-"):
        h = name[len("direct-") :]
        return h if h in HARNESS_CFG else None
    if name.startswith(_ZCODE_RELAY_PREFIX):
        return "zcode"
    if name.startswith(_QWEN_RELAY_PREFIX):
        return "qwen-code"
    if name == "cc-anthropic":
        return "claude"
    if name in ("cc-codex", "codex-plus"):
        return "codex"
    return None
