#!/usr/bin/env python3
"""vision-relay wiring 端到端冒烟脚本（沙箱 HOME，免交互，可重复运行）。

场景:
  ① 正常接线: start 后四种 harness 配置被改写且生成 .bak; stop 后与原始内容一致
     （JSON 按语义比较——快照路径还原为 indent=2 归一化写回，属既有产品行为；TOML 按字节）
  ② 重复启动: 已有 .bak 时不覆盖（原配置不丢），重复 start 幂等不抛错
  ③ legacy 迁移: .qwen-mm-proxy.bak 存在时按既有兼容逻辑处理（restore 侧能还原并删除,
     start 侧不新建/不覆盖新后缀备份）

用法: python tests/smoke/run_smoke.py
退出码: 0 全部通过 / 1 存在失败

全部 IO 落在临时目录（HOME 与 VISION_RELAY_CONFIG_DIR 均重定向），不触碰真实用户家目录。
跨平台：仅用 stdlib + pathlib，Windows Git Bash / macOS / Linux 均可直接运行。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vision_relay import wiring  # noqa: E402
from vision_relay.config import ProxyConfig, RoutingConfig  # noqa: E402

PROXY = "http://127.0.0.1:8787"
ALL_HARNESSES = ["claude", "codex", "qwen-code", "zcode"]

_passed = 0
_failures = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed
    if cond:
        _passed += 1
    else:
        _failures.append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))


def _dump_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _seed_home(home: Path) -> dict[str, Path]:
    """四种 harness 原始配置（含 codex catalog）；返回 {逻辑名: 路径}。"""
    paths = {}
    paths["claude"] = home / ".claude" / "settings.json"
    _dump_json(paths["claude"], {"env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}})
    codex_dir = home / ".codex"
    paths["codex"] = codex_dir / "config.toml"
    paths["codex"].parent.mkdir(parents=True, exist_ok=True)
    paths["codex"].write_text(
        'model = "gpt-4o"\nbase_url = "https://chatgpt.com"\nmodel_catalog_json = "catalog.json"\n',
        encoding="utf-8",
    )
    paths["codex-catalog"] = codex_dir / "catalog.json"
    _dump_json(paths["codex-catalog"], {"models": [{"id": "gpt-4o", "input_modalities": ["text"]}]})
    paths["qwen-code"] = home / ".qwen" / "settings.json"
    _dump_json(
        paths["qwen-code"],
        {
            "model": {"baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
            "modelProviders": {
                "openai": [
                    {
                        "id": "mw1",
                        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "envKey": "DASHSCOPE_KEY",
                        "generationConfig": {},
                    }
                ]
            },
        },
    )
    paths["zcode"] = home / ".zcode" / "v2" / "config.json"
    _dump_json(
        paths["zcode"],
        {
            "provider": {
                "bigmodel": {
                    "kind": "anthropic",
                    "enabled": True,
                    "options": {"baseURL": "https://open.bigmodel.cn/api/anthropic", "apiKey": "k-smoke"},
                    "models": {"glm-4": {"modalities": {"input": ["text"]}}},
                }
            }
        },
    )
    return paths


def _bak(path: Path) -> Path:
    return path.parent / (path.name + ".vision-relay.bak")


def _legacy_bak(path: Path) -> Path:
    return path.parent / (path.name + ".qwen-mm-proxy.bak")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _same_after_restore(path: Path, original: bytes) -> bool:
    """stop 后与原始内容一致性比较。

    JSON 文件按**语义**比较：快照路径还原经 write_base_url/_json_save_atomic 写回，
    格式归一化为 indent=2（既有产品行为），故任意原始格式（compact/indent=4/键序不同
    不影响——dict 保序）还原后语义一致但字节未必一致；TOML 文件按**字节**比较：
    toml 分支是正则行替换，行格式 byte-stable（种子用规范空格，既有行为）。
    """
    if path.suffix == ".json":
        return json.loads(path.read_bytes()) == json.loads(original)
    return path.read_bytes() == original


def scenario_normal(home: Path) -> None:
    print("场景① 正常接线：start 改写+生成 .bak；stop 后与原始内容一致（JSON 语义/TOML 字节）")
    paths = _seed_home(home)
    originals = {k: p.read_bytes() for k, p in paths.items()}
    cfg = ProxyConfig(bind_port=8787, routing=RoutingConfig(harnesses=ALL_HARNESSES))
    wiring.wiring_backup_and_rewrite(cfg)
    check("start 未抛异常", True)
    check("claude base_url 指代理", _read_json(paths["claude"])["env"]["ANTHROPIC_BASE_URL"] == PROXY)
    check("codex base_url 指代理", f'base_url = "{PROXY}"' in paths["codex"].read_text(encoding="utf-8"))
    check("codex catalog 补 image 门", "image" in _read_json(paths["codex-catalog"])["models"][0]["input_modalities"])
    qwen = _read_json(paths["qwen-code"])
    check("qwen model.baseUrl 指代理", qwen["model"]["baseUrl"] == PROXY)
    check("qwen 条目 baseUrl 指代理", qwen["modelProviders"]["openai"][0]["baseUrl"] == PROXY)
    check(
        "qwen 条目 modalities 门已开",
        qwen["modelProviders"]["openai"][0]["generationConfig"]["modalities"] == {"image": True},
    )
    check("zcode baseURL 指代理", _read_json(paths["zcode"])["provider"]["bigmodel"]["options"]["baseURL"] == PROXY)
    check(
        "read_base_url(zcode) 取激活供应商",
        wiring.read_base_url(str(paths["zcode"]), wiring.HARNESS_CFG["zcode"]) == PROXY,
    )
    for name in ALL_HARNESSES:
        check(f"{name} 生成 .bak", _bak(paths[name]).exists())
    check("codex catalog 生成 .bak", _bak(paths["codex-catalog"]).exists())
    wiring.wiring_restore_on_stop(cfg)
    check("stop 未抛异常", True)
    for name, raw in originals.items():
        check(f"{name} 还原后与原始内容一致", _same_after_restore(paths[name], raw))
    for name in ALL_HARNESSES:
        check(f"{name} .bak 已删除", not _bak(paths[name]).exists())
    check("codex catalog .bak 已删除", not _bak(paths["codex-catalog"]).exists())


def scenario_repeat_start(home: Path) -> None:
    print("场景② 重复启动：已有 .bak 不覆盖（原配置不丢），重复 start 幂等")
    paths = _seed_home(home)
    originals = {k: p.read_bytes() for k, p in paths.items()}
    cfg = ProxyConfig(bind_port=8787, routing=RoutingConfig(harnesses=ALL_HARNESSES))
    wiring.wiring_backup_and_rewrite(cfg)
    first_bak = {name: _bak(paths[name]).read_bytes() for name in ALL_HARNESSES}
    # 运行期漂移：用户/外部工具把配置改到别的上游
    drifted = json.loads(originals["claude"].decode("utf-8"))
    drifted["env"]["ANTHROPIC_BASE_URL"] = "https://drift.example"
    _dump_json(paths["claude"], drifted)
    wiring.wiring_backup_and_rewrite(cfg)  # 第二次 start：不得抛错、不得覆盖 .bak
    check("重复 start 未抛异常", True)
    for name in ALL_HARNESSES:
        check(f"{name} .bak 未被第二次 start 覆盖（原配置不丢）", _bak(paths[name]).read_bytes() == first_bak[name])
    check("claude 漂移后被重新接管", _read_json(paths["claude"])["env"]["ANTHROPIC_BASE_URL"] == PROXY)
    check("zcode 漂移后被重新接管", _read_json(paths["zcode"])["provider"]["bigmodel"]["options"]["baseURL"] == PROXY)
    # 整文件备份路径仍持有首次原始内容：wiring_restore（.bak 路径）可逐字节还原
    wiring.wiring_restore(cfg)
    for name in ALL_HARNESSES:
        check(f"{name} .bak 还原与首次原始一致", _same_after_restore(paths[name], originals[name]))
        check(f"{name} .bak 还原后已删除", not _bak(paths[name]).exists())


def scenario_legacy(home: Path) -> None:
    print("场景③ legacy 迁移：.qwen-mm-proxy.bak 按既有兼容逻辑处理")
    # restore 侧：旧版本接的线（配置指代理、只有旧后缀备份），stop 仍能收尾
    paths = _seed_home(home)
    original = paths["claude"].read_bytes()
    _dump_json(paths["claude"], {"env": {"ANTHROPIC_BASE_URL": PROXY}})
    legacy = _legacy_bak(paths["claude"])
    legacy.write_bytes(original)
    cfg = ProxyConfig(bind_port=8787, routing=RoutingConfig(harnesses=["claude"]))
    msgs = wiring.wiring_restore(cfg)
    check("restore 侧：旧后缀备份被还原", paths["claude"].read_bytes() == original)
    check("restore 侧：旧后缀备份还原后删除", not legacy.exists())
    check("restore 侧：未新建新后缀备份", not _bak(paths["claude"]).exists())
    check("restore 侧：消息含 restored", any("claude: restored" in m for m in msgs), str(msgs))
    # start 侧：已有旧后缀备份时不新建新后缀备份、不覆盖旧备份
    home2 = home / "start-side"
    wiring.HOME = str(home2)  # start 侧用独立沙箱 home
    paths2 = _seed_home(home2)
    original2 = paths2["claude"].read_bytes()
    legacy2 = _legacy_bak(paths2["claude"])
    legacy2.write_bytes(original2)
    cfg2 = ProxyConfig(bind_port=8787, routing=RoutingConfig(harnesses=["claude"]))
    wiring.wiring_backup_and_rewrite(cfg2)
    check("start 侧：未新建新后缀备份", not _bak(paths2["claude"]).exists())
    check("start 侧：旧后缀备份原样保留（不覆盖）", legacy2.read_bytes() == original2)
    check("start 侧：配置仍被接管", _read_json(paths2["claude"])["env"]["ANTHROPIC_BASE_URL"] == PROXY)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vr-smoke-"))
    try:
        print(f"沙箱根目录: {tmp}")
        for scenario in (scenario_normal, scenario_repeat_start, scenario_legacy):
            home = tmp / scenario.__name__
            home.mkdir()
            cfgdir = tmp / "cfg" / scenario.__name__
            cfgdir.mkdir(parents=True)
            os.environ["VISION_RELAY_CONFIG_DIR"] = str(cfgdir)
            wiring.HOME = str(home)  # 既有测试挂点；所有模块经 _wiring_home() 动态读取
            scenario(home)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    total = _passed + len(_failures)
    print(f"\nSMOKE RESULT: {_passed}/{total} PASS, {len(_failures)} FAIL")
    if _failures:
        print("失败项: " + "; ".join(_failures))
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
