// @vitest-environment jsdom
// 自定义测试图：上传后走既有 vlm-test 图片契约，未选图时保持默认 payload。
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
    api_key: "***",
    format: "chat",
    custom_tier1: null,
    custom_tier2: null,
  },
  vlm_by_harness: {},
  routing: { unknown_default: "text_only" },
  vision_log: { enabled: true, retention_days: 7 },
};

async function rendered() {
  render(<SettingsPage lang="zh" status={null} refresh={vi.fn()} setLang={vi.fn()} />);
  await screen.findByDisplayValue("vl-global");
}

describe("SettingsPage custom VLM test image", () => {
  beforeEach(() => {
    coreMock.mockReset();
    coreMock.mockImplementation(async (verb: string) =>
      verb === "config" ? JSON.parse(JSON.stringify(CONFIG)) : {},
    );
  });

  it("sends the selected image to the core", async () => {
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "config") return JSON.parse(JSON.stringify(CONFIG));
      if (verb === "vlm-test") return { desc: "红色方块", duration_ms: 5, model: "vl-mock" };
      return {};
    });
    await rendered();

    const file = new File([new Uint8Array([104, 105])], "screenshot.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("自定义图片"), { target: { files: [file] } });
    expect(await screen.findByAltText("自定义测试图片预览")).toBeTruthy();
    expect(screen.getByText("screenshot.png · image/png")).toBeTruthy();

    fireEvent.click(screen.getByText("开始测试"));
    await waitFor(() => expect(coreMock.mock.calls.some((call) => call[0] === "vlm-test")).toBe(true));
    const call = coreMock.mock.calls.find((call) => call[0] === "vlm-test")!;
    expect((call[1] as { stdin: Record<string, unknown> }).stdin).toEqual({
      mode: "tier1",
      question: null,
      custom_prompt: null,
      image_base64: "aGk=",
      media_type: "image/png",
    });
  });

  it("keeps the default payload when no image is selected", async () => {
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "config") return JSON.parse(JSON.stringify(CONFIG));
      if (verb === "vlm-test") return { desc: "内置图", duration_ms: 5, model: "vl-mock" };
      return {};
    });
    await rendered();
    fireEvent.click(screen.getByText("开始测试"));

    await waitFor(() => expect(coreMock.mock.calls.some((call) => call[0] === "vlm-test")).toBe(true));
    const call = coreMock.mock.calls.find((call) => call[0] === "vlm-test")!;
    expect((call[1] as { stdin: Record<string, unknown> }).stdin).toEqual({
      mode: "tier1",
      question: null,
      custom_prompt: null,
    });
  });

  it("rejects unsupported images without calling the core", async () => {
    await rendered();
    const input = screen.getByLabelText("自定义图片") as HTMLInputElement;
    const file = new File(["text"], "note.txt", { type: "text/plain" });

    fireEvent.change(input, { target: { files: [file] } });
    expect(await screen.findByText(/仅支持 PNG、JPEG、WebP 或 GIF/)).toBeTruthy();
    expect(coreMock.mock.calls.some((call) => call[0] === "vlm-test")).toBe(false);
  });

  it("rejects oversized images without calling the core", async () => {
    await rendered();
    const input = screen.getByLabelText("自定义图片") as HTMLInputElement;
    const file = new File(["png"], "big.png", { type: "image/png" });
    Object.defineProperty(file, "size", { value: 10 * 1024 * 1024 + 1 });

    fireEvent.change(input, { target: { files: [file] } });
    expect(await screen.findByText(/图片不能超过 10 MiB/)).toBeTruthy();
    expect(screen.queryByAltText("自定义测试图片预览")).toBeNull();
    expect(coreMock.mock.calls.some((call) => call[0] === "vlm-test")).toBe(false);
  });

  it("disables the test button while the image is being read", async () => {
    await rendered();
    const btn = () => screen.getByText("开始测试") as HTMLButtonElement;
    expect(btn().disabled).toBe(false);

    const file = new File([new Uint8Array([104, 105])], "screenshot.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("自定义图片"), { target: { files: [file] } });
    // FileReader 读取中：按钮必须禁用，防止此时点击发出默认/旧图请求
    expect(btn().disabled).toBe(true);

    await screen.findByAltText("自定义测试图片预览");
    await waitFor(() => expect(btn().disabled).toBe(false));
  });

  it("clearing an image restores the default payload", async () => {
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "config") return JSON.parse(JSON.stringify(CONFIG));
      if (verb === "vlm-test") return { desc: "内置图", duration_ms: 5, model: "vl-mock" };
      return {};
    });
    await rendered();
    const png = new File([new Uint8Array([104, 105])], "screenshot.png", { type: "image/png" });

    fireEvent.change(screen.getByLabelText("自定义图片"), { target: { files: [png] } });
    expect(await screen.findByAltText("自定义测试图片预览")).toBeTruthy();
    fireEvent.click(screen.getByText("清除图片"));
    fireEvent.click(screen.getByText("开始测试"));

    await waitFor(() => expect(coreMock.mock.calls.some((call) => call[0] === "vlm-test")).toBe(true));
    const call = coreMock.mock.calls.find((call) => call[0] === "vlm-test")!;
    expect((call[1] as { stdin: Record<string, unknown> }).stdin).toEqual({
      mode: "tier1",
      question: null,
      custom_prompt: null,
    });
  });
});
