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
