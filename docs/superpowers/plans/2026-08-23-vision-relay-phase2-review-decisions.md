# vision-relay 二期 Review 决策落地 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **上游 spec（必须尊重，冲突时以 spec 为准）:** [`docs/superpowers/specs/2026-08-21-vision-relay-phase2-control-plane-design.md`](../specs/2026-08-21-vision-relay-phase2-control-plane-design.md) —— 本计划同时**修订** spec 的三处表述（Task 7，已与用户确认）。
> **AGENTS.md 是工程宪法**：fail-open 不可破坏、GUI 不解析配置文件、不写路由工具的配置、日志/输出不带 key、测试先行、行为变更同步 spec。
> **决策来源**：2026-08-23 用户对 M1+M2 review 提出的 6 项 spec 偏离逐条拍板（记录见 spec §13 新增决策行）。

**Goal:** 落地 6 项已拍板的 review 决策——stop 按最新快照还原（.bak 兜底）、PID 复用防护、status 暴露 config_path/bind_port + GUI 打开配置入口、事件导出、probe 无结论改 ok:result=null、杂项收敛；同时把「探测一次 + 过目即用户意图」语义写进 spec。

**Architecture:** 全部改动落在既有分层内：还原逻辑进 `wiring.py`（reconcile/cli 只换调用点）、进程身份进新模块 `pid_util.py`（cli/reconcile 保留薄包装维持测试 monkeypatch 锚点）、契约只做**只读增量字段**（contract_version 保持 1，前端 envelope 容忍多余字段）、GUI 仍只消费动词 + 一个 Rust `open_path` 命令（无新依赖）。

**Tech Stack:** Python 3.10+ 标准库（ctypes/procfs，无新依赖）；pytest；Tauri 2 + React（pnpm/vitest）；cargo check 验证 Rust。

**基线（开工前必须复现）:** `.venv/Scripts/python -m pytest -q` → **393 passed**；`pnpm -C gui test` → **25 passed**；`ruff format --check .` + `ruff check .` 全绿；`cd gui/src-tauri && cargo check` 通过。任何任务完成后不得低于此基线。

**范围外（明确不做）:** stop 不撤销 absorb 产生的 `direct-*` relay（它们是用户供应商定义，不是运行时接线）；配置文件不做行号定位；语言切换保持即时生效不动；M3 事项（打包/自动监听）。

---

### Task 1: stop 统一按最新接管快照还原，.bak 兜底（偏离①方案 A）

**Files:**
- Modify: `vision_relay/wiring.py`（新增 `wiring_restore_on_stop`）
- Modify: `vision_relay/cli.py:184-194`（`cmd_stop` 换调用点）
- Test: `tests/test_proxy_wiring.py`（新增 TestRestoreOnStop）
- Test: `tests/test_proxy_cli.py`（`TestStopIntent` 的 monkeypatch 目标改名）
- Test: `tests/test_e2e_g2_routing.py`（新增 absorb→stop 场景）

- [ ] **Step 1: 写失败测试（追加到 `tests/test_proxy_wiring.py` 末尾）**

