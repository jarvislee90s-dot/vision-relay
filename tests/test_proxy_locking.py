"""config_lock: 自方多进程/多线程写者互斥（spec §4 多写者对策 2）。"""

import os
import threading

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
            acquired.append(True)
            got.__exit__(None, None, None)

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
