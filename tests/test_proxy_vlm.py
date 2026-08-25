from __future__ import annotations

import json

import httpx
import pytest

from vision_relay.config import VLMConfig
from vision_relay.ir import ImageBlock
from vision_relay.vlm import VLMClient, VLMError, probe_ollama


def _client_with(transport) -> VLMClient:
    cfg = VLMConfig(model="qwen-vl-max", base_url="https://dashscope.example/v1", api_key="k")
    client = VLMClient(cfg)
    client._http = httpx.Client(transport=transport)
    return client


def test_describe_chat_ok(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "qwen-vl-max"
        assert body["messages"][0]["content"][1]["type"] == "image_url"
        return httpx.Response(200, json={"choices": [{"message": {"content": "一只橘猫"}}]})

    client = _client_with(httpx.MockTransport(handler))
    assert client.describe(ImageBlock(url="data:image/png;base64,QUJD")) == "一只橘猫"


def test_describe_http_error_raises_vlm_error():
    client = _client_with(httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "bad key"})))
    with pytest.raises(VLMError) as exc:
        client.describe(ImageBlock(url="data:image/png;base64,QUJD"))
    assert exc.value.reason == "AUTH"


def test_probe_ollama_finds_vision_model(monkeypatch):
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen3-vl:4b"}, {"name": "llama3"}]})

    monkeypatch.setattr(
        "vision_relay.vlm.httpx.Client",
        lambda *a, **k: real_client(transport=httpx.MockTransport(handler)),
    )
    assert probe_ollama() == "qwen3-vl:4b"


def _anthropic_client_with(transport) -> VLMClient:
    cfg = VLMConfig(model="qwen-vl-max", base_url="https://dashscope.example/v1", api_key="k", format="anthropic")
    client = VLMClient(cfg)
    client._http = httpx.Client(transport=transport)
    return client


def test_describe_chat_missing_choices_raises_parse():
    client = _client_with(httpx.MockTransport(lambda r: httpx.Response(200, json={"foo": "bar"})))
    with pytest.raises(VLMError) as exc:
        client.describe(ImageBlock(url="[图片描述失败，视觉模型调用失败]"))
    assert exc.value.reason == "PARSE"


def test_describe_chat_top_level_list_raises_parse():
    client = _client_with(httpx.MockTransport(lambda r: httpx.Response(200, json=[{"choices": []}])))
    with pytest.raises(VLMError) as exc:
        client.describe(ImageBlock(url="[图片描述失败，视觉模型调用失败]"))
    assert exc.value.reason == "PARSE"


def test_describe_chat_non_str_content_raises_parse():
    client = _client_with(
        httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": ["a", "b"]}}]}))
    )
    with pytest.raises(VLMError) as exc:
        client.describe(ImageBlock(url="[图片描述失败，视觉模型调用失败]"))
    assert exc.value.reason == "PARSE"


def test_describe_anthropic_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        body = json.loads(request.content)
        assert body["model"] == "qwen-vl-max"
        assert body["messages"][0]["content"][1]["type"] == "image"
        return httpx.Response(200, json={"content": [{"type": "text", "text": "一只橘猫"}]})

    client = _anthropic_client_with(httpx.MockTransport(handler))
    assert client.describe(ImageBlock(base64="ZGF0YQ==", media_type="image/png")) == "一只橘猫"


def test_auto_local_ollama_uses_local_when_no_key(monkeypatch):
    """spec §6.3：无 key + auto_local_ollama 时优先用本地 Ollama 识图。"""
    handler_requests = []

    def handler(request):
        handler_requests.append(request.url.path)
        return httpx.Response(200, json={"choices": [{"message": {"content": "本地识图"}}]})

    cfg = VLMConfig(model="qwen-vl-max", base_url="https://dashscope.example/v1", api_key="", auto_local_ollama=True)
    client = VLMClient(cfg)
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("vision_relay.vlm.probe_ollama", lambda *a, **k: "qwen3-vl")
    assert client.describe(ImageBlock(url="data:image/png;base64,QUJD")) == "本地识图"
    # 应当走本地 /v1/chat/completions，而非云端 dashscope
    assert handler_requests == ["/v1/chat/completions"]


def test_auto_local_ollama_skips_when_key_set(monkeypatch):
    """spec §6.3：已配 key 则不探测 Ollama，走云端端点。"""
    probed = []

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "云端"}}]})

    cfg = VLMConfig(model="qwen-vl-max", base_url="https://dashscope.example/v1", api_key="k", auto_local_ollama=True)
    client = VLMClient(cfg)
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("vision_relay.vlm.probe_ollama", lambda *a, **k: probed.append(1) or "qwen3-vl")
    assert client.describe(ImageBlock(url="data:image/png;base64,QUJD")) == "云端"
    assert probed == []  # 有 key 不探测


