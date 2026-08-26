// @vitest-environment jsdom
// 冒烟：验证 jsdom + Testing Library + tsx 测试链路端到端可用（G11 前置）。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsPage } from "./Settings";
import type { StatusData } from "../shell/useStatus";

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
    // 注意：状态栏 span 是 `{…}——输入类修改统一由这里保存生效` 单个文本节点，需正则匹配
    expect(screen.getByText(/无未保存修改/)).toBeTruthy();
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
      routing: { unknown_default: "text_only", harnesses: ["claude", "codex", "qwen-code", "zcode"] },
      vision_log: { enabled: true, retention_days: 7 },
    });

    await waitFor(() => expect(screen.getByText(/无未保存修改/)).toBeTruthy());
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

  it("four-mode VLM test: tier2c maps to mode tier2 + question + custom_prompt", async () => {
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
    const stdin = (coreMock.mock.calls.find((c) => c[0] === "vlm-test")![1] as { stdin: Record<string, unknown> }).stdin;
    expect(stdin).toEqual({ mode: "tier2", question: "图里几个字", custom_prompt: "我的自选" });

    expect(await screen.findByText(/红色/)).toBeTruthy(); // 结果框展示 desc
  });

  it("switching to tier1 keeps leftover question (2026-08-23 决策：保持现状)", async () => {
    // 分歧已裁决：切换测试模式不清空「聚焦问题/自选提示词」输入，tier1 会带上一次
    // 输入的 question（服务端 tier1 提示词会忽略它，仅 payload 不洁——用户拍板保持）。
    // 本用例钉住现状；原文登记在修订手册第三节第 2 条。
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "config") return JSON.parse(JSON.stringify(CONFIG));
      if (verb === "vlm-test") return { desc: "红色", duration_ms: 5, model: "vl-mock" };
      return {};
    });
    await rendered();
    const mode = screen.getByRole("combobox");
    // 先在 tier2c 模式输入问题，再切回 tier1
    fireEvent.change(mode, { target: { value: "tier2c" } });
    fireEvent.change(screen.getByPlaceholderText("聚焦问题…"), { target: { value: "图里几个字" } });
    fireEvent.change(mode, { target: { value: "tier1" } });
    fireEvent.click(screen.getByText("开始测试"));
    await waitFor(() => expect(coreMock.mock.calls.some((c) => c[0] === "vlm-test")).toBe(true));
    const calls = coreMock.mock.calls.filter((c) => c[0] === "vlm-test");
    const stdin = (calls[calls.length - 1][1] as { stdin: Record<string, unknown> }).stdin;
    expect(stdin).toEqual({ mode: "tier1", question: "图里几个字", custom_prompt: null }); // 现状：question 残留
  });
});

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
    expect(screen.getByText(/无未保存修改/)).toBeTruthy(); // 脏计数归零
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

describe("SettingsPage routing scope (zcode 2026-08-26)", () => {
  beforeEach(() => {
    coreMock.mockReset();
    coreMock.mockImplementation(async (verb: string, _opts?: { stdin?: unknown }) => {
      if (verb === "config")
        return {
          vlm: { model: "m", base_url: "b", format: "chat", custom_tier1: null, custom_tier2: null },
          vlm_by_harness: {},
          routing: { unknown_default: "text_only", harnesses: ["claude", "codex", "qwen-code", "zcode"] },
          vision_log: { enabled: true, retention_days: 7 },
        };
      return { saved: true };
    });
  });

  it("路由范围勾选随 managed 列表渲染并随保存提交 harnesses", async () => {
    const calls: unknown[][] = [];
    coreMock.mockImplementation(async (verb: string, opts?: { stdin?: unknown }) => {
      calls.push([verb, opts?.stdin]);
      if (verb === "config")
        return {
          vlm: { model: "m", base_url: "b", format: "chat", custom_tier1: null, custom_tier2: null },
          vlm_by_harness: {},
          routing: { unknown_default: "text_only", harnesses: ["claude", "codex", "qwen-code", "zcode"] },
          vision_log: { enabled: true, retention_days: 7 },
        };
      return { saved: true };
    });
    render(<SettingsPage lang="zh" status={null} refresh={() => {}} setLang={() => {}} />);
    await waitFor(() => expect(screen.getByText("路由范围")).toBeTruthy());
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes.length).toBeGreaterThanOrEqual(4); // 四工具勾选框
    fireEvent.click(screen.getByLabelText("zcode")); // 取消勾选 zcode（不弹窗：status=null 无 zcode 运行信号）
    fireEvent.click(screen.getByText("💾 保存设置"));
    await waitFor(() => {
      const settings = calls.find(([v]) => v === "settings-set") as [string, { routing?: { harnesses?: string[] } }];
      expect(settings[1].routing?.harnesses).toEqual(["claude", "codex", "qwen-code"]);
    });
  });
});

describe("SettingsPage zcode 三选弹窗 (M4)", () => {
  // 前置：zcode 正在运行且 harnesses 含 zcode（否则 save() 不触发三选弹窗）
  const STATUS_ZCODE_RUNNING = {
    service_alive: true,
    routing_on: true,
    bind_port: 8787,
    harnesses: { zcode: { base_url: "http://127.0.0.1:8787", ownership: "ours", has_snapshot: true } },
    tools: [],
    relays: [],
    snapshots: {},
    vlm: { model: "m", base_url: "b", format: "chat", configured: true, custom_prompts: false, groups: [] },
    setup_state: { has_config: true, capability_confirmed: true, vlm_configured: true },
    first_run: false,
    zcode_runtime: { running: true, needs_restart: false },
  } as unknown as StatusData;

  beforeEach(() => {
    coreMock.mockReset();
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "config")
        return {
          vlm: { model: "m", base_url: "b", format: "chat", custom_tier1: null, custom_tier2: null },
          vlm_by_harness: {},
          routing: { unknown_default: "text_only", harnesses: ["claude", "codex", "qwen-code", "zcode"] },
          vision_log: { enabled: true, retention_days: 7 },
        };
      return { saved: true };
    });
  });

  it("M4: 三选弹窗选「保留勾选」后 zcode 复选框回滚为勾选", async () => {
    render(<SettingsPage lang="zh" status={STATUS_ZCODE_RUNNING} refresh={vi.fn()} setLang={vi.fn()} />);
    await screen.findByDisplayValue("m"); // config 落表单（managed 初值=已保存的四个勾选）

    const cb = screen.getByLabelText("zcode") as HTMLInputElement;
    expect(cb.checked).toBe(true); // 已保存值含 zcode
    fireEvent.click(cb); // 取消勾选 → dirty
    expect(cb.checked).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /保存设置/ })); // zcode 在跑 → 弹三选
    fireEvent.click(await screen.findByRole("button", { name: "保留勾选" })); // kind=abort

    // M4 核心：abort=放弃本次取消——复选框回滚为勾选（与已保存值一致，dirty 不残留语义）
    expect((screen.getByLabelText("zcode") as HTMLInputElement).checked).toBe(true);

    // 再点保存不再弹三选（回滚后无「取消勾选 zcode」触发条件），直接走保存
    fireEvent.click(screen.getByRole("button", { name: /保存设置/ }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "保留勾选" })).toBeNull());
    await waitFor(() => expect(coreMock.mock.calls.some((c) => c[0] === "vlm-set")).toBe(true)); // 本次保存真正发出
  });
});
