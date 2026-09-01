#!/usr/bin/env python3
"""wiring 端到端冒烟脚本（沙箱 HOME，免交互，可重复运行）。

在临时 HOME 中构造 claude / codex / qwen-code / zcode 四种 harness 的原始配置，
依次跑三个场景，全部通过则退出码 0，任一失败退出码 1：

  ① 正常接线：start 后各 harness 配置被改写且生成 .bak；stop 后与原始内容逐字节一致。
  ② 重复启动：已有 .bak 时不静默覆盖丢失原配置（.bak 始终保存第一次接管前的原始值）。
  ③ legacy 迁移：存在 .qwen-mm-proxy.bak 时按既有兼容逻辑还原并清理。

用法（仓库根目录，已激活 .venv）：
    python tests/smoke/smoke_wiring.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

# 确保从任意 CWD 运行都能导入 vision_relay（editable 安装时亦可）。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from vision_relay import snapshot, wiring  # noqa: E402
from vision_relay.config import ProxyConfig  # noqa: E402

PROXY = "http://127.0.0.1:8787"
NEW_BAK = wiring.BAK_SUFFIX
LEGACY_BAK = wiring.LEGACY_BAK_SUFFIX
FAILURES: list[str] = []


def _check(cond: bool, label: str) -> None:
    print(f"    [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILURES.append(label)


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _seed_harnesses(home: str) -> dict[str, str]:
    """在沙箱 HOME 写出四种 harness 的原始配置，返回 {harness: 原始内容}。"""
    originals: dict[str, str] = {}

    # claude: JSON, env.ANTHROPIC_BASE_URL
    p = wiring._path(home, "claude")
    originals["claude"] = json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://real-claude.example/api"}}, indent=2)
    _write(p, originals["claude"])

    # codex: TOML, base_url
    p = wiring._path(home, "codex")
    originals["codex"] = 'model = "gpt-5"\nbase_url = "https://real-codex.example/v1"\n'
    _write(p, originals["codex"])

    # qwen-code: JSON, model.baseUrl + modelProviders 条目级
    p = wiring._path(home, "qwen-code")
    qwen = {
        "env": {"QWEN_KEY": "sk-test"},
        "model": {"name": "m-1", "baseUrl": "https://real-qwen.example/v1"},
        "modelProviders": {
            "openai": [{"id": "p1", "name": "p1", "baseUrl": "https://real-qwen.example/v1", "envKey": "QWEN_KEY"}]
        },
    }
    originals["qwen-code"] = json.dumps(qwen, indent=2)
    _write(p, originals["qwen-code"])

    # zcode: v2 config.json, provider.<id>.options.baseURL（条目级）
    p = wiring._path(home, "zcode")
    zcode = {
        "provider": {
            "z1": {
                "name": "P",
                "kind": "anthropic",
                "enabled": True,
                "options": {"apiKey": "k-1234567890abcdef", "baseURL": "https://real-zcode.example/api"},
                "models": {"m": {"modalities": {"input": ["text"]}}},
            }
        }
    }
    originals["zcode"] = json.dumps(zcode, indent=2)
    _write(p, originals["zcode"])
    return originals


def _points_at_proxy(home: str, harness: str) -> bool:
    p = wiring._path(home, harness)
    if harness == "zcode":
        d = json.load(open(p, encoding="utf-8"))
        return all(e["options"]["baseURL"] == PROXY for e in d["provider"].values())
    if harness == "qwen-code":
        d = json.load(open(p, encoding="utf-8"))
        return d.get("model", {}).get("baseUrl") == PROXY and all(
            e["baseUrl"] == PROXY for e in d["modelProviders"]["openai"]
        )
    return wiring.read_base_url(p, wiring.HARNESS_CFG[harness]) == PROXY


def _effective_state(home: str, harness: str):
    """提取"有效路由状态"用于 stop 前后比对。

    JSON harness（claude/qwen/zcode）的 write_base_url 会整体重序列化（缩进/尾换行/
    qwen 准入门留下的空 generationConfig），逐字节比对无意义；故比对语义：
    全局 base_url + 各条目 baseUrl + zcode 模态门。codex 为 TOML 正则原位替换，
    额外逐字节比对（验证不污染其它行）。
    """
    p = wiring._path(home, harness)
    if harness == "zcode":
        d = json.load(open(p, encoding="utf-8"))
        return tuple(
            sorted(
                (pid, e["options"]["baseURL"], tuple(m.get("modalities", {}).get("input", [])))
                for pid, e in d["provider"].items()
                for m in e.get("models", {}).values()
            )
        )
    if harness == "qwen-code":
        d = json.load(open(p, encoding="utf-8"))
        entries = tuple(
            sorted(
                (auth, i, e.get("baseUrl"))
                for auth, lst in d.get("modelProviders", {}).items()
                for i, e in enumerate(lst or [])
            )
        )
        return (d.get("model", {}).get("baseUrl"), entries)
    # claude / codex：全局 base_url
    return wiring.read_base_url(p, wiring.HARNESS_CFG[harness])


def _isolate(home: str) -> None:
    """把 wiring / snapshot 的 HOME 与配置目录都指向沙箱（沿用仓库 monkeypatch 纪律）。"""
    wiring.HOME = home
    snapshot.HOME = home
    os.environ["VISION_RELAY_CONFIG_DIR"] = os.path.join(home, ".vr-cfg")


def scenario_normal_wiring(home: str, originals: dict[str, str]) -> None:
    print("  ① 正常接线：start 改写 + .bak；stop 还原为原始内容")
    cfg = ProxyConfig()
    orig_state = {h: _effective_state(home, h) for h in originals}
    wiring.wiring_backup_and_rewrite(cfg)
    for h in originals:
        _check(_points_at_proxy(home, h), f"{h}: start 后 base_url 指向本代理")
    # 整文件备份：claude / codex 生成 .vision-relay.bak（qwen/zcode 为条目级，备份语义另计）
    for h in ("claude", "codex"):
        _check(os.path.exists(wiring._path(home, h) + NEW_BAK), f"{h}: 生成 {NEW_BAK}")
    wiring.wiring_restore_on_stop(cfg)
    for h in originals:
        _check(_effective_state(home, h) == orig_state[h], f"{h}: stop 后有效路由状态与原始一致")
        if h == "codex":  # TOML 正则原位替换：逐字节一致，不污染其它行
            _check(_read(wiring._path(home, h)) == originals[h], "codex: stop 后与原始内容逐字节一致")
        _check(not os.path.exists(wiring._path(home, h) + NEW_BAK), f"{h}: .bak 已清理")


def scenario_repeated_start(home: str, originals: dict[str, str]) -> None:
    print("  ② 重复启动：已有 .bak 不静默覆盖丢失原配置")
    cfg = ProxyConfig()
    orig_state = {h: _effective_state(home, h) for h in originals}
    wiring.wiring_backup_and_rewrite(cfg)  # 第一次：备份原始值
    bak_claude = wiring._path(home, "claude") + NEW_BAK
    bak_codex = wiring._path(home, "codex") + NEW_BAK
    first_bak_claude = _read(bak_claude)
    first_bak_codex = _read(bak_codex)
    # 第二次 start（不 stop）：.bak 已存在，不得被代理态覆盖
    wiring.wiring_backup_and_rewrite(cfg)
    _check(_read(bak_claude) == first_bak_claude, "claude: 重复 start 后 .bak 仍为首次原始值")
    _check(_read(bak_codex) == first_bak_codex, "codex: 重复 start 后 .bak 仍为首次原始值")
    _check(_read(bak_claude) == originals["claude"], "claude: .bak 内容等于原始配置（未被代理地址污染）")
    # stop 仍能还原
    wiring.wiring_restore_on_stop(cfg)
    _check(_effective_state(home, "claude") == orig_state["claude"], "claude: stop 还原原始路由状态")
    _check(_read(wiring._path(home, "codex")) == originals["codex"], "codex: stop 还原原始")


def scenario_legacy_migration(home: str) -> None:
    print("  ③ legacy 迁移：.qwen-mm-proxy.bak 按既有兼容逻辑处理")
    cfg = ProxyConfig()
    p = wiring._path(home, "claude")
    original = json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://legacy-original.example/api"}}, indent=2)
    _write(p, json.dumps({"env": {"ANTHROPIC_BASE_URL": PROXY}}))  # 当前指向本代理
    _write(p + LEGACY_BAK, original)  # 仅 legacy 备份，无新后缀备份
    _check(not os.path.exists(p + NEW_BAK), "前置：仅 legacy .bak 存在")
    msgs = wiring.wiring_restore_on_stop(cfg)
    _check(any("restored" in m or "bak" in m for m in msgs), f"产生还原消息: {msgs}")
    _check(_read(p) == original, "claude: 从 legacy .bak 还原为原始内容")
    _check(not os.path.exists(p + LEGACY_BAK), "legacy .bak 已清理")
    _check(not os.path.exists(p + NEW_BAK), "无残留新后缀 .bak")


def main() -> int:
    print("wiring 端到端冒烟（沙箱 HOME，四种 harness）")
    for scenario in (scenario_normal_wiring,):
        home = tempfile.mkdtemp(prefix="vr-smoke-")
        try:
            _isolate(home)
            originals = _seed_harnesses(home)
            scenario(home, originals)
        finally:
            shutil.rmtree(home, ignore_errors=True)

    # 场景②/③各自用独立沙箱，互不污染
    home = tempfile.mkdtemp(prefix="vr-smoke-")
    try:
        _isolate(home)
        originals = _seed_harnesses(home)
        scenario_repeated_start(home, originals)
    finally:
        shutil.rmtree(home, ignore_errors=True)

    home = tempfile.mkdtemp(prefix="vr-smoke-")
    try:
        _isolate(home)
        scenario_legacy_migration(home)
    finally:
        shutil.rmtree(home, ignore_errors=True)

    if FAILURES:
        print(f"\n冒烟失败：{len(FAILURES)} 项")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("\n冒烟全部通过 ✅（三场景）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
