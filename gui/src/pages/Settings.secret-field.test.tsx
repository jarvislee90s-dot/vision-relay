// @vitest-environment jsdom
// 设置页 VLM API key 隐身按钮（显示/隐藏真实已存 key）——spec §6 设置·key 显隐。
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
    api_key: "●●●●", // 被动 config 仍打码
    format: "chat",
    custom_tier1: null,
    custom_tier2: null,
  },
  vlm_by_harness: { claude: { model: "vl-claude", base_url: "https://y.example/v1" } },
  routing: { unknown_default: "text_only" },
  vision_log: { enabled: true, retention_days: 7 },
};

const SECRET = {
  vlm: { api_key: "sk-global-real" },
  vlm_by_harness: { claude: { api_key: "sk-claude-real" } },
};

describe("SettingsPage SecretField（VLM key 显隐）", () => {
  beforeEach(() => {
    coreMock.mockReset();
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "config") return JSON.parse(JSON.stringify(CONFIG));
      if (verb === "vlm-secret") return JSON.parse(JSON.stringify(SECRET));
      return {};
    });
  });

  const saveBtn = () => screen.getByText("💾 保存设置") as HTMLButtonElement;

  async function rendered() {
    render(<SettingsPage lang="zh" status={null} refresh={vi.fn()} setLang={vi.fn()} />);
    await screen.findByDisplayValue("vl-global");
  }

  it("reveal 拉取真 key 并切为 text", async () => {
    await rendered();
    fireEvent.click(screen.getByRole("button", { name: "显示 API key" }));
    await waitFor(() => expect(coreMock.mock.calls.some((c) => c[0] === "vlm-secret")).toBe(true));
    const input = screen.getByDisplayValue("sk-global-real") as HTMLInputElement;
    expect(input.type).toBe("text");
  });

  it("隐藏 清空并恢复 password", async () => {
    await rendered();
    fireEvent.click(screen.getByRole("button", { name: "显示 API key" }));
    await screen.findByDisplayValue("sk-global-real");
    fireEvent.click(screen.getByRole("button", { name: "隐藏 API key" }));
    expect(screen.queryByDisplayValue("sk-global-real")).toBeNull();
    expect(screen.getByRole("button", { name: "显示 API key" })).toBeTruthy(); // 回到隐藏态
  });

  it("reveal 不标记脏：保存按钮保持禁用", async () => {
    await rendered();
    fireEvent.click(screen.getByRole("button", { name: "显示 API key" }));
    await screen.findByDisplayValue("sk-global-real");
    expect(screen.getByText(/无未保存修改/)).toBeTruthy();
    expect(saveBtn().disabled).toBe(true);
  });

  it("remount（切页）恢复隐藏", async () => {
    const { unmount } = render(<SettingsPage lang="zh" status={null} refresh={vi.fn()} setLang={vi.fn()} />);
    await screen.findByDisplayValue("vl-global");
    fireEvent.click(screen.getByRole("button", { name: "显示 API key" }));
    await screen.findByDisplayValue("sk-global-real");

    unmount(); // 模拟切到别的页
    render(<SettingsPage lang="zh" status={null} refresh={vi.fn()} setLang={vi.fn()} />); // 切回
    await screen.findByDisplayValue("vl-global");
    expect(screen.queryByDisplayValue("sk-global-real")).toBeNull(); // 回到默认不显示
    expect(screen.getByRole("button", { name: "显示 API key" })).toBeTruthy();
  });

  it("真 key 覆盖占位符，●●●● 永不进输入框/保存 payload", async () => {
    await rendered();
    fireEvent.click(screen.getByRole("button", { name: "显示 API key" }));
    await screen.findByDisplayValue("sk-global-real");
    fireEvent.change(screen.getByDisplayValue("vl-global"), { target: { value: "vl-new" } }); // 别处编辑=脏
    fireEvent.click(saveBtn());
    await waitFor(() => expect(coreMock.mock.calls.some((c) => c[0] === "vlm-set")).toBe(true));
    const stdin = (coreMock.mock.calls.find((c) => c[0] === "vlm-set")![1] as { stdin: { vlm: Record<string, unknown> } }).stdin;
    expect(stdin.vlm.api_key).toBe("sk-global-real"); // 幂等重发同值，无害
    expect(JSON.stringify(coreMock.mock.calls)).not.toContain("●●●●"); // 打码占位绝不回传
  });

  it("per-harness 显示该组 key", async () => {
    await rendered();
    fireEvent.click(screen.getByRole("button", { name: "显示 claude 的 API key" }));
    expect(await screen.findByDisplayValue("sk-claude-real")).toBeTruthy();
  });

  it("未配置 key：显示后仍保持隐藏", async () => {
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "config") return JSON.parse(JSON.stringify(CONFIG));
      if (verb === "vlm-secret") return { vlm: { api_key: "" }, vlm_by_harness: {} };
      return {};
    });
    await rendered();
    fireEvent.click(screen.getByRole("button", { name: "显示 API key" }));
    await waitFor(() => expect(coreMock.mock.calls.some((c) => c[0] === "vlm-secret")).toBe(true));
    // 无可显：字段仍空、仍是 password、按钮仍为「显示」
    expect(screen.getByRole("button", { name: "显示 API key" })).toBeTruthy();
    expect(screen.queryByDisplayValue("sk-global-real")).toBeNull();
  });

  it("已输入新 key 再点显示：不覆盖用户输入（评审修正）", async () => {
    await rendered();
    const pw = document.querySelector('input[type="password"]') as HTMLInputElement; // 第一个 password = 全局 key
    fireEvent.change(pw, { target: { value: "sk-new" } });
    fireEvent.click(screen.getByRole("button", { name: "显示 API key" }));
    await waitFor(() => expect(coreMock.mock.calls.some((c) => c[0] === "vlm-secret")).toBe(true));
    const input = screen.getByDisplayValue("sk-new") as HTMLInputElement;
    expect(input.type).toBe("text"); // 用户输入保留、仅切可见
    expect(screen.queryByDisplayValue("sk-global-real")).toBeNull(); // 已存 key 未覆盖输入
  });

  it("显示后再改 key 再隐藏：保留编辑不清空（评审修正）", async () => {
    await rendered();
    fireEvent.click(screen.getByRole("button", { name: "显示 API key" }));
    await screen.findByDisplayValue("sk-global-real");
    fireEvent.change(screen.getByDisplayValue("sk-global-real"), { target: { value: "sk-new" } });
    fireEvent.click(screen.getByRole("button", { name: "隐藏 API key" }));
    const input = screen.getByDisplayValue("sk-new") as HTMLInputElement;
    expect(input.type).toBe("password"); // 编辑保留、仅切回密码态
  });
});
