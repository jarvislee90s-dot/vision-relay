"""Lifecycle CLI 的 facade：参数解析分发 + main 入口，重导出子命令公共面。

spec §8.3。重构后 cli.py 只保留"组装层"职责：main() 子命令分发、_safe_stdio、
reconcile_reconcile 依赖注入别名，以及重导出 parse_args/_JSON_MAP/各 cmd_* 与
pid/进程助手（测试经 cli.* 引用与 monkeypatch）。实现按职责拆分到：
cli_args（解析+分发表）、cli_lifecycle（start/stop/status/logs/pid）、
cli_commands（其余交互命令）。
"""

from __future__ import annotations

import json
import sys

from . import verbs
from .cli_args import _JSON_MAP, parse_args
from .cli_commands import (
    _provider_for_group,
    cmd_check,
    cmd_diagnose,
    cmd_events,
    cmd_models,
    cmd_models_scan,
    cmd_probe,
    cmd_refresh,
    cmd_test_image,
    cmd_tools,
    cmd_visionlog,
)
from .cli_lifecycle import (
    LOG_FILE,
    PID_FILE,
    _log_path,
    _pid_matches_ours,
    _pid_path,
    _pid_running,
    _spawn_detached,
    _terminate,
    _write_pid,
    cmd_logs,
    cmd_start,
    cmd_start_detach,
    cmd_start_intent,
    cmd_status,
    cmd_stop,
)
from .reconcile import reconcile as reconcile_reconcile

__all__ = [
    "main",
    "parse_args",
    "_JSON_MAP",
    "reconcile_reconcile",
    # lifecycle
    "PID_FILE",
    "LOG_FILE",
    "_pid_path",
    "_log_path",
    "_write_pid",
    "_terminate",
    "_pid_running",
    "_pid_matches_ours",
    "_spawn_detached",
    "cmd_start_intent",
    "cmd_start_detach",
    "cmd_start",
    "cmd_stop",
    "cmd_status",
    "cmd_logs",
    # commands
    "cmd_refresh",
    "cmd_diagnose",
    "cmd_tools",
    "cmd_probe",
    "_provider_for_group",
    "cmd_events",
    "cmd_visionlog",
    "cmd_test_image",
    "cmd_models",
    "cmd_models_scan",
    "cmd_check",
]


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
        if args.command == "probe":  # --json 特判补参；--all-untested 走批量探测
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