```python
class TestRestoreOnStop:
    """stop 统一还原（spec §5 + 2026-08-23 决策）：最新快照优先，.bak 兜底。"""

    def _env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring, "HOME", str(tmp_path))
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))

    def test_snapshot_preferred_over_stale_bak(self, tmp_path, monkeypatch):
        """absorb 更新过快照后 stop：还原到快照值（最新），不是 .bak 里的最早原值。"""
        self._env(tmp_path, monkeypatch)
        import shutil

        # 接管前原值 A（.bak）→ 运行中吸收新供应商 B（快照更新为 B）→ 当前指向本代理
        for h, content in {
            "claude": json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://A.example"}}),
            "codex": 'model = "m"\nbase_url = "https://A.example"\n',
        }.items():
            p = wiring._path(str(tmp_path), h)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(content)
            shutil.copyfile(p, p + wiring.BAK_SUFFIX)
        wiring.write_base_url(wiring._path(str(tmp_path), "claude"), wiring.HARNESS_CFG["claude"], "http://127.0.0.1:8787")
        wiring.write_base_url(wiring._path(str(tmp_path), "codex"), wiring.HARNESS_CFG["codex"], "http://127.0.0.1:8787")
        snapshot.save("claude", snapshot.Snapshot(base_url="https://B.example", key_ref="k", model="m"))
        snapshot.save("codex", snapshot.Snapshot(base_url="https://B.example", key_ref="k", model="m"))

        msgs = wiring.wiring_restore_on_stop(ProxyConfig())
        assert wiring.read_base_url(wiring._path(str(tmp_path), "claude"), wiring.HARNESS_CFG["claude"]) == "https://B.example"
        assert wiring.read_base_url(wiring._path(str(tmp_path), "codex"), wiring.HARNESS_CFG["codex"]) == "https://B.example"
        # 快照还原后 .bak 已过期，删除防 stop/后续 restore 二次覆盖
        assert not os.path.exists(wiring._path(str(tmp_path), "claude") + wiring.BAK_SUFFIX)

    def test_bak_fallback_when_snapshot_missing(self, tmp_path, monkeypatch):
        """快照不可得的 harness：退回第一次接管前的整文件 .bak（用户确认的兜底路径）。"""
        self._env(tmp_path, monkeypatch)
        import shutil

        p = wiring._path(str(tmp_path), "claude")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        original = json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://A.example", "ANTHROPIC_AUTH_TOKEN": "sk-x"}})
        open(p, "w", encoding="utf-8").write(original)
        shutil.copyfile(p, p + wiring.BAK_SUFFIX)
        wiring.write_base_url(p, wiring.HARNESS_CFG["claude"], "http://127.0.0.1:8787")
        # 不写快照

        wiring.wiring_restore_on_stop(ProxyConfig())
        d = json.load(open(p, encoding="utf-8"))
        assert d["env"]["ANTHROPIC_BASE_URL"] == "https://A.example"
        assert d["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-x"  # 整文件还原连 key 位置一起回来
        assert not os.path.exists(p + wiring.BAK_SUFFIX)

    def test_noop_when_base_url_not_ours(self, tmp_path, monkeypatch):
        """当前不指向本代理：不动文件（与其他还原函数同守卫）。"""
        self._env(tmp_path, monkeypatch)
        p = wiring._path(str(tmp_path), "claude")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://elsewhere.example"}}))
        wiring.wiring_restore_on_stop(ProxyConfig())
        assert json.load(open(p, encoding="utf-8"))["env"]["ANTHROPIC_BASE_URL"] == "https://elsewhere.example"
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_proxy_wiring.py -q -k RestoreOnStop`
Expected: FAIL（`wiring_restore_on_stop` 不存在）

- [ ] **Step 3: 实现（`vision_relay/wiring.py` 末尾追加）**

```python
def wiring_restore_on_stop(cfg) -> list[str]:
    """stop 的统一还原（spec §5 + 2026-08-23 决策）：按最新接管组合快照；快照缺失的
    harness 退回第一次接管前的整文件 .bak 兜底。

    每 harness 独立决策：有快照 → 只写回 base_url（运行期间用户对配置文件的其他
    修改原样保留），并删除已过期的 .bak；无快照 → .bak 整文件还原（含 key 位置等
    完整原始状态）。两者都要求当前 base_url 指向本代理才动文件（与 wiring_restore
    同守卫）。崩溃修复路径不走这里（reconcile 仍用 wiring_restore_by_snapshot）。
    """
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    snaps = snapshot.load()
    msgs: list[str] = []
    for name in cfg.routing.harnesses:
        h = HARNESS_CFG[name]
        p = _path(HOME, name)
        if not os.path.exists(p):
            continue
        cur = read_base_url(p, h)
        if cur is None or (cur != proxy_url and not cur.startswith(proxy_url + "/")):
            msgs.append(f"{name}: 当前 base_url={cur!r} 非本代理，跳过还原")
            continue
        snap = snaps.get(name)
        if snap is not None:
            ok = write_base_url(p, h, snap.base_url)
            if ok:
                bak = _find_bak(p)
                if bak is not None:  # 快照是最新真相，整文件备份已过期
                    try:
                        os.unlink(bak)
                    except OSError:
                        pass
            msgs.append(f"{name}: snapshot restored to {snap.base_url} ({'ok' if ok else 'FAIL'})")
            continue
        bak = _find_bak(p)
        if bak is None:
            msgs.append(f"{name}: 无快照且无备份，跳过")
            continue
        try:
            import shutil

            shutil.copyfile(bak, p)
            os.unlink(bak)
            msgs.append(f"{name}: bak restored")
        except OSError as exc:
            msgs.append(f"{name}: restore FAIL {exc}")
    return msgs
```

注意：`wiring.py` 头部已有 `from . import snapshot`、`import os`；`ProxyConfig` 不需要 import（函数只用到 `cfg.bind_port/routing.harnesses`）。

- [ ] **Step 4: `cmd_stop` 换调用点（`vision_relay/cli.py`，回滚块内）**

把

```python
        from .wiring import relays_restore, wiring_restore

        c = load_config()
        if c.routing.auto_wire:
            for msg in wiring_restore(c):
                print(f"  [restore] {msg}")
```

改为

```python
        from .wiring import relays_restore, wiring_restore_on_stop

        c = load_config()
        if c.routing.auto_wire:
            for msg in wiring_restore_on_stop(c):
                print(f"  [restore] {msg}")
```

