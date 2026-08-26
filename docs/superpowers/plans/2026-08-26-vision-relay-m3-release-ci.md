# vision-relay M3 收尾（三平台打包发布 CI + 留存清理 + Minor 清账）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 v1.0.0——手动触发的三平台打包发布流水线（Windows NSIS / macOS DMG / Linux AppImage+deb，内嵌 PyInstaller 冻结核心）+ 留存清理生效 + Minor M1–M8 清账 + README/CHANGELOG/截图/模板 + 云端实发布。

**Architecture:** 冻结核心以 onedir 目录作为 Tauri resources 随 GUI 壳同包分发；`release.yml` 由 workflow_dispatch 触发，三平台矩阵「版本注入→冻结→冒烟→tauri build→上传」，publish job 汇总建 Draft Release；GUI 壳找核心顺序 = 用户显式路径 → 包内冻结核心 → PATH。

**Tech Stack:** PyInstaller（onedir）、Tauri 2 + pnpm 11 + Node 22 + Rust stable、uv + Python 3.12、GitHub Actions（pin SHA）、Pillow/mss/pygetwindow（图标转换与截图）。

**Spec:** `docs/superpowers/specs/2026-08-26-vision-relay-m3-release-ci-design.md`（本计划实现其全部 R1–R8 / D1–D9 / A1–A10）。

**本地环境前置:** python（3.10+）、pnpm 11、Rust toolchain、`gh` 已登录、`git`。Pillow 按需 `python -m pip install pillow`。

**执行顺序:** 严格按 Task 编号（依赖递进：清账→后端→脚本→图标→壳→CI→文档→截图→模板→发布）。每个 Task 结尾都有门禁 + commit 步骤。

---

## 文件结构总览

| 动作 | 文件 | 职责 |
|---|---|---|
| 修改 | `vision_relay/server.py` | M6 注释；留存清理线程接线 |
| 修改 | `vision_relay/verbs.py` | M7 zcode 探测 reason 文案 |
| 修改 | `vision_relay/wiring.py` | M5 空壳 `zcode:{}` 清理 |
| 修改 | `gui/src/pages/Overview.tsx` | M1 重启失败反馈 |
| 修改 | `gui/src/pages/Settings.tsx` | M4 保留勾选回滚 |
| 定位+修改 | M3 站点（见 Task 8） | 无 baseURL 供应商统计 |
| 新建 | `scripts/set_version.py` | 版本号三处对齐 |
| 新建 | `scripts/freeze_entry.py` | PyInstaller 入口（包内绝对导入） |
| 修改 | `gui/src-tauri/tauri.conf.json` | bundle 打开 + icon + resources |
| 修改 | `gui/src-tauri/src/lib.rs` | which_core 包内核心探测 |
| 生成 | `gui/src-tauri/icons/*`、`gui/app-icon.png` | 全套图标 |
| 修改 | `.github/workflows/ci.yml` | 新增 gui 门禁 job |
| 新建 | `.github/workflows/release.yml` | 发布流水线 |
| 修改 | `.gitignore`、`CHANGELOG.md`、`README.md`、`README.zh.md`、`.github/ISSUE_TEMPLATE/bug_report.yml`、`.github/PULL_REQUEST_TEMPLATE.md` | 配套 |
| 新建 | `docs/screenshots/*.png`、`tests/test_set_version.py` 等 | 截图与测试 |

---

### Task 1: 入库工作区已完成的 suppressed_relays 修复（spec D7 前置）

**Files:**
- Modify（已在工作区改完，只跑门禁与提交）: `vision_relay/server.py`、`vision_relay/verbs.py`、`conftest.py`、`tests/test_proxy_server.py`、`gui/src/lib/chain.ts`、`gui/src/lib/chain.test.ts`、`gui/src/pages/Overview.tsx`、`gui/src/pages/Overview.test.tsx`、`gui/src/pages/Settings.test.tsx`、`docs/superpowers/specs/2026-08-21-vision-relay-phase2-control-plane-design.md`、`docs/superpowers/specs/2026-08-26-zcode-harness-design.md`

- [ ] **Step 1: 全量门禁**

```bash
cd /e/LLMproject/Github/vision-relay
python -m pytest -q && ruff format --check . && ruff check .
```
Expected: `548 passed, 1 skipped`（或更多，含新增 129 行测试）、ruff 两项无输出（干净）。

- [ ] **Step 2: GUI 侧门禁（工作区含 gui 改动）**

```bash
cd gui && pnpm test && pnpm build && cd ..
```
Expected: vitest 全绿、`tsc --noEmit && vite build` 无错误。

- [ ] **Step 3: 提交（含上述全部已修改文件，一个提交）**

```bash
git add vision_relay/server.py vision_relay/verbs.py conftest.py tests/test_proxy_server.py \
  gui/src/lib/chain.ts gui/src/lib/chain.test.ts gui/src/pages/Overview.tsx \
  gui/src/pages/Overview.test.tsx gui/src/pages/Settings.test.tsx \
  docs/superpowers/specs/2026-08-21-vision-relay-phase2-control-plane-design.md \
  docs/superpowers/specs/2026-08-26-zcode-harness-design.md
git commit -m "fix(server): 停用转发全层生效——suppressed_relays 命中条目选路①②③层全部不可见（spec §7.5 收严）;配套测试与 GUI 链路展示"
```

---

### Task 2: M8 — `.gitignore` 补 `tmp_icons/`（ruff 恒红根因）

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: 追加条目**

在 `.gitignore` 末尾追加：

```gitignore
# M8: 图标设计草稿（tmp_icons/ 全仓 ruff 恒红根因）
tmp_icons/
gui/app-icon.png.jpg
gui/src-tauri/resources/
freeze-dist/
freeze-build/
```
（后三行为 Task 11/14 的构建产物预防性忽略。）

- [ ] **Step 2: 验证 ruff 干净**

```bash
ruff check . && ruff format --check .
```
Expected: 两项均无输出。

- [ ] **Step 3: 提交**

```bash
git add .gitignore && git commit -m "chore: gitignore 补 tmp_icons/(M8 ruff 恒红)+图标源/冻结产物/打包资源目录"
```

---

### Task 3: M6 — server.py 注释引错 spec 章节

**Files:**
- Modify: `vision_relay/server.py:68`

- [ ] **Step 1: 修正引用**

`_select_relay` docstring 首行（zcode spec §6 无 §6.3 子节，选路三层实际是 §6 的第 1/2/6 条）：

```text
旧: """按 spec §6.3 选 relay：①(模型,协议,密钥指纹)精确 → ②(模型,协议)顺序 → ③仅协议 → 默认。
新: """按 spec §6（第1/2/6条）选 relay：①(模型,协议,密钥指纹)精确 → ②(模型,协议)顺序 → ③仅协议 → 默认。
```

- [ ] **Step 2: 快速回归 + 提交**

```bash
python -m pytest tests/test_proxy_server.py -q && ruff check .
git add vision_relay/server.py && git commit -m "docs: 修正选路注释的 spec 章节引用(M6,§6.3 不存在)"
```
Expected: server 测试全绿。

---

### Task 4: M7 — zcode 探测无目标 reason 文案（TDD）

**Files:**
- Modify: `vision_relay/verbs.py`（`probe_target_info`，当前 L537-563）
- Test: `tests/test_proxy_zcode.py`（追加）

- [ ] **Step 1: 写失败测试（文件末尾追加）**

