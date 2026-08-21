"""Cross-process advisory lock for vision-relay's own writers (spec §4).

同一进程内同线程可重入（threading.local + 计数）；跨进程用 OS 文件锁
（Windows msvcrt.locking / Unix fcntl.flock）。外部工具不受此锁约束——
它们靠对账收敛（spec §5），本锁只排队"我们自己人"。
"""

from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path

_reentrant = threading.local()


def _lock_path() -> Path:
    from .env_util import config_dir

    return Path(config_dir()) / "relay.lock"


@contextlib.contextmanager
def config_lock(timeout_s: float | None = 30.0):
    """阻塞获取（默认 30s 超时，到期抛 TimeoutError）；同线程重入直接通过。"""
    token = getattr(_reentrant, "depth", 0)
    if token > 0:
        _reentrant.depth = token + 1
        try:
            yield
        finally:
            _reentrant.depth -= 1
        return
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if not _os_lock(fd, blocking=True, timeout_s=timeout_s):
            raise TimeoutError(f"config_lock: timeout after {timeout_s}s ({path})")
        _reentrant.depth = 1
        yield
    finally:
        # 仅在真正拿到过锁（depth>0）时才解锁；TimeoutError 路径锁本来就没拿到
        if getattr(_reentrant, "depth", 0) > 0:
            _reentrant.depth = 0
            _os_unlock(fd)
        os.close(fd)


def try_config_lock():
    """非阻塞尝试；拿不到返回 None（不抛异常，方便轮询方使用）。

    故意不做同线程重入短路：总是真实尝试 OS 级锁——config_lock 持锁期间，同线程
    经第二个 fd 的尝试同样会被字节锁挡住（Windows msvcrt / Unix flock 均如此），
    因此可用于断言"锁确被持有"。同线程重入放行语义只在 config_lock() 里保留。
    """
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    if not _os_lock(fd, blocking=False):
        os.close(fd)
        return None

    @contextlib.contextmanager
    def _held():
        try:
            yield True  # 持锁 token：None 保留给"没拿到锁"
        finally:
            _os_unlock(fd)
            os.close(fd)

    return _held()


def _os_lock(fd: int, blocking: bool, timeout_s: float | None = 30.0) -> bool:
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK if not blocking else msvcrt.LK_LOCK, 1)
            return True
        except OSError:
            if not blocking:
                return False
            # msvcrt.LK_LOCK 自身重试 10 次；再不行人工小睡重试到 deadline
            import time

            deadline = time.time() + (30.0 if timeout_s is None else timeout_s)
            while time.time() < deadline:
                time.sleep(0.2)
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    return True
                except OSError:
                    continue
            return False
    import fcntl
    import time

    if not blocking:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    deadline = time.time() + (30.0 if timeout_s is None else timeout_s)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.time() >= deadline:
                return False
            time.sleep(0.2)


def _os_unlock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
