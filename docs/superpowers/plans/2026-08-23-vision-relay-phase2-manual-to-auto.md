# 手工测试自动化（GUI 组件测试）+ 手工范围修订 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **上游 spec：** [`docs/superpowers/specs/2026-08-21-vision-relay-phase2-control-plane-design.md`](../specs/2026-08-21-vision-relay-phase2-control-plane-design.md) §6（GUI 交互/保存语义/向导/托盘）。
> **背景：** G1–G8/G12 后端链路已由 `tests/test_e2e_g*.py` 自动化；本计划把 M2 手工手册里**可点选的 GUI 决策逻辑**（G10 关闭选择、G11 保存语义、G13 核心不可用、G1 向导表单、G5/G2-G4 的 UI 面）自动化为 vitest 组件测试，然后产出**修订版手工测试范围**（哪些已自动化、哪些必须人工及原因、已知分歧）。

**Goal:** 用 jsdom + Testing Library 为 6 个 GUI 单元（Settings / Models / Overview / Wizard / CloseGuard / App 横幅）建立组件测试，覆盖手工项里可模拟的点选与决策逻辑；测试全绿后生成修订版手工测试清单。

**Architecture:** 纯前端测试，不动任何生产代码。组件测试通过 `vi.mock("../core")` / `vi.mock("@tauri-apps/api/*")` 隔离 Tauri 与子进程边界（GUI 仍是薄壳）；每文件用 `// @vitest-environment jsdom` 文档块声明环境（现有 vitest 默认 node 环境保持不变，纯逻辑测试不受影响）。这些是**特征测试（characterization tests）**：断言当前行为；发现组件与手工预期不符时记录为「分歧」，不擅自改组件。

**Tech Stack:** vitest（已有）+ jsdom + @testing-library/react（新增 devDependencies，React 18 兼容的 RTL 16）。不装 user-event（fireEvent 够用）、不装 jest-dom（用原生断言），依赖最小化。

**基线（开工前复现）：** `pnpm -C gui test` → **25 passed / 5 files**；`pnpm -C gui build`（tsc+vite）✓；`.venv/Scripts/python -m pytest -q` → 407 passed（本计划不动 Python，结束时必须仍是 407）。

**特征测试纪律（本计划特有，优先级最高）：**
1. 新组件测试写的断言是**当前实现的真实行为**。跑挂了先分诊：(a) 测试写错（选择器/时序）→ 修测试；(b) 组件真 bug 或与手工预期分歧 → **不修组件**，把该用例改为 `it.skip("…", …)` 并在用例上方注释分歧点，同时登记到 Task 8 修订手册的「自动化发现待确认」小节，最终汇报里列出。
2. 已知分歧预告：G11 手册预期「改字段不保存直接切页再回来，脏计数与字段保持」——但 `App.tsx` 切页会**卸载**页面组件，重挂后本地 state 必然丢失。Task 3 用测试钉住现状（卸载=丢失），Task 8 手册里改为「切页即丢弃未保存修改」并标注如需保持须提升状态层（产品决策）。
3. 计时器类（RoutingToggle 的 1.2s 延迟刷新、8s 轮询）断言调用发生即可，不推进时间；如出现 flaky，按纪律 1 处理。

---

### Task 1: 组件测试基建（devDeps + jsdom 冒烟）

**Files:**
- Modify: `gui/package.json`（devDependencies）
- Create: `gui/src/pages/Settings.test.tsx`（本任务只放冒烟用例，Task 2 扩充）

- [ ] **Step 1: 写冒烟测试（新文件 `gui/src/pages/Settings.test.tsx`）**

```tsx
// @vitest-environment jsdom
// 冒烟：验证 jsdom + Testing Library + tsx 测试链路端到端可用（G11 前置）。
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsPage } from "./Settings";

vi.mock("../core", () => ({ core: vi.fn(), setCorePath: vi.fn() }));

import { core } from "../core";

const coreMock = vi.mocked(core);

const CONFIG = {
  vlm: {
    model: "vl-global",
    base_url: "https://x.example/v1",
    api_key: "●●●●",
    format: "chat",
    custom_tier1: null,
    custom_tier2: null,
  },
  vlm_by_harness: { claude: { model: "vl-claude", base_url: "https://y.example/v1" } },
  routing: { unknown_default: "text_only" },
  vision_log: { enabled: true, retention_days: 7 },
};

describe("SettingsPage smoke", () => {
  beforeEach(() => {
    coreMock.mockReset();
    coreMock.mockImplementation(async (verb: string) =>
      verb === "config" ? JSON.parse(JSON.stringify(CONFIG)) : {},
    );
  });

  it("renders the VLM card and loads config fields", async () => {
    render(<SettingsPage lang="zh" status={null} refresh={vi.fn()} setLang={vi.fn()} />);
    expect(screen.getByText(/VLM（唯一必配）/)).toBeTruthy();
    expect(await screen.findByDisplayValue("vl-global")).toBeTruthy(); // config 动词数据落到表单
  });
});
```

- [ ] **Step 2: 跑确认失败（依赖缺失）**

Run: `pnpm -C gui test`
Expected: FAIL——`Error: Cannot find module '@testing-library/react'`（或 jsdom 环境缺失报错）

- [ ] **Step 3: 安装依赖**

Run: `pnpm -C gui add -D jsdom @testing-library/react`
Expected: package.json devDependencies 出现 `jsdom`、`@testing-library/react`（RTL 16.x，连带 @testing-library/dom）。

