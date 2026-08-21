"""Verifiable modality probe (spec §5): one minimal image request per (provider, model).

探针=红色 8×8 PNG + "这是什么颜色"。三档判定：
  报错且报文含模态语义 → text_only（主力信号：不识图直接报错）
  200 且答对颜色       → image（真读到图）
  200 但答错颜色       → text_only（静默吞图，防误判）
  200 但没答出来/解析失败 → None（含糊，不下结论）
  401/403/404/5xx/超时/连接失败 → None（不下结论，回落目录建议）
结果由调用方按 (provider, model) 写入 probe_results 缓存。
"""

from __future__ import annotations

import base64
import re
import struct
import zlib

import httpx

_QUESTION = "这张图片是什么颜色？只回答一个颜色词（例如：红色）。"
# 中文颜色词按子串命中；英文必须整词（\bred\b）——hundred/credit 含子串 red 但不是颜色词。
_RE_RED = re.compile(r"\bred\b")


def red_png() -> bytes:
    """8×8 纯红 PNG（纯标准库生成，无外部资源）。"""
    width = height = 8
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(red_png()).decode()


# 报错文本中的模态语义线索（不识图的模型报错通常带这些词）
_MODALITY_ERROR_HINTS = ("image", "vision", "multimodal", "modalit", "图片", "图像", "视觉")


def _mentions_color(low: str) -> bool:
    """颜色词命中：中文 "红" 子串；英文 "red" 整词（词边界，防 hundred/credit 误判）。"""
    return "红" in low or bool(_RE_RED.search(low))


def _verdict(status: int, answer: str | None) -> str | None:
    if status == 200:
        if answer is None:
            return None  # 解析失败=含糊
        return "image" if _mentions_color(answer.lower()) else "text_only"
    if status in (401, 403, 404) or status >= 500:
        return None
    low = (answer or "").lower()
    if any(h in low for h in _MODALITY_ERROR_HINTS):
        return "text_only"
    return None  # 含糊报错不下结论


def _chat_body(model: str) -> dict:
    return {
        "model": model,
        "max_tokens": 24,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _QUESTION},
                    {"type": "image_url", "image_url": {"url": _data_url()}},
                ],
            }
        ],
    }


def _responses_body(model: str) -> dict:
    return {
        "model": model,
        "max_output_tokens": 24,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _QUESTION},
                    {"type": "input_image", "image_url": _data_url()},
                ],
            }
        ],
    }


def _anthropic_body(model: str) -> dict:
    return {
        "model": model,
        "max_tokens": 24,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _QUESTION},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(red_png()).decode(),
                        },
                    },
                ],
            }
        ],
    }


def _extract(protocol: str, data) -> str | None:
    try:
        if protocol == "anthropic":
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        if protocol == "responses":
            for item in data.get("output", []):
                for part in item.get("content", []):
                    if part.get("type") in ("output_text", "text"):
                        return part.get("text")
            return None
        content = data["choices"][0]["message"]["content"]
        return content if isinstance(content, str) else None
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def probe_modality(base_url: str, api_key: str, model: str, protocol: str, timeout: float = 30.0) -> str | None:
    """发一次最小带图请求并判定。返回 'image' | 'text_only' | None(不下结论)。"""
    if protocol == "anthropic":
        root = base_url.rstrip("/")
        if root.endswith("/v1"):  # base 已含版本段时不重复拼接（防 /v1/v1/messages）
            root = root[: -len("/v1")]
        url = root + "/v1/messages"
        body = _anthropic_body(model)
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"} if api_key else {}
    elif protocol == "responses":
        url = base_url.rstrip("/") + "/responses"
        body = _responses_body(model)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    else:
        url = base_url.rstrip("/") + "/chat/completions"
        body = _chat_body(model)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=timeout, trust_env=False)
    except (httpx.HTTPError, httpx.InvalidURL):  # InvalidURL 不是 HTTPError 子类，需单列
        return None
    try:
        answer = _extract(protocol, resp.json())
    except ValueError:
        answer = None
    if resp.status_code == 200 and not answer:
        answer = _extract_from_text(protocol, resp.text)
    return _verdict(resp.status_code, answer)


def _extract_from_text(protocol: str, text: str) -> str | None:
    """JSON 解析失败时退而做一次宽松文本嗅探（找颜色词；词边界见 _mentions_color）。"""
    if _mentions_color(text.lower()):
        return "红"
    return None
