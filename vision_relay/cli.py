"""Lifecycle CLI: start/stop/status/logs/test-image/check (spec §8.3)."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys

from . import __version__, pid_util, verbs
from .env_util import config_dir
from .reconcile import reconcile as reconcile_reconcile

PID_FILE = "proxy.pid"
LOG_FILE = "proxy.log"

# --json 动词分发表（spec §4 通信契约：envelope + contract_version，GUI 只消费这个）。
_JSON_MAP = {
    "status": verbs.status,
    "refresh": verbs.refresh,
    "diagnose": verbs.diagnose,
    "models-scan": verbs.models_scan,
    "models-set": verbs.models_set,
    "config": verbs.config_get,
    "tools": verbs.tools,
    "events": verbs.events,
    "visionlog": verbs.visionlog,
    "vlm-set": verbs.vlm_set,  # Task 2: stdin JSON 写全局/分组/自定义提示词
    "vlm-test": verbs.vlm_test,  # Task 2: 与生产同一调用路径的连通测试（stdin JSON）
    "vlm-secret": verbs.vlm_secret,  # 设置页「显示」按钮按需回显明文 VLM key（config 仍打码）
    "settings-set": verbs.settings_set,  # Task 3: stdin 白名单设置（unknown_default / vision_log）
    "relay-set": verbs.relay_set,  # Task 3: 停用压制 / 补 key
    "probe": verbs.probe_one,  # Task 3: --json 探针（main 特判补 harness/provider/model）
    "models-fetch": verbs.models_fetch,  # Task 3: 拉上游模型 ID 清单（spec §5）
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vision-relay")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    # 公共 parent：管理动词统一可挂 --json（子命令后置 flag 也可用）。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output (contract_version pinned)")
    st = sub.add_parser("start")
    st.add_argument("--detach", action="store_true", help="分离进程启动（GUI/自动重试用）")
    sub.add_parser("stop", parents=[common])
    sub.add_parser("status", parents=[common])
    sub.add_parser("logs", parents=[common])
    ti = sub.add_parser("test-image")
    ti.add_argument("path")
    ti.add_argument("--question", default=None)
    sub.add_parser("check", parents=[common])
    sub.add_parser("models-scan", parents=[common])  # 非交互打印模型能力草稿
    sub.add_parser("models-set", parents=[common])  # Task 1: stdin 三元组写入（source=user；null=清除）
    sub.add_parser("models", parents=[common])  # 显式交互入口：重新确认/编辑 model_capabilities
    sub.add_parser("refresh", parents=[common])  # M1: 手动对账（= 刷新按钮后端）
    sub.add_parser("diagnose", parents=[common])  # M1: 观测 + 自动修复 + 报告
    sub.add_parser("tools", parents=[common])  # M1: 工具档案探测
    pr = sub.add_parser("probe", parents=[common])  # M1: 模态探针（--json 走 probe_one）
    pr.add_argument("--harness")
    pr.add_argument("--provider")
    pr.add_argument("--model")
    pr.add_argument("--all-untested", action="store_true")
    ev = sub.add_parser("events", parents=[common])  # M1: 事件日志 tail（--limit 0 = 全量导出）
    ev.add_argument("--limit", type=int, default=50)
    vl = sub.add_parser("visionlog", parents=[common])  # M1: 识图记录查询
    vl.add_argument("--harness")
    sub.add_parser("config", parents=[common])  # Task 14: --json 配置读取（打码）
    sub.add_parser("vlm-set", parents=[common])  # Task 2: stdin JSON 写 VLM 全局/分组/自定义提示词
    sub.add_parser("vlm-test", parents=[common])  # Task 2: stdin JSON VLM 连通测试（共享生产路径）
    sub.add_parser("vlm-secret", parents=[common])  # 设置页「显示」按钮按需回显明文 VLM key
    sub.add_parser("settings-set", parents=[common])  # Task 3: stdin 白名单设置（unknown_default / vision_log）
    sub.add_parser("relay-set", parents=[common])  # Task 3: 停用压制 / 补 key
    sub.add_parser("models-fetch", parents=[common])  # Task 3: 拉上游模型 ID 清单（spec §5）
    return parser.parse_args(argv)


def _pid_path() -> str:
    return os.path.join(config_dir(), PID_FILE)


def _log_path() -> str:
    return os.path.join(config_dir(), "logs", LOG_FILE)


def _write_pid() -> None:
    pid_util.write_pid_file(_pid_path())


def cmd_start(cfg) -> int:
    # L1: pid 文件存在但进程已死（硬崩溃残留）-> 清掉继续启动（与 cmd_stop 对称），而非拒绝。
    # 决策⑤：pid 文件带进程身份指纹——文件里的 pid 活着但指纹对不上（PID 复用）同样清掉。
    pid, token = pid_util.read_pid_file(_pid_path())
    if pid != -1:
        if _pid_matches_ours(pid, token):
            print(f"already running (pid {pid})")
            return 1
        try:
            os.unlink(_pid_path())
        except OSError:
            pass
    if cfg.routing.auto_wire:
        # L3: 分离重启（reconcile._restart_service 注入 VISION_RELAY_RESTART=1）的子进程
        # 无控制台，交互 onboarding 会挂死——跳过向导（首次确认仍可手动 start/models 完成）。
        if os.environ.get("VISION_RELAY_RESTART"):
            print("restart: 跳过首次向导")
        else:
            # 首次启用：显式交互确认各模型看图能力(默认纯文本最安全)；未确认则不接线、不起服。
            from .onboarding import run_onboarding

            if not run_onboarding(cfg):
                print("未完成模型看图能力确认，未启动代理。", file=sys.stderr)
                print(
                    "请用交互终端重跑 vision-relay start，或 vision-relay models-scan 复核。",
                    file=sys.stderr,
                )
                return 1
        from .wiring import relays_activate, wiring_backup_and_rewrite

        for msg in relays_activate(cfg):
            print(f"  [relay] {msg}")
        for msg in wiring_backup_and_rewrite(cfg):
            print(f"  [wire] {msg}")
    _write_pid()
    cmd_start_intent(True)
    if cfg.routing.auto_wire:
        # 对账收敛（spec §4 所有触发源走同一套逻辑）。必须排在 _write_pid 之后：
        # 服务存活信号=端口或 pid 文件，若在写 pid 前对账会被判为"服务已死"，
        # 触发按快照还原（wiring_restore_by_snapshot），反而撤销刚完成的接线。
        try:
            report = reconcile_reconcile(cfg, trigger="start")
            for a in report["actions"]:
                print(f"  [reconcile] {a}")
            for n in report["needs_you"]:  # m-2: 需要用户处理的事项（如 direct-* 缺 key）不能静默丢弃
                print(f"  [需要你] {n}")
        except TimeoutError as exc:
            # 拿不到写锁不该阻止起服：降级提示，后续 refresh/自动监听再收敛。
            print(f"  [reconcile] 配置写入忙，对账稍后重试: {exc}")
    from .server import run_server

    server = run_server(cfg)
    print(f"vision-relay listening on {cfg.bind_host}:{cfg.bind_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        os.unlink(_pid_path())
    return 0


def cmd_stop() -> int:
    pid, token = pid_util.read_pid_file(_pid_path())
    if pid == -1 or not _pid_matches_ours(pid, token):
        try:
            os.unlink(_pid_path())
        except OSError:
            pass
        print("not running")
        return 1
    if not _terminate(pid):
        print(f"cannot stop {pid}")
        return 1
    try:
        os.unlink(_pid_path())
    except OSError:
        pass
    print(f"stopped {pid}")
    # 接线回滚（配置损坏则跳过，保证 stop 不锁死）
    try:
        from .config import load_config
        from .wiring import relays_restore, wiring_restore_on_stop

        c = load_config()
        if c.routing.auto_wire:
            for msg in wiring_restore_on_stop(c):
                print(f"  [restore] {msg}")
            for msg in relays_restore(c):
                print(f"  [restore] {msg}")
    except Exception:  # noqa: BLE001 - 回滚尽力而为
        pass
    cmd_start_intent(False)  # stop 记录关闭意图（崩溃后自动修复按此推导，spec §5）
    return 0


# ---- M1: 意图状态 / 分离启动 / 对账动词（GUI 与 CLI 共用同一套入口） ----


def cmd_start_intent(on: bool) -> None:
    """记录用户路由意图（崩溃后自动修复按此推导，spec §5）。"""
    from .reconcile import set_routing_on

    set_routing_on(on)


def _spawn_detached(argv: list[str]) -> int:
    """分离 spawn 完整命令（argv[0]=可执行文件）。返回 0 成功 / 1 失败。

    分离进程无控制台：一律注入 VISION_RELAY_RESTART=1，让子进程 cmd_start 跳过
    交互 onboarding（env 用拷贝注入，不污染当前进程环境）。"""
    import subprocess

    kwargs: dict = {"env": {**os.environ, "VISION_RELAY_RESTART": "1"}}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(argv, **kwargs)
        return 0
    except OSError as exc:
        print(f"cannot spawn {argv}: {exc}", file=sys.stderr)
        return 1


def cmd_start_detach(cfg) -> int:
    """分离进程启动：父进程立即返回，子进程跑普通 start（写 pid/意图）。"""
    rc = _spawn_detached([sys.executable, "-m", "vision_relay", "start"])
    print("started (detached)" if rc == 0 else "detach failed")
    return rc


def cmd_refresh(cfg) -> int:
    """手动对账 = GUI「刷新」按钮的后端（spec §5 唯一写路径）。"""
    try:
        report = reconcile_reconcile(cfg, trigger="manual")
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
    try:
        report = reconcile_reconcile(cfg, trigger="diagnose")
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
        prov = f" · 供应商 {s.active_provider} ({s.provider_base_url})" if s.active_provider else ""
        print(f"{s.name}: :{s.port} {'在线' if s.online else '离线'}{prov}")
    return 0


def cmd_probe(args, cfg) -> int:
    from .annotate import run_probe
    from .reconcile import observe

    obs = observe(cfg)
    if args.all_untested or not args.model:
        # 对所有已知 (provider, model) 且无探针缓存的组合探测
        from .onboarding import scan_model_groups

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
    from .reconcile import tail_events

    for row in tail_events(50):
        import time as _t

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


def _terminate(pid: int) -> bool:
    """终止进程。Windows 上 os.kill(pid, SIGTERM) 对失效 pid 抛 WinError 87，
    用 TerminateProcess 兜底（与 _pid_running 同源的 Windows 健壮性处理）。"""
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        if os.name == "nt":
            try:
                import ctypes

                PROCESS_TERMINATE = 0x0001
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                if handle:
                    ok = ctypes.windll.kernel32.TerminateProcess(handle, 1)
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return bool(ok)
            except Exception:
                return False
        return False


def _pid_running(pid: int) -> bool:
    """薄包装：跨平台存活判定（Windows 用 OpenProcess/GetExitCodeProcess，
    Unix 信号 0 且 EPERM=活着）。决策⑤ 后与 pid_util.pid_alive 同一实现，
    保留此名供既有测试 monkeypatch。"""
    return pid_util.pid_alive(pid)


def _pid_matches_ours(pid: int, token: int | None) -> bool:
    """pid 存活 且（文件带 token 时）进程身份指纹匹配，才认定是我们的进程。

    token=None（老格式 pid 文件/取不到指纹）退回仅存活检查——迁移期兼容，
    不改变既有行为；token 对不上（残留 pid 撞上 PID 复用）则视为非我进程。"""
    if not _pid_running(pid):
        return False
    if token is None:
        return True
    actual = pid_util.process_token(pid)
    return actual is not None and actual == token


def cmd_status() -> int:
    pid, token = pid_util.read_pid_file(_pid_path())
    if pid != -1 and _pid_matches_ours(pid, token):
        print(f"running (pid {pid})")
        return 0
    print("not running")
    return 1


def cmd_logs() -> int:
    try:
        with open(_log_path(), encoding="utf-8") as f:
            sys.stdout.write("".join(f.readlines()[-50:]))
        return 0
    except FileNotFoundError:
        print("no log yet")
        return 1


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
    # 三处 harness 第一跳接线状态
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
        print(f"\u26a0 {p}")
    if not problems:
        print("check ok")
    return 1 if problems else 0


def _safe_stdio() -> None:
    """stdout/stderr 用 errors='replace'：Windows 控制台若是 GBK/cp936，mimo 描述里
    非编码字符（如 OCR 图标 ☼ U+263C）会让 print() 抛 UnicodeEncodeError 崩溃。
    这里只替换、不崩溃（保持终端原本的编码偏好）。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    _safe_stdio()
    args = parse_args(argv)
    as_json = getattr(args, "json", False) and args.command in _JSON_MAP
    # stop/status/logs 只读 PID/日志，不依赖配置——损坏的 proxy.json 不能锁死这些生命周期命令。
    # 例外：status --json 走 verbs（需要完整配置观测），必须等 cfg 加载后统一分发。
    if args.command in ("stop", "status", "logs") and not as_json:
        return {"stop": cmd_stop, "status": cmd_status, "logs": cmd_logs}[args.command]()
    from .config import ConfigError, load_config

    try:
        cfg = load_config()
    except ConfigError as exc:
        if as_json:
            # GUI 统一解析：--json 下错误也走 envelope（ok=False），rc 仍 2。
            print(json.dumps(verbs.envelope(False, {"error": str(exc)}), ensure_ascii=False))
            return 2
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    if as_json:
        if args.command == "probe":  # --json 特判补参；--all-untested 走批量探测（Task 9）
            if getattr(args, "all_untested", False):
                out = verbs.probe_all_untested(cfg)
            else:
                out = _JSON_MAP["probe"](
                    cfg,
                    harness=getattr(args, "harness", None),
                    provider=getattr(args, "provider", None),
                    model=getattr(args, "model", None),
                )
        else:
            kw = {"harness": getattr(args, "harness", None)} if args.command == "visionlog" else {}
            if args.command == "events":
                kw = {"limit": getattr(args, "limit", 50)}
            out = _JSON_MAP[args.command](cfg, **kw)
        print(json.dumps(out, ensure_ascii=False))
        return 0
    if args.command == "start" and getattr(args, "detach", False):
        return cmd_start_detach(cfg)
    if args.command == "start":
        return cmd_start(cfg)
    if args.command == "test-image":
        return cmd_test_image(args, cfg)
    if args.command == "models":
        return cmd_models(cfg)
    if args.command == "models-scan":
        return cmd_models_scan(cfg)
    if args.command == "check":
        return cmd_check(cfg)
    if args.command == "refresh":
        return cmd_refresh(cfg)
    if args.command == "diagnose":
        return cmd_diagnose(cfg)
    if args.command == "tools":
        return cmd_tools(cfg)
    if args.command == "probe":
        return cmd_probe(args, cfg)
    if args.command == "events":
        return cmd_events(cfg)
    if args.command == "visionlog":
        return cmd_visionlog(args, cfg)
    return 1