- [ ] **Step 4: 跑通过 + tsc 验证**

Run: `pnpm -C gui test` → PASS（26 tests / 6 files）；`pnpm -C gui build` → tsc 零错误（新 .tsx 测试文件被 include:["src"] 覆盖，必须类型干净）。

- [ ] **Step 5: Commit**

```bash
git add gui/package.json gui/pnpm-lock.yaml gui/src/pages/Settings.test.tsx
git commit -m "chore(gui): component-test toolchain (jsdom + testing-library) with settings smoke test"
```

---

### Task 2: Settings 组件测试——保存语义与 payload（G11 核心）

**Files:**
- Modify: `gui/src/pages/Settings.test.tsx`（追加用例）

- [ ] **Step 1: 追加用例（放在 smoke 用例之后）**

```tsx
describe("SettingsPage save semantics (G11)", () => {
  beforeEach(() => {
    coreMock.mockReset();
    coreMock.mockImplementation(async (verb: string) =>
      verb === "config" ? JSON.parse(JSON.stringify(CONFIG)) : {},
    );
  });

  const saveBtn = () => screen.getByText("💾 保存设置") as HTMLButtonElement;

  async function rendered() {
    const refresh = vi.fn();
    render(<SettingsPage lang="zh" status={null} refresh={refresh} setLang={vi.fn()} />);
    await screen.findByDisplayValue("vl-global");
    return refresh;
  }

  it("starts clean: zero dirty, save disabled", async () => {
    await rendered();
    expect(screen.getByText("无未保存修改")).toBeTruthy();
    expect(saveBtn().disabled).toBe(true);
  });

  it("edit marks dirty; save sends exact vlm-set/settings-set payloads and resets", async () => {
    const refresh = await rendered();
    fireEvent.change(screen.getByDisplayValue("vl-global"), { target: { value: "vl-new" } });
    expect(screen.getByText(/1 处未保存修改/)).toBeTruthy();
    expect(saveBtn().disabled).toBe(false);

    fireEvent.click(saveBtn());
    await waitFor(() => expect(coreMock.mock.calls.some((c) => c[0] === "vlm-set")).toBe(true));
    await waitFor(() => expect(coreMock.mock.calls.some((c) => c[0] === "settings-set")).toBe(true)); // 两个动词顺序 await，逐个等

    const vlmCall = coreMock.mock.calls.find((c) => c[0] === "vlm-set")!;
    const stdin = (vlmCall![1] as { stdin: Record<string, unknown> }).stdin;
    // 空 api_key 不出现在 payload（留空=不修改，密钥铁律的 UI 面）
    expect(stdin.vlm).toEqual({ model: "vl-new", base_url: "https://x.example/v1", format: "chat" });
    expect(stdin.custom_tier1).toBeNull();
    expect(stdin.custom_tier2).toBeNull();
    // claude 分组原样带回（model/base_url；组不送 format）
    expect(stdin.vlm_by_harness).toEqual({
      claude: { model: "vl-claude", base_url: "https://y.example/v1" },
    });

    const setCall = coreMock.mock.calls.find((c) => c[0] === "settings-set")!;
    expect((setCall![1] as { stdin: unknown }).stdin).toEqual({
      routing: { unknown_default: "text_only" },
      vision_log: { enabled: true, retention_days: 7 },
    });

    await waitFor(() => expect(screen.getByText("无未保存修改")).toBeTruthy());
    expect(refresh).toHaveBeenCalled();
  });

  it("filled api_key is sent; blank is not", async () => {
    await rendered();
    const pw = document.querySelector('input[type="password"]') as HTMLInputElement;
    fireEvent.change(pw, { target: { value: "sk-new" } });
    fireEvent.click(saveBtn());
    await waitFor(() => expect(coreMock.mock.calls.some((c) => c[0] === "vlm-set")).toBe(true));
    const stdin = (coreMock.mock.calls.find((c) => c[0] === "vlm-set")![1] as { stdin: { vlm: Record<string, unknown> } }).stdin;
    expect(stdin.vlm.api_key).toBe("sk-new");
  });

  it("clearing retention input falls back to 7 (never sends 0 — 决策⑥ retention>=1)", async () => {
    await rendered();
    const retention = screen.getByDisplayValue("7");
    fireEvent.change(retention, { target: { value: "" } });
    fireEvent.click(saveBtn());
    await waitFor(() => expect(coreMock.mock.calls.some((c) => c[0] === "settings-set")).toBe(true));
    const stdin = (coreMock.mock.calls.find((c) => c[0] === "settings-set")![1] as { stdin: { vision_log: { retention_days: number } } }).stdin;
    expect(stdin.vision_log.retention_days).toBe(7);
  });

  it("save failure alerts and keeps dirty state", async () => {
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "config") return JSON.parse(JSON.stringify(CONFIG));
      throw new Error("retention_days must be an int >= 1");
    });
    await rendered();
    fireEvent.change(screen.getByDisplayValue("vl-global"), { target: { value: "vl-new" } });
    fireEvent.click(saveBtn());
    await waitFor(() => expect(alert).toHaveBeenCalledWith(expect.stringContaining("int >= 1")));
    expect(screen.getByText(/1 处未保存修改/)).toBeTruthy(); // 脏状态不丢
    alert.mockRestore();
  });

  it("four-mode VLM test maps tier1c/tier2c → mode + custom_prompt correctly", async () => {
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "config") return JSON.parse(JSON.stringify(CONFIG));
      if (verb === "vlm-test") return { desc: "红色", duration_ms: 5, model: "vl-mock" };
      return {};
    });
    await rendered();
    const mode = screen.getByRole("combobox");

    // tier2 + 自选提示词
    fireEvent.change(mode, { target: { value: "tier2c" } });
    fireEvent.change(screen.getByPlaceholderText("聚焦问题…"), { target: { value: "图里几个字" } });
    fireEvent.change(screen.getByPlaceholderText("自选提示词…"), { target: { value: "我的自选" } });
    fireEvent.click(screen.getByText("开始测试"));
    await waitFor(() => expect(coreMock.mock.calls.some((c) => c[0] === "vlm-test")).toBe(true));
    let stdin = (coreMock.mock.calls.find((c) => c[0] === "vlm-test")![1] as { stdin: Record<string, unknown> }).stdin;
    expect(stdin).toEqual({ mode: "tier2", question: "图里几个字", custom_prompt: "我的自选" });

    // tier1 默认：mode=tier1 且 custom_prompt=null
    fireEvent.change(mode, { target: { value: "tier1" } });
    fireEvent.click(screen.getByText("开始测试"));
    await waitFor(() => expect(coreMock.mock.calls.filter((c) => c[0] === "vlm-test").length).toBe(2));
    const calls = coreMock.mock.calls.filter((c) => c[0] === "vlm-test");
    stdin = (calls[calls.length - 1][1] as { stdin: Record<string, unknown> }).stdin;
    expect(stdin).toEqual({ mode: "tier1", question: null, custom_prompt: null });

    expect(await screen.findByText(/红色/)).toBeTruthy(); // 结果框展示 desc
  });
});
```

