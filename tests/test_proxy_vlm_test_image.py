"""钉住 vlm-test 的可选自定义图片契约（GUI 测试图使用同一 VLM 调用路径）。"""

import vision_relay.verbs as verbs
from vision_relay.config import ProxyConfig


def test_custom_image_is_passed_to_vlm(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    images = []

    class FakeClient:
        def __init__(self, vlm_cfg):
            self.cfg = vlm_cfg

        def describe(self, image, **_kwargs):
            images.append(image)
            return "识别结果"

    monkeypatch.setattr(verbs, "_VLMClient", FakeClient)
    payload = {"mode": "tier1", "image_base64": "aGk=", "media_type": "image/webp"}
    out = verbs.vlm_test(ProxyConfig(), payload=payload)

    assert out["ok"] is True and out["data"]["desc"] == "识别结果"
    assert images[0].base64 == "aGk="
    assert images[0].media_type == "image/webp"