```python
def test_probe_reason_zcode_accurate(monkeypatch):
    """M7: zcode 无目标的 reason 不能再说「路由工具不在线」——zcode 没有路由工具概念。"""
    from vision_relay import model_sources, verbs
    from vision_relay.config import ProxyConfig

    monkeypatch.setattr(model_sources, "zcode_probe_target", lambda cfg, provider: ("", "", "chat"))
    _base, _key, _proto, reason = verbs.probe_target_info(ProxyConfig(), "zcode", "ghost-provider", {})
    assert reason is not None and reason.startswith("zcode:")
    assert "路由工具" not in reason
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_proxy_zcode.py::test_probe_reason_zcode_accurate -q
```
Expected: FAIL——现文案为 `"zcode: 路由工具不在线,且未配置可探测的直连上游"`，含「路由工具」。

- [ ] **Step 3: 最小实现**

`vision_relay/verbs.py` 的 `probe_target_info` 中，把：

```python
    base, key, proto = probe_target_for(cfg, harness, provider, tool_by_name)
    if base:
        return base, key, proto, None
    return base, key, proto, f"{harness}: 路由工具不在线,且未配置可探测的直连上游"
```
改为：

```python
    base, key, proto = probe_target_for(cfg, harness, provider, tool_by_name)
    if base:
        return base, key, proto, None
    if harness == "zcode":  # M7: zcode 无路由工具，无目标=找不到该供应商的原始上游
        return base, key, proto, f"{harness}: 未找到该供应商的原始上游（供应商不存在或未接管）"
    return base, key, proto, f"{harness}: 路由工具不在线,且未配置可探测的直连上游"
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

```bash
python -m pytest tests/test_proxy_zcode.py -q && python -m pytest -q
```
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add vision_relay/verbs.py tests/test_proxy_zcode.py
git commit -m "fix(verbs): zcode 探测无目标 reason 文案与实际原因对齐(M7)——不再误称路由工具不在线"
```

---

### Task 5: M5 — 模态门还原后模型级 `zcode:{}` 空壳残留（TDD）

**Files:**
- Modify: `vision_relay/wiring.py`（`_restore_zcode_providers`，当前 L298-303）
- Test: `tests/test_proxy_zcode.py`（追加；门/还原函数为 `_rewrite_zcode_providers` / `_restore_zcode_providers`）

- [ ] **Step 1: 写失败测试（文件末尾追加；fixture 结构参考本文件既有 zcode 配置测试）**

```python
def test_restore_removes_empty_zcode_shell(tmp_path):
    """M5: 还原后模型级 zcode:{} 空壳（开窗时 setdefault 创建）必须一并移除。"""
    from vision_relay import wiring

    cfg_path = tmp_path / "zcode.settings.json"
    cfg_path.write_text(
        '{"provider":{"demo-pid":{"kind":"anthropic","options":{"baseURL":"http://127.0.0.1:8787",'
        '"apiKey":"sk-fake"},"models":{"m1":{"modalities":{"input":["text"]}}}}}}',
        encoding="utf-8",
    )
    url_map, mod_map, _stats = wiring._rewrite_zcode_providers(str(cfg_path), "http://127.0.0.1:8787")
    assert mod_map, "前置：开窗应产生模态门记录"
    d = __import__("json").loads(cfg_path.read_text(encoding="utf-8"))
    assert d["provider"]["demo-pid"]["models"]["m1"].get("zcode") == {"modalitiesConfigured": True}

    wiring._restore_zcode_providers(str(cfg_path), "http://127.0.0.1:8787", url_map, mod_map)
    d = __import__("json").loads(cfg_path.read_text(encoding="utf-8"))
    m1 = d["provider"]["demo-pid"]["models"]["m1"]
    assert "zcode" not in m1, "M5: 还原后空壳 zcode:{} 必须移除"
    assert "image" not in m1["modalities"]["input"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_proxy_zcode.py::test_restore_removes_empty_zcode_shell -q
```
Expected: FAIL——`assert "zcode" not in m1`（残留 `{"modalitiesConfigured": …被 pop 后的 {}}`）。

- [ ] **Step 3: 最小实现**

`vision_relay/wiring.py` `_restore_zcode_providers` 中，把：

```python
                zc = m.get("zcode")
                if isinstance(zc, dict) and "flag" in rec:
                    if rec["flag"] == _MOD_ABSENT:
                        zc.pop("modalitiesConfigured", None)
                    else:
                        zc["modalitiesConfigured"] = rec["flag"]
```
改为：

```python
                zc = m.get("zcode")
                if isinstance(zc, dict) and "flag" in rec:
                    if rec["flag"] == _MOD_ABSENT:
                        zc.pop("modalitiesConfigured", None)
                        if not zc:  # M5: 空壳（开窗时 setdefault 创建）一并移除；非空=用户数据不动
                            m.pop("zcode", None)
                    else:
                        zc["modalitiesConfigured"] = rec["flag"]
```

- [ ] **Step 4: 回归 + 提交**

```bash
python -m pytest tests/test_proxy_zcode.py tests/test_proxy_wiring.py -q
git add vision_relay/wiring.py tests/test_proxy_zcode.py
git commit -m "fix(wiring): 模态门还原清除模型级 zcode:{} 空壳(M5)——非空(用户数据)不动"
```
Expected: 两文件测试全绿。

---

### Task 6: M1 — zcode-restart 失败无 UI 反馈（TDD）

**Files:**
- Modify: `gui/src/pages/Overview.tsx`（组件顶部状态区 + `zcode-restart-hint` 块，当前 L45-48）
- Test: `gui/src/pages/Overview.test.tsx`（追加；复用该文件既有的 props 构造与 `core` mock 方式）

- [ ] **Step 1: 写失败测试（文件末尾追加；props/mock 复用本文件现有写法，核心断言如下）**

```tsx
it("M1: zcode 重启失败时给出错误反馈（不再静默消失）", async () => {
  const { core } = await import("../core");
  (core as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ ok: false, restarted: false });
  // 用本文件既有方式渲染 <Overview p={...zcode_runtime:{needs_restart:true}...}/>，
  // 保证 data-testid="zcode-restart-hint" 可见（既有测试已有同款 props）。
  fireEvent.click(screen.getByRole("button", { name: /重启|restart/i }));
  await waitFor(() =>
    expect(screen.getByTestId("zcode-restart-err").textContent).toMatch(/重启失败/),
  );
});
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd gui && pnpm vitest run src/pages/Overview.test.tsx && cd ..
```
Expected: FAIL——`zcode-restart-err` 不存在。

- [ ] **Step 3: 最小实现**

`gui/src/pages/Overview.tsx`：
1) 组件状态区（`const s = p.status;` 之前）加：
```tsx
  const [restartErr, setRestartErr] = useState<string | null>(null);
```
2) `zcode-restart-hint` 块改为：
```tsx
      {s.zcode_runtime?.needs_restart && (
        <div className="alert-err row between" data-testid="zcode-restart-hint">
          <span>⚡ {t(p.lang, "zcodePendingRestart")}</span>
          <button className="btn" onClick={async () => {
            setRestartErr(null);
            const r = await core<{ ok: boolean }>("zcode-restart");
            if (!r.ok) setRestartErr("zcode 重启失败：已停止但未能拉起，请稍后重试或手动启动 zcode。"); // M1
            p.refresh();
          }}>{t(p.lang, "restartZcodeNow")}</button>
        </div>
      )}
      {restartErr && <div className="alert-err" data-testid="zcode-restart-err">{restartErr}</div>}
```

- [ ] **Step 4: 回归 + 提交**

```bash
cd gui && pnpm test && pnpm build && cd ..
git add gui/src/pages/Overview.tsx gui/src/pages/Overview.test.tsx
git commit -m "fix(gui): zcode 重启失败给出错误反馈条(M1)——kill 成功但拉起失败不再静默"
```

