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
