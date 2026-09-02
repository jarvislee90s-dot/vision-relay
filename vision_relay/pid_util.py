"""PID file with process-identity token (Windows PID-reuse hardening, 2026-08-23 决策⑤).

Windows PID 池小、复用积极：残留 pid 文件撞上复用进程会让 status 误报"服务在跑"、
让 stop 误杀无辜进程。pid 文件因此从"只存 pid"升级为 JSON {pid, token}：
token 是进程创建时间指纹（Windows=CreationTime FILETIME ticks；POSIX=/proc/<pid>/stat
的 starttime jiffies），同一次启动内稳定、不同进程必然不同。老格式纯数字文件读作
(pid, None)：token 缺失时退回仅存活检查（迁移期兼容，不改变既有行为）。
"""

from __future__ import annotations

import ctypes
import json
import os
import sys

# Windows kernel32 绑定（延迟初始化；仅 os.name=="nt" 时触碰）。ctypes.windll 默认把
# 参数按 c_int 转换，64 位进程里 byref 指针被截断成 32 位——GetProcessTimes 写越界
# 地址直接访问违例（Python 3.14 / 64 位 Windows 实测崩溃）。这里显式声明 argtypes/
# restype，指针按 c_void_p 传、句柄按 HANDLE 收，跨 32/64 位都稳。
_WIN: dict | None = None


def _win() -> dict:
    """延迟绑定 kernel32 需要的函数与结构（非 Windows 平台永不调用）。"""
    global _WIN
    if _WIN is not None:
        return _WIN
    import ctypes.wintypes as wt

    class _FT(ctypes.Structure):  # FILETIME
        _fields_ = [("lo", ctypes.c_ulong), ("hi", ctypes.c_ulong)]

    class _ST(ctypes.Structure):  # 4 个 FILETIME（creation/exit/kernel/user）
        _fields_ = [("creation", _FT), ("exit", _FT), ("kernel", _FT), ("user", _FT)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _decl(name: str, argtypes: list, restype):
        fn = getattr(k32, name)
        fn.argtypes = argtypes
        fn.restype = restype
        return fn

    _WIN = {
        "FT": _FT,
        "ST": _ST,
        # GetProcessTimes 收 4 个独立 FILETIME 指针：调用处按字段逐个 byref。
        "GetProcessTimes": _decl(
            "GetProcessTimes",
            [wt.HANDLE, ctypes.POINTER(_FT), ctypes.POINTER(_FT), ctypes.POINTER(_FT), ctypes.POINTER(_FT)],
            wt.BOOL,
        ),
        "OpenProcess": _decl("OpenProcess", [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
        "GetExitCodeProcess": _decl("GetExitCodeProcess", [wt.HANDLE, ctypes.POINTER(wt.DWORD)], wt.BOOL),
        "CloseHandle": _decl("CloseHandle", [wt.HANDLE], wt.BOOL),
    }
    return _WIN


def default_pid_path() -> str:
    from .env_util import config_dir

    return os.path.join(config_dir(), "proxy.pid")


def core_argv(args: list[str]) -> list[str]:
    """重拉核心进程的完整 argv（args 为子命令及其参数，如 ["start"]）。

    源码环境 sys.executable 是 python 解释器，走 `-m vision_relay`；PyInstaller
    冻结态 sys.executable 即核心 exe 自身，`-m` 会被 argparse 拒绝（exit 2），
    必须直接传子命令——v1.0.1 打包版 start --detach 孙进程静默死亡、服务永不
    启动的根因（cmd_start_detach 与 reconcile._restart_service 共用此处）。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "vision_relay", *args]


def process_token(pid: int) -> int | None:
    """进程身份指纹；拿不到（进程不存在/权限/平台不支持）返回 None。"""
    if os.name == "nt":
        api = _win()
        handle = api["OpenProcess"](0x1000, False, pid)
        if not handle:
            return None
        try:
            st = api["ST"]()
            if not api["GetProcessTimes"](
                handle,
                ctypes.byref(st.creation),
                ctypes.byref(st.exit),
                ctypes.byref(st.kernel),
                ctypes.byref(st.user),
            ):
                return None
            return st.creation.hi << 32 | st.creation.lo
        finally:
            api["CloseHandle"](handle)
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            # comm 字段可含空格/括号：从最后一个 ')' 之后取字段；starttime 是整体第 22 域
            rest = f.read().rsplit(")", 1)[1].split()
            return int(rest[19])
    except (OSError, ValueError, IndexError):
        return None


def pid_alive(pid: int) -> bool:
    """跨平台存活判定（沿用 reconcile._pid_alive 的健壮实现：Windows GetExitCodeProcess，
    Unix 信号 0 且 EPERM=活着）。"""
    if os.name == "nt":
        api = _win()
        handle = api["OpenProcess"](0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if api["GetExitCodeProcess"](handle, ctypes.byref(exit_code)):
                return exit_code.value == 259  # STILL_ACTIVE
            return False
        finally:
            api["CloseHandle"](handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True  # EPERM＝进程存在但属他人
    except OSError:
        return False


def pid_is_ours(pid: int, token: int | None) -> bool:
    """pid 活着 且（有 token 时指纹匹配）。token=None（老文件/取不到）退回仅存活检查。"""
    if not pid_alive(pid):
        return False
    if token is None:
        return True
    actual = process_token(pid)
    return actual is not None and actual == token


def write_pid_file(path: str | None = None) -> None:
    path = path or default_pid_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pid = os.getpid()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pid": pid, "token": process_token(pid)}, f)


def read_pid_file(path: str | None = None) -> tuple[int, int | None]:
    """返回 (pid, token)。文件缺失/损坏 → (-1, None)；老格式纯数字 → (pid, None)。"""
    path = path or default_pid_path()
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError:
        return -1, None
    if raw.startswith("{"):
        try:
            d = json.loads(raw)
            return int(d["pid"]), d.get("token")
        except (ValueError, KeyError, TypeError):
            return -1, None
    try:
        return int(raw), None
    except ValueError:
        return -1, None
