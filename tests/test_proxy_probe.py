"""verifiable modality probe (spec §5 模型能力标注): red-pixel PNG + three-way verdict."""

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

    def test_200_correct_color_means_image(self):
        assert self._classify(200, "红色") == "image"
        assert self._classify(200, "It is RED") == "image"

    def test_200_wrong_answer_means_text_only(self):
        assert self._classify(200, "蓝色") == "text_only"  # 200 但没读图
        assert self._classify(200, "我不知道") == "text_only"  # 吞图

    def test_modality_error_means_text_only(self):
        assert (
            self._classify(400, json.dumps({"error": {"message": "model does not support image input"}})) == "text_only"
        )
        assert self._classify(400, "This model does not support vision content") == "text_only"

    def test_auth_notfound_5xx_timeout_are_inconclusive(self):
        assert self._classify(401, "unauthorized") is None
        assert self._classify(403, "forbidden") is None
        assert self._classify(404, "model not found") is None
        assert self._classify(500, "oops") is None
        assert self._classify(200, None) is None  # 解析失败=含糊


class TestProbeCall:
    def test_chat_request_shape_and_result_cached(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        seen = {}

        class _Resp:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": "红色"}}]}

            text = '{"ok":1}'

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
        # 结果已按 (provider, model) 缓存 —— 由调用方写入 probe_results；本函数只回判定
