"""config_lock: 自方多进程/多线程写者互斥（spec §4 多写者对策 2）。"""

import os
import threading

import pytest

from vision_relay import locking


def test_lock_is_reentrant_per_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    with locking.config_lock():
        with locking.config_lock():  # 同线程可重入
            pass


def test_lock_blocks_second_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    acquired = []

    def try_lock():
        got = locking.try_config_lock()
        if got is not None:
            with got:  # 先 __enter__ 再 __exit__（3.14 惰性 CM 下裸 __exit__ 是地雷）
                acquired.append(True)

    with locking.config_lock():
        t = threading.Thread(target=try_lock)
        t.start()
        t.join(timeout=5)
    assert acquired == [], "持锁期间第二个写者必须拿不到锁"


def test_lock_released_after_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    with locking.config_lock():
        pass
    with locking.try_config_lock() as lk:
        assert lk is not None


def test_lock_file_created_in_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    with locking.config_lock():
        assert os.path.exists(str(tmp_path / "relay.lock"))


def test_timeout_raises_not_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(locking, "_os_lock", lambda fd, blocking, timeout_s=30.0: False)
    with pytest.raises(TimeoutError):
        with locking.config_lock(timeout_s=0.1):
            pass  # 若静默放行会执行到这里 → DID NOT RAISE → 红


def test_body_exception_releases_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    with pytest.raises(RuntimeError):
        with locking.config_lock():
            raise RuntimeError("boom")
    with locking.try_config_lock() as lk:
        assert lk is not None
