// @vitest-environment jsdom
// 冒烟：验证 jsdom + Testing Library + tsx 测试链路端到端可用（G11 前置）。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
      routing: { unknown_default: "text_only" },
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

  it.skip("tier1 default sends mode=tier1 with null question/custom_prompt", async () => {
    // 分歧：手册/计划预期「切到 tier1 后 vlm-test 发送 question:null」，
    // 但实现保留 testQ state（切模式不清空聚焦问题输入），切回 tier1 后仍发送上一次的问题。
    // 已登记到修订手册第三节（自动化发现的分歧）。待产品确认：切模式是否应清空问题/自选提示词输入。
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "config") return JSON.parse(JSON.stringify(CONFIG));
      if (verb === "vlm-test") return { desc: "红色", duration_ms: 5, model: "vl-mock" };
      return {};
    });
    await rendered();
    const mode = screen.getByRole("combobox");
    fireEvent.change(mode, { target: { value: "tier1" } });
    fireEvent.click(screen.getByText("开始测试"));
    await waitFor(() => expect(coreMock.mock.calls.some((c) => c[0] === "vlm-test")).toBe(true));
    const calls = coreMock.mock.calls.filter((c) => c[0] === "vlm-test");
    const stdin = (calls[calls.length - 1][1] as { stdin: Record<string, unknown> }).stdin;
    expect(stdin).toEqual({ mode: "tier1", question: null, custom_prompt: null });
  });
});