文件头 import 改为：`import { fireEvent, render, screen, waitFor } from "@testing-library/react";`

- [ ] **Step 2: 跑通过**

Run: `pnpm -C gui test`
Expected: Settings.test.tsx 7 tests PASS（含冒烟）。任何挂掉按「特征测试纪律」分诊。

- [ ] **Step 3: tsc + Commit**

```bash
pnpm -C gui build
git add gui/src/pages/Settings.test.tsx
git commit -m "test(gui): settings component tests — save semantics, payloads, blank-key rule, retention fallback, failure alert, four-mode vlm-test (G11)"
```

---

### Task 3: Settings 重挂与放弃修改——钉住 G11 分歧现状

**Files:**
- Modify: `gui/src/pages/Settings.test.tsx`（追加）

- [ ] **Step 1: 追加用例**

```tsx
describe("SettingsPage remount & discard (G11 已知分歧)", () => {
  beforeEach(() => {
    coreMock.mockReset();
    coreMock.mockImplementation(async (verb: string) =>
      verb === "config" ? JSON.parse(JSON.stringify(CONFIG)) : {},
    );
  });

  it("remount (page switch) resets fields and dirty — App 卸载页面组件，本地 state 丢失", async () => {
    // 手册 G11 原预期「切页再回来字段保持」与实现不符：App.tsx 用条件渲染切换页面，
    // SettingsPage 卸载后 useState 全部重置。本用例钉住现状；分歧登记在修订手册（Task 8）。
    const { unmount } = render(<SettingsPage lang="zh" status={null} refresh={vi.fn()} setLang={vi.fn()} />);
    await screen.findByDisplayValue("vl-global");
    fireEvent.change(screen.getByDisplayValue("vl-global"), { target: { value: "vl-new" } });
    expect(screen.getByText(/1 处未保存修改/)).toBeTruthy();

    unmount(); // 模拟切到总览页
    render(<SettingsPage lang="zh" status={null} refresh={vi.fn()} setLang={vi.fn()} />); // 切回
    await screen.findByDisplayValue("vl-global"); // 回到已保存值
    expect(screen.getByText("无未保存修改")).toBeTruthy(); // 脏计数归零
  });

  it("放弃修改 reloads the page", async () => {
    const originalLocation = window.location;
    const reload = vi.fn();
    try {
      // jsdom 的 location.reload 未实现且不可 spyOn —— 整体替换为测试替身
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      delete (window as any).location;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any).location = { ...originalLocation, reload };
      render(<SettingsPage lang="zh" status={null} refresh={vi.fn()} setLang={vi.fn()} />);
      await screen.findByDisplayValue("vl-global");
      fireEvent.change(screen.getByDisplayValue("vl-global"), { target: { value: "vl-new" } });
      fireEvent.click(screen.getByText("放弃修改"));
      expect(reload).toHaveBeenCalledTimes(1);
    } finally {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      delete (window as any).location;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any).location = originalLocation;
    }
  });
});
```

- [ ] **Step 2: 跑通过 + Commit**

Run: `pnpm -C gui test` → PASS；`pnpm -C gui build`。

```bash
git add gui/src/pages/Settings.test.tsx
git commit -m "test(gui): settings remount/discard characterization — documents G11 page-switch state-loss divergence"
```

---

### Task 4: Models 组件测试——三态循环/脏集/保存/重测（G5 UI 面）

**Files:**
- Create: `gui/src/pages/Models.test.tsx`

- [ ] **Step 1: 写测试**

