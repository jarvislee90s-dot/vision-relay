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

describe("ModelsPage 探测反馈", () => {
  beforeEach(() => {
    coreMock.mockReset();
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      return {};
    });
  });

  it("重测:无目标时弹 reason(不可达),不再静默", async () => {
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    coreMock.mockImplementation(async (verb: string, opts?: { args?: string[] }) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      if (verb === "probe" && opts?.args?.[0] === "--all-untested") return { probed: 0, results: [] };
      return { result: null, target_found: false, reason: "claude: 路由工具不在线,且未配置可探测的直连上游" };
    });
    render(<ModelsPage lang="zh" refresh={vi.fn()} />);
    await screen.findByText("gpt-5-codex");
    fireEvent.click(screen.getAllByText("重测")[0]);
    await waitFor(() => expect(alert).toHaveBeenCalledWith(expect.stringContaining("路由工具不在线")));
    alert.mockRestore();
  });

  it("重测:有目标但无结论时提示不下判定", async () => {
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      return { result: null, target_found: true, reason: null };
    });
    render(<ModelsPage lang="zh" refresh={vi.fn()} />);
    await screen.findByText("gpt-5-codex");
    fireEvent.click(screen.getAllByText("重测")[0]);
    await waitFor(() => expect(alert).toHaveBeenCalledWith(expect.stringContaining("无结论")));
    alert.mockRestore();
  });

  it("探测全部:全部无结论时汇总弹窗;有结论时静默", async () => {
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    coreMock.mockImplementation(async (verb: string, opts?: { args?: string[] }) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      if (verb === "probe" && opts?.args?.[0] === "--all-untested")
        return {
          probed: 2,
          results: [
            { result: null, target_found: false, reason: "claude: 不可达" },
            { result: null, target_found: true, reason: null },
          ],
        };
      return {};
    });
    render(<ModelsPage lang="zh" refresh={vi.fn()} />);
    await screen.findByText("gpt-5-codex");
    fireEvent.click(screen.getByText("🔍 探测全部未测"));
    await waitFor(() => expect(alert).toHaveBeenCalledWith(expect.stringContaining("均无结论")));
    alert.mockClear();
    // 有结论 → 静默
    coreMock.mockImplementation(async (verb: string, opts?: { args?: string[] }) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      if (verb === "probe" && opts?.args?.[0] === "--all-untested")
        return { probed: 1, results: [{ result: "image", target_found: true, reason: null }] };
      return {};
    });
    fireEvent.click(screen.getByText("🔍 探测全部未测"));
    await waitFor(() => expect(coreMock).toHaveBeenCalled());
    expect(alert).not.toHaveBeenCalled();
    alert.mockRestore();
  });
});
