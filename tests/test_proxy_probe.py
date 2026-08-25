"""verifiable modality probe (spec §5): red-pixel PNG + acceptance-based verdict.

纯接收判定（2026-08-25 决策，参考 dsh-image-vision「检测」机制）：200 即 image，
回答内容一律不看；报错含「不支持」类模态语义 -> text_only；其余不下结论。
"""

import json
import zlib

from vision_relay import probe


class TestRedPng:
    def test_valid_png_magic_and_decompress(self):
        raw = probe.red_png()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
        # IDAT 能解压且首行滤镜字节后是红色像素
        idx = raw.find(b"IDAT")
        length = int.from_bytes(raw[idx - 4 : idx], "big")
        idat = raw[idx + 4 : idx + 4 + length]
        pixels = zlib.decompress(idat)
        assert pixels[1:4] == b"\xff\x00\x00"  # 第一行第一个像素 = RGB(255,0,0)


class TestVerdict:
    def _classify(self, status, text):
        return probe._verdict(status, text)

    def test_200_means_image_regardless_of_answer(self):
        # 接收判定：带图请求被接受即识图；thinking-only 正文为空同样算接受
        assert self._classify(200, "红色") == "image"
        assert self._classify(200, "It is RED") == "image"
        assert self._classify(200, "蓝色") == "image"  # 旧「答错颜色->text_only」已废弃（用户拍板）
        assert self._classify(200, "我不知道") == "image"  # 吞图降级同样废弃
        assert self._classify(200, "") == "image"

    def test_modality_error_means_text_only(self):
        assert (
            self._classify(400, json.dumps({"error": {"message": "model does not support image input"}})) == "text_only"
        )
        assert self._classify(400, "This model does not support vision content") == "text_only"
        assert self._classify(400, "该模型不支持图片输入") == "text_only"
        assert self._classify(400, "此模型为多模态受限模型") == "text_only"

    def test_format_errors_are_inconclusive_not_text_only(self):
        # 我方图片格式/尺寸类报错 != 模型不识图，不得误判 text_only（旧裸词 "image" 会误伤）
        assert self._classify(400, "invalid image format") is None
        assert self._classify(413, "image too large") is None

    def test_auth_notfound_5xx_are_inconclusive(self):
        assert self._classify(401, "unauthorized") is None
        assert self._classify(403, "forbidden") is None
        assert self._classify(404, "model not found") is None
        assert self._classify(500, "oops") is None


class TestAnthropicUrl:
    def _capture_url(self, monkeypatch, base_url):
        seen = {}

        class _Resp:
            status_code = 401
            text = ""

        def fake_post(url, **kw):
            seen["url"] = url
            return _Resp()

        monkeypatch.setattr(probe.httpx, "post", fake_post)
        probe.probe_modality(base_url, "sk", "m", "anthropic")
        return seen["url"]

    def test_v1_suffix_not_duplicated(self, monkeypatch):
        url = self._capture_url(monkeypatch, "https://x/v1")
        assert url.endswith("/v1/messages")
        assert "/v1/v1" not in url

    def test_trailing_slash_v1_not_duplicated(self, monkeypatch):
        url = self._capture_url(monkeypatch, "https://x/v1/")
        assert url.endswith("/v1/messages")
        assert "/v1/v1" not in url

    def test_bare_base_still_appends_v1(self, monkeypatch):
        url = self._capture_url(monkeypatch, "https://x")
        assert url == "https://x/v1/messages"


class TestInvalidURLGuard:
    def test_invalid_url_returns_none_not_raise(self, monkeypatch):
        def fake_post(url, **kw):
            raise probe.httpx.InvalidURL("bad url")

        monkeypatch.setattr(probe.httpx, "post", fake_post)
        assert probe.probe_modality("https://x", "", "m", "chat") is None


class TestProbeCall:
    def test_chat_request_shape_and_result_cached(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        seen = {}

        class _Resp:
            status_code = 200
            text = '{"choices": [{"message": {"content": "红色"}}]}'

        def fake_post(url, json=None, headers=None, timeout=None, trust_env=None):
            seen["url"], seen["body"], seen["headers"] = url, json, headers
            return _Resp()

        monkeypatch.setattr(probe.httpx, "post", fake_post)
        result = probe.probe_modality("https://api.example", "sk-k", "glm-5-plus", "chat")
        assert result == "image"
        assert seen["url"].endswith("/chat/completions")
        content = seen["body"]["messages"][0]["content"]
        assert content[0]["type"] == "text" and "颜色" in content[0]["text"]
        assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert seen["headers"]["Authorization"] == "Bearer sk-k"
        # 结果已按 (provider, model) 缓存 -- 由调用方写入 probe_results；本函数只回判定

    def test_200_thinking_only_body_is_image(self, monkeypatch):
        # 思考模型把 max_tokens 烧在 thinking 块上、无 text 块：接收判定下仍是 image
        class _Resp:
            status_code = 200
            text = '{"content": [{"type": "thinking", "thinking": "我们被要求只回答一个颜色词，但图片是unsupported，看不到。"}]}'

        def fake_post(url, **kw):
            return _Resp()

        monkeypatch.setattr(probe.httpx, "post", fake_post)
        assert probe.probe_modality("https://x", "", "m", "anthropic") == "image"