同时把 `tests/test_proxy_cli.py::TestStopIntent::test_stop_clears_routing_intent` 里的
`monkeypatch.setattr(wiring, "wiring_restore", lambda c: [])` 改为
`monkeypatch.setattr(wiring, "wiring_restore_on_stop", lambda c: [])`（monkeypatch 目标随函数改名，测试语义不变）。
`wiring.py` 里旧的 `wiring_restore` 保留不删（仍被既有单测与 `wiring_report` 生态引用；YAGNI 清理由后续统一做）。

- [ ] **Step 5: E2E 场景（追加到 `tests/test_e2e_g2_routing.py` 末尾）**

```python
def test_stop_after_absorb_restores_latest_snapshot(tmp_path):
    """偏离①验收：运行中吸收新供应商 B → stop 还原到 B（最新快照），而非最早的 A。"""
    home = tmp_path / "home"
    cfg_dir = tmp_path / "cfg"
    write_harness_configs(home, ORIGIN)  # A = ORIGIN
    cfg_dir.mkdir()
    port = free_port()
    write_proxy_json(cfg_dir, server={"bind_host": "127.0.0.1", "bind_port": port})
    proc = run_cli(["start", "--detach"], cfg_dir, home)
    assert proc.returncode == 0 and wait_port(port, up=True, timeout=20)

    # 运行中换供应商：claude 被改成陌生地址 B → refresh 吸收（快照更新为 B）
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://B.example/api"}}), encoding="utf-8")
    data = envelope_of(run_cli(["refresh", "--json"], cfg_dir, home))["data"]
    assert any(a["type"] == "absorb" and a["harness"] == "claude" for a in data["actions"])

    assert run_cli(["stop"], cfg_dir, home).returncode == 0
    assert wait_port(port, up=False, timeout=20)
    assert read_harness_base_url(home, "claude") == "https://B.example/api"  # 最新快照，不是 ORIGIN
    assert read_harness_base_url(home, "codex") == ORIGIN  # 无吸收的 harness 仍按 .bak 还原原值
```

文件头若缺 `import json` / `free_port` / `write_proxy_json` 导入则补上。

- [ ] **Step 6: 全量回归 + lint + commit**

Run: `.venv/Scripts/python -m pytest -q`（≥394）、`.venv/Scripts/python -m ruff format vision_relay tests && .venv/Scripts/python -m ruff check vision_relay tests`

```bash
git add vision_relay/wiring.py vision_relay/cli.py tests/test_proxy_wiring.py tests/test_proxy_cli.py tests/test_e2e_g2_routing.py
git commit -m "fix(wiring,cli): stop restores by latest takeover snapshot; .bak fallback for harnesses without snapshot (spec §5)"
```

---

### Task 2: PID 复用防护——pid 文件携带进程身份指纹（偏离⑤方案 A）

**Files:**
- Create: `vision_relay/pid_util.py`
- Modify: `vision_relay/cli.py`（`_write_pid`/`cmd_start`/`cmd_stop`/`cmd_status` 改走 pid_util；保留 `_pid_running` 薄包装）
- Modify: `vision_relay/reconcile.py:89-129`（`_service_alive` 带 token 校验；`_pid_alive` 变薄包装）
- Test: `tests/test_proxy_pid_util.py`（新）
- Test: `tests/test_integration_m1.py`（新增 TestPidReuseHardening）

- [ ] **Step 1: 写失败测试（新文件 `tests/test_proxy_pid_util.py`）**

```python
"""pid_util: pid 文件携带进程身份指纹，防 Windows PID 复用误报/误杀（2026-08-23 决策⑤）。"""

import json
import os

from vision_relay import pid_util


def test_process_token_of_self_is_stable_nonzero():
    token = pid_util.process_token(os.getpid())
    assert token is not None and token > 0
    assert pid_util.process_token(os.getpid()) == token  # 同进程指纹稳定


def test_process_token_of_dead_pid_is_none():
    assert pid_util.process_token(99999999) is None


def test_write_and_read_roundtrip(tmp_path):
    path = str(tmp_path / "proxy.pid")
    pid_util.write_pid_file(path)
    pid, token = pid_util.read_pid_file(path)
    assert pid == os.getpid()
    assert token == pid_util.process_token(os.getpid())


def test_read_legacy_bare_int_file(tmp_path):
    """老格式纯数字 pid 文件 → (pid, None)，退回仅存活检查（迁移期兼容）。"""
    path = str(tmp_path / "proxy.pid")
    open(path, "w").write("4242")
    assert pid_util.read_pid_file(path) == (4242, None)


def test_read_missing_file(tmp_path):
    assert pid_util.read_pid_file(str(tmp_path / "nope.pid")) == (-1, None)


def test_pid_is_ours_token_mismatch_is_not_ours():
    # os.getpid() 一定活着，但指纹对不上 → 不是我们的进程（复用场景）
    assert pid_util.pid_is_ours(os.getpid(), token=12345) is False
    assert pid_util.pid_is_ours(os.getpid(), token=None) is True  # 老文件：仅存活检查
    assert pid_util.pid_is_ours(99999999, token=None) is False
```

