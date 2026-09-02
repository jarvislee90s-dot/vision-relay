// @vitest-environment jsdom
// G5 UI 面：三态循环、脏集、只发改动行、行内重测（后端链路已由 test_e2e_g5_models.py 覆盖）。
// 2026-08-25 探测体验重做：非激活供应商折叠、防连点+spinner、前端逐行批量探测与汇总、fetch 回环解释。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ModelsPage } from "./Models";

vi.mock("../core", () => ({ core: vi.fn() }));

import { core } from "../core";

const coreMock = vi.mocked(core);

const MODELS = {
  models: [
    { harness: "codex", provider: "?", model: "gpt-5-codex", value: null, source: null, probe_cached: null, is_current: true },
    { harness: "qwen-code", provider: "?", model: "qwen3-coder", value: "image", source: "user", probe_cached: "image", is_current: true },
    { harness: "claude", provider: "Other", model: "kimi-x", value: null, source: null, probe_cached: null, is_current: false },
  ],
};

function mockScan(models = MODELS) {
  coreMock.mockImplementation(async (verb: string) => {
    if (verb === "models-scan") return JSON.parse(JSON.stringify(models));
    return {};
  });
}

describe("ModelsPage (G5 UI)", () => {
  beforeEach(() => {
    coreMock.mockReset();
    mockScan();
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
    expect(screen.getAllByText("未标注（空）").length).toBe(2); // 主表 gpt-5-codex + 折叠区 kimi-x
    expect(screen.getByText("支持图片")).toBeTruthy(); // 第二行
    expect(screen.getByText("无未保存修改")).toBeTruthy();
    expect(saveBtn().disabled).toBe(true);
  });

  it("非激活供应商行默认折叠，展开后可见且重测禁用", async () => {
    await rendered();
    const summary = screen.getByText(/未激活供应商（1 行，不参与探测）/);
    const details = summary.closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(false); // 默认折叠
    fireEvent.click(summary);
    expect(details.open).toBe(true);
    expect(screen.getByText("kimi-x")).toBeTruthy();
    const dead = screen.getByTitle("非当前激活供应商，无可达探测路径");
    fireEvent.click(dead); // 折叠行重测禁用：不发起探测
    await waitFor(() => expect(coreMock.mock.calls.some((c) => c[0] === "probe")).toBe(false));
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
    expect(screen.getAllByText("未标注（空）").length).toBe(2); // 回到未标注（kimi-x 折叠行同标签）
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

  it("重测后非 user 行清 draft（探测结果立即上屏，2026-09-02 回归：draft 不再盖住新标注）", async () => {
    await rendered();
    fireEvent.click(screen.getAllByText("切换")[0]); // gpt-5-codex（source=null）制造未保存 draft
    expect(screen.getByText(/1 处未保存/)).toBeTruthy();
    fireEvent.click(screen.getAllByText("重测")[0]);
    await waitFor(() =>
      expect(coreMock.mock.calls.some(
        (c) => c[0] === "probe" && JSON.stringify((c[1] as { args?: string[] }).args) === JSON.stringify(["--harness", "codex", "--provider", "?", "--model", "gpt-5-codex"]),
      )).toBe(true),
    );
    await waitFor(() => expect(coreMock.mock.calls.filter((c) => c[0] === "models-scan").length).toBeGreaterThanOrEqual(2));
    expect(screen.getByText(/无未保存修改/)).toBeTruthy(); // 非 user 来源：探测后 draft 清掉
  });

  it("重测后 user 行 draft 保留（用户意图优先，探测只更新实测列）", async () => {
    await rendered();
    const qwenRow = screen.getByText("qwen3-coder").closest("tr")!;
    fireEvent.click(qwenRow.querySelector("a")!); // 行内「切换」：image → 未标注
    expect(screen.getByText(/1 处未保存/)).toBeTruthy();
    fireEvent.click(qwenRow.querySelectorAll("a")[1]); // 行内「重测」
    await waitFor(() => expect(coreMock.mock.calls.some((c) => c[0] === "probe")).toBe(true));
    await waitFor(() => expect(coreMock.mock.calls.filter((c) => c[0] === "models-scan").length).toBeGreaterThanOrEqual(2));
    expect(screen.getByText(/1 处未保存/)).toBeTruthy(); // source=user：draft 不清
  });
});

describe("ModelsPage 探测反馈", () => {
  beforeEach(() => {
    coreMock.mockReset();
    mockScan();
  });

  it("重测:等待中显示 spinner+探测中，连点被防抖（只发一次 probe）", async () => {
    let release!: (v: unknown) => void;
    const gate = new Promise((r) => (release = r));
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      if (verb === "probe") await gate;
      return {};
    });
    render(<ModelsPage lang="zh" refresh={vi.fn()} />);
    await screen.findByText("gpt-5-codex");
    fireEvent.click(screen.getAllByText("重测")[0]);
    fireEvent.click(screen.getAllByText("探测中")[0]); // 等待中再点同一行
    expect(document.querySelector(".spinner")).toBeTruthy();
    release(undefined);
    await waitFor(() => expect(document.querySelector(".spinner")).toBeNull());
    expect(coreMock.mock.calls.filter((c) => c[0] === "probe").length).toBe(1);
  });

  it("重测:有结论走状态行轻提示，不弹窗", async () => {
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      return { result: "text_only", target_found: true, reason: null };
    });
    render(<ModelsPage lang="zh" refresh={vi.fn()} />);
    await screen.findByText("gpt-5-codex");
    fireEvent.click(screen.getAllByText("重测")[0]);
    await waitFor(() => expect(screen.getByText(/gpt-5-codex：✗ 纯文本（实测）/)).toBeTruthy());
    expect(alert).not.toHaveBeenCalled();
    alert.mockRestore();
  });

  it("重测:无结论走状态行；不可达才弹 reason", async () => {
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      return { result: null, target_found: true, reason: null };
    });
    render(<ModelsPage lang="zh" refresh={vi.fn()} />);
    await screen.findByText("gpt-5-codex");
    fireEvent.click(screen.getAllByText("重测")[0]);
    await waitFor(() => expect(screen.getByText(/无结论/)).toBeTruthy());
    expect(alert).not.toHaveBeenCalled();

    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      return { result: null, target_found: false, reason: "codex: 路由工具不在线,且未配置可探测的直连上游" };
    });
    fireEvent.click(screen.getAllByText("重测")[0]);
    await waitFor(() => expect(alert).toHaveBeenCalledWith(expect.stringContaining("路由工具不在线")));
    alert.mockRestore();
  });

  it("探测全部:前端逐行驱动(不走 --all-untested)，显示进度并弹汇总", async () => {
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    const batch = {
      models: [
        { harness: "codex", provider: "?", model: "gpt-5-codex", value: null, source: null, probe_cached: null, is_current: true },
        { harness: "codex", provider: "?", model: "gpt-5.5", value: null, source: null, probe_cached: null, is_current: true },
        { harness: "qwen-code", provider: "?", model: "qwen3-coder", value: "image", source: "user", probe_cached: "image", is_current: true }, // 有缓存,跳过
        { harness: "claude", provider: "Other", model: "kimi-x", value: null, source: null, probe_cached: null, is_current: false }, // 非当前,跳过
      ],
    };
    let release!: (v: unknown) => void;
    const gate = new Promise((r) => (release = r));
    let first = true;
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(batch));
      if (verb === "probe") {
        if (first) {
          first = false;
          await gate;
          return { result: "image", target_found: true, reason: null };
        }
        return { result: null, target_found: false, reason: "codex: 不可达" };
      }
      return {};
    });
    render(<ModelsPage lang="zh" refresh={vi.fn()} />);
    await screen.findByText("gpt-5-codex");
    fireEvent.click(screen.getByText("🔍 探测全部未测"));
    await screen.findByText(/正在探测 1\/2：gpt-5-codex/); // 候选=当前且无缓存（2 个）
    expect(document.querySelector(".spinner")).toBeTruthy();
    release(undefined);
    await waitFor(() => expect(alert).toHaveBeenCalledWith(expect.stringContaining("探测 2 个：1 支持图片、0 纯文本、0 无结论、1 不可达")));
    await waitFor(() => expect(alert).toHaveBeenCalledWith(expect.stringContaining("codex: 不可达")));
    // 逐行单探测：绝不发 --all-untested
    expect(coreMock.mock.calls.some((c) => c[0] === "probe" && (c[1] as { args?: string[] }).args?.[0] === "--all-untested")).toBe(false);
    alert.mockRestore();
  });

  it("探测全部:零候选时状态行提示，不弹窗不探测", async () => {
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    coreMock.mockImplementation(async () => ({
      models: MODELS.models.map((m) => ({ ...m, probe_cached: m.probe_cached ?? "text_only", is_current: true })),
    }));
    render(<ModelsPage lang="zh" refresh={vi.fn()} />);
    await screen.findByText("qwen3-coder");
    fireEvent.click(screen.getByText("🔍 探测全部未测"));
    await screen.findByText(/均已有实测结论/);
    expect(alert).not.toHaveBeenCalled();
    expect(coreMock.mock.calls.some((c) => c[0] === "probe")).toBe(false);
    alert.mockRestore();
  });

  it("批量探测可终止：按钮切「终止探测」，剩余行跳过、汇总注明完成数（2026-09-02 优化①）", async () => {
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    const batch = {
      models: [
        { harness: "codex", provider: "?", model: "m1", value: null, source: null, probe_cached: null, is_current: true },
        { harness: "codex", provider: "?", model: "m2", value: null, source: null, probe_cached: null, is_current: true },
        { harness: "codex", provider: "?", model: "m3", value: null, source: null, probe_cached: null, is_current: true },
      ],
    };
    let release!: (v: unknown) => void;
    const gate = new Promise((r) => (release = r));
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(batch));
      if (verb === "probe") await gate;
      return { result: "image", target_found: true, reason: null };
    });
    render(<ModelsPage lang="zh" refresh={vi.fn()} />);
    await screen.findByText("m1");
    fireEvent.click(screen.getByText("🔍 探测全部未测"));
    await screen.findByText(/正在探测 1\/3：m1/);
    expect(screen.getByText("⏹ 终止探测")).toBeTruthy(); // 进行中按钮变为终止
    fireEvent.click(screen.getByText("⏹ 终止探测"));
    release(undefined); // 当前行跑完 → m2/m3 跳过
    await waitFor(() => expect(alert).toHaveBeenCalledWith(expect.stringContaining("已手动终止，完成 1/3")));
    expect(coreMock.mock.calls.filter((c) => c[0] === "probe").length).toBe(1);
    alert.mockRestore();
  });

  it("拉清单:全部回环被跳过时解释清单在工具界面；有错误时展示错误", async () => {
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      if (verb === "models-fetch") return { providers: {}, errors: {}, skipped: { "cc-claude": "loopback", "codex-plus": "loopback" } };
      return {};
    });
    render(<ModelsPage lang="zh" refresh={vi.fn()} />);
    await screen.findByText("gpt-5-codex");
    fireEvent.click(screen.getByText("⬇ 从上游拉取模型清单（可选）"));
    await waitFor(() => expect(alert).toHaveBeenCalledWith(expect.stringContaining("2 个 relay 均为工具回环端口")));
    expect(alert).toHaveBeenCalledWith(expect.stringContaining("工具自己的界面查看"));

    coreMock.mockImplementation(async (verb: string) => {
      if (verb === "models-scan") return JSON.parse(JSON.stringify(MODELS));
      if (verb === "models-fetch") return { providers: {}, errors: { r: "boom" }, skipped: {} };
      return {};
    });
    fireEvent.click(screen.getByText("⬇ 从上游拉取模型清单（可选）"));
    await waitFor(() => expect(alert).toHaveBeenCalledWith(expect.stringContaining("未拉到清单")));
    alert.mockRestore();
  });
});
