"""--json 动词的通信契约层：统一信封 + contract_version + 共用 IO 段 + 依赖注入点。

本模块是 GUI↔核心 CLI 通道的"契约面"——所有 --json 动词的输出都必须是
`envelope(ok, data)` = `{"contract_version": 1, "ok": bool, "data": …}`。
审查"是否动了契约"只需看本模块：信封结构、contract_version 语义、stdin 读取
与加锁落盘段、以及测试可替换的依赖注入点（各指向 reconcile/tools/visionlog
真实现）都集中于此。领域动词实现见 verbs_* 模块。
"""

from __future__ import annotations

import json
import sys

from .config import ProxyConfig, save_config
from .locking import config_lock
from .reconcile import observe as _observe_impl
from .reconcile import reconcile as _reconcile_impl
from .reconcile import tail_events as _tail_events_impl
from .tools import probe_tools as _probe_tools_impl
from .visionlog import query as _vl_query_impl

# 契约版本号：信封结构变更必须升版本并在 spec 记录（GUI 按此解析）。
CONTRACT_VERSION = 1


def envelope(ok: bool, data) -> dict:
    """统一信封：所有 --json 动词输出的唯一外壳。"""
    return {"contract_version": CONTRACT_VERSION, "ok": ok, "data": data}


def _stdin_json(kind: str):
    """写动词共用的 stdin 读取+顶层类型校验。返回 (payload, None) 或 (None, 错误 envelope)。"""
    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        return None, envelope(False, {"error": f"invalid stdin json: {exc}"})
    expected = {"array": list, "object": dict}[kind]
    if not isinstance(payload, expected):
        return None, envelope(False, {"error": f"expected a JSON {kind}"})
    return payload, None


def _locked_save(cfg: ProxyConfig) -> None:
    """写动词共用的落盘段：自方写者经文件锁串行（spec §4）。"""
    with config_lock():
        save_config(cfg)


# ── 依赖注入点（测试经 monkeypatch verbs.<name> 替换；生产各指向真实现）──
# 领域动词通过 facade（verbs.<name>）在调用时解析这些名字，故替换 facade 上的
# 绑定即对全部动词生效；不要在领域模块里直接 import 它们（会绕过注入）。
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