Run: `.venv/Scripts/python -m pytest tests/test_proxy_pid_util.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 2: 实现 `vision_relay/pid_util.py`**

```python
"""PID file with process-identity token (Windows PID-reuse hardening, 2026-08-23 决策⑤).

Windows PID 池小、复用积极：残留 pid 文件撞上复用进程会让 status 误报“服务在跑”、
让 stop 误杀无辜进程。pid 文件因此从“只存 pid”升级为 JSON {pid, token}：
token 是进程创建时间指纹（Windows=CreationTime FILETIME ticks；POSIX=/proc/<pid>/stat
的 starttime jiffies），同一次启动内稳定、不同进程必然不同。老格式纯数字文件读作
(pid, None)：token 缺失时退回仅存活检查（迁移期兼容，不改变既有行为）。
"""

from __future__ import annotations

import ctypes
import json
import os


def default_pid_path() -> str:
    from .env_util import config_dir

    return os.path.join(config_dir(), "proxy.pid")


def process_token(pid: int) -> int | None:
    """进程身份指纹；拿不到（进程不存在/权限/平台不支持）返回 None。"""
    if os.name == "nt":

        class _FT(ctypes.Structure):
            _fields_ = [("lo", ctypes.c_ulong), ("hi", ctypes.c_ulong)]

        class _ST(ctypes.Structure):
            _fields_ = [("creation", _FT), ("exit", _FT), ("kernel", _FT), ("user", _FT)]

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            st = _ST()
            if not ctypes.windll.kernel32.GetProcessTimes(handle, ctypes.byref(st)):
                return None
            return st.creation.hi << 32 | st.creation.lo
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            # comm 字段可含空格/括号：从最后一个 ')' 之后取字段；starttime 是整体第 22 域
            rest = f.read().rsplit(")", 1)[1].split()
            return int(rest[19])
    except (OSError, ValueError, IndexError):
        return None


def pid_alive(pid: int) -> bool:
    """跨平台存活判定（沿用 reconcile._pid_alive 的健壮实现：Windows GetExitCodeProcess，
    Unix 信号 0 且 EPERM=活着）。"""
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == 259  # STILL_ACTIVE
            return False
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True  # EPERM＝进程存在但属他人
    except OSError:
        return False


def pid_is_ours(pid: int, token: int | None) -> bool:
    """pid 活着 且（有 token 时指纹匹配）。token=None（老文件/取不到）退回仅存活检查。"""
    if not pid_alive(pid):
        return False
    if token is None:
        return True
    actual = process_token(pid)
    return actual is not None and actual == token


def write_pid_file(path: str | None = None) -> None:
    path = path or default_pid_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pid = os.getpid()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pid": pid, "token": process_token(pid)}, f)


def read_pid_file(path: str | None = None) -> tuple[int, int | None]:
    """返回 (pid, token)。文件缺失/损坏 → (-1, None)；老格式纯数字 → (pid, None)。"""
    path = path or default_pid_path()
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError:
        return -1, None
    if raw.startswith("{"):
        try:
            d = json.loads(raw)
            return int(d["pid"]), d.get("token")
        except (ValueError, KeyError, TypeError):
            return -1, None
    try:
        return int(raw), None
    except ValueError:
        return -1, None
```

Run: `.venv/Scripts/python -m pytest tests/test_proxy_pid_util.py -q` → PASS

- [ ] **Step 3: cli.py 接线（保留 `_pid_running` 供既有测试 monkeypatch）**

3a. 头部 import 补 `from . import pid_util`。`_write_pid` 改为委托：

```python
def _write_pid() -> None:
    pid_util.write_pid_file(_pid_path())
```

3b. `_pid_running` 改为薄包装（签名/语义不变，既有 monkeypatch 继续生效）：

```python
def _pid_running(pid: int) -> bool:
    return pid_util.pid_alive(pid)
```

新增 token 感知的判定（alive 部分走 `_pid_running`，保持可 patch）：

```python
def _pid_matches_ours(pid: int, token: int | None) -> bool:
    if not _pid_running(pid):
        return False
    if token is None:
        return True
    actual = pid_util.process_token(pid)
    return actual is not None and actual == token
```

3c. `cmd_start` 的既有 pid 检查段改为：

```python
    pid, token = pid_util.read_pid_file(_pid_path())
    if pid != -1:
        if _pid_matches_ours(pid, token):
            print(f"already running (pid {pid})")
            return 1
        try:
            os.unlink(_pid_path())
        except OSError:
            pass
