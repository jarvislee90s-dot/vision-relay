"""Integration/E2E test shared infrastructure: real subprocess CLI + isolated env + mock upstream.

隔离要点（不污染真实 ~/.vision-relay 与 ~/）：
- VISION_RELAY_CONFIG_DIR 指向 tmp_path；
- USERPROFILE/HOME 指向假 home（wiring/snapshot/tools 的 expanduser 都跟着走）；
- 子进程 cwd=repo 根（`python -m vision_relay` 可导入）；
- PYTHONIOENCODING=utf-8（复刻 Tauri run_core 的做法，spec 风险 4）。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(
    args: list[str],
    cfg_dir: str | Path,
    home: str | Path | None = None,
    stdin: str | None = None,
    timeout: float = 60.0,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """真实调用 `python -m vision_relay <args>`（等价 Tauri run_core 的调用方式）。"""
    env = {**os.environ, "VISION_RELAY_CONFIG_DIR": str(cfg_dir), "PYTHONIOENCODING": "utf-8"}
    if home is not None:
        # Windows expanduser 看 USERPROFILE；POSIX 看 HOME。两个都设，跨平台稳。
        env["USERPROFILE"] = str(home)
        env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "vision_relay", *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        env=env,
        timeout=timeout,
        check=check,
    )


def cli_env(cfg_dir: str | Path, home: str | Path | None = None) -> dict:
    env = {**os.environ, "VISION_RELAY_CONFIG_DIR": str(cfg_dir), "PYTHONIOENCODING": "utf-8"}
    if home is not None:
        env["USERPROFILE"] = str(home)
        env["HOME"] = str(home)
    return env


def popen_cli(args: list[str], cfg_dir: str | Path, home: str | Path | None, stdin: str | None = None):
    return subprocess.Popen(
        [sys.executable, "-m", "vision_relay", *args],
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=REPO_ROOT,
        env=cli_env(cfg_dir, home),
    )


def write_harness_configs(home: Path, base_url: str) -> None:
    """在假 home 下放三个 harness 配置文件（对应 wiring.HARNESS_CFG 的三处布局）。"""
    claude = home / ".claude" / "settings.json"
    claude.parent.mkdir(parents=True, exist_ok=True)
    claude.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": base_url}}), encoding="utf-8")

    codex = home / ".codex" / "config.toml"
    codex.parent.mkdir(parents=True, exist_ok=True)
    codex.write_text(f'model = "gpt-5-codex"\nbase_url = "{base_url}"\n', encoding="utf-8")

    qwen = home / ".qwen" / "settings.json"
    qwen.parent.mkdir(parents=True, exist_ok=True)
    qwen.write_text(
        json.dumps({"model": {"baseUrl": base_url, "model": "qwen3-coder"}}),
        encoding="utf-8",
    )


def read_harness_base_url(home: Path, harness: str) -> str | None:
    """读回 harness 当前 base_url（测试断言用；不经生产代码路径）。"""
    if harness == "codex":
        import re

        txt = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
        m = re.search(r'base_url\s*=\s*"([^"]*)"', txt)
        return m.group(1) if m else None
    if harness == "claude":
        d = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
        return d["env"]["ANTHROPIC_BASE_URL"]
    d = json.loads((home / ".qwen" / "settings.json").read_text(encoding="utf-8"))
    return d["model"]["baseUrl"]


def write_proxy_json(cfg_dir: Path, **overrides) -> None:
    """直接落一份最小 proxy.json（集成测试的 arrange 阶段；被测 CLI 负责读它）。"""
    cfg_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "server": {"bind_host": "127.0.0.1", "bind_port": 8787},
        "relays": [],
        "vlm": {},
        "routing": {},
    }
    doc.update(overrides)
    (cfg_dir / "proxy.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def load_proxy_json(cfg_dir: Path) -> dict:
    return json.loads((cfg_dir / "proxy.json").read_text(encoding="utf-8"))


def envelope_of(proc: subprocess.CompletedProcess) -> dict:
    """stdout 必须是合法 envelope {contract_version, ok, data}。"""
    data = json.loads(proc.stdout)
    assert data.get("contract_version") == 1, f"contract_version missing/wrong: {proc.stdout[:200]}"
    assert "ok" in data and "data" in data
    return data


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, up: bool = True, timeout: float = 10.0) -> bool:
    """轮询端口通断；up=True 等到通，up=False 等到断。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.3)
            connected = s.connect_ex(("127.0.0.1", port)) == 0
        if connected == up:
            return True
        time.sleep(0.2)
    return False


# ---------- 本地 mock 上游（http.server，不依赖外部网络） ----------


class _UpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默
        pass

    def _send(self, payload: dict | list, status: int = 200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._send({"data": [{"id": "mock-vl-max"}, {"id": "mock-text-lite"}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        if self.path.endswith("/chat/completions"):
            length = int(self.headers.get("content-length", 0))
            self.rfile.read(length)
            self._send({"choices": [{"message": {"content": "红色 red"}}]})
        else:
            self._send({"error": "not found"}, 404)


def start_mock_upstream() -> tuple[str, int, list]:
    """起本地 mock 上游；返回 (base_url, port, servers)。

    base_url 用 `http://localhost:<port>/v1` 拼写——models-fetch / probe_target_for 的
    回环过滤只匹配 "http://127.0.0.1" 前缀，localhost 拼写可穿过（这正是被测逻辑）。
    双栈绑定 127.0.0.1 与 ::1：localhost 解析到哪个族都能接住。
    """
    port = free_port()
    servers: list[HTTPServer] = []
    for host in ("127.0.0.1", "::1"):
        try:
            srv = HTTPServer((host, port), _UpstreamHandler)
        except OSError:
            continue  # 无 IPv6 的环境只绑到 127.0.0.1
        servers.append(srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    assert servers, "mock upstream: no loopback bind succeeded"
    return f"http://localhost:{port}/v1", port, servers


def stop_mock_upstream(servers: list) -> None:
    for srv in servers:
        srv.shutdown()
        srv.server_close()
