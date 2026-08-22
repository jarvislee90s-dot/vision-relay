"""pid_util: pid 文件携带进程身份指纹，防 Windows PID 复用误报/误杀（2026-08-23 决策⑤）。"""

import os

from vision_relay import pid_util


def test_process_token_of_self_is_stable_nonzero():
    token = pid_util.process_token(os.getpid())
    assert token is not None and token > 0
    assert pid_util.process_token(os.getpid()) == token  # 同进程指纹稳定


def test_process_token_of_dead_pid_is_none():
    assert pid_util.process_token(99999999) is None


def test_write_and_read_roundtrip(tmp_path):
    path = str(tmp_path / "proxy.pid")
    pid_util.write_pid_file(path)
    pid, token = pid_util.read_pid_file(path)
    assert pid == os.getpid()
    assert token == pid_util.process_token(os.getpid())


def test_read_legacy_bare_int_file(tmp_path):
    """老格式纯数字 pid 文件 → (pid, None)，退回仅存活检查（迁移期兼容）。"""
    path = str(tmp_path / "proxy.pid")
    open(path, "w").write("4242")
    assert pid_util.read_pid_file(path) == (4242, None)


def test_read_missing_file(tmp_path):
    assert pid_util.read_pid_file(str(tmp_path / "nope.pid")) == (-1, None)


def test_pid_is_ours_token_mismatch_is_not_ours():
    # os.getpid() 一定活着，但指纹对不上 → 不是我们的进程（复用场景）
    assert pid_util.pid_is_ours(os.getpid(), token=12345) is False
    assert pid_util.pid_is_ours(os.getpid(), token=None) is True  # 老文件：仅存活检查
    assert pid_util.pid_is_ours(99999999, token=None) is False