```tsx
// @vitest-environment jsdom
// G5 UI 面：三态循环、脏集、只发改动行、行内重测（后端链路已由 test_e2e_g5_models.py 覆盖）。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ModelsPage } from "./Models";

vi.mock("../core", () => ({ core: vi.fn() }));

import { core } from "../core";

const coreMock = vi.mocked(core);

const MODELS = {
  models: [
    { harness: "codex", provider: "?", model: "gpt-5-codex", value: null, source: null, probe_cached: null },
    { harness: "qwen-code", provider: "?", model: "qwen3-coder", value: "image", source: "user", probe_cached: "image" },
  ],
};

describe("ModelsPage (G5 UI)", () => {
  beforeEach(() => {
    coreMock.mockReset();
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      return {};
    });
  });

  const saveBtn = () => screen.getByText("保存修改") as HTMLButtonElement;

  async function rendered() {
    const refresh = vi.fn();
    render(<ModelsPage lang="zh" refresh={refresh} />);
    await screen.findByText("gpt-5-codex");
    return refresh;
  }

  it("renders scanned rows and starts clean", async () => {
    await rendered();
    expect(screen.getByText("qwen3-coder")).toBeTruthy();
    expect(screen.getByText("未标注（空）")).toBeTruthy(); // 第一行三态
    expect(screen.getByText("支持图片")).toBeTruthy(); // 第二行
    expect(screen.getByText("无未保存修改")).toBeTruthy();
    expect(saveBtn().disabled).toBe(true);
  });

  it("切换 cycles 未标注→纯文本→图片→未标注 and tracks dirty", async () => {
    await rendered();
    const cycleFirst = () => screen.getAllByText("切换")[0];
    fireEvent.click(cycleFirst());
    expect(screen.getByText("纯文本")).toBeTruthy();
    expect(screen.getByText(/1 处未保存/)).toBeTruthy();
    fireEvent.click(cycleFirst());
    expect(screen.getAllByText("支持图片").length).toBe(2); // 两行都是支持图片
    fireEvent.click(cycleFirst());
    expect(screen.getByText("未标注（空）")).toBeTruthy();
  });

  it("save sends only changed rows with drafted value", async () => {
    const refresh = await rendered();
    fireEvent.click(screen.getAllByText("切换")[0]); // null → text_only
    fireEvent.click(saveBtn());
    await waitFor(() => expect(coreMock.mock.calls.some((c) => c[0] === "models-set")).toBe(true));
    const stdin = (coreMock.mock.calls.find((c) => c[0] === "models-set")![1] as { stdin: unknown[] }).stdin;
    expect(stdin).toEqual([
      { harness: "codex", provider: "?", model: "gpt-5-codex", value: "text_only" },
    ]);
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });

  it("重测 probes the row and reloads rows (draft preserved)", async () => {
    await rendered();
    fireEvent.click(screen.getAllByText("切换")[0]); // 制造一个未保存 draft
    fireEvent.click(screen.getAllByText("重测")[0]);
    await waitFor(() =>
      expect(coreMock.mock.calls.some(
        (c) => c[0] === "probe" && JSON.stringify((c[1] as { args?: string[] }).args) === JSON.stringify(["--harness", "codex", "--provider", "?", "--model", "gpt-5-codex"]),
      )).toBe(true),
    );
    await waitFor(() => expect(coreMock.mock.calls.filter((c) => c[0] === "models-scan").length).toBeGreaterThanOrEqual(2));
    expect(screen.getByText(/1 处未保存/)).toBeTruthy(); // refreshRows 不清 draft
  });
});
```

- [ ] **Step 2: 跑通过 + tsc + Commit**

Run: `pnpm -C gui test` → PASS；`pnpm -C gui build`。

```bash
git add gui/src/pages/Models.test.tsx
git commit -m "test(gui): models page component tests — tri-state cycle, dirty set, save-only-changed, retest (G5)"
```

---

### Task 5: Overview 组件测试——横幅/链路/抽屉/自动区/诊断/开关（G2/G3/G4 UI 面）

**Files:**
- Create: `gui/src/pages/Overview.test.tsx`

- [ ] **Step 1: 写测试**