---

### Task 7: M4 — Settings「保留勾选」复选框不回滚（TDD）

**Files:**
- Modify: `gui/src/pages/Settings.tsx`（ZcodeDialog `onChoose`，当前 L354-358）
- Test: `gui/src/pages/Settings.test.tsx`（追加；复用既有 mock/渲染方式）

- [ ] **Step 1: 写失败测试（文件末尾追加，断言核心）**

```tsx
it("M4: 三选弹窗选「保留勾选」后 zcode 复选框回滚为勾选", async () => {
  // 用本文件既有方式渲染 Settings，前置：status.harnesses 含 zcode 且 zcode 运行中。
  const cb = screen.getByLabelText("zcode") as HTMLInputElement;
  fireEvent.click(cb);                      // 取消勾选
  fireEvent.click(screen.getByRole("button", { name: /保存设置/ }));
  fireEvent.click(await screen.findByRole("button", { name: "保留勾选" }));
  expect((screen.getByLabelText("zcode") as HTMLInputElement).checked).toBe(true);
});
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd gui && pnpm vitest run src/pages/Settings.test.tsx && cd ..
```
Expected: FAIL——复选框仍为未勾选。

- [ ] **Step 3: 最小实现**

`gui/src/pages/Settings.tsx` 的 `onChoose` 中，把：

```tsx
          onChoose={(kind) => {
            setZcodeDlg(false);
            if (kind === "abort") return;  // 保留勾选：仅关弹窗（用户可再勾回或自行再保存）
```
改为：

```tsx
          onChoose={(kind) => {
            setZcodeDlg(false);
            if (kind === "abort") {
              // M4: 保留勾选=放弃本次取消——复选框回滚到已保存值（弹窗只在取消勾选时出现，
              // 已保存值必含 zcode），否则 dirty 态残留、再保存会再弹窗
              setManaged((m) => (m.includes("zcode") ? m : [...m, "zcode"]));
              return;
            }
```

- [ ] **Step 4: 回归 + 提交**

```bash
cd gui && pnpm test && pnpm build && cd ..
git add gui/src/pages/Settings.tsx gui/src/pages/Settings.test.tsx
git commit -m "fix(gui): 「保留勾选」后 zcode 复选框回滚到已保存值(M4)——不再重复弹窗"
```

---

### Task 8: M3 — 无 baseURL 的供应商在统计里隐身

**Files:**
- 定位后修改（候选见 Step 1）+ 对应测试文件

- [ ] **Step 1: 定位隐身点（10 分钟上限，找到即停）**

```bash
# 候选①：GUI 派生层按 base_url 过滤
grep -rn "base_url\|baseURL" gui/src/lib/ gui/src/pages/ | grep -v test
# 候选②：verbs 聚合统计跳过空 base
grep -n "base_url" vision_relay/verbs.py
# 候选③：模型能力页分组/统计来源
grep -rn "统计\|group\|VLM 分组" gui/src/pages/Settings.tsx gui/src/pages/Models.tsx 2>/dev/null
```
判定标准：找到「`base_url` 为空 → continue/跳过/不渲染」的代码点（现象：cc-switch/codex++ 供应商档案里没填上游地址的条目在统计/分组里消失）。

- [ ] **Step 2: 写失败测试（按定位结果选择 pytest 或 vitest；断言形态）**

- 若过滤在 Python 侧（如 `model_sources.py` 行被丢弃）：pytest 追加——构造一条 `base_url=""` 的 `ProviderRow`，断言聚合输出**包含**该供应商（以空值/「未接线」呈现）。
- 若过滤在 GUI 侧：vitest 追加——mock 含空 baseURL 供应商的数据，断言该供应商**出现**在统计/分组里且显示占位文案（如「未接线」）。

- [ ] **Step 3: 实现（模式：不丢行，空值给占位）**

```python
# Python 侧模式（示意，以定位点实际结构为准）：
# 旧: if not row.base_url: continue
# 新: 保留行；渲染/透出时 base_url 为空 → GUI 显示「未接线」
```
```tsx
// GUI 侧模式（示意）：
// 旧: {p.base_url && <Row p={p}/>}
// 新: <Row p={p} sub={p.base_url || "未接线"}/>
```

- [ ] **Step 4: 回归 + 提交**

```bash
python -m pytest -q && cd gui && pnpm test && cd ..
git add -A && git commit -m "fix: 无 baseURL 的供应商纳入统计并以「未接线」呈现(M3)——不再隐身"
```
（若 Step 1 十分钟内无法定位，记档 `docs/history/` 一行说明 + 在 zcode 手工验收清单 M3 项标注「未定位」，**不得带此项进入发布**——发布前必须闭环或明确降级为已知问题记入 CHANGELOG。）

---

### Task 9: 留存清理生效（spec R4 / §7，TDD）

**Files:**
- Modify: `vision_relay/server.py`（`run_server` 尾部 + 新增两个函数）
- Test: `tests/test_proxy_server.py`（追加）

- [ ] **Step 1: 写失败测试（文件末尾追加）**

```python
def test_retention_once_removes_expired_and_keeps_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    from vision_relay import server, visionlog
    from vision_relay.config import ProxyConfig, VisionLogConfig

    d = tmp_path / "visionlog"
    d.mkdir()
    (d / "2020-01-01.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    (d / "2999-01-01.jsonl").write_text('{"x":2}\n', encoding="utf-8")
    cfg = ProxyConfig(vision_log=VisionLogConfig(enabled=True, retention_days=7))
    server._retention_once(cfg)
    assert not (d / "2020-01-01.jsonl").exists()
    assert (d / "2999-01-01.jsonl").exists()


def test_retention_disabled_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path))
    from vision_relay import server
    from vision_relay.config import ProxyConfig, VisionLogConfig

    d = tmp_path / "visionlog"
    d.mkdir()
    (d / "2020-01-01.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    server._retention_once(ProxyConfig(vision_log=VisionLogConfig(enabled=False)))
    assert (d / "2020-01-01.jsonl").exists()


def test_retention_worker_disabled_never_starts(tmp_path, monkeypatch):
    from vision_relay import server
    from vision_relay.config import ProxyConfig, VisionLogConfig

    called = []
    monkeypatch.setattr(server, "_retention_once", lambda cfg: called.append(1))
    server._start_retention_worker(ProxyConfig(vision_log=VisionLogConfig(enabled=False)))
    assert not called, "disabled 时不该有任何清理动作"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_proxy_server.py -q -k retention
```
Expected: FAIL——`server._retention_once` 不存在。

- [ ] **Step 3: 最小实现**

`vision_relay/server.py`：在 `run_server` 之前新增：

```python
def _retention_once(cfg: ProxyConfig) -> None:
    """识图留痕留存清理（M3-B，spec §7）：删过期日文件，清理量入事件日志。fail-open。"""
    if not cfg.vision_log.enabled:
        return
    from . import reconcile, visionlog  # 函数内导入，避免与 reconcile 的导入环

    try:
        removed = visionlog.cleanup(cfg.vision_log.retention_days)
        if removed:
            reconcile.append_event(
                "visionlog_cleanup", None,
                {"removed": removed, "retention_days": cfg.vision_log.retention_days},
            )
    except Exception:  # fail-open：清理绝不影响代理主链路
        pass


def _start_retention_worker(cfg: ProxyConfig) -> None:
    """启动即清一次，此后每 24h 重复；vision_log.enabled=false 时完全不起线程。"""
    if not cfg.vision_log.enabled:
        return

    def loop() -> None:
        _retention_once(cfg)
        while True:
            time.sleep(24 * 3600)
            _retention_once(cfg)

    threading.Thread(target=loop, name="visionlog-retention", daemon=True).start()
```
`run_server` 中 `_start_provider_cache_refresher(server)` 之后、`return server` 之前加一行：

