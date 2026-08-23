// @vitest-environment jsdom
// G13 UI 契约：核心不可用时顶部红条 + 总览占位。PATH 真实移除场景留人工（修订手册）。
// detectCore 的失败文案契约已在 core.integration.test.ts 覆盖（/找不到 vision-relay 核心/）。
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
  beforeEach(() => {
    // jsdom 默认 navigator.language=en-US；固定中文以便断言 UI 契约文案（localStorage 桩来自 setup.ts）
    localStorage.setItem("vr.lang", "zh");
  });

  it("shows 核心不可用 banner when status verb fails", async () => {
    render(<App />);
    expect(await screen.findByText(/核心不可用/)).toBeTruthy();
  });

  it("overview shows loading card while status unavailable", async () => {
    render(<App />);
    expect(await screen.findByText(/加载中/)).toBeTruthy();
  });
});
