"""模型矩阵来源层(spec §5):cc-switch / Codex++ 工具档案只读读取 + 直连兜底。

铁律:绝不写工具的任何文件;sqlite 只以 mode=ro 打开;只读白名单键
(模型名、base_url、供应商名、is_current)——任何密钥字段不读、不外传、不落日志。
读取 best-effort:任何失败返回空,由调用方降级到直连扫描。
禁止读取 Codex++ 的 modelVlm(用户裁决 2026-08-24:该配置不准)。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

try:  # Python ≥3.11 才有 tomllib;3.10 退正则
    import tomllib
except ImportError:  # pragma: no cover - 取决于解释器版本
    tomllib = None

from .tools import CCSWITCH_DB as _CCSWITCH_DB_DEFAULT

# 模块级常量:测试 monkeypatch 的挂点(默认指向真机路径)
CCSWITCH_DB = _CCSWITCH_DB_DEFAULT
CODEXPP_SETTINGS: str = ""  # Task 2 填真实默认值


@dataclass(frozen=True)
class ProviderRow:
    tool: str  # cc-switch | codex-plus | direct
    harness: str  # claude | codex | qwen-code
    provider: str  # 工具档案里的供应商显示名;直连态为域名推导名或 "?"
    base_url: str
    is_current: bool
    models: list[str]


# claude settings_config 里视作"模型值"的 env 键(白名单:不含任何 token 键)
_CLAUDE_MODEL_KEY = re.compile(r"^ANTHROPIC_(?:DEFAULT_\w+_)?MODEL(?:_NAME)?$")


def _dedup_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _claude_models(sc: dict) -> list[str]:
    env = sc.get("env") if isinstance(sc.get("env"), dict) else {}
    models = [v for k, v in env.items() if _CLAUDE_MODEL_KEY.match(k) and isinstance(v, str)]
    top = sc.get("model")
    if isinstance(top, str):
        models.insert(0, top)  # 顶层 model 是主模型,列首位;env 模型随后(测试断言该顺序)
    return _dedup_keep_order(models)


_TOML_MODEL = re.compile(r'(?m)^model\s*=\s*"([^"]+)"')
_TOML_BASE = re.compile(r'(?m)^base_url\s*=\s*"([^"]+)"')


def _codex_models(sc: dict) -> list[str]:
    models: list[str] = []
    text = sc.get("config")
    if isinstance(text, str) and text.strip():
        if tomllib is not None:
            try:
                parsed = tomllib.loads(text)
                m = parsed.get("model")
                if isinstance(m, str):
                    models.append(m)
            except tomllib.TOMLDecodeError:
                models += _TOML_MODEL.findall(text)
        else:
            models += _TOML_MODEL.findall(text)
    cat = sc.get("modelCatalog")
    if isinstance(cat, dict):
        for m in cat.get("models") or []:
            if isinstance(m, dict) and isinstance(m.get("model"), str):
                models.append(m["model"])
    return _dedup_keep_order(models)


def _codex_base_url(sc: dict) -> str:
    text = sc.get("config")
    if isinstance(text, str) and text.strip():
        if tomllib is not None:
            try:
                for p in tomllib.loads(text).get("model_providers", {}).values():
                    if isinstance(p, dict) and isinstance(p.get("base_url"), str):
                        return p["base_url"]
            except tomllib.TOMLDecodeError:
                pass
        hit = _TOML_BASE.search(text)
        if hit:
            return hit.group(1)
    return ""


def ccswitch_matrix() -> dict[str, list[ProviderRow]]:
    """providers 表 → {app_type: [ProviderRow]}。只取 claude/codex;任何失败返回 {}。"""
    try:
        conn = sqlite3.connect(f"file:{CCSWITCH_DB}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT app_type, name, is_current, settings_config FROM providers "
                "WHERE app_type IN ('claude','codex') ORDER BY app_type, is_current DESC, sort_index"
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - schema 漂移/库缺失:best-effort 空矩阵
        return {}
    out: dict[str, list[ProviderRow]] = {}
    for app_type, name, is_current, cfg_text in rows:
        try:
            sc = json.loads(cfg_text)
        except (TypeError, ValueError):
            continue
        if app_type == "claude":
            env = sc.get("env") if isinstance(sc.get("env"), dict) else {}
            base = env.get("ANTHROPIC_BASE_URL", "")
            models = _claude_models(sc)
        else:
            base = _codex_base_url(sc)
            models = _codex_models(sc)
        if not models:
            continue  # 如 OpenAI Official 的空 config:无模型可标,不产行
        out.setdefault(app_type, []).append(
            ProviderRow(
                tool="cc-switch",
                harness=app_type,
                provider=name,
                base_url=base if isinstance(base, str) else "",
                is_current=bool(is_current),
                models=models,
            )
        )
    return out