```python
    _start_retention_worker(cfg)
```

- [ ] **Step 4: 回归 + 提交**

```bash
python -m pytest -q && ruff check . && ruff format --check .
git add vision_relay/server.py tests/test_proxy_server.py
git commit -m "feat(server): 识图留痕留存清理生效(M3-B)——启动即清+24h 周期,enabled=false 不起线程,fail-open,清理量入事件日志"
```

---

### Task 10: 版本对齐脚本 `scripts/set_version.py`（spec R3 / §6，TDD）

**Files:**
- Create: `scripts/set_version.py`
- Test: `tests/test_set_version.py`

- [ ] **Step 1: 写失败测试**

```python
"""set_version：一次写三处版本号（spec §6 版本一致性）。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "set_version.py"


def _make_root(tmp_path: Path) -> Path:
    (tmp_path / "vision_relay").mkdir()
    (tmp_path / "vision_relay" / "__init__.py").write_text(
        '__version__ = "0.1.0"\n', encoding="utf-8")
    (tmp_path / "gui" / "src-tauri").mkdir(parents=True)
    (tmp_path / "gui" / "src-tauri" / "tauri.conf.json").write_text(
        '{\n  "productName": "vision-relay",\n  "version": "0.1.0"\n}\n', encoding="utf-8")
    (tmp_path / "gui").mkdir(exist_ok=True)
    (tmp_path / "gui" / "package.json").write_text(
        '{\n  "name": "vision-relay-gui",\n  "version": "0.1.0"\n}\n', encoding="utf-8")
    return tmp_path


def _run(root: Path, version: str) -> int:
    return subprocess.run([sys.executable, str(SCRIPT), version, "--root", str(root)]).returncode


def test_sets_three_places(tmp_path):
    root = _make_root(tmp_path)
    assert _run(root, "1.0.0") == 0
    assert '__version__ = "1.0.0"' in (root / "vision_relay" / "__init__.py").read_text(encoding="utf-8")
    for rel in ("gui/src-tauri/tauri.conf.json", "gui/package.json"):
        assert json.loads((root / rel).read_text(encoding="utf-8"))["version"] == "1.0.0"


def test_rejects_bad_version(tmp_path):
    root = _make_root(tmp_path)
    for bad in ("1.0", "v1.0.0", "1.0.0-beta", ""):
        assert _run(root, bad) == 2, bad
    assert '__version__ = "0.1.0"' in (root / "vision_relay" / "__init__.py").read_text(encoding="utf-8")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_set_version.py -q
```
Expected: FAIL——脚本不存在（collection error）。

- [ ] **Step 3: 实现**

新建 `scripts/set_version.py`：

```python
#!/usr/bin/env python3
"""发布版本号对齐：一次写入三处（spec 2026-08-26 §6 版本一致性）。

用法：python scripts/set_version.py X.Y.Z [--root DIR]（默认仓库根）。
tag v<X.Y.Z> 是发布时唯一事实源；代码库日常保持开发态版本。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("version", help="语义化三段式版本号，如 1.0.0")
    ap.add_argument("--root", default=Path(__file__).resolve().parent.parent,
                    help="仓库根目录（默认：脚本所在仓库）")
    args = ap.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        print(f"invalid version: {args.version!r} (want X.Y.Z)", file=sys.stderr)
        return 2

    root = Path(args.root)
    targets = {
        "vision_relay/__init__.py": "regex",
        "gui/src-tauri/tauri.conf.json": "json",
        "gui/package.json": "json",
    }
    for rel in targets:
        if not (root / rel).is_file():
            print(f"missing target: {rel}", file=sys.stderr)
            return 1

    init = root / "vision_relay" / "__init__.py"
    src = init.read_text(encoding="utf-8")
    new, n = re.subn(r'__version__ = "[^"]+"', f'__version__ = "{args.version}"', src)
    if n != 1:
        print("__version__ pattern not found (or found multiple)", file=sys.stderr)
        return 1
    init.write_text(new, encoding="utf-8")

    for rel in ("gui/src-tauri/tauri.conf.json", "gui/package.json"):
        path = root / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = args.version
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"version set to {args.version} in: {', '.join(targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归 + 提交**

```bash
python -m pytest tests/test_set_version.py -q && python -m pytest -q && ruff check . && ruff format --check .
git add scripts/set_version.py tests/test_set_version.py
git commit -m "feat(scripts): set_version 版本对齐脚本——一次写核心/tauri.conf/gui package.json 三处,语义化校验"
```

---

### Task 11: 图标更新——JPEG 转 PNG + 生成全套

**Files:**
- Create: `gui/app-icon.png`（由 `gui/app-icon.png.jpg` 转换，2048×2048 不透明方形）
- Regenerate: `gui/src-tauri/icons/`（icon.ico、icon.icns、32x32.png、128x128.png、128x128@2x.png、Square*.png 等）

- [ ] **Step 1: JPEG → PNG**

```bash
python -m pip install pillow  # 已装则跳过
cd /e/LLMproject/Github/vision-relay/gui
python -c "from PIL import Image; im = Image.open('app-icon.png.jpg'); print(im.format, im.size); im.convert('RGB').save('app-icon.png')"
python -c "from PIL import Image; im = Image.open('app-icon.png'); assert im.size == (2048, 2048); print('ok', im.format)"
```
Expected: 第一条打印 `JPEG (2048, 2048)`；第二条打印 `ok PNG`。

- [ ] **Step 2: 生成全套图标（覆盖 icons/，当前只有 icon.ico）**

```bash
cd /e/LLMproject/Github/vision-relay/gui
pnpm tauri icon app-icon.png
ls src-tauri/icons/
```
Expected: 列表包含 `icon.ico icon.icns icon.png 32x32.png 128x128.png 128x128@2x.png Square*.png`（NSIS 需 .ico ✓，DMG 需 .icns ✓，AppImage 需 png ✓）。

- [ ] **Step 3: 提交**

```bash
cd /e/LLMproject/Github/vision-relay
git add gui/app-icon.png gui/src-tauri/icons/
git commit -m "assets(icons): 新图标全套生成——JPEG 源转 PNG 后 tauri icon 出 ico/icns/各尺寸(打包素材补齐)"
```

---

### Task 12: 打包配置 + GUI 壳包内核心探测（spec R2 / §5）

**Files:**
- Modify: `gui/src-tauri/tauri.conf.json`
- Modify: `gui/src-tauri/src/lib.rs:13-26`（`which_core`）
- Create: `scripts/freeze_entry.py`（Task 14 的 PyInstaller 入口，先落地）
- Modify: `gui/src-tauri/resources/.gitkeep`（占位，目录本身已 gitignore——不，resources 需要存在才能映射；改为**不**忽略该目录但忽略其内容，见 Step 4）

- [ ] **Step 1: freeze_entry.py**

新建 `scripts/freeze_entry.py`：

```python
#!/usr/bin/env python3
"""PyInstaller 冻结入口：包内绝对导入（直接冻结 vision_relay/__main__.py 会有相对导入问题）。"""
from vision_relay.__main__ import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: tauri.conf.json 打开打包**

`"bundle": { "active": false }` 改为：