```tsx
// @vitest-environment jsdom
// G2/G3/G4 的 UI 面：横幅端口、链路旁路、详情抽屉（含配置文件打开入口=决策③）、
// 自动处理/需要你区、诊断弹层两态、路由开关调用。数据链路已由 test_e2e_g2~g4 覆盖。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Overview } from "./Overview";
import type { StatusData } from "../shell/useStatus";

const startService = vi.fn(async () => {});
const stopService = vi.fn(async () => {});
const openPath = vi.fn(async () => {});

vi.mock("../core", () => ({
  core: vi.fn(),
  startService: (...a: unknown[]) => startService(...a),
  stopService: (...a: unknown[]) => stopService(...a),
  openPath: (...a: unknown[]) => openPath(...a),
}));

import { core } from "../core";
const coreMock = vi.mocked(core);

const STATUS = {
  service_alive: true,
  routing_on: true,
  bind_port: 8787,
  harnesses: {
    claude: {
      base_url: "http://127.0.0.1:8787",
      ownership: "ours",
      has_snapshot: true,
      config_path: "C:/Users/t/.claude/settings.json",
    },
  },
  tools: [{ name: "cc-switch", port: 15721, online: false, active_provider: null, provider_base_url: null }],
  relays: [
    { name: "cc-claude", protocol: "anthropic", base_url: "http://127.0.0.1:15721", via: "cc-switch", models: ["*"], suppressed: false, has_key: true },
    { name: "direct-codex", protocol: "responses", base_url: "https://up.example", via: null, models: ["*"], suppressed: false, has_key: false },
  ],
  snapshots: {
    claude: { base_url: "https://origin.example", key_ref: "env.ANTHROPIC_AUTH_TOKEN", model: "glm-5", second_hop: null, ts: 1 },
  },
  vlm: { model: "vl", base_url: "https://x", format: "chat", configured: true, custom_prompts: false, groups: [] },
  setup_state: { has_config: true, capability_confirmed: true, vlm_configured: true },
  first_run: false,
} as unknown as StatusData;

const EVENTS = [
  { ts: 1, type: "reclaim", harness: "codex", from: "http://127.0.0.1:57321/v1", to: "http://127.0.0.1:8787" },
];

function renderOverview(status: StatusData = STATUS, showDiag = false) {
  const refresh = vi.fn();
  const setShowDiag = vi.fn();
  render(<Overview status={status} refresh={refresh} lang="zh" showDiag={showDiag} setShowDiag={setShowDiag} />);
  return { refresh, setShowDiag };
}

describe("Overview (G2/G3/G4 UI)", () => {
  beforeEach(() => {
    coreMock.mockReset();
    startService.mockClear();
    stopService.mockClear();
    openPath.mockClear();
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "events") return [...EVENTS];
      return {};
    });
  });

  it("banner shows service state and bind_port from status (决策⑥c：不硬编码)", () => {
    renderOverview();
    expect(screen.getByText("服务运行中")).toBeTruthy();
    expect(screen.getByText(/127\.0\.0\.1:8787 · 自动对账中/)).toBeTruthy();
    expect(screen.getByText("✓ 已接管")).toBeTruthy();
  });

  it("routing off → relay hop marked 已旁路 (G2 关态拓扑)", () => {
    renderOverview({ ...STATUS, service_alive: false, routing_on: false } as StatusData);
    expect(screen.getAllByText(/已旁路/).length).toBeGreaterThanOrEqual(1); // relay 与离线工具都可能旁路
  });

  it("drawer shows snapshot, relays, config path with 打开 entry (决策③)", async () => {
    const prompt = vi.spyOn(window, "prompt").mockReturnValue(null);
    renderOverview();
    fireEvent.click(screen.getByText(/详情：快照 · relay · 停用/));
    expect(screen.getByText(/https:\/\/origin\.example/)).toBeTruthy(); // 接管快照
    expect(screen.getByText(/cc-claude/)).toBeTruthy();
    expect(screen.getByText(/direct-codex/)).toBeTruthy();
    expect(screen.getByText("补填 key")).toBeTruthy(); // 需要你区动作（缺 key 的 direct relay）
    expect(screen.getByText("配置文件")).toBeTruthy();
    fireEvent.click(screen.getByText("打开"));
    await waitFor(() => expect(openPath).toHaveBeenCalledWith("C:/Users/t/.claude/settings.json"));
    prompt.mockRestore();
  });

  it("自动处理区 maps event types to human text (G3 绿条等效)", async () => {
    renderOverview();
    expect(await screen.findByText(/漂移已自动抢回/)).toBeTruthy();
  });

  it("需要你区 lists direct relays missing keys when no diag report", () => {
    renderOverview();
    expect(screen.getByText(/直连上游缺 API key/)).toBeTruthy();
  });

  it("diag modal shows loading then auto-fixed actions (G4 等效)", async () => {
    let resolveDiag!: (v: unknown) => void;
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "events") return [...EVENTS];
      if (verb === "diagnose") return new Promise((r) => { resolveDiag = r; });
      return {};
    });
    renderOverview(STATUS, true);
    expect(screen.getByText(/诊断中…/)).toBeTruthy();
    resolveDiag({
      actions: [{ type: "auto_fix", harness: "claude", fix: "restart", ok: true }],
      needs_you: [],
      observed: { service_alive: true, tools: [] },
    });
    expect(await screen.findByText(/已自动修复/)).toBeTruthy();
    expect(screen.getByText(/claude auto_fix\(restart\)/)).toBeTruthy();
  });

  it("routing toggle calls stopService when on (G2 开关等效)", async () => {
    renderOverview();
    const track = document.querySelector(".track") as HTMLElement;
    fireEvent.click(track);
    await waitFor(() => expect(stopService).toHaveBeenCalledTimes(1));
    expect(startService).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 跑通过 + tsc + Commit**

Run: `pnpm -C gui test` → PASS；`pnpm -C gui build`。

```bash
git add gui/src/pages/Overview.test.tsx
git commit -m "test(gui): overview component tests — banner/bind_port, chain bypass, drawer+open entry, auto/needs-you areas, diag modal, toggle (G2/G3/G4 UI)"
```

---

### Task 6: Wizard 组件测试——两步流转与表单门禁（G1 UI 面）

**Files:**
- Create: `gui/src/wizard/Wizard.test.tsx`

- [ ] **Step 1: 写测试**

```tsx
// @vitest-environment jsdom
// G1 UI 面：key 空禁用下一步、vlm-set payload、两步流转、跳过/完成两条确认路径。
// 确认标志语义已由 test_e2e_g1_wizard.py + tests/test_proxy_verbs.py 覆盖。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Wizard } from "./Wizard";

vi.mock("../core", () => ({ core: vi.fn() }));

