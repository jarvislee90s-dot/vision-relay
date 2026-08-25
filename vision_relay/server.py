"""Data plane (8787): three inbound protocol routes; control plane (8788): /status."""

from __future__ import annotations

import http.server
import json
import threading
import time

import httpx

from . import route_fallback, tools
from .cache import DescriptionCache
from .config import ProxyConfig, RelayConfig, load_config
from .ir import (
    detect_protocol,
    parse_anthropic,
    parse_chat,
    parse_responses,
    serialize_anthropic,
    serialize_chat,
    serialize_responses,
)
from .logging_util import log_json
from .pipeline import Pipeline
from .vlm import VLMClient

_PARSERS = {"anthropic": parse_anthropic, "responses": parse_responses, "chat": parse_chat}
_SERIALIZERS = {"anthropic": serialize_anthropic, "responses": serialize_responses, "chat": serialize_chat}
_PROTO_BY_PATH = {"/v1/messages": "anthropic", "/v1/responses": "responses", "/v1/chat/completions": "chat"}

# 入站协议 → harness（用于按 harness 区分模型能力判定）
_HARNESS_BY_PROTO = {"anthropic": "claude", "responses": "codex", "chat": "qwen-code"}


def build_vlm_clients(cfg: ProxyConfig) -> dict:
    """按 harness 预建 VLM 客户端（vlm_for 合并覆盖；三 harness 一律建 + _default 兜底）。"""
    clients = {"_default": VLMClient(cfg.vlm)}
    for harness in ("claude", "codex", "qwen-code"):
        clients[harness] = VLMClient(cfg.vlm_for(harness))
    return clients


def extract_session_id(proto: str, body: dict) -> str | None:
    """尽力而为的会话标识（spec §6 识图记录）：anthropic metadata.user_id（常含 __sessionId 后缀）。"""
    if proto != "anthropic":
        return None
    metadata = body.get("metadata")
    user_id = metadata.get("user_id") if isinstance(metadata, dict) else None
    if not isinstance(user_id, str) or not user_id:
        return None
    return user_id.rsplit("__", 1)[-1]


def _resolve_provider(cfg: ProxyConfig, relay: RelayConfig, tool_states_cache: dict) -> str | None:
    """请求期 provider 解析（尽力而为）：两层=工具激活供应商；一层=relay 名。"""
    if getattr(relay, "via", None):
        return tool_states_cache.get(relay.via) or relay.via  # 激活供应商名未取到时退工具名
    return relay.name if relay.name != "default" else None


def _select_relay(cfg: ProxyConfig, inbound_proto: str, model: str = "") -> RelayConfig:
    """按 spec §6.3 选 relay：先 (model, protocol) 匹配，再仅 protocol，最后默认 relay。"""
    import fnmatch

    for relay in cfg.relays:
        if relay.protocol == inbound_proto and any(fnmatch.fnmatch(model, p) for p in relay.models):
            return relay
    for relay in cfg.relays:
        if relay.protocol == inbound_proto:
            return relay
    return RelayConfig(name="default", protocol=inbound_proto, base_url="", api_key="")


_SHARED_ROUTE_CACHE: route_fallback.PortCache | None = None


def _shared_route_cache() -> route_fallback.PortCache:
    """handler 拿不到 server 级缓存时的兜底单例（run_server 正常都会挂 route_cache）。"""
    global _SHARED_ROUTE_CACHE
    if _SHARED_ROUTE_CACHE is None:
        _SHARED_ROUTE_CACHE = route_fallback.PortCache()
    return _SHARED_ROUTE_CACHE


def _upstream_url(cfg: RelayConfig, path: str) -> str:
    """按协议拼接上游 URL。

    - anthropic：Anthropic API 端点固定 /v1/messages。无论 base 是否带版本段都补
      /v1（配合 /v1/v1 去重）——兼容用户填直连根（如 .../api/coding → .../api/coding/v1/messages）；
      仅当 base 已以 path 结尾时原样返回。
    - chat / responses：Codex++ build_versioned_url 启发式（对齐 CodexPlusPlus
      crates/codex-plus-core/src/protocol_proxy.rs）：纯 origin 加 /v1；已带 v<数字>
      版本段 / 非纯 origin 路径 / # 结尾 → 直接拼；/v1/v1 去重。
    """
    base = cfg.base_url.rstrip("/")
    if base.lower().endswith(path):
        return base
    if cfg.protocol == "anthropic":
        url = base + "/v1" + path
        while "/v1/v1" in url:
            url = url.replace("/v1/v1", "/v1")
        return url
    import re

    skip_version = base.endswith("#")
    base = base.rstrip("#").rstrip("/")
    origin_only = False
    if "://" in base:
        origin_only = "/" not in base.split("://", 1)[1]
    last_seg = base.rsplit("/", 1)[-1]
    has_version = bool(re.match(r"^v\d", last_seg))
    if skip_version or has_version or not origin_only:
        url = base + path
    else:
        url = base + "/v1" + path
    while "/v1/v1" in url:
        url = url.replace("/v1/v1", "/v1")
    return url


def _is_loopback(url: str) -> bool:
    """True when the URL host is a loopback address (127.0.0.1 / localhost / ::1)."""
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("localhost", "::1") or host.startswith("127.") or host == "0.0.0.0"