```

（原 `int(open(...))` + `_pid_running(pid)` 两段删除。）

3d. `cmd_stop` 开头改为（复用/死亡都不杀，只清文件）：

```python
def cmd_stop() -> int:
    pid, token = pid_util.read_pid_file(_pid_path())
    if pid == -1 or not _pid_matches_ours(pid, token):
        try:
            os.unlink(_pid_path())
        except OSError:
            pass
        print("not running")
        return 1
    if not _terminate(pid):
        print(f"cannot stop {pid}")
        return 1
```

（原 try/int/`_pid_running` 三段删除；后续 terminate/unlink/还原/意图逻辑不动。）

3e. `cmd_status`（人类输出）改为：

```python
def cmd_status() -> int:
    pid, token = pid_util.read_pid_file(_pid_path())
    if pid != -1 and _pid_matches_ours(pid, token):
        print(f"running (pid {pid})")
        return 0
    print("not running")
    return 1
```

- [ ] **Step 4: reconcile.py 接线**

头部补 `from . import pid_util`。`_pid_alive` 改为 `return pid_util.pid_alive(pid)`（函数体其余删；测试直接调它断言真实进程，语义不变）。`_service_alive` 的 pid 段改为：

```python
    pid, token = pid_util.read_pid_file()
    if pid == -1:
        return False
    if token is None:
        return _pid_alive(pid)
    actual = pid_util.process_token(pid)
    return _pid_alive(pid) and actual is not None and actual == token
```

（删除原 `open(pid_file)` + `int(...)` 段；`proxy.pid` 路径统一由 pid_util 解析。）

- [ ] **Step 5: 集成测试（追加到 `tests/test_integration_m1.py`，文件头补 `import subprocess, sys`——已有则跳过）**

```python
class TestPidReuseHardening:
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
            assert "token" in json.loads((cfg_dir / "proxy.pid").read_text(encoding="utf-8"))  # 新格式
            os.kill(pid, 9)
            assert wait_port(port, up=False, timeout=20)

            dummy = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                # 模拟 PID 复用：pid 文件指向活着的 dummy，但指纹是别人的
                (cfg_dir / "proxy.pid").write_text(
                    json.dumps({"pid": dummy.pid, "token": 1234567890}), encoding="utf-8")
                d = envelope_of(run_cli(["status", "--json"], cfg_dir, home))["data"]
                assert d["service_alive"] is False  # 不误报
                proc = run_cli(["stop"], cfg_dir, home)
                assert proc.returncode == 1 and "not running" in proc.stdout  # 不误杀路径
                assert dummy.poll() is None  # dummy 还活着！
            finally:
                dummy.kill()
                dummy.wait()
        finally:
            run_cli(["stop"], cfg_dir, home, timeout=60)
```

（文件头需要 `import os`——test_integration_m1.py 已有。）

- [ ] **Step 6: 全量回归 + lint + commit**

Run: `.venv/Scripts/python -m pytest -q`（≥ 上一任务后的数量 + 7）；`ruff format/check`。

```bash
git add vision_relay/pid_util.py vision_relay/cli.py vision_relay/reconcile.py tests/test_proxy_pid_util.py tests/test_integration_m1.py
git commit -m "feat(pid): pid file carries process-identity token — status/stop verify before kill (Windows PID-reuse hardening)"
```

---

### Task 3: status 契约增量——harnesses.*.config_path + 顶层 bind_port（偏离③/⑥c 的核心侧）

**Files:**
- Modify: `vision_relay/reconcile.py`（`observe` 加两字段）
- Test: `tests/test_proxy_reconcile.py`（observe 断言扩展）

- [ ] **Step 1: 写失败测试（找到 `tests/test_proxy_reconcile.py` 里现有 observe 用例类；若没有独立 observe 断言类，在文件末尾追加）**

```python
class TestObserveContractFields:
    def test_harness_rows_expose_config_path_and_bind_port(self, env):
        home, cfgdir = env
        cfg = ProxyConfig()
        obs = reconcile.observe(cfg, tool_states=[])
        assert obs["bind_port"] == cfg.bind_port
        row = obs["harnesses"]["claude"]
        assert row["config_path"].endswith(os.path.join(".claude", "settings.json"))
        assert row["config_path"].startswith(str(home))  # 隔离 home 生效（HOME 被 monkeypatch）