import { core } from "../core";
const coreMock = vi.mocked(core);

const MODELS = {
  models: [
    { harness: "codex", provider: "?", model: "gpt-5-codex", value: null, source: null, probe_cached: null },
    { harness: "qwen-code", provider: "?", model: "qwen3-coder", value: "text_only", source: "probe", probe_cached: "text_only" },
  ],
};

const nextBtn = () => screen.getByText(/下一步：过目模型能力/) as HTMLButtonElement;

async function atStep2() {
  const onDone = vi.fn();
  render(<Wizard onDone={onDone} />);
  fireEvent.change(document.querySelector('input[type="password"]') as HTMLInputElement, { target: { value: "sk-wiz" } });
  fireEvent.click(nextBtn());
  await screen.findByText("gpt-5-codex");
  return onDone;
}

describe("Wizard (G1 UI)", () => {
  beforeEach(() => {
    coreMock.mockReset();
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      return {};
    });
  });

  it("step1: next disabled until api_key filled; enabled after", () => {
    render(<Wizard onDone={vi.fn()} />);
    expect(nextBtn().disabled).toBe(true); // G1：key 空时「下一步」禁用
    fireEvent.change(document.querySelector('input[type="password"]') as HTMLInputElement, { target: { value: "sk-wiz" } });
    expect(nextBtn().disabled).toBe(false);
  });

  it("step1 → step2: vlm-set receives model/base_url/api_key; step2 lists scanned rows", async () => {
    await atStep2();
    const stdin = (coreMock.mock.calls.find((c) => c[0] === "vlm-set")![1] as { stdin: Record<string, unknown> }).stdin;
    expect(stdin.vlm).toMatchObject({ model: "qwen-vl-max", api_key: "sk-wiz" });
    expect(screen.getByText("qwen3-coder")).toBeTruthy();
    expect(screen.getByText("未标注")).toBeTruthy(); // 三态标签渲染
  });

  it("跳过 sends empty models-set and closes (onDone)", async () => {
    const onDone = await atStep2();
    fireEvent.click(screen.getByText(/跳过（按默认）/));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    const stdin = (coreMock.mock.calls.find((c) => c[0] === "models-set")![1] as { stdin: unknown[] }).stdin;
    expect(stdin).toEqual([]);
  });

  it("完成 sends all scanned rows as-is (过目即用户意图)", async () => {
    const onDone = await atStep2();
    fireEvent.click(screen.getByText(/完成 → 开启路由/));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    const stdin = (coreMock.mock.calls.find((c) => c[0] === "models-set")![1] as { stdin: unknown[] }).stdin;
    expect(stdin).toEqual(MODELS.models.map((r) => ({ harness: r.harness, provider: r.provider, model: r.model, value: r.value })));
  });

  it("vlm-set failure keeps step1 with error visible", async () => {
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "vlm-set") throw new Error("masked placeholder not allowed");
      return {};
    });
    render(<Wizard onDone={vi.fn()} />);
    fireEvent.change(document.querySelector('input[type="password"]') as HTMLInputElement, { target: { value: "sk" } });
    fireEvent.click(nextBtn());
    expect(await screen.findByText(/masked placeholder not allowed/)).toBeTruthy();
    expect(nextBtn().disabled).toBe(false); // 仍可重试
  });
});
```

- [ ] **Step 2: 跑通过 + tsc + Commit**

```bash
pnpm -C gui test && pnpm -C gui build
git add gui/src/wizard/Wizard.test.tsx
git commit -m "test(gui): wizard component tests — step gating, vlm-set payload, skip/finish paths (G1 UI)"
```

---

### Task 7: CloseGuard 决策逻辑（G10 可自动化部分）+ App 核心不可用横幅（G13 UI 契约）

**Files:**
- Create: `gui/src/shell/CloseGuard.test.tsx`
- Create: `gui/src/App.test.tsx`

- [ ] **Step 1: CloseGuard 测试（新文件）**

```tsx
// @vitest-environment jsdom
// G10 可自动化部分：关闭事件的四种决策路径（记住 ui / 记住 stop / 弹确认→仅关界面 /
// 弹确认→停止并记住）。真窗口隐藏/托盘菜单的实机行为不可 jsdom 模拟，留人工（修订手册）。
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CloseGuard } from "./CloseGuard";

const h = vi.hoisted(() => ({
  hide: vi.fn(),
  destroy: vi.fn(),
  handlers: [] as Array<(e: { preventDefault: () => void }) => void>,
}));

vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => ({
    onCloseRequested: (cb: (e: { preventDefault: () => void }) => void) => {
      h.handlers.push(cb);
      return Promise.resolve(() => {});
    },
    hide: h.hide,
    destroy: h.destroy,
  }),
}));

const stopService = vi.fn(async () => {});
vi.mock("../core", () => ({ stopService: (...a: unknown[]) => stopService(...a) }));

function triggerClose() {
  expect(h.handlers.length).toBeGreaterThan(0);
  act(() => {
    h.handlers[h.handlers.length - 1]({ preventDefault: () => {} });
  });
}

