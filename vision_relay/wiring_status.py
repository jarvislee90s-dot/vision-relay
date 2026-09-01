"""wiring 状态查询：四处 harness 当前 base_url 归属与条目级统计的只读报告。

纯查询、无副作用、不改写任何配置：汇总各 harness 的 live base_url、是否指向本代理、
是否有备份，并附 qwen-code / zcode 的条目级 wired/gated 统计。供 `wiring report`
与对账观察使用。接收显式 home，不读全局 HOME。
"""

from __future__ import annotations

import os

from .harness_io import read_base_url
from .harness_spec import HARNESS_CFG, _find_bak, _path
from .qwen_providers import _qwen_provider_stats
from .zcode_providers import _zcode_provider_stats


def report(cfg, home: str) -> list[dict]:
    """四处 harness 当前 base_url 归属。qwen-code 另附条目级统计（0.22.0 条目
    baseUrl 优先，全局字段 wired 不代表真接管；门不开图片进不了请求，故 wired
    要求 URL 指向本代理且门全开）。"""
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    out = []
    for name in HARNESS_CFG:
        p = _path(home, name)
        cur = read_base_url(p, HARNESS_CFG[name]) if os.path.exists(p) else None
        row = {
            "harness": name,
            "path": p,
            "base_url": cur,
            "wired": bool(cur and (cur == proxy_url or cur.startswith(proxy_url + "/"))),
            "has_backup": _find_bak(p) is not None,
        }
        if name == "qwen-code" and os.path.exists(p):
            stats = _qwen_provider_stats(p, proxy_url)
            row["providers"] = stats
            row["wired"] = row["wired"] and stats["eligible"] == stats["wired"] and stats["eligible"] == stats["gated"]
        if name == "zcode" and os.path.exists(p):
            stats = _zcode_provider_stats(p, proxy_url)
            row["providers"] = stats
            # zcode 纯条目级：wired 只看 eligible 全覆盖+门全开（激活供应商可能是空 key 未接管者，
            # 其直连地址不代表接管失败）
            row["wired"] = stats["eligible"] > 0 and stats["eligible"] == stats["wired"] == stats["gated"]
        out.append(row)
    return out