```json
  "bundle": {
    "active": true,
    "targets": "all",
    "publisher": "jarvislee90s-dot",
    "icon": [
      "icons/32x32.png",
      "icons/128x128.png",
      "icons/128x128@2x.png",
      "icons/icon.icns",
      "icons/icon.ico"
    ],
    "resources": { "resources/core": "core/" }
  }
```
（`resources/core` 由 CI 冻结步骤放入；本机不存在时 tauri build 报缺资源——故 Step 4 调整 gitignore 策略并放占位。）

- [ ] **Step 3: lib.rs which_core 包内探测**

把现有（L13-26）：

```rust
#[tauri::command]
fn which_core() -> Option<String> {
    // 顺序：用户显式路径（前端传入并缓存） -> PATH 上的 vision-relay(.exe)
    let name = if cfg!(windows) { "vision-relay.exe" } else { "vision-relay" };
    if let Some(dir) = std::env::var_os("PATH") {
        for d in std::env::split_paths(&dir) {
            let cand = d.join(name);
            if cand.is_file() {
                return cand.to_str().map(|s| s.to_string());
            }
        }
    }
    None
}
```
改为：

```rust
#[tauri::command]
fn which_core(app: tauri::AppHandle) -> Option<String> {
    // 顺序：用户显式路径（前端在其上优先，现状语义）→ 包内冻结核心（M3 spec §5）→ PATH
    // （tauri dev 开发模式无 resources，自然落 PATH，开发体验不变）。
    let name = if cfg!(windows) { "vision-relay.exe" } else { "vision-relay" };
    if let Ok(res) = app.path().resource_dir() {
        let cand = res.join("core").join(name);
        if cand.is_file() {
            return cand.to_str().map(|s| s.to_string());
        }
    }
    if let Some(dir) = std::env::var_os("PATH") {
        for d in std::env::split_paths(&dir) {
            let cand = d.join(name);
            if cand.is_file() {
                return cand.to_str().map(|s| s.to_string());
            }
        }
    }
    None
}
```
（`use tauri::Manager;` 已在文件头 L8，`app.path()` 可用。）

- [ ] **Step 4: resources 目录与 gitignore 调整**

Task 2 忽略了 `gui/src-tauri/resources/`——Tauri 映射要求目录存在。改为只忽略其内容：

```bash
# .gitignore 中把 Task 2 的 gui/src-tauri/resources/ 替换为：
#   gui/src-tauri/resources/core/
mkdir -p gui/src-tauri/resources/core
printf '# CI 冻结步骤写入 core/（构建产物，不入库）\n' > gui/src-tauri/resources/README.md
```
并把 `resources/README.md` 提交（保目录）。

- [ ] **Step 5: 验证编译**

```bash
cd gui/src-tauri && cargo check && cd ../..
cd gui && pnpm build && cd ..
```
Expected: cargo check 无错误；前端构建无错误。

- [ ] **Step 6: 本机冒烟（PATH 兜底路径仍通）**

```bash
cd gui && pnpm tauri dev &   # 起 GUI，确认界面正常、功能可用（无 resources/core 时走 PATH）
# 人工过一眼总览页后关闭
```

- [ ] **Step 7: 提交**

```bash
git add gui/src-tauri/tauri.conf.json gui/src-tauri/src/lib.rs scripts/freeze_entry.py \
  gui/src-tauri/resources/README.md .gitignore
git commit -m "feat(gui): 打包配置落地(bundle.active+icon+resources 映射)+which_core 包内冻结核心探测(显式路径>包内>PATH)+freeze_entry 冻结入口"
```

---

### Task 13: ci.yml 新增 GUI 门禁 job（spec §9）

**Files:**
- Modify: `.github/workflows/ci.yml`（`jobs:` 下追加；沿用本文件既有 pin SHA：checkout `3d3c42e5...`、setup-uv `08807647e...`）

- [ ] **Step 1: 追加 gui job（缩进与 test/packaging 对齐）**

```yaml
  # ================================================================
  # job 3：gui —— 前端测试 + 类型检查/构建 + Rust 壳编译检查
  # ================================================================
  gui:
    name: GUI checks
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false

      - name: Setup pnpm
        uses: pnpm/action-setup@v4
        with:
          version: 11

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: pnpm
          cache-dependency-path: gui/pnpm-lock.yaml

      - name: Setup Rust
        uses: dtolnay/rust-toolchain@stable

      - name: Install Linux system dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y --no-install-recommends \
            build-essential pkg-config file libssl-dev patchelf xdg-utils \
            libgtk-3-dev librsvg2-dev libayatana-appindicator3-dev \
            libwebkit2gtk-4.1-dev libsoup-3.0-dev

      - name: Install frontend dependencies
        run: pnpm install --frozen-lockfile
        working-directory: gui

      - name: Frontend tests + typecheck + build
        run: pnpm test && pnpm build
        working-directory: gui

      - name: Rust shell check
        run: cargo check
        working-directory: gui/src-tauri
```
（`pnpm/action-setup@v4`、`setup-node@v4`、`dtolnay/rust-toolchain@stable` 与 ci.yml「锁 SHA」惯例不完全一致——执行时用 Step 2 命令解析为 SHA 后替换。）

- [ ] **Step 2: 解析并替换 tag 为 SHA（供应链安全，仓库惯例）**

```bash
for repo_tag in "pnpm/action-setup v4" "actions/setup-node v4" "dtolnay/rust-toolchain stable"; do
  set -- $repo_tag
  echo "$1@$2 -> $(gh api repos/$1/commits/$2 --jq .sha)"
done
```
把输出 SHA 替换 yml 中对应 `@tag`（保留行尾注释 `# vX.Y.Z`）。若 `dtolnay/rust-toolchain@stable` 无法解析 SHA，保留 tag（该 action 官方推荐用法即 tag）。

- [ ] **Step 3: 本地 YAML 校验 + 提交**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml', encoding='utf-8')); print('yaml ok')"
git add .github/workflows/ci.yml && git commit -m "ci: ci.yml 新增 GUI 门禁 job(vitest+tsc/vite build+cargo check,仍仅手动触发)"
```

---

### Task 14: release.yml 发布流水线（spec R1 / §4 / §11——核心任务）

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: 解析各 action 的 pin SHA（先做，写入下面 yml）**

```bash
for rt in "actions/checkout v4" "pnpm/action-setup v4" "actions/setup-node v4" \
          "dtolnay/rust-toolchain stable" "astral-sh/setup-uv v8.1.0" \
          "actions/upload-artifact v4" "actions/download-artifact v4" \
          "softprops/action-gh-release v2"; do
  set -- $rt
  echo "$1@$2 -> $(gh api repos/$1/commits/$2 --jq .sha)"
done
```
（`CHECKOUT_SHA`/`SETUP_UV_SHA` 也可直接复用 ci.yml 里的 `3d3c42e5aac5ba805825da76410c181273ba90b1` 与 `08807647e7069bb48b6ef5acd8ec9567f424441b`。）

- [ ] **Step 2: 写入 release.yml（`<SHA:xxx>` 用 Step 1 结果替换）**

```yaml
# 发布流水线（spec 2026-08-26 §4）：仅手动触发；三平台任一失败 = 整次失败，
# 不建 tag / 不建 Release，重跑零成本。成功 → Draft Release（人工验收后 Publish）。
name: Release

on:
  workflow_dispatch:
    inputs:
      version:
        description: "版本号（X.Y.Z，不带 v）"
        required: true
        type: string

permissions:
  contents: read

concurrency:
  group: release-${{ inputs.version }}
  cancel-in-progress: false