```

Run: `.venv/Scripts/python -m pytest tests/test_proxy_reconcile.py -q -k ObserveContractFields`
Expected: FAIL（`bind_port`/`config_path` 键不存在）

- [ ] **Step 2: 实现（`reconcile.observe`）**

`harness_rows[name] = {...}` 字典里加一行 `"config_path": p`（`p` 已在本循环内：`p = wiring._path(HOME, name)`）；返回的顶层字典加 `"bind_port": cfg.bind_port`。即：

```python
        harness_rows[name] = {
            "base_url": cur,
            "ownership": wiring.classify_base_url(cur, cfg.bind_port),
            "has_snapshot": name in snapshot.load(),
            "config_exists": exists,
            "config_path": p,  # GUI 详情抽屉「配置文件」入口（2026-08-23 决策③）
        }
    return {
        "service_alive": _service_alive(cfg),
        "bind_port": cfg.bind_port,  # GUI 拓扑卡/横幅不再硬编码 8787（决策⑥c）
        "harnesses": harness_rows,
        ...
```

（只读增量字段，contract_version 维持 1；前端 `parseEnvelope` 已容忍多余字段。）

- [ ] **Step 3: 集成断言（`tests/test_integration_m1.py::TestCliSubprocessVerbs::test_status_envelope_and_stderr_clean` 追加）**

```python
        assert data["data"]["bind_port"] == 8787
        assert data["data"]["harnesses"]["claude"]["config_path"].endswith(
            os.path.join(".claude", "settings.json")
        )
```

（文件头需 `import os`——已有。）

- [ ] **Step 4: 全量回归 + lint + commit**

Run: `.venv/Scripts/python -m pytest -q`；`ruff format/check`。

```bash
git add vision_relay/reconcile.py tests/test_proxy_reconcile.py tests/test_integration_m1.py
git commit -m "feat(verbs): status exposes harness config_path and top-level bind_port (additive, contract v1)"
```

---

### Task 4: GUI——详情抽屉配置路径+打开入口、拓扑卡/横幅用 bind_port、Rust open_path（偏离③/⑥c 的壳侧）

**Files:**
- Modify: `gui/src-tauri/src/lib.rs`（新增 `open_path` 命令并注册）
- Modify: `gui/src/core.ts`（新增 `openPath`）
- Modify: `gui/src/shell/useStatus.ts`（类型：`bind_port`、`HarnessRow.config_path`）
- Modify: `gui/src/pages/Overview.tsx`（横幅/chainHops 用 `s.bind_port`；抽屉加路径行+打开按钮）

- [ ] **Step 1: Rust `open_path`（`lib.rs`，`start_core_detached` 之后追加并注册）**

```rust
#[tauri::command]
fn open_path(path: String) -> Result<(), String> {
    // 用系统默认程序打开文件/定位（2026-08-23 决策③：只到文件，不做行号）
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        std::process::Command::new("cmd")
            .args(["/C", "start", "", &path])
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(not(windows))]
    {
        let opener = if cfg!(target_os = "macos") { "open" } else { "xdg-open" };
        std::process::Command::new(opener).arg(&path).spawn().map_err(|e| e.to_string())?;
    }
    Ok(())
}
```

`invoke_handler` 改为 `generate_handler![which_core, run_core, start_core_detached, open_path]`。

Run: `cd gui/src-tauri && cargo check` → Finished。

- [ ] **Step 2: `core.ts` 末尾追加**

```ts
export async function openPath(p: string): Promise<void> {
  // 系统默认程序打开（配置文件入口，spec §6 详情抽屉）；非核心 CLI 动词，直接 invoke
  await invoke("open_path", { path: p });
}
```

- [ ] **Step 3: `useStatus.ts` 类型**

`HarnessRow` 增加 `config_path: string;`；`StatusData` 顶层增加 `bind_port: number;`。

- [ ] **Step 4: `Overview.tsx`**

4a. 头部 `import { core } from "../core";` 改为 `import { core, openPath } from "../core";`。

4b. 横幅硬编码：`127.0.0.1:8787 · 自动对账中` → `127.0.0.1:{s.bind_port} · 自动对账中`。

4c. `chainHops(s.harnesses[h], h, tool, s.routing_on && s.service_alive, 8787)` 最后实参 `8787` → `s.bind_port`。

4d. 抽屉表格第一行前插入（`接管快照` 行之前）：

```tsx
<tr><td className="dim small">配置文件</td><td className="small mono">
  {s.harnesses[h].config_path ?? "—"}{" "}
  {s.harnesses[h].config_path && (
    <button className="btn" onClick={() => openPath(s.harnesses[h].config_path).catch((e) => window.alert(String(e)))}>打开</button>
  )}
