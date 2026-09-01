"""交互命令实现：refresh / diagnose / tools / probe / events / visionlog /
test-image / models / models-scan / check。

这些是 start/stop 之外的人机交互子命令：对账刷新、诊断、工具探测、模态探针、
事件与识图日志 tail、VLM 试识图、模型能力编辑/扫描、接线自检。reconcile_reconcile
别名定义在 facade，本模块经 cli.* 调用（测试 monkeypatch cli.reconcile_reconcile）。
"""

from __future__ import annotations

import json
import sys

from . import verbs


def cmd_refresh(cfg) -> int:
    """手动对账 = GUI「刷新」按钮的后端（spec §5 唯一写路径）。"""
    from . import cli

    try:
        report = cli.reconcile_reconcile(cfg, trigger="manual")
    except TimeoutError as exc:
        print(f"刷新失败：配置写入忙，请稍后重试（{exc}）")
        return 1
    for a in report["actions"]:
        if a.get("type") == "auto_fix" and a.get("ok") is False:
            print(f"  ⚠ 自动修复失败: {a}")  # L2: 失败必须显式可见，不能混进普通日志
        else:
            print(f"  [reconcile] {a}")
    for n in report["needs_you"]:
        print(f"  [需要你] {n}")
    return 0


def cmd_diagnose(cfg) -> int:
    """诊断报告（自动运行 + 自动修复 + needs_you；spec §5 修复流程）。"""
    from . import cli

    try:
        report = cli.reconcile_reconcile(cfg, trigger="diagnose")
    except TimeoutError as exc:
        print(f"诊断失败：配置写入忙，请稍后重试（{exc}）")
        return 1
    obs = report["observed"]
    print(f"服务: {'运行中' if obs['service_alive'] else '未运行'} · 路由意图: {'开' if obs['routing_on'] else '关'}")
    for t in obs["tools"]:
        prov = f" · 供应商 {t['active_provider']}" if t["active_provider"] else ""
        print(f"  工具 {t['name']} :{t['port']} {'在线' if t['online'] else '离线'}{prov}")
    for name, row in obs["harnesses"].items():
        print(f"  {name}: base_url={row['base_url'] or '(无)'} [{row['ownership']}]")
    for a in report["actions"]:
        if a.get("type") == "auto_fix" and a.get("ok") is False:
            print(f"  ⚠ 自动修复失败: {a}")  # L2: 同 cmd_refresh
        else:
            print(f"  [已自动处理] {a}")
    for n in report["needs_you"]:
        print(f"  ⚠ 需要你: {n}")
    return 0 if not report["needs_you"] else 1


def cmd_tools(cfg) -> int:
    from .tools import probe_tools

    for s in probe_tools():
        # M3：无 baseURL 的供应商不隐身——名字照常显示，空址占位「未接线」
        prov = f" · 供应商 {s.active_provider} ({s.provider_base_url or '未接线'})" if s.active_provider else ""
        print(f"{s.name}: :{s.port} {'在线' if s.online else '离线'}{prov}")
    return 0


def cmd_probe(args, cfg) -> int:
    from .annotate import run_probe
    from .onboarding import scan_model_groups
    from .reconcile import observe

    obs = observe(cfg)
    if args.all_untested or not args.model:
        # 对所有已知 (provider, model) 且无探针缓存的组合探测
        count = 0
        tool_by_name = {t["name"]: t for t in obs["tools"]}
        for g in scan_model_groups(cfg):
            provider = _provider_for_group(g.group, tool_by_name)
            for ent in g.entries:
                if args.model and ent.model != args.model:
                    continue
                cached = cfg.probe_results.get(provider or "?", {}).get(ent.model)
                if cached and not args.all_untested:
                    continue
                base, key, proto = verbs.probe_target_for(cfg, g.group, provider, tool_by_name)
                result = run_probe(cfg, g.group, provider or "?", ent.model, base, key, proto)
                print(f"  {ent.model}: {result}")
                count += 1
        print(f"probed {count} model(s)")
        return 0
    base, key, proto = verbs.probe_target_for(
        cfg, args.harness or "", args.provider or "?", {t["name"]: t for t in obs["tools"]}
    )
    result = run_probe(cfg, args.harness or "?", args.provider or "?", args.model, base, key, proto)
    print(f"{args.model}: {result}")
    return 0 if result else 1


def _provider_for_group(group: str, tool_by_name: dict) -> str | None:
    """harness -> 当前供应商名:工具档案矩阵 is_current 优先(磁盘真相,与工具在不在线无关);
    退在线工具激活供应商;直连场景名未知回 None,调用方用 '?' 占位。"""
    from . import model_sources, tools
    from .config import load_config

    try:
        hit = model_sources.current_provider(load_config(), group)
        if hit != "?":
            return hit
    except Exception:  # noqa: BLE001 - 档案读取失败走旧链路
        pass
    for name, d in tools.TOOL_DOSSIERS.items():
        if (
            group in d.harnesses
            and tool_by_name.get(name, {}).get("online")
            and tool_by_name[name].get("active_provider")
        ):
            return tool_by_name[name]["active_provider"]
    return None


