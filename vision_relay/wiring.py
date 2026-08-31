"""start/stop 自动接线与回滚的 facade：把四处 harness 的 base_url 指到本代理(第一跳，有备份)，并在 stop 还原。

本模块在重构后只保留"组装层"职责：持有测试可 monkeypatch 的 HOME 隔离点、
重导出接线子模块的公共 API、并为依赖 home 的入口函数注入 HOME（薄包装）。
具体实现按职责拆分到：harness_spec（规格/路径/归属）、modalities（图片门原语）、
harness_io（base_url 读写/codex 目录补丁）、qwen_providers / zcode_providers
（条目级接线）、relays（模板/工具 relay）、wiring_orchestrate（start/stop 编排）。
"""

from __future__ import annotations

import os

from . import qwen_providers, wiring_orchestrate, wiring_status, zcode_providers
from .harness_io import (
    _codex_catalog_path,
    _first_model,
    _json_save_atomic,
    _patch_codex_catalog_modalities,
    _restore_codex_catalog,
    read_base_url,
    write_base_url,
)
from .harness_spec import (
    _QWEN_AUTH_PROTO,
    _QWEN_RELAY_PREFIX,
    _ZCODE_PROTO,
    _ZCODE_RELAY_PREFIX,
    BAK_SUFFIX,
    HARNESS_CFG,
    LEGACY_BAK_SUFFIX,
    _find_bak,
    _Harness,
    _path,
    classify_base_url,
)
from .modalities import _MOD_ABSENT, _ensure_image, _mod_input, _modalities_open, _open_modalities
from .qwen_providers import (
    _qwen_entry_keys,
    _qwen_provider_items,
    _qwen_provider_items_from,
    _qwen_provider_stats,
    _qwen_relay_groups,
    _qwen_relay_name,
    _qwen_resolve_key,
    _restore_qwen_providers,
    _rewrite_qwen_providers,
)
from .relays import _relay_name, ensure_tool_relays, relays_activate, relays_restore
from .wiring_orchestrate import (
    _generic_snapshot_or_bak_restore,
    _restore_harness_on_stop,
)
from .zcode_providers import (
    _is_zcode_relay,
    _mark_zcode_rewrite,
    _restore_zcode_providers,
    _rewrite_zcode_providers,
    _zcode_entries,
    _zcode_key,
    _zcode_marker_path,
    _zcode_provider_gated,
    _zcode_provider_stats,
    _zcode_relay_desired,
    _zcode_slug,
    remove_zcode_relays,
    zcode_rewrite_ts,
)

# 重导出清单：`from wiring import *` 与 F401 均以此为准（facade 公共面 = 原子模块公共面）。
__all__ = [
    "HOME",
    # harness_spec
    "BAK_SUFFIX",
    "LEGACY_BAK_SUFFIX",
    "HARNESS_CFG",
    "_Harness",
    "_QWEN_AUTH_PROTO",
    "_QWEN_RELAY_PREFIX",
    "_ZCODE_PROTO",
    "_ZCODE_RELAY_PREFIX",
    "_find_bak",
    "_path",
    "classify_base_url",
    # modalities
    "_MOD_ABSENT",
    "_ensure_image",
    "_modalities_open",
    "_mod_input",
    "_open_modalities",
    # harness_io
    "read_base_url",
    "write_base_url",
    "_json_save_atomic",
    "_first_model",
    "_codex_catalog_path",
    "_patch_codex_catalog_modalities",
    "_restore_codex_catalog",
    # qwen_providers
    "ensure_qwen_relays",
    "reconcile_qwen_providers",
    "_qwen_provider_items_from",
    "_qwen_provider_items",
    "_qwen_entry_keys",
    "_qwen_resolve_key",
    "_rewrite_qwen_providers",
    "_restore_qwen_providers",
    "_qwen_relay_name",
    "_qwen_relay_groups",
    "_qwen_provider_stats",
    # zcode_providers
    "ensure_zcode_relays",
    "remove_zcode_relays",
    "reconcile_zcode_providers",
    "zcode_rewrite_ts",
    "_zcode_marker_path",
    "_mark_zcode_rewrite",
    "_rewrite_zcode_providers",
    "_restore_zcode_providers",
    "_zcode_slug",
    "_zcode_relay_desired",
    "_is_zcode_relay",
    "_zcode_key",
    "_zcode_entries",
    "_zcode_provider_gated",
    "_zcode_provider_stats",
    # relays
    "relays_activate",
    "relays_restore",
    "ensure_tool_relays",
    "_relay_name",
    # wiring_orchestrate（wiring_* 为本模块内的 HOME 绑定包装）
    "wiring_backup_and_rewrite",
    "wiring_restore",
    "wiring_report",
    "wiring_restore_by_snapshot",
    "wiring_restore_harness",
    "wiring_restore_on_stop",
    "_restore_harness_on_stop",
    "_generic_snapshot_or_bak_restore",
]

# 测试可 monkeypatch 此值以隔离（不触碰真实 ~）。子模块函数不直接读它，
# 而由下列薄包装在调用时注入——保证 monkeypatch wiring.HOME 立即对全部入口生效。
HOME = os.path.expanduser("~")


# ── 依赖 home 的入口：薄包装，注入 HOME ──────────────────────────────
def ensure_qwen_relays(cfg) -> list[str]:
    return qwen_providers.ensure_qwen_relays(cfg, HOME)


def reconcile_qwen_providers(cfg) -> dict | None:
    return qwen_providers.reconcile_qwen_providers(cfg, HOME)


def ensure_zcode_relays(cfg) -> list[str]:
    return zcode_providers.ensure_zcode_relays(cfg, HOME)


def reconcile_zcode_providers(cfg) -> dict | None:
    return zcode_providers.reconcile_zcode_providers(cfg, HOME)


def wiring_backup_and_rewrite(cfg) -> list[str]:
    return wiring_orchestrate.backup_and_rewrite(cfg, HOME)


def wiring_restore(cfg) -> list[str]:
    return wiring_orchestrate.restore(cfg, HOME)


def wiring_report(cfg) -> list[dict]:
    return wiring_status.report(cfg, HOME)


def wiring_restore_by_snapshot(cfg) -> list[str]:
    return wiring_orchestrate.restore_by_snapshot(cfg, HOME)


def wiring_restore_harness(cfg, name: str) -> list[str]:
    return wiring_orchestrate.restore_harness(cfg, name, HOME)


def wiring_restore_on_stop(cfg) -> list[str]:
    return wiring_orchestrate.restore_on_stop(cfg, HOME)