</td></tr>
```

- [ ] **Step 5: 验证 + commit**

Run: `pnpm -C gui test`（25 passed 不减）、`pnpm -C gui build`（tsc 零错误）、`cd gui/src-tauri && cargo check`。

```bash
git add gui/src-tauri/src/lib.rs gui/src/core.ts gui/src/shell/useStatus.ts gui/src/pages/Overview.tsx
git commit -m "feat(gui): detail drawer shows config path with system-open entry; overview uses bind_port from status"
```

---

### Task 5: 事件导出——verbs limit(0=全量) + 前端 JSONL 下载（偏离④方案 A）

**Files:**
- Modify: `vision_relay/reconcile.py:71-83`（`tail_events` 支持 0/None=全量）
- Modify: `vision_relay/verbs.py`（`events` 参数 `tail`→`limit`）
- Modify: `vision_relay/cli.py`（`events` 子命令加 `--limit`；main 分发传参）
- Test: `tests/test_proxy_verbs.py`（追加 TestEventsLimit）
- Modify: `gui/src/pages/Events.tsx`（导出按钮）

- [ ] **Step 1: 写失败测试（追加到 `tests/test_proxy_verbs.py`）**

```python
class TestEventsLimit:
    def test_limit_slices_and_zero_returns_all(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        from vision_relay import reconcile

        for i in range(3):
            reconcile.append_event("reclaim", "codex", {"i": i})
        assert len(verbs.events(ProxyConfig(), limit=2)["data"]) == 2  # 最近 2 条
        assert len(verbs.events(ProxyConfig(), limit=0)["data"]) == 3  # 0 = 全量（导出用）
        assert verbs.events(ProxyConfig(), limit=2)["data"][-1]["i"] == 2
```

Run: `.venv/Scripts/python -m pytest tests/test_proxy_verbs.py -q -k EventsLimit`
Expected: FAIL（`events() got an unexpected keyword argument 'limit'`）

- [ ] **Step 2: 实现**

`reconcile.tail_events`：

```python
def tail_events(n: int = 50) -> list[dict]:
    try:
        with open(_events_path(), encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    selected = lines if n is None or n <= 0 else lines[-n:]  # 0/None = 全量（导出，2026-08-23 决策④）
    out = []
    for line in selected:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
```

`verbs.events`：

```python
def events(cfg: ProxyConfig, limit: int = 50) -> dict:
    return envelope(True, _tail_events(limit))
```

`cli.parse_args`：`events` 子命令改为带参注册：

```python
    ev = sub.add_parser("events", parents=[common])  # M1: 事件日志 tail（--limit 0 = 全量导出）
    ev.add_argument("--limit", type=int, default=50)
```

`cli.main()` 的 `--json` 分发处，`kw` 构造改为：

```python
            kw = {"harness": getattr(args, "harness", None)} if args.command == "visionlog" else {}
            if args.command == "events":
                kw = {"limit": getattr(args, "limit", 50)}
```

- [ ] **Step 3: 前端导出按钮（`Events.tsx`）**

头行按钮区（`<select>` 同级）加：

```tsx
        <button className="btn" onClick={exportAll}>⬇ 导出</button>
```

组件内加：

```tsx
  const exportAll = async () => {
    try {
      const rows = await core<EventRow[]>("events", { args: ["--limit", "0"] });
      const blob = new Blob([rows.map((r) => JSON.stringify(r)).join("\n") + "\n"], { type: "application/x-ndjson" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `vision-relay-events-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.jsonl`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      window.alert(String(e));
    }
  };
```

- [ ] **Step 4: 全量回归 + lint + commit**

Run: `pytest -q`；`pnpm -C gui test` + `pnpm -C gui build`；`ruff format/check`。

```bash
git add vision_relay/reconcile.py vision_relay/verbs.py vision_relay/cli.py tests/test_proxy_verbs.py gui/src/pages/Events.tsx
git commit -m "feat(verbs,gui): events export — --limit N (0=all) verb flag + JSONL blob download"
```

---

### Task 6: probe 无结论改 ok:result=null；事件筛选收敛（偏离⑥b/⑥a）

**Files:**
- Modify: `vision_relay/verbs.py`（`probe_one`）
- Test: `tests/test_proxy_verbs.py`（TestProbeJson 扩展）
- Modify: `gui/src/pages/Events.tsx`（TYPES 删两项）

- [ ] **Step 1: 写失败测试（`TestProbeJson` 内追加用例）**

```python
    def test_inconclusive_is_ok_with_null_result(self, tmp_path, monkeypatch):
        """含糊不下结论是合法结果（spec §5），不是错误：ok 恒 True，GUI 重测静默显示“未测”。"""
        monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(verbs, "_run_probe", lambda cfg, h, p, m, tb=None: None)
        out = verbs.probe_one(ProxyConfig(), harness="claude", provider="p", model="m")
        assert out["ok"] is True and out["data"]["result"] is None
```

Run: `.venv/Scripts/python -m pytest tests/test_proxy_verbs.py -q -k inconclusive`
Expected: FAIL（`ok` 为 False）

- [ ] **Step 2: 实现（`verbs.probe_one`）**

```python
def probe_one(cfg: ProxyConfig, harness: str, provider: str, model: str) -> dict:
    result = _run_probe(cfg, harness, provider, model)
    # 无结论（None）= 含糊不下结论（spec §5 合法三态），不是错误；GUI 重测后显示“未测”
    return envelope(True, {"result": result})
```

- [ ] **Step 3: `Events.tsx` TYPES 收敛（永不产生的事件类型不进筛选）**

```ts
const TYPES = ["all", "reclaim", "absorb", "auto_fix", "auto_annotate", "relay_added"];
```

- [ ] **Step 4: 回归 + commit**

Run: `pytest -q`；`pnpm -C gui test` + `build`；`ruff format/check`。

```bash
git add vision_relay/verbs.py tests/test_proxy_verbs.py gui/src/pages/Events.tsx
git commit -m "feat(verbs): probe --json treats inconclusive as ok result null; drop never-emitted event filter options"
```

---

### Task 7: spec 修订 + CHANGELOG（决策②为纯文档项，在此落地）

**Files:**
- Modify: `docs/superpowers/specs/2026-08-21-vision-relay-phase2-control-plane-design.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: spec §5「备份、快照与回退语义」的“备份”条目句尾追加**

> ；正常 stop 的主还原依据是接管组合快照（最新），.bak 仅在该 harness 快照缺失时兜底（2026-08-23 决策）。

- [ ] **Step 2: spec §5「可验证探针」条目句尾追加**

> ；探针只在该 (provider, 模型) 组合首次出现时自动执行一次，结果作为待确认值推给用户（向导②/模型能力页）——用户过目后无论原样保存还是修改，即为用户意图（source=user），此后不再被自动探测覆盖；重测仅由用户显式触发（2026-08-23 决策）。

- [ ] **Step 3: spec §6 总览③ 的「配置文件路径与行」改为**

> 配置文件路径（含系统打开入口；不做行号定位——2026-08-23 收窄）

- [ ] **Step 4: spec §13 决策记录表追加三行**

```markdown
| stop 还原依据（2026-08-23） | 统一为最新接管组合快照；快照缺失的 harness 退回第一次接管前的整文件 .bak 兜底 |
| 探测节奏与过目语义（2026-08-23） | 探针只在新组合首次出现时自动跑一次并把待确认结果推给用户；向导/模型页的过目（保存或修改）即为用户意图（user），此后自动标注不再覆盖；重测是显式动作 |
| 配置文件定位（2026-08-23） | 详情抽屉提供配置文件路径与系统打开入口；不做行号定位（收窄 §6 原表述） |
```

- [ ] **Step 5: CHANGELOG `Unreleased` 追加**

`### Fixed` 下：

```markdown
- 正常 stop 的还原依据统一为最新接管组合快照（运行中吸收过新供应商时不再回跳最早的原始地址）；快照缺失的 harness 退回第一次接管前的整文件备份兜底。
- pid 文件升级为 `{pid, token}`（进程创建时间指纹）：Windows PID 复用不再导致 status 误报“运行中”、stop 误杀无关进程；老格式纯数字文件保持兼容。
```

`### Added` 下：

```markdown
- `status --json` 暴露每个 harness 的 `config_path` 与顶层 `bind_port`；GUI 详情抽屉提供配置文件路径与系统打开入口，总览不再硬编码 8787。
- 事件日志页「导出」：`events --json --limit 0` 拉全量并下载 JSONL。
- `probe --json` 无结论（含糊不下结论）改返回 `ok:true, result:null`（合法三态而非错误）。
```

- [ ] **Step 6: 最终全量验证 + commit**

Run: `.venv/Scripts/python -m pytest -q`（不低于 405）、`.venv/Scripts/python -m ruff format --check . && .venv/Scripts/python -m ruff check .`、`pnpm -C gui test && pnpm -C gui build`、`cd gui/src-tauri && cargo check`。

```bash
git add docs/superpowers/specs/2026-08-21-vision-relay-phase2-control-plane-design.md CHANGELOG.md
git commit -m "docs: spec decision records + wording alignment (stop-by-snapshot, probe-once/confirm semantics, config path scope), CHANGELOG"
```

---

## 附录：执行纪律（给子代理）

1. **严格 TDD 顺序**；每个 checkbox 打勾前必须真的跑过命令并核对预期输出。
2. **尊重 spec 与 AGENTS.md**：fail-open / GUI 不解析配置 / 不写工具配置 / 输出无 key 不可破坏；与计划冲突的现场事实（签名/引用对不上）以现状为准做等价调整并在 commit message 注明，**不许放宽既有测试**。
3. **每 Task 一 commit**（计划已给 message）；不许多 Task 混一个 commit，不许跳测试。
4. 门禁（Windows / Git Bash）：`python -m pytest -q` 用 `.venv/Scripts/python -m pytest -q`；每 Task 后全绿才算完成：pytest ≥ 基线 + ruff format/check + `pnpm -C gui test`/`build`（涉 Rust 时 `cargo check`）。
5. 集成/E2E 测试全部走 `tests/integration_helpers.py`（假 home + tmp config dir），**绝不允许测试触碰真实 `~/` 或真实 `~/.vision-relay`**。
6. 决策②（探测一次+过目即用户意图）是**纯文档语义**，本计划不改任何相关代码——现有行为即用户拍板的正确行为。