def cmd_events(cfg) -> int:
    import time as _t

    from .reconcile import tail_events

    for row in tail_events(50):
        stamp = _t.strftime("%m-%d %H:%M:%S", _t.localtime(row.get("ts", 0)))
        # append_event 把 detail 扁平展开进事件行（没有 'detail' 键）：payload =
        # ts/type/harness 之外的剩余字段（reclaim 的 from/to、absorb 的新地址、
        # auto_fix 的 fix/ok、auto_annotate 的 model/result），行尾以 JSON 呈现。
        payload = {k: v for k, v in row.items() if k not in ("ts", "type", "harness")}
        detail = " " + json.dumps(payload, ensure_ascii=False) if payload else ""
        print(f"{stamp} [{row.get('type')}] {row.get('harness') or '-'}{detail}")
    return 0


def cmd_visionlog(args, cfg) -> int:
    from .visionlog import query

    for row in query(harness=getattr(args, "harness", None), limit=50):
        print(
            f"{row.get('ts')} {row.get('harness')} t{row.get('tier')} cache={row.get('cache_hit')} {str(row.get('injected'))[:60]}"
        )
    return 0


def cmd_test_image(args, cfg) -> int:
    import base64
    import mimetypes

    from .ir import ImageBlock
    from .vlm import VLMClient

    try:
        with open(args.path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
    except OSError as exc:
        print(f"cannot read {args.path}: {exc}")
        return 1
    media = mimetypes.guess_type(args.path)[0] or "image/png"
    client = VLMClient(cfg.vlm)
    img = ImageBlock(base64=data, media_type=media)
    try:
        t1 = client.describe(img, tier=1)
        t2 = client.describe(img, question=args.question, tier=2) if args.question else t1
    except Exception as exc:
        # VLMError 带 reason（TIMEOUT/HTTP/AUTH/PARSE/TRANSPORT）；mimo 瞬时 5xx/超时可能返回，
        # 只打 str(exc) 在空 body 时是无意义的 "VLM error:"，带上 reason 才有可能诊断。
        reason = getattr(exc, "reason", type(exc).__name__)
        print(f"VLM error [{reason}]: {exc}")
        return 1
    print("Tier1 (全面):\n" + t1 + "\n\nTier2 (聚焦):\n" + t2)
    return 0


def cmd_models(cfg) -> int:
    from .onboarding import edit_all

    if edit_all(cfg):
        print("模型能力配置已更新。")
        return 0
    print("已取消，未改动。", file=sys.stderr)
    return 1


def cmd_models_scan(cfg) -> int:
    from .onboarding import models_scan_report

    models_scan_report(cfg)
    return 0


def cmd_check(cfg) -> int:
    import socket

    problems = []
    for port in (cfg.bind_port,):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                problems.append(f"port {port} already in use")
    if not cfg.vlm.api_key and not cfg.vlm.auto_local_ollama:
        problems.append("no VLM key configured and auto_local_ollama disabled")
    if not cfg.relays:
        problems.append("no relays configured")
    # 每条 relay 的拓扑提示（一层直连 / 两层经工具）＋ via 与 base_url 端口一致性校验。
    import re

    from .config import VIA_TOOLS

    for relay in cfg.relays:
        m = re.search(r":(\d+)", relay.base_url)
        port = int(m.group(1)) if m else None
        via = relay.via
        if via is None and port in (15721, 57321):
            via = "cc-switch" if port == 15721 else "codex-plus"
        topo = f"两层(经工具 via={via})" if via else "一层(直连)"
        print(f"  {relay.name}: {relay.protocol} → {topo}  ({relay.base_url})")
        if via and port is not None and VIA_TOOLS[via] != port:
            problems.append(f"relay {relay.name!r}: via={via} 期望端口 {VIA_TOOLS[via]}，实际 base_url 端口 {port}")
    # 四处 harness 第一跳接线状态
    try:
        from .wiring import wiring_report

        for row in wiring_report(cfg):
            state = "OK" if row["wired"] else ("偏离(" + (row["base_url"] or "无") + ")")
            hint = "" if row["wired"] else " → 请重跑 vision-relay start 重新接线"
            print(f"  {row['harness']}: base_url={row['base_url'] or '(无)'} [{state}]{hint}")
            if not row["wired"] and row["base_url"] and row.get("has_backup"):
                problems.append(f"harness {row['harness']} base_url 未指向本代理（工具可能切换了配置，请重跑 start）")
    except Exception:  # noqa: BLE001 - 接线报错是尽力而为
        pass
    if cfg.routing.auto_wire and not cfg.routing.capability_confirmed:
        print("  ⚠ model 看图能力尚未确认——首次启用需走交互引导（vision-relay start 或 models-scan）")
        problems.append("model_capabilities 未确认")
    for p in problems:
        print(f"⚠ {p}")
    if not problems:
        print("check ok")
    return 1 if problems else 0
