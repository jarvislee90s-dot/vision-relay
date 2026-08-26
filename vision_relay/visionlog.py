"""Vision call records (spec §6 识图记录): per-call prompt/raw/injected, local-only.

留存默认 7 天可关闭；记录含提示词与原始返回，属敏感数据——只存本机，绝不外发。
record() 永不抛异常（fail-open：留痕失败不能影响代理转发）。
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta
from glob import glob

from .env_util import config_dir

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl$")

# 代理单进程（ThreadingHTTPServer 每请求一线程），进程内锁即可串行化 append；
# 无锁并发 append 在 Windows 上会静默丢行/撕裂行。fail-open 语义不变：锁只包
# 住写盘，获取阻塞即等待写盘完成（毫秒级），任何异常仍被吞掉不影响转发。
_write_lock = threading.Lock()


def _dir() -> str:
    return os.path.join(config_dir(), "visionlog")


def record(row: dict | None, enabled: bool, retention_days: int) -> None:
    if not enabled or not isinstance(row, dict):
        return
    try:
        with _write_lock:
            os.makedirs(_dir(), exist_ok=True)
            path = os.path.join(_dir(), datetime.now().strftime("%Y-%m-%d") + ".jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:  # 留痕绝不影响主链路（fail-open 铁律）
        pass


def cleanup(retention_days: int, directory: str | None = None) -> int:
    """删除超过留存天数的日文件；返回删除数。按文件名日期整日判断（不做行级判断）。

    directory 显式指定目标目录（worker 起线程时快照传入，防测试隔离还原后误碰真实家目录）。"""
    removed = 0
    cutoff = datetime.now() - timedelta(days=retention_days)
    for path in glob(os.path.join(_dir() if directory is None else directory, "*.jsonl")):
        name = os.path.basename(path)
        if not _DATE_RE.match(name):
            continue
        try:
            if datetime.strptime(name[:10], "%Y-%m-%d") < cutoff:
                os.unlink(path)
                removed += 1
        except (ValueError, OSError):
            continue
    return removed


def query(harness: str | None = None, session: str | None = None, limit: int = 200) -> list[dict]:
    if limit <= 0:
        return []
    rows: list[dict] = []
    for path in sorted(glob(os.path.join(_dir(), "*.jsonl")), reverse=True):
        name = os.path.basename(path)
        if not _DATE_RE.match(name):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if harness and row.get("harness") != harness:
                continue
            if session and row.get("session") != session:
                continue
            rows.append(row)
            if len(rows) >= limit:
                return rows
    return rows
