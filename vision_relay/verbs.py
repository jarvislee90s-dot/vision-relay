"""--json management verbs 的 facade：重导出契约层与各领域动词，公共面不变。

GUI（M2）只消费这些动词的输出；结构变更必须升 contract_version 并在 spec 记录。
重构后 verbs.py 只保留"组装层"职责：以 `__all__` 重导出子模块公共面，并暴露
测试可 monkeypatch 的依赖注入点（领域动词经本模块在调用时解析它们）。实现按
职责拆分到：verbs_contract（信封/契约/DI 点）、verbs_models、verbs_vlm、
verbs_probe、verbs_settings、verbs_status。
"""

from __future__ import annotations

import httpx as httpx  # re-export: tests patch verbs.httpx.get（= 全局 httpx 模块）

from .verbs_contract import (
    CONTRACT_VERSION,
    _locked_save,
    _observe_for_status,
    _probe_tools,
    _reconcile,
    _stdin_json,
    _tail_events,
    _vl_query,
    envelope,
)
from .verbs_models import (
    _lookup_cap,
    _lookup_probe,
    _scan_triples,
    models_fetch,
    models_scan,
    models_set,
)
from .verbs_probe import (
    _run_probe,
    probe_all_untested,
    probe_one,
    probe_target_for,
    probe_target_info,
)
from .verbs_settings import relay_set, settings_set, zcode_restart
from .verbs_status import (
    config_get,
    diagnose,
    events,
    refresh,
    status,
    tools,
    visionlog,
)
from .verbs_vlm import _VLMClient, vlm_secret, vlm_set, vlm_test

__all__ = [
    "CONTRACT_VERSION",
    "envelope",
    "_stdin_json",
    "_locked_save",
    # DI 注入点（测试 monkeypatch 目标；领域动词经本模块解析）
    "_observe_for_status",
    "_reconcile",
    "_probe_tools",
    "_tail_events",
    "_vl_query",
    # models
    "_lookup_cap",
    "_lookup_probe",
    "_scan_triples",
    "models_scan",
    "models_set",
    "models_fetch",
    # vlm
    "_VLMClient",
    "vlm_set",
    "vlm_secret",
    "vlm_test",
    # probe
    "probe_target_for",
    "probe_target_info",
    "_run_probe",
    "probe_one",
    "probe_all_untested",
    # settings
    "settings_set",
    "relay_set",
    "zcode_restart",
    # status / read
    "status",
    "refresh",
    "diagnose",
    "config_get",
    "tools",
    "events",
    "visionlog",
]
