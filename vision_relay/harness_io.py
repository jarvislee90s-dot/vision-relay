"""harness 配置文件读写：四种格式的 base_url 读写、原子 JSON 落盘、模型名抽取、codex 目录补丁。

格式覆盖 claude/qwen 的 JSON 点路径、codex 的 TOML、env 文件 KEY=VALUE、
zcode-v2 的条目级激活供应商读取；另含 codex model catalog 的 image 模态补丁与对称还原。
全部函数只接收显式路径参数，不读全局 HOME，便于隔离测试。
"""

from __future__ import annotations

import json
import os
import re

from .harness_spec import BAK_SUFFIX, _Harness
from .modalities import _ensure_image


def _json_save_atomic(path: str, data: dict) -> bool:
    """JSON 原子写（tmp + replace，失败清 tmp）；qwen settings 与 codex catalog 共用。"""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        return False


def read_base_url(path: str, h: _Harness) -> str | None:
    """按 harness 格式读取当前 base_url（zcode-v2 特判返回激活供应商地址）；失败返回 None。"""
    try:
        if h.kind == "zcode-v2":
            d = json.load(open(path, encoding="utf-8"))
            provs = d.get("provider")
            if isinstance(provs, dict):
                for e in provs.values():
                    if isinstance(e, dict) and e.get("enabled") is True:
                        opts = e.get("options")
                        if isinstance(opts, dict) and isinstance(opts.get("baseURL"), str) and opts["baseURL"]:
                            return opts["baseURL"]
            return None
        if h.kind == "json":
            d = json.load(open(path, encoding="utf-8"))
            node = d
            for part in h.key.split("."):
                node = node.get(part) if isinstance(node, dict) else None
                if node is None:
                    break
            return node if isinstance(node, str) else None
        if h.kind == "env":
            for line in open(path, encoding="utf-8"):
                m = re.match(rf"^{h.key}=(.*)$", line.strip())
                if m:
                    return m.group(1).strip()
            return None
        # toml
        m = re.search(r'base_url\s*=\s*"([^"]*)"', open(path, encoding="utf-8").read())
        return m.group(1) if m else None
    except (OSError, json.JSONDecodeError):
        return None


def write_base_url(path: str, h: _Harness, url: str) -> bool:
    """按 harness 格式原子改写 base_url（tmp + replace，失败清 tmp）。"""
    tmp = path + ".tmp"
    try:
        if h.kind == "json":
            d = json.load(open(path, encoding="utf-8"))
            parts = h.key.split(".")
            node = d
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = url
            data = json.dumps(d, ensure_ascii=False, indent=2) + "\n"
        elif h.kind == "env":
            lines = []
            hit = False
            for line in open(path, encoding="utf-8"):
                if re.match(rf"^{h.key}=", line.strip()):
                    lines.append(f"{h.key}={url}\n")
                    hit = True
                else:
                    lines.append(line)
            if not hit:
                lines.append(f"{h.key}={url}\n")
            data = "".join(lines)
        else:  # toml
            raw = open(path, encoding="utf-8").read()
            if re.search(r'base_url\s*=\s*"[^"]*"', raw):
                data = re.sub(r'base_url\s*=\s*"[^"]*"', f'base_url = "{url}"', raw)
            else:
                data = raw + f'\nbase_url = "{url}"\n'
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
        return True
    except (OSError, json.JSONDecodeError):
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        return False


def _first_model(path: str) -> str:
    """从 harness 配置尽力抽一个模型名（快照记录用；失败返回空串）。"""
    try:
        txt = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    m = re.search(r'(?i)(?:model|name)["\']?\s*[:=]\s*["\']([\w@.\-/]+)["\']', txt)
    return m.group(1) if m else ""


def _codex_catalog_path(config_path: str) -> str | None:
    """config.toml 引用的 model catalog 路径（相对路径按 config 所在目录解析）；无引用 None。"""
    try:
        raw = open(config_path, encoding="utf-8").read()
    except OSError:
        return None
    m = re.search(r'model_catalog_json\s*=\s*"([^"]*)"', raw)
    if not m or not m.group(1).strip():
        return None
    v = m.group(1).strip()
    return v if v.startswith("/") else os.path.join(os.path.dirname(config_path), v)


def _patch_codex_catalog_modalities(config_path: str) -> str | None:
    """目录模态补丁（接管配套）：Codex 按 catalog 的 input_modalities 放行 view_image/
    贴图——纯文本标注会把图片挡在请求之外，代理转写永远收不到图。给 models[] 每条
    补 "image"（已含者不动，缺字段/非列表设 ["text","image"]）；首次改动前整文件备份
    （与 harness 配置同后缀、同"已有备份不覆盖"语义），重复接管幂等。读/解析失败
    静默跳过，不打断接线。"""
    cat = _codex_catalog_path(config_path)
    if cat is None or not os.path.exists(cat):
        return None
    try:
        d = json.load(open(cat, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    models = d.get("models") if isinstance(d, dict) else None
    if not isinstance(models, list):
        return None
    patched = 0
    for m in models:
        if not isinstance(m, dict):
            continue
        mods = m.get("input_modalities")
        if isinstance(mods, list):
            if _ensure_image(mods):
                patched += 1
        else:
            m["input_modalities"] = ["text", "image"]
            patched += 1
    if not patched:
        return None
    if not os.path.exists(cat + BAK_SUFFIX):
        try:
            import shutil

            shutil.copyfile(cat, cat + BAK_SUFFIX)
        except OSError:
            pass
    if not _json_save_atomic(cat, d):
        return None
    return f"catalog {os.path.basename(cat)}: +image modalities ({patched} models)"


def _restore_codex_catalog(config_path: str) -> str | None:
    """目录补丁的对称还原：备份存在则整文件还原并删备份（base_url 守卫由调用方负责）。"""
    cat = _codex_catalog_path(config_path)
    if cat is None:
        return None
    bak = cat + BAK_SUFFIX
    if not os.path.exists(bak):
        return None
    try:
        import shutil

        shutil.copyfile(bak, cat)
        os.unlink(bak)
    except OSError:
        return None
    return f"catalog {os.path.basename(cat)}: restored"