def _forward(cfg: RelayConfig, body: dict, stream: bool):
    headers = {}
    if cfg.api_key:
        if cfg.protocol == "anthropic":
            headers = {"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01"}
        else:
            headers = {"Authorization": f"Bearer {cfg.api_key}"}
    path = (
        "/chat/completions" if cfg.protocol == "chat" else "/messages" if cfg.protocol == "anthropic" else "/responses"
    )
    # 回环地址直连（绕过宿主系统代理，否则 127.0.0.1 上游会被透明代理劫持成 502）；
    # 远程 upstream 仍走系统代理。
    trust_env = not _is_loopback(cfg.base_url)
    with httpx.Client(timeout=300.0, trust_env=trust_env) as client:
        resp = client.post(_upstream_url(cfg, path), json=body, headers=headers)
        return resp.status_code, resp.text


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 走自定义日志
        pass

    def setup(self):
        super().setup()
        self._cfg: ProxyConfig = self.server.cfg  # type: ignore[attr-defined]
        self._pipeline: Pipeline = self.server.pipeline  # type: ignore[attr-defined]

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except ValueError:
            self._json_error(400, "invalid json")
            return
        proto = _PROTO_BY_PATH.get(self.path)
        if proto is None:
            try:
                proto = detect_protocol(self.path, body)
            except ValueError as exc:
                self._json_error(400, str(exc))
                return
        started = time.time()
        try:
            ir = _PARSERS[proto](body)
            harness = _HARNESS_BY_PROTO.get(proto)
            relay = _select_relay(self._cfg, proto, ir.model)
            # 请求期两层/直连解析（spec §5 工具离线→relay 回落直连）：端口在线经工具；
            # 离线回落工具档案当前供应商（协议跟随档案，codex++ chat 上游做协议转换）。
            route_cache = getattr(self.server, "route_cache", None) or _shared_route_cache()
            eff_relay, fallback = route_fallback.resolve_effective_relay(relay, route_cache)
            # 按 harness 请求级选用 VLM（spec §7.1，I-1）：clients 挂在 pipeline 上
            # （run_server 配套注入），经 process(vlm=...) 请求级注入，不变异共享
            # pipeline.vlm；pipeline 被测试整体替换时无此属性，vlm_arg=None 回落
            # pipeline.vlm，互不越权。
            clients = getattr(self._pipeline, "vlm_clients", None)
            vlm_arg = (clients.get(harness) or clients.get("_default")) if clients else None
            provider = _resolve_provider(self._cfg, relay, getattr(self.server, "tool_provider_cache", {}))
            result = self._pipeline.process(
                ir,
                self._cfg,
                harness,
                provider,
                session_id=extract_session_id(proto, body),
                vlm=vlm_arg,
            )
            out_body = _SERIALIZERS[eff_relay.protocol](result.ir)
            try:
                status, text = _forward(eff_relay, out_body, ir.stream)
            except httpx.ConnectError:
                # 两层工具端口请求中途死掉：作废缓存、强制按离线重解析、重试一次。
                # 直连远端自身连不上不重试（与现状一致）。
                if not getattr(relay, "via", None) or not _is_loopback(eff_relay.base_url):
                    raise
                route_cache.invalidate(relay.via)
                eff2, fallback = route_fallback.resolve_effective_relay(relay, route_cache, force_offline=True)
                if eff2 is relay:
                    raise  # 档案不可读，无处可落
                eff_relay = eff2
                out_body = _SERIALIZERS[eff_relay.protocol](result.ir)
                status, text = _forward(eff_relay, out_body, ir.stream)
            log_json(
                {
                    "event": "proxy_request",
                    "proto": proto,
                    "model": ir.model,
                    "stripped": result.stripped,
                    "injected": result.injected,
                    "fail_open": result.fail_open,
                    "fallback": fallback,
                    "upstream_status": status,
                    "duration_ms": int((time.time() - started) * 1000),
                }
            )
            body_bytes = text.encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)
        except Exception as exc:  # noqa: BLE001 - fail-open: never worse than no proxy
            log_json({"event": "proxy_error", "proto": proto, "error": repr(exc)})
            self._json_error(502, "proxy internal error (fail-open)")

    def do_GET(self):
        if self.path.startswith("/status"):
            payload = json.dumps(
                {"ok": True, "relays": len(self._cfg.relays), "vlm_model": self._cfg.vlm.model}
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.end_headers()

    def _json_error(self, status: int, message: str) -> None:
        payload = json.dumps({"error": message}).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run_server(cfg: ProxyConfig | None = None, handler_cls=ProxyHandler):
    """Build the ThreadingHTTPServer with cfg + pipeline attached; caller calls serve_forever()."""
    cfg = cfg or load_config()
    clients = build_vlm_clients(cfg)
    pipeline = Pipeline(clients["_default"], DescriptionCache())
    server = http.server.ThreadingHTTPServer((cfg.bind_host, cfg.bind_port), handler_cls)
    server.cfg = cfg  # type: ignore[attr-defined]
    server.pipeline = pipeline  # type: ignore[attr-defined]
    # per-harness VLM 客户端（spec §7.1）挂在 pipeline 上：请求期按 harness 选用并经
    # process(vlm=...) 请求级注入。测试注入的新 pipeline 天然没有该属性，自动回落
    # pipeline.vlm；初始 vlm 复用 clients["_default"]，不再另建冗余 client。
    pipeline.vlm_clients = clients  # type: ignore[attr-defined]
    server.tool_provider_cache = {}  # type: ignore[attr-defined]
    server.route_cache = route_fallback.PortCache()  # type: ignore[attr-defined]
    _start_provider_cache_refresher(server)
    return server


def _start_provider_cache_refresher(server) -> None:
    """接活 tool_provider_cache 死钩子：30s 懒刷新 {工具名: 激活供应商名}（能力判定用）。"""

    def loop():
        while True:
            try:
                for st in tools.probe_tools():
                    if st.online and st.active_provider:
                        server.tool_provider_cache[st.name] = st.active_provider
            except Exception:  # noqa: BLE001 - 可观测辅助，不 gate 服务
                pass
            time.sleep(30)

    threading.Thread(target=loop, daemon=True).start()
