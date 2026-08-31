"""start/stop 接线编排与回滚：备份→改写→快照，stop/对账时按快照或 .bak 还原。

本模块是 wiring 的薄编排层：遍历启用的 harness，委托 harness_io / qwen_providers /
zcode_providers 完成各格式改写与还原，并维护接管组合快照。所有函数接收显式 home
（由 wiring facade 注入，作为测试隔离 monkeypatch 点），自身不读全局 HOME。
"""

from __future__ import annotations

import os

from . import snapshot
from .harness_io import (
    _first_model,
    _patch_codex_catalog_modalities,
    _restore_codex_catalog,
    read_base_url,
    write_base_url,
)
from .harness_spec import BAK_SUFFIX, HARNESS_CFG, _find_bak, _path, classify_base_url
from .qwen_providers import _qwen_provider_stats, _restore_qwen_providers, _rewrite_qwen_providers, ensure_qwen_relays
from .tools import TOOL_DOSSIERS
from .zcode_providers import (
    _mark_zcode_rewrite,
    _restore_zcode_providers,
    _rewrite_zcode_providers,
    _zcode_provider_stats,
    ensure_zcode_relays,
)


def backup_and_rewrite(cfg, home: str) -> list[str]:
    """为启用的 harness 备份(不重复)并把 base_url 指到本代理；接管前记录组合快照。

    qwen-code 额外做条目级接管(modelProviders[].baseUrl)：qwen 0.22.0 起条目 baseUrl
    优先于 model.baseUrl，只改全局字段等于没接管。"""
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    changed: list[str] = []
    for name in cfg.routing.harnesses:
        h = HARNESS_CFG[name]
        p = _path(home, name)
        if not os.path.exists(p):
            changed.append(f"{name}: no config file, skipped")
            continue
        original = read_base_url(p, h)
        if _find_bak(p) is None:  # 已有备份（含旧后缀）不覆盖，防止把代理地址存成"原始值"
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                import shutil

                shutil.copyfile(p, p + BAK_SUFFIX)
            except OSError:
                pass
        # qwen 条目级改写（必须在备份之后；快照需要合并条目原值映射）
        qwen_new, qwen_mod, qwen_skipped, qwen_rewritten, qwen_gated = (None, None, 0, 0, 0)
        zcode_new: dict = {}
        zcode_mod: dict = {}
        zcode_stats: dict = {}
        if name == "qwen-code":
            qwen_new, qwen_mod, qwen_skipped, qwen_rewritten, qwen_gated = _rewrite_qwen_providers(p, proxy_url)
        elif name == "zcode":
            zcode_new, zcode_mod, zcode_stats = _rewrite_zcode_providers(p, proxy_url)
        base_changed = bool(original) and classify_base_url(original, cfg.bind_port) != "ours"
        if base_changed or qwen_new or qwen_mod or zcode_new or zcode_mod:
            # 接管前组合快照：base_url + key 位置 + 模型 + 第二跳归属（spec §5）；
            # 条目映射与既有快照合并（重复 start 时已指本代理的条目保留首次记录）
            second_hop = classify_base_url(original, cfg.bind_port) if original else None
            second_hop = second_hop if second_hop in TOOL_DOSSIERS else None
            model = _first_model(p)  # 内部自吞读取失败（返回空串），无需再包
            old = snapshot.load().get(name)
            merged = dict(old.provider_urls or {}) if old and old.provider_urls else {}
            merged.update(qwen_new or zcode_new or {})
            merged_mod = dict(old.provider_modalities or {}) if old and old.provider_modalities else {}
            merged_mod.update(qwen_mod or zcode_mod or {})
            try:
                snapshot.save(
                    name,
                    snapshot.Snapshot(
                        base_url=original if base_changed else (old.base_url if old else (original or "")),
                        key_ref=snapshot.key_ref_for(name),
                        model=model,
                        second_hop=second_hop,
                        provider_urls=merged or None,
                        provider_modalities=merged_mod or None,
                    ),
                )
            except Exception:  # 快照尽力而为，绝不打断接管（保护面不依赖 snapshot 内部实现）
                pass
        if name == "zcode":
            changed.append(
                f"zcode: providers {zcode_stats.get('rewritten', 0)} -> proxy"
                + (f", {zcode_stats.get('gated', 0)} modalities gated" if zcode_stats.get("gated") else "")
                + f", {zcode_stats.get('skipped_nokey', 0)} nokey skipped, {zcode_stats.get('skipped_kind', 0)} unknown-kind skipped"
            )
        else:
            ok = write_base_url(p, h, proxy_url)
            msg = f"{name}: base_url -> {proxy_url} ({'ok' if ok else 'FAIL'})"
            if name == "qwen-code":
                msg += (
                    f"; providers {qwen_rewritten} entries -> proxy"
                    + (f", {qwen_gated} modalities gate opened" if qwen_gated else "")
                    + f", {qwen_skipped} skipped"
                )
            changed.append(msg)
        # base 已指本代理的重复接管也补（捕获 Codex++ 运行期重新生成的目录）
        if name == "codex":
            cat_msg = _patch_codex_catalog_modalities(p)
            if cat_msg:
                changed.append(f"codex: {cat_msg}")
    if any(h == "qwen-code" for h in cfg.routing.harnesses):
        for n in ensure_qwen_relays(cfg, home):
            changed.append(f"qwen-code: relay {n} added (一层直连, 鉴权透传客户端头)")
    if any(h == "zcode" for h in cfg.routing.harnesses):
        for n in ensure_zcode_relays(cfg, home):
            changed.append(f"zcode: relay {n} added (一层直连, 鉴权透传, 指纹选路)")
    return changed