def test_describe_anthropic_malformed_content_raises_parse():
    client = _anthropic_client_with(
        httpx.MockTransport(lambda r: httpx.Response(200, json={"content": {"type": "text"}}))
    )
    with pytest.raises(VLMError) as exc:
        client.describe(ImageBlock(base64="ZGF0YQ==", media_type="image/png"))
    assert exc.value.reason == "PARSE"


# ---- 2026-08-25 修正：base_url 填了完整端点时的双拼 + HTML 倾倒 ----
# 实测（opencode.ai/zen）：base_url=https://…/v1/chat/completions 时客户端再拼一次
# /chat/completions → …/chat/completions/chat/completions → 404 HTML 页面被当错误倾倒。


def test_chat_base_url_full_endpoint_no_double_suffix():
    """base_url 含完整 /chat/completions 端点时不再双拼（回归：用户把完整端点填进 base_url）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions", (
            request.url.path
        )  # 不能是 .../chat/completions/chat/completions
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    cfg = VLMConfig(model="qwen-vl-max", base_url="https://dashscope.example/v1/chat/completions", api_key="k")
    client = VLMClient(cfg)
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    assert client.describe(ImageBlock(url="data:image/png;base64,QUJD")) == "ok"


def test_anthropic_base_url_full_endpoint_no_double_suffix():
    """anthropic 格式 base_url 含完整 /messages 端点时同样不双拼。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages", request.url.path
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    cfg = VLMConfig(
        model="qwen-vl-max", base_url="https://dashscope.example/v1/messages", api_key="k", format="anthropic"
    )
    client = VLMClient(cfg)
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    assert client.describe(ImageBlock(base64="ZGF0YQ==", media_type="image/png")) == "ok"


def test_html_404_raises_clear_error_not_raw_dump():
    """上游返回 HTML 网页（404 着陆页等）→ 可读提示（base URL 应为 API 根路径），而非倾倒原始 HTML。"""
    html = '<!DOCTYPE html><html lang="en" dir="ltr"><head><meta name="viewport" content="width=device-width"></head></html>'
    client = _client_with(
        httpx.MockTransport(
            lambda r: httpx.Response(404, text=html, headers={"content-type": "text/html; charset=utf-8"})
        )
    )
    with pytest.raises(VLMError) as exc:
        client.describe(ImageBlock(url="data:image/png;base64,QUJD"))
    assert exc.value.reason == "HTTP"
    msg = str(exc.value)
    assert msg.startswith("上游返回 HTML")  # 主体是可读提示，而非原始 HTML 倾倒
    assert "base URL" in msg  # 提示修复方向


def test_non_html_error_keeps_snippet():
    """非 HTML 的上游错误仍保留响应片段（不破坏既有行为）。"""
    client = _client_with(
        httpx.MockTransport(
            lambda r: httpx.Response(500, text="upstream exploded", headers={"content-type": "text/plain"})
        )
    )
    with pytest.raises(VLMError) as exc:
        client.describe(ImageBlock(url="data:image/png;base64,QUJD"))
    assert exc.value.reason == "HTTP"
    assert "upstream exploded" in str(exc.value)


# ---- Phase2 M1: describe_detail for vision log ----


class TestDescribeDetail:
    def test_detail_contains_prompt_and_raw(self, monkeypatch):
        from vision_relay.config import VLMConfig
        from vision_relay.ir import ImageBlock
        from vision_relay.vlm import VLMClient

        client = VLMClient(VLMConfig(api_key="k"))
        monkeypatch.setattr(VLMClient, "_describe_chat", lambda self, image, prompt: "红色卡车")
        detail = {}
        desc = client.describe(ImageBlock(base64="aGk=", media_type="image/png"), detail=detail)
        assert desc == "红色卡车"
        assert "Describe the image" in detail["prompt"]
        assert detail["raw"] == "红色卡车"


class TestCustomPrompts:
    def test_custom_tier1_used_when_set(self):
        from vision_relay.config import VLMConfig
        from vision_relay.vlm import VLMClient

        cfg = VLMConfig(api_key="k", custom_tier1="CT1")
        client = VLMClient(cfg)
        # 直接验证 _prompt 选择逻辑（不起 HTTP）
        assert client._prompt(None, 1) == "CT1"
        assert client._prompt("q", 2).startswith("Answer the question")  # tier2 未自定义走默认

    def test_prompt_override_wins_all(self):
        from vision_relay.config import VLMConfig
        from vision_relay.vlm import VLMClient

        client = VLMClient(VLMConfig(custom_tier1="CT1"))
        assert client._prompt(None, 1, override="OV") == "OV"
