"""图片准入门原语：跨 qwen / zcode / codex 三种配置形态共用的 image 模态门读写。

qwen 用 generationConfig.modalities.image 准入门，zcode 用 modalities.input 列表，
codex catalog 用 input_modalities 列表；本模块只提供"门是否开 / 代开 / 补 image"
的最小原语，不含任何 harness 专属路径或备份逻辑。
"""

from __future__ import annotations

from .harness_spec import _MOD_ABSENT


def _modalities_open(entry: dict) -> bool:
    """qwen 条目：generationConfig.modalities.image 准入门是否已开。"""
    gc = entry.get("generationConfig")
    mod = gc.get("modalities") if isinstance(gc, dict) else None
    return isinstance(mod, dict) and mod.get("image") is True


def _open_modalities(entry: dict) -> object:
    """打开 qwen 准入门，返回原值（哨兵 ~absent~ 表示原本没有 modalities 字段）。

    qwen-code 的 inputModalities 准入门不开，Read/粘贴的图片根本不会进请求体
    （本代理转写无从谈起）。接管后"所有模型都识图"（文本模型由本代理转写），
    故门一律代开；stop 按原值还原，避免直连态下图片被塞给纯文本上游报错。
    """
    gc = entry.setdefault("generationConfig", {})
    if not isinstance(gc, dict):
        return _MOD_ABSENT
    mod = gc.get("modalities", _MOD_ABSENT)
    if isinstance(mod, dict) and mod.get("image") is True:
        return _MOD_ABSENT  # 已开着：不产生变更记录（幂等，还原不动它）
    original = mod
    gc["modalities"] = {"image": True}
    return original


def _mod_input(model: dict) -> list | None:
    """zcode 模型 → modalities.input 列表；形态不认识返回 None（不硬造，spec §5.1）。"""
    mods = model.get("modalities")
    inp = mods.get("input") if isinstance(mods, dict) else None
    return inp if isinstance(inp, list) else None


def _ensure_image(mods_list: list) -> bool:
    """输入模态列表没有 "image" 则追加（模态门共用原语：zcode 与 codex 目录补丁共用）。"""
    if "image" in mods_list:
        return False
    mods_list.append("image")
    return True