describe("CloseGuard decision logic (G10)", () => {
  beforeEach(() => {
    localStorage.clear();
    h.hide.mockClear();
    h.destroy.mockClear();
    stopService.mockClear();
    h.handlers.length = 0;
  });

  it("no saved mode → asks; 仅关闭界面 hides without stopping or remembering", async () => {
    render(<CloseGuard />);
    await act(async () => { triggerClose(); });
    expect(screen.getByText(/关闭 vision-relay 控制台？/)).toBeTruthy();
    fireEvent.click(screen.getByText("仅关闭界面（服务继续）"));
    await waitFor(() => expect(h.hide).toHaveBeenCalledTimes(1));
    expect(h.destroy).not.toHaveBeenCalled();
    expect(stopService).not.toHaveBeenCalled();
    expect(localStorage.getItem("vr.close")).toBeNull(); // 未勾记住 → 不落选择
  });

  it("asks → 停止服务 + 记住选择 → localStorage=stop", async () => {
    render(<CloseGuard />);
    await act(async () => { triggerClose(); });
    fireEvent.click(screen.getByLabelText(/记住我的选择/));
    fireEvent.click(screen.getByText("关闭界面并停止服务"));
    await waitFor(() => expect(h.destroy).toHaveBeenCalledTimes(1)); // stopService→destroy 链路完成
    expect(stopService).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem("vr.close")).toBe("stop");
  });

  it("remembered ui → close hides directly, no dialog", async () => {
    localStorage.setItem("vr.close", "ui");
    render(<CloseGuard />);
    await act(async () => { triggerClose(); });
    expect(screen.queryByText(/关闭 vision-relay 控制台？/)).toBeNull();
    await waitFor(() => expect(h.hide).toHaveBeenCalledTimes(1));
  });

  it("remembered stop → close stops service and destroys, no dialog", async () => {
    localStorage.setItem("vr.close", "stop");
    render(<CloseGuard />);
    await act(async () => { triggerClose(); });
    expect(screen.queryByText(/关闭 vision-relay 控制台？/)).toBeNull();
    await waitFor(() => expect(h.destroy).toHaveBeenCalledTimes(1));
    expect(stopService).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: App 横幅测试（新文件 `gui/src/App.test.tsx`）**

```tsx
// @vitest-environment jsdom
// G13 UI 契约：核心不可用时顶部红条 + 总览占位。PATH 真实移除场景留人工（修订手册）。
// detectCore 的失败文案契约已在 core.integration.test.ts 覆盖（/找不到 vision-relay 核心/）。
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async () => vi.fn()) }));
vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => ({
    onCloseRequested: vi.fn(async () => vi.fn()),
    hide: vi.fn(),
    destroy: vi.fn(),
  }),
}));
vi.mock("./core", () => ({
  core: vi.fn(async () => {
    throw new Error("找不到 vision-relay 核心：请在设置里指定路径");
  }),
  startService: vi.fn(),
  stopService: vi.fn(),
  openPath: vi.fn(),
  setCorePath: vi.fn(),
}));

describe("App shell (G13 UI)", () => {
  it("shows 核心不可用 banner when status verb fails", async () => {
    render(<App />);
    expect(await screen.findByText(/核心不可用/)).toBeTruthy();
  });

  it("overview shows loading card while status unavailable", async () => {
    render(<App />);
    expect(await screen.findByText(/加载中/)).toBeTruthy();
  });
});
```

- [ ] **Step 3: 跑通过 + tsc + Commit**

Run: `pnpm -C gui test` → 全部 PASS（预计 ≥ 40 tests / ≥ 11 files）；`pnpm -C gui build`。

```bash
git add gui/src/shell/CloseGuard.test.tsx gui/src/App.test.tsx
git commit -m "test(gui): close-guard decision logic (G10) + app core-unavailable banner (G13 UI)"
```

---

### Task 8: 修订版手工测试范围文档 + 旧手册加注

**Files:**
- Create: `docs/superpowers/plans/2026-08-23-vision-relay-phase2-manual-test-revised.md`
- Modify: `docs/superpowers/plans/2026-08-22-vision-relay-phase2-m2-manual-test.md`（顶部加注）

- [ ] **Step 1: 写修订版手册（内容如下，若 Task 2–7 出现 skip 分歧用例，追加到第三节）**

````markdown
# vision-relay 二期 手工测试范围（自动化后修订版）· 2026-08-23

> 本文件划分 M2 验收的「已自动化」与「必须人工」范围，取代
> [2026-08-22 手册](2026-08-22-vision-relay-phase2-m2-manual-test.md) 的执行范围划分（原文保留为历史记录）。
> 环境：Windows 本机；`vision-relay` 已 pip 安装且在 PATH；CC Switch / Codex++ 至少一个可用；VLM key 可用。

## 一、已自动化（跑门禁即验证，无需人工）

| 原手工项 | 自动化位置 |
|---|---|
| G1 首次向导（两步/跳过/完成/不重跑） | `tests/test_e2e_g1_wizard.py`（后端链路）+ `gui/src/wizard/Wizard.test.tsx`（表单门禁/payload） |
| G2 路由开关（启停/接线/还原全程） | `tests/test_e2e_g2_routing.py` + `gui/src/pages/Overview.test.tsx`（开关调用/横幅/旁路） |
| G3 刷新与抢回 | `tests/test_e2e_g3_reclaim.py` + Overview 测试（自动处理区文案） |
| G4 诊断报告（两种意图） | `tests/test_e2e_g4_diagnose.py` + Overview 测试（弹层两态） |
| G5 模型能力（三态/保存/重测） | `tests/test_e2e_g5_models.py` + `gui/src/pages/Models.test.tsx` |
| G6 VLM 设置与测试 | `tests/test_e2e_g6_vlm.py` + Settings 测试（四模式映射/payload/打码留空规则） |
| G7 提示词编辑 | `tests/test_e2e_g7_prompts.py` |
| G8 识图记录（三段/会话/转发） | `tests/test_e2e_g8_visionlog.py` |
| G12 未标注开关 | `tests/test_e2e_g8_visionlog.py`（核心行为）+ Settings 测试（留存兜底） |
| G10 关闭**决策逻辑**（四种路径/记住选择） | `gui/src/shell/CloseGuard.test.tsx` |
| G11 保存语义（脏计数/payload/放弃/失败提示） | `gui/src/pages/Settings.test.tsx` |
| G13 核心不可用**UI 契约**（红条文案） | `gui/src/App.test.tsx` + `gui/src/core.integration.test.ts`（detectCore 指引） |

## 二、必须人工（含原因）

| 项 | 步骤 | 为什么不能自动化 |
|---|---|---|
| G10 实机：窗口真隐藏到托盘、托盘四项菜单（打开/路由开关/诊断/退出真停服务） | dev 运行 GUI 点托盘逐项 | jsdom 无法模拟系统托盘/真窗口生命周期 |
| G11 实机：切页往返后的视觉脏指示 | 设置页改字段→切总览→切回 | 组件测试已钉行为（见三）；视觉确认留人工 |
| G13 实机：PATH 真移除 vision-relay 启动 GUI→设置页手填路径恢复 | 临时改 PATH 启动 | 真进程发现链路（which_core→PATH 扫描）无法 jsdom 模拟 |
| 决策③实机：「打开」按钮真调起系统默认编辑器 | 总览抽屉点打开 | open_path 走系统 shell（cmd start），jsdom 无法验证 |
| 视觉核对 mockup（布局/措辞/配色/三段明细颜色） | 对照 `docs/superpowers/specs/gui-mockups/index.html` | 视觉回归不在本期范围 |
| M1 手册 A2：真 CC Switch 抢线/吸收（真工具回读行为） | CC Switch 切换供应商→refresh | E2E 用手改文件模拟；真工具的档案回读只有实机能测 |
| M1 手册 A4：真供应商探针三档判定（含吞图模型） | probe 不同模型核对三档 | 需真实 key 与真实模型行为 |
| M1 手册 A5 实机：真 VLM key 发图→识图记录三段 | curl 发带图请求→GUI 看记录 | 数据面已用 mock 全链路验证；真 VLM 返回质量需目验 |

## 三、自动化发现的分歧（待产品确认）

1. **G11「切页保持」**：手册原预期「改字段不保存切页再回来，脏计数与字段保持」；
   实现是切页卸载组件、本地 state 丢失（`Settings.test.tsx` 重挂用例钉住现状）。
   两个选项：① 接受现状，交互文案已提示「输入类修改统一由页底保存」——切页前记得保存；
   ② 需要跨页保持则把草稿状态提升到 App 层或用 CSS 隐藏代替卸载（一次小重构）。

## 四、M3 打包后另行验收（不属于本轮手工范围）

三平台安装包「装完即用」（MSI/DMG/AppImage 装完→开关路由→诊断报告）、macOS/Linux 实机回归、杀毒误报与体积观察（spec §8 已知代价）。
````

- [ ] **Step 2: 旧手册顶部加注（`2026-08-22-vision-relay-phase2-m2-manual-test.md` 文件最前面插入）**

```markdown
> **[2026-08-23 注]** 执行范围以[修订版手册](2026-08-23-vision-relay-phase2-manual-test-revised.md)为准：
> G1–G8/G12 及 G10/G11/G13 的可模拟部分已自动化（vitest 组件测试 + pytest E2E）；本手册保留为原始验收记录。
```

- [ ] **Step 3: 最终全量验证 + Commit**

Run：`pnpm -C gui test`（≥ 40 tests / ≥ 11 files）+ `pnpm -C gui build` + `.venv/Scripts/python -m pytest -q`（仍为 407 passed，本计划不动 Python）+ `.venv/Scripts/python -m ruff format --check . && .venv/Scripts/python -m ruff check .`。

```bash
git add docs/superpowers/plans/2026-08-23-vision-relay-phase2-manual-test-revised.md docs/superpowers/plans/2026-08-22-vision-relay-phase2-m2-manual-test.md
git commit -m "docs: revised manual-test scope — automated coverage map, human-only items with reasons, G11 divergence note"
```

---

## 附录：执行纪律（给子代理）

1. **特征测试纪律优先**（见计划头部三条）：跑挂先分诊，不许为了让测试变绿而改生产组件或放宽断言；分歧 → `it.skip` + 注释 + 登记到修订手册第三节 + 最终汇报。
2. 严格按 checkbox 顺序；每步真跑过命令才打勾；每 Task 一 commit（计划已给 message）。
3. 门禁：`pnpm -C gui test` 只增不减（25 → ≥ 40）、`pnpm -C gui build` 的 tsc 必须零错误（新测试文件受 `noUnusedLocals/noUnusedParameters` 约束）；Python 侧 407 不得变化（本计划不碰 `vision_relay/` 与 `tests/`）。
4. 不引入计划外依赖（只装 `jsdom` + `@testing-library/react`）；不修改任何 `gui/src` 生产代码。
5. 计时器/轮询用例若 flaky：按纪律 1 处理（skip + 注释），不许加 sleep 硬等。