def restore(cfg, home: str) -> list[str]:
    """从整文件备份还原（start 的对称动作；仅当前 base_url 指向本代理时执行）。

    与 restore_by_snapshot 的分工：本函数按"第一次接管前的原始文件"还原，
    供正常 stop 使用；崩溃/漂移后的修复走 restore_by_snapshot（组合快照）。
    """
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    restored: list[str] = []
    for name in cfg.routing.harnesses:
        h = HARNESS_CFG[name]
        p = _path(home, name)
        bak = _find_bak(p)
        if bak is None:
            continue
        cur = read_base_url(p, h)
        # 精确匹配（或带路径前缀）才视为本代理：startswith(proxy_url) 会把 :87870 误判为 :8787。
        if cur is not None and cur != proxy_url and not cur.startswith(proxy_url + "/"):
            restored.append(f"{name}: 当前 base_url={cur!r} 非本代理，未还原(保留备份)")
            continue
        if name == "codex":  # 目录还原须在 config 整文件换回前（当前 config 仍引用被补丁目录）
            cat_msg = _restore_codex_catalog(p)
            if cat_msg:
                restored.append(f"{name}: {cat_msg}")
        try:
            import shutil

            shutil.copyfile(bak, p)
            os.unlink(bak)
            restored.append(f"{name}: restored")
        except OSError as exc:
            restored.append(f"{name}: restore FAIL {exc}")
    return restored


def report(cfg, home: str) -> list[dict]:
    """四处 harness 当前 base_url 归属。qwen-code 另附条目级统计（0.22.0 条目
    baseUrl 优先，全局字段 wired 不代表真接管；门不开图片进不了请求，故 wired
    要求 URL 指向本代理且门全开）。"""
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    out = []
    for name in HARNESS_CFG:
        p = _path(home, name)
        cur = read_base_url(p, HARNESS_CFG[name]) if os.path.exists(p) else None
        row = {
            "harness": name,
            "path": p,
            "base_url": cur,
            "wired": bool(cur and (cur == proxy_url or cur.startswith(proxy_url + "/"))),
            "has_backup": _find_bak(p) is not None,
        }
        if name == "qwen-code" and os.path.exists(p):
            stats = _qwen_provider_stats(p, proxy_url)
            row["providers"] = stats
            row["wired"] = row["wired"] and stats["eligible"] == stats["wired"] and stats["eligible"] == stats["gated"]
        if name == "zcode" and os.path.exists(p):
            stats = _zcode_provider_stats(p, proxy_url)
            row["providers"] = stats
            # zcode 纯条目级：wired 只看 eligible 全覆盖+门全开（激活供应商可能是空 key 未接管者，
            # 其直连地址不代表接管失败）
            row["wired"] = stats["eligible"] > 0 and stats["eligible"] == stats["wired"] == stats["gated"]
        out.append(row)
    return out


def restore_by_snapshot(cfg, home: str) -> list[str]:
    """按接管组合快照还原（spec §5 修复：路由关-崩溃路径）。仅当前指向本代理时执行。

    与 restore 的分工：本函数按"接管前正确组合"还原（防外部工具档案污染），
    供对账/修复使用；正常 stop 的整文件备份还原走 restore。还原成功后删掉
    该 harness 的 .bak——防止后续 stop 走 restore 时用过期整文件备份覆盖掉
    已按快照还原的状态。
    """
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    snaps = snapshot.load()
    restored: list[str] = []
    for name in cfg.routing.harnesses:
        snap = snaps.get(name)
        if snap is None:
            continue
        p = _path(home, name)
        if name == "zcode":
            if snap.provider_urls or snap.provider_modalities:
                n = _restore_zcode_providers(p, proxy_url, snap.provider_urls or {}, snap.provider_modalities)
                restored.append(f"{name}: providers restored ({n} entries)")
            bak = _find_bak(p)
            if bak is not None:  # 整文件备份已过期（快照才是真相），删除防误还原
                try:
                    os.unlink(bak)
                except OSError:
                    pass
            continue
        cur = read_base_url(p, HARNESS_CFG[name]) if os.path.exists(p) else None
        if cur is None or (cur != proxy_url and not cur.startswith(proxy_url + "/")):
            restored.append(f"{name}: 当前 base_url={cur!r} 非本代理，跳过还原")
            continue
        if name == "codex":
            cat_msg = _restore_codex_catalog(p)
            if cat_msg:
                restored.append(f"{name}: {cat_msg}")
        ok = write_base_url(p, HARNESS_CFG[name], snap.base_url)
        if ok and name == "qwen-code" and snap.provider_urls:
            n = _restore_qwen_providers(p, proxy_url, snap.provider_urls, snap.provider_modalities)
            restored.append(f"{name}: providers restored ({n} entries)")
        if ok:
            bak = _find_bak(p)
            if bak is not None:  # 整文件备份已过期（快照才是真相），删除防误还原
                try:
                    os.unlink(bak)
                except OSError:
                    pass
        restored.append(f"{name}: restored to {snap.base_url} ({'ok' if ok else 'FAIL'})")
    return restored


