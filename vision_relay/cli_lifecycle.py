"""生命周期命令：start / stop / status / logs 与 pid 管理、分离启动、意图状态。

负责服务进程的启动（含 onboarding/接线/对账/起服）、停止（含接线回滚）、
pid 文件读写与跨平台进程存活判定、分离 spawn、路由意图记录。被测试
monkeypatch 的 _terminate/_pid_running/_spawn_detached 经 facade（cli.*）
在调用时解析；reconcile_reconcile 别名定义在 facade，本模块经 cli.* 调用。
"""

from __future__ import annotations

import os
import signal
import sys

from . import pid_util
from .env_util import config_dir

PID_FILE = "proxy.pid"
LOG_FILE = "proxy.log"


def _pid_path() -> str:
    return os.path.join(config_dir(), PID_FILE)


def _log_path() -> str:
    return os.path.join(config_dir(), "logs", LOG_FILE)


def _write_pid() -> None:
    from . import cli

    pid_util.write_pid_file(cli._pid_path())


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
    Unix 信号 0 且 EPERM=活着）。与 pid_util.pid_alive 同一实现，
    保留此名供既有测试 monkeypatch。"""
    return pid_util.pid_alive(pid)


def _pid_matches_ours(pid: int, token: int | None) -> bool:
    """pid 存活 且（文件带 token 时）进程身份指纹匹配，才认定是我们的进程。

    token=None（老格式 pid 文件/取不到指纹）退回仅存活检查——迁移期兼容，
    不改变既有行为；token 对不上（残留 pid 撞上 PID 复用）则视为非我进程。"""
    from . import cli

    if not cli._pid_running(pid):
        return False
    if token is None:
        return True
    actual = pid_util.process_token(pid)
    return actual is not None and actual == token


def cmd_start_intent(on: bool) -> None:
    """记录用户路由意图（崩溃后自动修复按此推导，spec §5）。"""
    from .reconcile import set_routing_on

    set_routing_on(on)


def _spawn_detached(argv: list[str]) -> int:
    """分离 spawn 完整命令（argv[0]=可执行文件）。返回 0 成功 / 1 失败。

    分离进程无控制台：一律注入 VISION_RELAY_RESTART=1，让子进程 cmd_start 跳过
    交互 onboarding（env 用拷贝注入，不污染当前进程环境）。stdio 重定向 DEVNULL——
    守护进程不继承调用方管道，否则 `subprocess.run(capture_output=True)` 因子进程常驻
    永远等不到 EOF 而挂起（e2e G2/G3/G4/G8 的 start --detach 曾因此 60s 超时）。"""
    import subprocess

    kwargs: dict = {"env": {**os.environ, "VISION_RELAY_RESTART": "1"}}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    kwargs.setdefault("stdout", subprocess.DEVNULL)
    kwargs.setdefault("stderr", subprocess.DEVNULL)
    try:
        subprocess.Popen(argv, **kwargs)
        return 0
    except OSError as exc:
        print(f"cannot spawn {argv}: {exc}", file=sys.stderr)
        return 1


def cmd_start_detach(cfg) -> int:
    """分离进程启动：父进程立即返回，子进程跑普通 start（写 pid/意图）。"""
    from . import cli

    rc = cli._spawn_detached(pid_util.core_argv(["start"]))
    print("started (detached)" if rc == 0 else "detach failed")
    return rc


def cmd_start(cfg) -> int:
    # L1: pid 文件存在但进程已死（硬崩溃残留）-> 清掉继续启动（与 cmd_stop 对称），而非拒绝。
    # 决策⑤：pid 文件带进程身份指纹——文件里的 pid 活着但指纹对不上（PID 复用）同样清掉。
    from . import cli

    pid, token = pid_util.read_pid_file(cli._pid_path())
    if pid != -1:
        if _pid_matches_ours(pid, token):
            print(f"already running (pid {pid})")
            return 1
        try:
            os.unlink(cli._pid_path())
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
        if "qwen-code" in cfg.routing.harnesses:
            print("  [wire] qwen-code: 接线在会话启动时加载，已开的 qwen 会话需重启才走本代理")
    _write_pid()
    cmd_start_intent(True)
    if cfg.routing.auto_wire:
        # 对账收敛（spec §4 所有触发源走同一套逻辑）。必须排在 _write_pid 之后：
        # 服务存活信号=端口或 pid 文件，若在写 pid 前对账会被判为"服务已死"，
        # 触发按快照还原（wiring_restore_by_snapshot），反而撤销刚完成的接线。
        try:
            report = cli.reconcile_reconcile(cfg, trigger="start")
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
        os.unlink(cli._pid_path())
    return 0


def cmd_stop() -> int:
    from . import cli

    pid, token = pid_util.read_pid_file(cli._pid_path())
    if pid == -1 or not _pid_matches_ours(pid, token):
        try:
            os.unlink(cli._pid_path())
        except OSError:
            pass
        print("not running")
        return 1
    if not cli._terminate(pid):
        print(f"cannot stop {pid}")
        return 1
    try:
        os.unlink(cli._pid_path())
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


def cmd_status() -> int:
    from . import cli

    pid, token = pid_util.read_pid_file(cli._pid_path())
    if pid != -1 and _pid_matches_ours(pid, token):
        print(f"running (pid {pid})")
        return 0
    print("not running")
    return 1


def cmd_logs() -> int:
    from . import cli

    try:
        with open(cli._log_path(), encoding="utf-8") as f:
            sys.stdout.write("".join(f.readlines()[-50:]))
        return 0
    except FileNotFoundError:
        print("no log yet")
        return 1
