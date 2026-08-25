"""Verifiable modality probe (spec §5): one minimal image request per (provider, model).

探针=红色 8×8 PNG（base64 内嵌进各协议原生图片字段）。纯接收判定（2026-08-25 决策，
参考 dsh-image-vision 的「检测」机制；旧「答对颜色」校验废弃--思考模型把小 token
预算烧在 thinking 块上吐不出正文，答案校验非确定且不可靠，且问句示例会泄漏答案）：
  200（带图请求被接受）            -> image
  报错且报文含「不支持」类模态语义  -> text_only（主力信号：不识图直接报错）
  401/403/404/5xx/超时/连接失败    -> None（不下结论，回落目录建议）
  报错但无模态语义（含格式类报错） -> None（含糊，不下结论）
已知残余风险：静默吞图模型（200 收图但模型没看到）会被判 image；由 user 标注优先
（probe 不覆盖 user 来源值）+ 界面行内切换兜底。
结果由调用方按 (provider, model) 写入 probe_results 缓存。
"""

from __future__ import annotations

import base64
import struct
import zlib

import httpx

_QUESTION = "这张图片是什么颜色？"


def red_png() -> bytes:
    """8×8 纯红 PNG（纯标准库生成，无外部资源；1×1 有触发最小尺寸校验的风险，不缩）。"""
    width = height = 8
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(red_png()).decode()


# 报错文本中的「不支持识图」语义短语（裸词 "image" 会误伤 "invalid image format" 类格式报错，
# 故只收支持性短语；中英文都覆盖）
_MODALITY_ERROR_HINTS = ("not support", "text-only", "text only", "multimodal", "modalit", "不支持", "多模态", "识图")


def _verdict(status: int, error_text: str) -> str | None:
    if status == 200:
        return "image"
    low = (error_text or "").lower()
    if any(h in low for h in _MODALITY_ERROR_HINTS):
        return "text_only"
    return None  # 含糊报错（鉴权/限流/格式/模型不存在）不下结论


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


def probe_modality(base_url: str, api_key: str, model: str, protocol: str, timeout: float = 30.0) -> str | None:
    """发一次最小带图请求，按「接收与否」判定。返回 'image' | 'text_only' | None(不下结论)。"""
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
    # 200 的响应体不解析（thinking 块/答案对错与判定无关）；非 200 只看报错语义
    return _verdict(resp.status_code, resp.text)
