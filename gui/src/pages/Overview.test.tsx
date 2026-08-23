// @vitest-environment jsdom
// G2/G3/G4 的 UI 面：横幅端口、链路旁路、详情抽屉（含配置文件打开入口=决策③）、
// 自动处理/需要你区、诊断弹层两态、路由开关调用。数据链路已由 test_e2e_g2~g4 覆盖。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Overview } from "./Overview";
import type { StatusData } from "../shell/useStatus";

const h = vi.hoisted(() => ({
  // rest 形参是 wrapper 展开所必需的（tsc TS2556：展开实参须落在 rest 形参上）
  startService: vi.fn(async (..._args: unknown[]) => {}),
  stopService: vi.fn(async (..._args: unknown[]) => {}),
  openPath: vi.fn(async (..._args: unknown[]) => {}),
}));

vi.mock("../core", () => ({
  core: vi.fn(),
  startService: (...a: unknown[]) => h.startService(...a),
  stopService: (...a: unknown[]) => h.stopService(...a),
  openPath: (...a: unknown[]) => h.openPath(...a),
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
    h.startService.mockClear();
    h.stopService.mockClear();
    h.openPath.mockClear();
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
    renderOverview({ ...STATUS, service_alive: false, routing_on: false } as unknown as StatusData);
    expect(screen.getAllByText(/已旁路/).length).toBeGreaterThanOrEqual(1); // relay 与离线工具都可能旁路
  });

  it("drawer shows snapshot, relays, config path with 打开 entry (决策③)", async () => {
    const prompt = vi.spyOn(window, "prompt").mockReturnValue(null);
    renderOverview();
    fireEvent.click(screen.getByText(/详情：快照 · relay · 停用/));
    expect(screen.getByText(/https:\/\/origin\.example/)).toBeTruthy(); // 接管快照
    expect(screen.getByText(/cc-claude/)).toBeTruthy();
    // direct-codex 同时出现在抽屉 relay 行与「需要你」区（缺 key 的 direct relay），用 getAllByText
    expect(screen.getAllByText(/direct-codex/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/补填 key/)).toBeTruthy(); // 需要你区动作（缺 key 的 direct relay）
    expect(screen.getByText("配置文件")).toBeTruthy();
    fireEvent.click(screen.getByText("打开"));
    await waitFor(() => expect(h.openPath).toHaveBeenCalledWith("C:/Users/t/.claude/settings.json"));
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
    await waitFor(() => expect(h.stopService).toHaveBeenCalledTimes(1));
    expect(h.startService).not.toHaveBeenCalled();
  });
});
