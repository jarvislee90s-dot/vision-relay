"""M1 内部集成测试：真实子进程 CLI、真实跨进程文件锁、真实三态轮转、真实快照读写、本地 mock HTTP。

与单元测试的区别：不 mock 文件 IO / 子进程 / HTTP——`python -m vision_relay <verb> --json`
真实执行，配置目录与假 home 全部隔离在 tmp_path。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

import pytest
from integration_helpers import (
    REPO_ROOT,
    cli_env,
    envelope_of,
    free_port,
    load_proxy_json,
    read_harness_base_url,
    run_cli,
    skipif_github_macos,
    start_mock_upstream,
    stop_mock_upstream,
    wait_port,
    write_harness_configs,
    write_proxy_json,
)

ORIGIN = "https://origin.example/api"


@pytest.fixture()
def env(tmp_path):
    """隔离环境：假 home（三 harness 配置指向 ORIGIN）+ 空 config dir。"""
    home = tmp_path / "home"
    cfg_dir = tmp_path / "cfg"
    write_harness_configs(home, ORIGIN)
    cfg_dir.mkdir()
    write_proxy_json(cfg_dir)
    return home, cfg_dir


# ---------- A. CLI 子进程真实调用 ----------


class TestCliSubprocessVerbs:
    def test_status_envelope_and_stderr_clean(self, env):
        """status --json：stdout 合法 envelope、stderr 干净（GUI 契约：stderr 不污染）。"""
        home, cfg_dir = env
        proc = run_cli(["status", "--json"], cfg_dir, home)
        assert proc.returncode == 0
        assert proc.stderr == ""
        data = envelope_of(proc)
        assert data["ok"] is True
        for key in (
            "service_alive",
            "routing_on",
            "harnesses",
            "tools",
            "relays",
            "snapshots",
            "vlm",
            "setup_state",
            "first_run",
        ):
            assert key in data["data"], f"status missing {key}"
        assert data["data"]["harnesses"]["claude"]["base_url"] == ORIGIN
        # 决策③/⑥c：顶层 bind_port + harness config_path（只读增量，contract v1）
        assert data["data"]["bind_port"] == 8787
        assert data["data"]["harnesses"]["claude"]["config_path"].endswith(os.path.join(".claude", "settings.json"))

    def test_models_set_writes_user_source_and_null_clears(self, env):
        """models-set：stdin 数组写 user 覆盖；value=null 清除回未标注（spec §7 三态）。"""
        home, cfg_dir = env
        rows = [
            {"harness": "claude", "provider": "bigmodel", "model": "m1", "value": "image"},
            {"harness": "claude", "provider": "bigmodel", "model": "m2", "value": None},
        ]
        proc = run_cli(["models-set", "--json"], cfg_dir, home, stdin=json.dumps(rows))
        assert proc.returncode == 0 and proc.stderr == ""
        assert envelope_of(proc)["ok"] is True
        doc = load_proxy_json(cfg_dir)
        assert doc["model_capabilities"]["claude"]["bigmodel"]["m1"] == "image"
        assert doc["capability_sources"]["claude"]["bigmodel"]["m1"] == "user"
        assert "m2" not in doc["model_capabilities"]["claude"]["bigmodel"]
        # null 清除：预先写一个 m3 再清
        run_cli(
            ["models-set", "--json"],
            cfg_dir,
            home,
            stdin=json.dumps([{"harness": "claude", "provider": "bigmodel", "model": "m3", "value": "text_only"}]),
        )
        run_cli(
            ["models-set", "--json"],
            cfg_dir,
            home,
            stdin=json.dumps([{"harness": "claude", "provider": "bigmodel", "model": "m3", "value": None}]),
        )
        doc = load_proxy_json(cfg_dir)
        assert "m3" not in doc["model_capabilities"]["claude"]["bigmodel"]
        assert "m3" not in doc["capability_sources"]["claude"]["bigmodel"]

    def test_models_set_invalid_value_rejected_no_partial_write(self, env):
        home, cfg_dir = env
        rows = [
            {"harness": "claude", "provider": "p", "model": "ok1", "value": "image"},
            {"harness": "claude", "provider": "p", "model": "bad", "value": "movie"},
        ]
        proc = run_cli(["models-set", "--json"], cfg_dir, home, stdin=json.dumps(rows))
        data = envelope_of(proc)
        assert data["ok"] is False and "movie" in proc.stdout
        doc = load_proxy_json(cfg_dir)
        assert doc.get("model_capabilities", {}) == {}  # 校验失败不落盘

    def test_vlm_set_blank_key_keeps_old_and_masks_on_read(self, env):
        """vlm-set：api_key 空串=不修改；config --json 绝不回明文 key（密钥铁律）。"""
        home, cfg_dir = env
        run_cli(
            ["vlm-set", "--json"],
            cfg_dir,
            home,
            stdin=json.dumps({"vlm": {"model": "m1", "api_key": "sk-keep-secret"}}),
        )
        run_cli(["vlm-set", "--json"], cfg_dir, home, stdin=json.dumps({"vlm": {"model": "m2", "api_key": ""}}))
        doc = load_proxy_json(cfg_dir)
        assert doc["vlm"]["model"] == "m2"
        assert doc["vlm"]["api_key"] == "sk-keep-secret"
        proc = run_cli(["config", "--json"], cfg_dir, home)
        assert "sk-keep-secret" not in proc.stdout
        assert envelope_of(proc)["data"]["vlm"]["api_key"] == "●●●●"

    def test_settings_set_roundtrip(self, env):
        home, cfg_dir = env
        proc = run_cli(
            ["settings-set", "--json"],
            cfg_dir,
            home,
            stdin=json.dumps(
                {"routing": {"unknown_default": "image"}, "vision_log": {"enabled": False, "retention_days": 3}}
            ),
        )
        assert envelope_of(proc)["ok"] is True and proc.stderr == ""
        doc = load_proxy_json(cfg_dir)
        assert doc["routing"]["unknown_default"] == "image"
        assert doc["vision_log"] == {"enabled": False, "retention_days": 3}

    def test_relay_set_suppress_and_fill_key(self, env):
        home, cfg_dir = env
        write_proxy_json(
            cfg_dir, relays=[{"name": "direct-claude", "protocol": "anthropic", "base_url": ORIGIN, "models": ["*"]}]
        )
        proc = run_cli(
            ["relay-set", "--json"], cfg_dir, home, stdin=json.dumps({"name": "direct-claude", "suppressed": True})
        )
        assert envelope_of(proc)["ok"] is True
        assert load_proxy_json(cfg_dir)["routing"]["suppressed_relays"] == ["direct-claude"]
        proc = run_cli(
            ["relay-set", "--json"], cfg_dir, home, stdin=json.dumps({"name": "direct-claude", "api_key": "sk-fill"})
        )
        assert envelope_of(proc)["ok"] is True
        doc = load_proxy_json(cfg_dir)
        assert doc["relays"][0]["api_key"] == "sk-fill"
        assert "sk-fill" not in run_cli(["config", "--json"], cfg_dir, home).stdout


# ---------- E. HTTP 集成（本地 mock 上游） ----------


class TestHttpIntegration:
    @pytest.fixture()
    def upstream(self):
        base, port, servers = start_mock_upstream()
        yield base, port
        stop_mock_upstream(servers)

    def test_models_fetch_lists_model_ids(self, env, upstream):
        """models-fetch 真实打到本地 mock /v1/models 并解析（只补 ID 清单，spec §5）。"""
        base, _ = upstream
        home, cfg_dir = env
        write_proxy_json(
            cfg_dir, relays=[{"name": "mock-direct", "protocol": "chat", "base_url": base, "models": ["mock-*"]}]
        )
        proc = run_cli(["models-fetch", "--json"], cfg_dir, home)
        assert proc.returncode == 0 and proc.stderr == ""
        data = envelope_of(proc)["data"]
        assert data["providers"]["mock-direct"] == ["mock-vl-max", "mock-text-lite"]

    def test_probe_json_real_http_roundtrip(self, env, upstream):
        """probe --json 真实发最小带图请求：mock 回答“红色” → 判定 image + 落 probe_results 缓存。"""
        base, _ = upstream
        home, cfg_dir = env
        # qwen-code 指向 mock（回环）：直连候选被跳过，探测落到 mock-direct relay 测真实上游
        qwen = home / ".qwen" / "settings.json"
        qwen.write_text(json.dumps({"model": {"baseUrl": base, "model": "mock-vl-max"}}), encoding="utf-8")
        write_proxy_json(
            cfg_dir, relays=[{"name": "mock-direct", "protocol": "chat", "base_url": base, "models": ["mock-*"]}]
        )
        proc = run_cli(
            ["probe", "--json", "--harness", "qwen-code", "--provider", "mock", "--model", "mock-vl-max"],
            cfg_dir,
            home,
        )
        assert proc.returncode == 0 and proc.stderr == ""
        data = envelope_of(proc)["data"]
        assert data["result"] == "image"
        cached = load_proxy_json(cfg_dir)["probe_results"]["mock"]["mock-vl-max"]
        assert cached["result"] == "image"

    def test_vlm_test_real_http_roundtrip(self, env, upstream):
        """vlm-test 与生产同一调用路径：真实 POST mock /chat/completions，返回 desc/prompt_used。"""
        base, _ = upstream
        home, cfg_dir = env
        write_proxy_json(cfg_dir, vlm={"model": "mock-vl", "base_url": base, "api_key": "k"})
        proc = run_cli(["vlm-test", "--json"], cfg_dir, home, stdin=json.dumps({"mode": "tier1"}))
        assert proc.returncode == 0 and proc.stderr == ""
        data = envelope_of(proc)["data"]
        assert "红" in data["desc"]
        assert "Describe the image" in data["prompt_used"]
        assert data["model"] == "mock-vl" and data["duration_ms"] >= 0


# ---------- B. 文件锁跨进程 + 原子写 ----------


class TestFileLockCrossProcess:
    def test_lock_held_by_child_blocks_parent(self, tmp_path, monkeypatch):
        """子进程真实持有 relay.lock 期间，父进程 try_config_lock 拿不到；释放后可拿。"""
        from vision_relay.locking import try_config_lock

        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(cfg_dir))  # 父进程必须锁同一个文件
        child_code = (
            "import time\n"
            "from vision_relay.locking import config_lock\n"
            "with config_lock():\n"
            "    print('HELD', flush=True)\n"
            "    time.sleep(1.5)\n"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=REPO_ROOT,
            env=cli_env(cfg_dir),
        )
        try:
            assert child.stdout.readline().decode().strip() == "HELD"  # 等到锁真正落地
            assert try_config_lock() is None  # 持锁期间父进程必须拿不到
            child.wait(timeout=15)
        finally:
            child.kill()
            child.wait()
        with try_config_lock() as held:
            assert held is True  # 子进程退出后锁释放

    def test_concurrent_refresh_never_corrupts(self, env):
        """两个真实 refresh 子进程并发：第二个排队等锁；最终接线正确、事件行完整。"""
        home, cfg_dir = env
        port = free_port()
        write_proxy_json(cfg_dir, server={"bind_host": "127.0.0.1", "bind_port": port})
        # 三个 harness 都被“工具抢线”到 15721（cc-switch 端口）→ reclaim 路径必触发
        write_harness_configs(home, "http://127.0.0.1:15721")
        listener = socket.socket()
        listener.bind(("127.0.0.1", port))
        listener.listen(8)  # 端口在线 = service_alive（reconcile 只探通断）
        try:
            procs = [
                subprocess.Popen(
                    [sys.executable, "-m", "vision_relay", "refresh", "--json"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=REPO_ROOT,
                    env=cli_env(cfg_dir, home),
                )
                for _ in range(2)
            ]
            outs = []
            for p in procs:
                out, err = p.communicate(timeout=60)
                err = err.decode("utf-8", "replace")
                assert p.returncode == 0, err
                assert err == ""
                outs.append(json.loads(out.decode("utf-8")))
        finally:
            listener.close()
        expected = f"http://127.0.0.1:{port}"
        for harness in ("claude", "codex", "qwen-code"):
            assert read_harness_base_url(home, harness) == expected
        # 事件流水每行都是完整 JSON（并发 append 不撕裂；均在 config_lock 内）
        lines = (cfg_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert lines, "refresh 必须留痕 reclaim 事件"
        rows = [json.loads(x) for x in lines]
        assert any(r["type"] == "reclaim" for r in rows)
        # 两轮 refresh 至少一轮 reclaim、另一轮幂等；proxy.json 始终可解析
        assert load_proxy_json(cfg_dir)["server"]["bind_port"] == port

    def test_atomic_write_survives_kill_mid_loop(self, tmp_path):
        """写入循环中途强杀：os.replace 原子性保证 proxy.json 永远完整可加载。"""
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        child_code = (
            "import time\n"
            "from vision_relay.config import ProxyConfig, save_config\n"
            "for i in range(500):\n"
            "    cfg = ProxyConfig()\n"
            "    cfg.bind_port = 9000 + (i % 2)\n"
            "    save_config(cfg)\n"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=REPO_ROOT,
            env=cli_env(cfg_dir),
        )
        time.sleep(0.5)
        child.kill()
        child.wait()
        # 重启后（新子进程）配置不损坏：status --json 正常返回
        proc = run_cli(["status", "--json"], cfg_dir)
        assert proc.returncode == 0
        assert envelope_of(proc)["ok"] is True
        assert load_proxy_json(cfg_dir)["server"]["bind_port"] in (9000, 9001)


# ---------- C. 三态存储真实轮转（user 覆盖不被探针推翻） ----------


class TestTriStateRotation:
    def test_user_override_survives_probe_then_null_clears(self, env, tmp_path):
        """spec §7.3 硬规则：user 标注不被 probe 结果覆盖；probe 只写缓存。"""
        base, _, servers = start_mock_upstream()
        try:
            home, cfg_dir = env
            # qwen-code 指向 mock（回环）：直连候选被跳过，探测落到 mock-direct relay 测真实上游
            qwen = home / ".qwen" / "settings.json"
            qwen.write_text(json.dumps({"model": {"baseUrl": base, "model": "mock-vl-max"}}), encoding="utf-8")
            write_proxy_json(
                cfg_dir, relays=[{"name": "mock-direct", "protocol": "chat", "base_url": base, "models": ["mock-*"]}]
            )
            # 1) 用户写 text_only（mock 实测会答对颜色 → image，制造冲突）
            run_cli(
                ["models-set", "--json"],
                cfg_dir,
                home,
                stdin=json.dumps(
                    [{"harness": "qwen-code", "provider": "mock", "model": "mock-vl-max", "value": "text_only"}]
                ),
            )
            # 2) 真实探测
            proc = run_cli(
                ["probe", "--json", "--harness", "qwen-code", "--provider", "mock", "--model", "mock-vl-max"],
                cfg_dir,
                home,
            )
            data = envelope_of(proc)["data"]
            # 3) 生效值仍是 user 的 text_only；缓存记录实测 image
            assert data["result"] == "text_only"
            doc = load_proxy_json(cfg_dir)
            assert doc["model_capabilities"]["qwen-code"]["mock"]["mock-vl-max"] == "text_only"
            assert doc["capability_sources"]["qwen-code"]["mock"]["mock-vl-max"] == "user"
            assert doc["probe_results"]["mock"]["mock-vl-max"]["result"] == "image"
            # 4) null 清除 → 未标注
            run_cli(
                ["models-set", "--json"],
                cfg_dir,
                home,
                stdin=json.dumps([{"harness": "qwen-code", "provider": "mock", "model": "mock-vl-max", "value": None}]),
            )
            doc = load_proxy_json(cfg_dir)
            assert "mock-vl-max" not in doc["model_capabilities"]["qwen-code"]["mock"]
            assert "mock-vl-max" not in doc["capability_sources"]["qwen-code"]["mock"]
        finally:
            stop_mock_upstream(servers)


# ---------- D. 接管快照真实读写（absorb 记快照 → reclaim 不覆盖快照） ----------


class TestTakeoverSnapshotRotation:
    def test_absorb_records_snapshot_then_reclaim_keeps_it(self, env):
        """陌生地址 → absorb：快照=新地址、direct relay 落盘、接线接管；再被工具抢线 → reclaim 不改快照。"""
        home, cfg_dir = env
        port = free_port()
        write_proxy_json(cfg_dir, server={"bind_host": "127.0.0.1", "bind_port": port})
        listener = socket.socket()
        listener.bind(("127.0.0.1", port))
        listener.listen(8)
        try:
            proc = run_cli(["refresh", "--json"], cfg_dir, home)  # ORIGIN = 陌生地址 → absorb
            data = envelope_of(proc)["data"]
            assert any(a["type"] == "absorb" and a["harness"] == "claude" for a in data["actions"])
            snaps = json.loads((cfg_dir / "snapshots.json").read_text(encoding="utf-8"))
            assert snaps["claude"]["base_url"] == ORIGIN
            assert "sk-" not in (cfg_dir / "snapshots.json").read_text(encoding="utf-8")
            relays = load_proxy_json(cfg_dir)["relays"]
            direct = [r for r in relays if r["name"] == "direct-claude"]
            assert direct and direct[0]["base_url"] == ORIGIN
            expected = f"http://127.0.0.1:{port}"
            assert read_harness_base_url(home, "claude") == expected

            # 第二轮：被工具抢线（改到 cc-switch 端口）→ reclaim；快照仍是 ORIGIN 不被污染
            (home / ".claude" / "settings.json").write_text(
                json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:15721"}}), encoding="utf-8"
            )
            run_cli(["refresh", "--json"], cfg_dir, home)
            assert read_harness_base_url(home, "claude") == expected
            snaps = json.loads((cfg_dir / "snapshots.json").read_text(encoding="utf-8"))
            assert snaps["claude"]["base_url"] == ORIGIN
            rows = [json.loads(x) for x in (cfg_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            assert any(r["type"] == "reclaim" and r["harness"] == "claude" for r in rows)
        finally:
            listener.close()


# ---------- 决策⑤：PID 复用防护（pid 文件带进程身份指纹） ----------


class TestPidReuseHardening:
    @skipif_github_macos
    def test_reused_pid_never_reported_alive_never_killed(self, tmp_path):
        """强杀后 pid 文件伪造为复用进程（token 对不上）：status 不误报、stop 不误杀。"""
        home = tmp_path / "home"
        cfg_dir = tmp_path / "cfg"
        write_harness_configs(home, ORIGIN)
        cfg_dir.mkdir()
        port = free_port()
        write_proxy_json(cfg_dir, server={"bind_host": "127.0.0.1", "bind_port": port})
        try:
            assert run_cli(["start", "--detach"], cfg_dir, home).returncode == 0
            assert wait_port(port, up=True, timeout=20)
            pid = int(json.loads((cfg_dir / "proxy.pid").read_text(encoding="utf-8"))["pid"])
            assert "token" in json.loads((cfg_dir / "proxy.pid").read_text(encoding="utf-8"))
            os.kill(pid, 9)
            assert wait_port(port, up=False, timeout=20)

            dummy = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                (cfg_dir / "proxy.pid").write_text(
                    json.dumps({"pid": dummy.pid, "token": 1234567890}), encoding="utf-8"
                )
                d = envelope_of(run_cli(["status", "--json"], cfg_dir, home))["data"]
                assert d["service_alive"] is False
                proc = run_cli(["stop"], cfg_dir, home)
                assert proc.returncode == 1 and "not running" in proc.stdout
                assert dummy.poll() is None  # dummy 还活着！
            finally:
                dummy.kill()
                dummy.wait()
        finally:
            run_cli(["stop"], cfg_dir, home, timeout=60)