def _restore_harness_on_stop(cfg, snaps: dict, name: str, home: str) -> list[str]:
    """stop 的单 harness 还原步骤（restore_on_stop 与「取消勾选即还原」共用，spec §8）。"""
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    h = HARNESS_CFG[name]
    p = _path(home, name)
    if not os.path.exists(p):
        return []
    if name == "zcode":
        snap = snaps.get(name)
        if snap is not None and (snap.provider_urls or snap.provider_modalities):
            n = _restore_zcode_providers(p, proxy_url, snap.provider_urls or {}, snap.provider_modalities)
            bak = _find_bak(p)
            if bak is not None:
                try:
                    os.unlink(bak)
                except OSError:
                    pass
            return [f"{name}: providers restored ({n} entries)"]
        stats = _zcode_provider_stats(p, proxy_url)
        if stats["wired"] == 0:
            return []  # 无本代理痕迹：不动
        bak = _find_bak(p)
        if bak is None:
            return [f"{name}: 无快照且无备份，跳过"]
        try:
            import shutil

            shutil.copyfile(bak, p)
            os.unlink(bak)
            _mark_zcode_rewrite()
            return [f"{name}: bak restored"]
        except OSError as exc:
            return [f"{name}: restore FAIL {exc}"]
    # ---- 以下为既有 claude/codex/qwen-code 逻辑（逐字保留）----
    cur = read_base_url(p, h)
    if cur is None or (cur != proxy_url and not cur.startswith(proxy_url + "/")):
        return [f"{name}: 当前 base_url={cur!r} 非本代理，跳过还原"]
    if name == "codex":  # 目录还原须在 config 整文件换回前（当前 config 仍引用被补丁目录）
        cat_msg = _restore_codex_catalog(p)
        if cat_msg:
            return [f"{name}: {cat_msg}"] + _generic_snapshot_or_bak_restore(cfg, snaps, name, p, h, cur, proxy_url)
    return _generic_snapshot_or_bak_restore(cfg, snaps, name, p, h, cur, proxy_url)


def _generic_snapshot_or_bak_restore(cfg, snaps, name, p, h, cur, proxy_url):
    """既有通用还原尾段（快照优先 → .bak 兜底），从原 wiring_restore_on_stop 循环体原样抽出。"""
    msgs: list[str] = []
    snap = snaps.get(name)
    if snap is not None:
        ok = write_base_url(p, h, snap.base_url)
        if ok and name == "qwen-code" and snap.provider_urls:
            n = _restore_qwen_providers(p, proxy_url, snap.provider_urls, snap.provider_modalities)
            msgs.append(f"{name}: providers restored ({n} entries)")
        if ok:
            bak = _find_bak(p)
            if bak is not None:
                try:
                    os.unlink(bak)
                except OSError:
                    pass
        msgs.append(f"{name}: snapshot restored to {snap.base_url} ({'ok' if ok else 'FAIL'})")
        return msgs
    bak = _find_bak(p)
    if bak is None:
        return [f"{name}: 无快照且无备份，跳过"]
    try:
        import shutil

        shutil.copyfile(bak, p)
        os.unlink(bak)
        return [f"{name}: bak restored"]
    except OSError as exc:
        return [f"{name}: restore FAIL {exc}"]


def restore_harness(cfg, name: str, home: str) -> list[str]:
    """单 harness 还原（「路由范围取消勾选」用；与 stop 同一还原步骤，spec §8）。"""
    if name not in HARNESS_CFG:
        return [f"{name}: unknown harness"]
    return _restore_harness_on_stop(cfg, snapshot.load(), name, home)


def restore_on_stop(cfg, home: str) -> list[str]:
    """stop 的统一还原（既有语义不变）：按最新接管组合快照；快照缺失退回整文件 .bak 兜底。

    每 harness 独立决策：有快照 → 只写回 base_url（运行期间用户对配置文件的其他
    修改原样保留），并删除已过期的 .bak；无快照 → .bak 整文件还原（含 key 位置等
    完整原始状态）。两者都要求当前 base_url 指向本代理才动文件（zcode 为条目级
    守卫，见 _restore_zcode_providers）。崩溃修复路径不走这里（reconcile 仍用
    restore_by_snapshot）。
    """
    snaps = snapshot.load()
    msgs: list[str] = []
    for name in cfg.routing.harnesses:
        msgs.extend(_restore_harness_on_stop(cfg, snaps, name, home))
    return msgs
