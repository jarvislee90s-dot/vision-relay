"""zcode 进程检测与重启（spec §7.2/§10）：平台 best-effort，绝不抛出、检测不到=空。

无 psutil 依赖（工程约束），Windows 单次 PowerShell 批量取三元组、unix 用 ps。
全部子进程短超时；结果短 TTL 缓存（status 每 5s 轮询，避免每次都枚举进程）。"""

from __future__ import annotations

import json
import os
import subprocess
import time

_TTL = 5.0
_cache: dict = {"ts": 0.0, "procs": []}


def _run(cmd: list[str], timeout: float = 3.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


_PSWIN_DETAILS = (
    "Get-Process -Name zcode* -ErrorAction SilentlyContinue | "
    "ForEach-Object { $exe=''; $ts=0; "
    "try { $exe=$_.Path; $ts=[int64](($_.StartTime.ToUniversalTime()-[datetime]::new(1970,1,1)).TotalSeconds) } catch {} ; "
    "[pscustomobject]@{ id=$_.Id; ts=$ts; exe=$exe } } | ConvertTo-Json -Compress"
)


def _win_details() -> list[dict]:
    """单次 PowerShell 批量取 {id, ts(epoch 秒), exe}（评审⑦：逐 pid 串行子进程会让
    status 每 5s 的轮询阻塞数秒）。StartTime 不可读（权限/已退出）→ ts=0（视为待重启）。"""
    out = _run(["powershell", "-NoProfile", "-c", _PSWIN_DETAILS], timeout=5.0)
    try:
        d = json.loads(out) if out.strip() else []
    except ValueError:
        return []
    if isinstance(d, dict):
        d = [d]
    return d if isinstance(d, list) else []


def find_zcode_processes(force: bool = False) -> list[dict]:
    """[{pid, start_ts, exe}]——best-effort。误报防护：跳过本代理自身（zcode-relay.exe）。"""
    now = time.time()
    if not force and now - _cache["ts"] < _TTL:
        return _cache["procs"]
    procs: list[dict] = []
    if os.name == "nt":
        for e in _win_details():
            try:
                pid = int(e.get("id"))
            except (TypeError, ValueError):
                continue
            exe = str(e.get("exe") or "")
            if os.path.basename(exe.replace("/", "\\")).lower() == "zcode-relay.exe":
                continue
            try:
                start_ts = float(e.get("ts"))
            except (TypeError, ValueError):
                start_ts = 0.0
            procs.append({"pid": pid, "start_ts": start_ts, "exe": exe})
    else:
        out = _run(["ps", "-eo", "pid,comm"])
        for line in out.splitlines():
            low = line.lower()
            if "zcode" in low and "vision-relay" not in low and "zcode-relay" not in low and "zcode_proc" not in low:
                fields = line.split()
                if fields and fields[0].isdigit():
                    procs.append({"pid": int(fields[0]), "start_ts": 0.0, "exe": ""})
    _cache.update(ts=now, procs=procs)
    return procs


def zcode_needs_restart(rewrite_ts: float) -> bool:
    """运行中的 zcode 进程启动时间早于本代理最后一次改写 → 需重启才能吃到新配置。"""
    if rewrite_ts <= 0:
        return False
    procs = find_zcode_processes()
    if not procs:
        return False
    return all(p["start_ts"] < rewrite_ts for p in procs)  # start_ts 未知(0.0)视为待重启


def restart_zcode() -> bool:
    """结束 zcode 全部进程并按探测到的 exe 分离重启（best-effort；无进程/无 exe 返回 False）。"""
    procs = find_zcode_processes(force=True)
    if not procs:
        return False
    exe = next((p["exe"] for p in procs if p.get("exe")), "")
    for p in procs:
        if os.name == "nt":
            _run(["taskkill", "/PID", str(p["pid"]), "/T", "/F"])
        else:
            _run(["kill", str(p["pid"])])
    if not exe or not os.path.exists(exe):
        return False
    kwargs: dict = {"creationflags": 0x00000008} if os.name == "nt" else {"start_new_session": True}
    try:
        subprocess.Popen(
            [exe], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs
        )
        return True
    except OSError:
        return False