jobs:
  validate:
    name: Validate version
    runs-on: ubuntu-latest
    steps:
      - name: Semver check
        run: |
          if [[ ! "${{ inputs.version }}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "::error::版本号必须形如 X.Y.Z（如 1.0.0），收到: ${{ inputs.version }}"
            exit 1
          fi

  build:
    name: build (${{ matrix.platform }})
    needs: validate
    strategy:
      fail-fast: false
      matrix:
        include:
          - { os: windows-latest, platform: win,    arch: x64 }
          - { os: macos-latest,   platform: macos,  arch: universal }
          - { os: ubuntu-latest,  platform: linux,  arch: x64 }
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@<SHA:checkout>
        with:
          persist-credentials: false

      - name: Setup pnpm
        uses: pnpm/action-setup@<SHA:pnpm>
        with:
          version: 11

      - name: Setup Node.js
        uses: actions/setup-node@<SHA:node>
        with:
          node-version: "22"
          cache: pnpm
          cache-dependency-path: gui/pnpm-lock.yaml

      - name: Setup Rust
        uses: dtolnay/rust-toolchain@stable

      - name: Add macOS universal targets
        if: runner.os == 'macOS'
        run: rustup target add aarch64-apple-darwin x86_64-apple-darwin

      - name: Version inject
        shell: bash
        run: python scripts/set_version.py "${{ inputs.version }}"

      # ---------- 冻结核心 ----------
      - name: Freeze core (uv, win/linux)
        if: runner.os != 'macOS'
        uses: astral-sh/setup-uv@<SHA:uv>
        with:
          python-version: "3.12"
          enable-cache: true

      - name: Freeze core (PyInstaller onedir, win/linux)
        if: runner.os != 'macOS'
        shell: bash
        env:
          PYTHONUTF8: "1"
        run: |
          uv run --no-project --python 3.12 --with pyinstaller --with httpx \
            pyinstaller --noconfirm --clean --name vision-relay \
            --distpath freeze-dist --workpath freeze-build scripts/freeze_entry.py
          test -f freeze-dist/vision-relay/vision-relay$([[ "$RUNNER_OS" == "Windows" ]] && echo .exe)

      - name: Freeze core (python.org universal2, macOS)
        if: runner.os == 'macOS'
        run: |
          curl -fsSL -o /tmp/py.pkg https://www.python.org/ft/python/3.12.8/python-3.12.8-macos11.pkg
          sudo installer -pkg /tmp/py.pkg -target /
          /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -m venv .freeze
          .freeze/bin/pip install --quiet pyinstaller httpx
          .freeze/bin/pyinstaller --noconfirm --clean --name vision-relay \
            --distpath freeze-dist --workpath freeze-build scripts/freeze_entry.py
          lipo -archs freeze-dist/vision-relay/vision-relay | grep -q x86_64
          lipo -archs freeze-dist/vision-relay/vision-relay | grep -q arm64

      - name: Smoke frozen core (version + import chain)
        shell: bash
        run: |
          BIN=freeze-dist/vision-relay/vision-relay$([[ "$RUNNER_OS" == "Windows" ]] && echo .exe)
          OUT=$("$BIN" --version)
          echo "frozen --version: $OUT"
          [[ "$OUT" == *"${{ inputs.version }}"* ]] || { echo "::error::冻结核心版本号不符"; exit 1; }
          "$BIN" --help > /dev/null

      - name: Place frozen core into Tauri resources
        shell: bash
        run: |
          mkdir -p gui/src-tauri/resources/core
          cp -r freeze-dist/vision-relay/. gui/src-tauri/resources/core/
          ls gui/src-tauri/resources/core | head -5

      # ---------- 前端依赖 ----------
      - name: Install frontend dependencies
        run: pnpm install --frozen-lockfile
        working-directory: gui

      - name: Install Linux system dependencies
        if: runner.os == 'Linux'
        run: |
          sudo apt-get update
          sudo apt-get install -y --no-install-recommends \
            build-essential pkg-config file libssl-dev patchelf xdg-utils \
            libgtk-3-dev librsvg2-dev libayatana-appindicator3-dev \
            libwebkit2gtk-4.1-dev libsoup-3.0-dev

      # ---------- Tauri 打包 ----------
      - name: Build (Windows NSIS)
        if: runner.os == 'Windows'
        run: pnpm tauri build --bundles nsis
        working-directory: gui

      - name: Build (macOS universal DMG)
        if: runner.os == 'macOS'
        run: pnpm tauri build --bundles dmg --target universal-apple-darwin
        working-directory: gui

      - name: Build (Linux AppImage + deb)
        if: runner.os == 'Linux'
        run: pnpm tauri build --bundles appimage,deb
        working-directory: gui

      # ---------- 收集改名上传 ----------
      - name: Collect artifacts
        shell: bash
        run: |
          set -euo pipefail
          V="${{ inputs.version }}"
          mkdir -p release-assets
          case "$RUNNER_OS" in
            Windows)
              exe=$(find gui/src-tauri/target/release/bundle/nsis -name "*-setup.exe" | head -1)
              [ -n "$exe" ] || { echo "::error::NSIS setup.exe not found"; exit 1; }
              cp "$exe" "release-assets/vision-relay-$V-win-x64-setup.exe" ;;
            macOS)
              dmg=$(find gui/src-tauri/target -name "*.dmg" | head -1)
              [ -n "$dmg" ] || { echo "::error::DMG not found"; exit 1; }
              cp "$dmg" "release-assets/vision-relay-$V-macos-universal.dmg" ;;
            Linux)
              appimage=$(find gui/src-tauri/target/release/bundle/appimage -name "*.AppImage" | head -1)
              deb=$(find gui/src-tauri/target/release/bundle/deb -name "*.deb" | head -1)
              [ -n "$appimage" ] && [ -n "$deb" ] || { echo "::error::AppImage/deb not found"; exit 1; }
              cp "$appimage" "release-assets/vision-relay-$V-linux-x64.AppImage"
              cp "$deb" "release-assets/vision-relay-$V-linux-x64.deb" ;;
          esac
          ls -la release-assets/

      - name: Upload artifacts
        uses: actions/upload-artifact@<SHA:upload>
        with:
          name: release-assets-${{ matrix.platform }}
          path: release-assets/*
          if-no-files-found: error

  publish:
    name: Publish draft release
    needs: build
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@<SHA:checkout>
        with:
          persist-credentials: false

      - name: Download all artifacts
        uses: actions/download-artifact@<SHA:download>
        with:
          pattern: release-assets-*
          path: release-assets
          merge-multiple: true

      - name: Checksums
        run: cd release-assets && sha256sum * > SHA256SUMS.txt && cat SHA256SUMS.txt

      - name: Build release body from CHANGELOG
        env:
          VERSION: ${{ inputs.version }}
        run: |
          python - <<'PY'
          import os, re
          v = os.environ["VERSION"]
          text = open("CHANGELOG.md", encoding="utf-8").read()
          m = re.search(rf"## \[{re.escape(v)}\][^\n]*\n(.*?)(?=\n## \[|\Z)", text, re.S)
          body = m.group(1).strip() if m else f"vision-relay v{v}"
          open("body.md", "w", encoding="utf-8").write(f"vision-relay v{v}\n\n{body}\n")
          PY
          cat body.md

      - name: Create draft release
        uses: softprops/action-gh-release@<SHA:ghrelease>
        with:
          tag_name: v${{ inputs.version }}
          name: vision-relay v${{ inputs.version }}
          body_path: body.md
          draft: true
          prerelease: false
          files: release-assets/*
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

- [ ] **Step 3: 本地校验 + 提交**

```bash
python -c "import yaml; d=yaml.safe_load(open('.github/workflows/release.yml', encoding='utf-8')); assert set(d['jobs'])=={'validate','build','publish'}; print('yaml ok, jobs:', list(d['jobs']))"
git add .github/workflows/release.yml
git commit -m "ci(release): 发布流水线——手动触发填版本号;三平台矩阵(冻结onedir+冒烟+tauri build);失败不产tag/Release;全绿出Draft(正文取CHANGELOG)+SHA256SUMS"
```

**回退预案（spec §5 两级退路，若 macOS universal2 冻结失败）:**
- 退路一（aarch64-only）：删除 macOS 的 python.org 步骤，改用 uv（同 win/linux），Build 步骤去掉 `--target universal-apple-darwin`，产物改名 `vision-relay-$V-macos-arm64.dmg`；发布说明注明「Intel 支持随后版本」。
- 退路二（分架构双 DMG）：matrix 的 macOS 拆两条（`macos-latest` arm64 与 `macos-15-intel` x64，参考 CodexPlusPlus），各出各架构 DMG。

---

### Task 15: CHANGELOG 出 1.0.0 小节（spec R7，发布正文硬前置）

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 重组 Unreleased → 1.0.0**

把 `## [Unreleased]` 整节内容**原样移入**新小节（条目不动，仅换标题 + 补首版摘要与新条目）：

```markdown
## [Unreleased]

（空——下个版本累积）

## [1.0.0] - <执行日 YYYY-MM-DD>

首个公开版本：一期数据面（三协议透明代理 + VLM 转述 + fail-open）+ 二期控制面（GUI 控制台、对账与自动修复、模型能力实测、识图留痕、四 harness）+ 三平台安装包分发（装完即用零 Python 依赖）。

<!-- 此下保留原 [Unreleased] 的 Added/Changed/Removed/Fixed 全部既有条目 -->

### Added（1.0.0 新增）
- 三平台安装包与发布流水线（Windows NSIS exe / macOS DMG / Linux AppImage+deb，GUI + 冻结核心单包，手动触发云端构建）。
- 识图留痕留存策略生效：默认 7 天自动清理、可关闭（启动即清 + 每 24h 周期）。

### Fixed（1.0.0 新增）
- 停用转发的 relay 选路全层不可见（suppressed_relays 收严）。
- zcode 重启失败给出 UI 反馈（M1）；无 baseURL 供应商不再于统计隐身（M3）；「保留勾选」复选框回滚（M4）；模态门还原清除 `zcode:{}` 空壳（M5）；zcode 探测无目标文案修正（M7）。
```
（`<执行日>` 换成当天日期；M3 若 Task 8 降级，此条按实际记档措辞调整。）

- [ ] **Step 2: 提交**

```bash
git add CHANGELOG.md && git commit -m "docs(changelog): Unreleased 重组为 1.0.0 首版小节(发布说明正文来源)"
```

---

### Task 16: GUI 截图（隔离假数据环境，spec D8 / A9）

**Files:**
- Create: `docs/screenshots/{overview,models,records,settings}.png`

- [ ] **Step 1: 搭假数据环境（不入库）**

```bash
cd /e/LLMproject/Github/vision-relay
mkdir -p tmp_shots/home/.claude tmp_shots/home/.qwen tmp_shots/cfg
# 假 harness 配置（假端点假 key）
cat > tmp_shots/home/.claude/settings.json <<'EOF'
{ "env": { "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787", "ANTHROPIC_AUTH_TOKEN": "sk-demo-fake-key" } }
EOF
cat > tmp_shots/home/.qwen/settings.json <<'EOF'
{ "provider": { "demo-provider": { "base_url": "http://127.0.0.1:8787/v1", "api_key": "sk-demo-fake-key", "model": "demo-text-model" } } }
EOF
# 假 proxy.json（假 VLM + 假 relay）
cat > tmp_shots/cfg/proxy.json <<'EOF'
{ "vlm": { "model": "demo-vl-max", "base_url": "https://demo-vlm.example.com/v1", "api_key": "sk-demo-fake-vlm" },
  "relays": [
    { "name": "demo-direct", "protocol": "anthropic", "base_url": "https://demo-api.example.com", "models": ["demo-claude-model"] },
    { "name": "demo-chat", "protocol": "chat", "base_url": "https://demo-openai.example.com/v1", "models": ["demo-gpt-model"] }
  ] }
EOF
# 假识图记录（3 家 harness 各一条）
VISION_RELAY_CONFIG_DIR="$PWD/tmp_shots/cfg" python - <<'EOF'
from vision_relay import visionlog
for i, (h, s) in enumerate([("claude", "design-review"), ("codex", "api-debug"), ("zcode", "nightly-build")]):
    visionlog.record({"ts": "2026-08-26T10:0%d:00" % i, "harness": h, "session": s,
        "model": "demo-text-model", "vlm_model": "demo-vl-max",
        "prompt": f"示例提示词 {i+1}：请描述这张界面截图的主要内容",
        "vlm_raw": f"这是一张示例截图的转述文本 {i+1}，用于展示识图记录页的版式。",
        "injected": f"[image] 这是一张示例截图的转述文本 {i+1}。",
        "image_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
        enabled=True, retention_days=7)
EOF
```

- [ ] **Step 2: 假 HOME 起 GUI**

```bash
mkdir -p docs/screenshots
USERPROFILE="$PWD/tmp_shots/home" HOME="$PWD/tmp_shots/home" \
VISION_RELAY_CONFIG_DIR="$PWD/tmp_shots/cfg" \
pnpm --dir gui tauri dev &
# 等待窗口「vision-relay 控制台」出现（首次 Rust 增量编译约 30-90s）
```

- [ ] **Step 3: 逐页截图（computer-use 截窗 + 导航；或 python 截窗脚本）**

```bash
python -m pip install mss pygetwindow pillow
```
```python
# shot.py <输出名> —— 按窗口标题截 vision-relay 控制台
import sys, time
import mss, pygetwindow as gw
from PIL import Image
w = [x for x in gw.getAllWindows() if "vision-relay" in x.title][0]
w.activate(); time.sleep(0.6)
with mss.mss() as sct:
    img = sct.grab({"left": w.left, "top": w.top, "width": w.width, "height": w.height})
    Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX").save(sys.argv[1])
```
依次（每页用 computer-use 点击左侧导航后执行）：`python shot.py docs/screenshots/overview.png` → models → records → settings。

- [ ] **Step 4: 敏感信息过目（A9 硬闸）**

用 Read 工具逐张检查四张 PNG：**不得出现**真实供应商端点、真实模型名、真实 key。发现真实数据 → 检查 Step 1 环境变量是否生效（GUI 是从真 HOME 读到的），修正重截。

- [ ] **Step 5: 清理 + 提交**

```bash
rm -rf tmp_shots shot.py
# 结束 tauri dev 后台进程
git add docs/screenshots/
git commit -m "docs(readme素材): GUI 四页截图(总览/模型能力/识图记录/设置,隔离假数据环境)"
```

---

### Task 17: README 双语更新（spec R7）

**Files:**
- Modify: `README.md`、`README.zh.md`

- [ ] **Step 1: README.md（英文）——在 "How it works" 图示之后、"Why a proxy" 相关内容之前插入两节**

```markdown
## Desktop console (GUI)

A Tauri desktop console manages everything visually: routing toggle with live per-harness topology, model modality matrix backed by real probes, vision call records (prompt / raw VLM reply / injected text), read-only diagnostics with auto-repair, and per-harness VLM settings. Supported harnesses: Claude Code, Codex, Qwen Code, zcode.

| Overview | Model capabilities | Vision records |
|---|---|---|
| ![Overview](docs/screenshots/overview.png) | ![Models](docs/screenshots/models.png) | ![Records](docs/screenshots/records.png) |

## Install

**Desktop app (recommended)** — grab the installer from [Releases](https://github.com/jarvislee90s-dot/vision-relay/releases):

| Platform | Artifact |
|---|---|
| Windows x64 | `vision-relay-<version>-win-x64-setup.exe` |
| macOS (Intel + Apple Silicon) | `vision-relay-<version>-macos-universal.dmg` |
| Linux x64 | `vision-relay-<version>-linux-x64.AppImage` / `.deb` |

Zero Python required — the core ships frozen inside the app. Install, open, done.

**pip (advanced / headless)** — `pip install vision-relay`, then `vision-relay start`. Same core, no GUI.
```
（若 Task 14 走了退路，macOS 行按实际产物名调整。）

- [ ] **Step 2: README.zh.md 同步镜像（同位置插入中文版）**

```markdown
## 桌面控制台（GUI）

Tauri 桌面控制台把一切可视化：路由开关与逐 harness 实时拓扑、实测背书的模型能力矩阵、识图记录（提示词 / VLM 原始返回 / 实际注入文本）、只读诊断报告 + 自动修复、按 harness 的 VLM 配置。支持 harness：Claude Code、Codex、Qwen Code、zcode。

| 总览 | 模型能力 | 识图记录 |
|---|---|---|
| ![总览](docs/screenshots/overview.png) | ![模型能力](docs/screenshots/models.png) | ![识图记录](docs/screenshots/records.png) |

## 安装

**桌面应用（推荐）** — 从 [Releases](https://github.com/jarvislee90s-dot/vision-relay/releases) 下载安装包：

| 平台 | 产物 |
|---|---|
| Windows x64 | `vision-relay-<版本>-win-x64-setup.exe` |
| macOS（Intel + Apple Silicon） | `vision-relay-<版本>-macos-universal.dmg` |
| Linux x64 | `vision-relay-<版本>-linux-x64.AppImage` / `.deb` |

零 Python 依赖——核心已冻结内嵌，装完即用。

**pip（高级 / 无界面）** — `pip install vision-relay` 后 `vision-relay start`，同一核心、无 GUI。
```

- [ ] **Step 3: 渲染自检 + 提交**

```bash
# 确认图片相对路径可解析（文件存在）
ls docs/screenshots/overview.png docs/screenshots/models.png docs/screenshots/records.png
git add README.md README.zh.md && git commit -m "docs(readme): 双语补桌面控制台介绍+截图+安装方式(安装包/零Python依赖/pip 通道)"
```

---

### Task 18: Issue / PR 模板修订（spec §9）

**Files:**
- Modify: `.github/ISSUE_TEMPLATE/bug_report.yml`、`.github/PULL_REQUEST_TEMPLATE.md`

- [ ] **Step 1: bug_report.yml——补 OS、zcode、脱敏指引**

1) harness 输入项（`id: harness`）description 改为：

```yaml
      description: For example, Claude Code, Codex, Qwen Code, zcode, Gemini CLI, or a raw HTTP client.
```
2) 在 `id: harness` 之后新增：

```yaml
  - type: dropdown
    id: os
    attributes:
      label: Operating system
      options:
        - Windows
        - macOS (Apple Silicon)
        - macOS (Intel)
        - Linux
    validations:
      required: true
```
3) 在 `id: problem` 之前新增 markdown 提示：

```yaml
  - type: markdown
    attributes:
      value: |
        **Before pasting logs or diagnostic reports** (`vision-relay diagnose --json` may contain
        provider base URLs): scrub API keys, tokens, and anything you consider private.
```

- [ ] **Step 2: PR 模板——Verification 清单补两项**

`.github/PULL_REQUEST_TEMPLATE.md` 的 Verification 列表（`- [ ] \`python -m pytest -q\` passes` 之后）追加：

```markdown
- [ ] `pnpm test` (in `gui/`) passes when frontend/GUI code changed
- [ ] Behavior changes update the matching spec under `docs/superpowers/specs/`
```

- [ ] **Step 3: 校验 + 提交**

```bash
python -c "import yaml; yaml.safe_load(open('.github/ISSUE_TEMPLATE/bug_report.yml', encoding='utf-8')); print('yaml ok')"
git add .github/ISSUE_TEMPLATE/bug_report.yml .github/PULL_REQUEST_TEMPLATE.md
git commit -m "docs(templates): bug 模板补 OS/zcode/诊断报告脱敏指引;PR 清单补 GUI 测试与 spec 同步项"
```

---

### Task 19: 云端实发布 v1.0.0（spec R8 / §11 / D9——首发即实战验收）

**Files:** 无代码改动（操作任务）

- [ ] **Step 1: 本地全门禁（四层全过才推）**

```bash
python -m pytest -q && ruff format --check . && ruff check .
cd gui && pnpm test && pnpm build && cd ..
cd gui/src-tauri && cargo check && cd ../..
```

- [ ] **Step 2: 推送 main（workflow_dispatch 必须在默认分支上才可从 Actions 页触发）**

```bash
git log --oneline -3   # 确认 Task 1-18 的提交都在
git push origin main
```

- [ ] **Step 3: 触发发布流水线**

```bash
gh workflow run release.yml -f version=1.0.0
sleep 5 && gh run list --workflow=release.yml --limit 1
gh run watch $(gh run list --workflow=release.yml --limit 1 --json databaseId --jq '.[0].databaseId')
```
Expected: validate → build×3 → publish 全绿。失败 → 看 `gh run view --log-failed` 修复提交后重跑（无残留，spec §11 失败处置表）。

- [ ] **Step 4: 核验 Draft Release（A1/A2/A8）**

```bash
gh release view v1.0.0 --json isDraft,assets --jq '{isDraft, assets: [.assets[].name]}'
```
Expected: `isDraft: true`；assets 含 `vision-relay-1.0.0-win-x64-setup.exe`、`-macos-universal.dmg`、`-linux-x64.AppImage`、`-linux-x64.deb`、`SHA256SUMS.txt`；正文来自 CHANGELOG 1.0.0 小节。

- [ ] **Step 5: 人工验收闸门（A3/A4——用户执行，阻塞 Publish）**

从 Draft Release 下载 Windows 安装包，干净环境（无 PATH 上的 vision-relay）安装 → GUI 起 → 开路由 → 诊断报告正常。**验收通过前不 Publish。**

- [ ] **Step 6: Publish + 收尾（A10）**

```bash
gh release edit v1.0.0 --draft=false
gh release view v1.0.0 --json url --jq .url
```
在 M3 汇总文档勾掉对应待办；杀毒误报与安装包体积观察值记入 CHANGELOG 或 docs/history（spec A8 记档不阻断）。

---

## 计划自审记录

- **Spec 覆盖**: R1→Task 14；R2→Task 11/12/14；R3→Task 10/14（冒烟断言）/15；R4→Task 9；R5→Task 1-8；R6→Task 13/18；R7→Task 15/16/17；R8→Task 19；A1-A10→Task 14/16/19 步骤内对应。D1-D9 均落实（D4 onedir 在 Task 14 冻结参数、D8 在 Task 16、D9 在 Task 19）。
- **占位符**: `<SHA:xxx>` 与 `<执行日>` 为执行期解析项（解析命令已给）；无 TBD/TODO。
- **类型/命名一致性**: `_retention_once`/`_start_retention_worker`（Task 9 内定义并引用一致）；`set_version.py` 三处目标与 Task 14 调用一致；`freeze_entry.py` 在 Task 12 创建、Task 14 使用；产物命名四处（Task 14 收集、README 表、Step 4 核验）一致。
