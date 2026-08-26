import { describe, expect, it } from "vitest";
import { chainHops, harnessLabel, toolFor } from "./chain";
import type { HarnessRow, SnapshotRow, ToolRow } from "../shell/useStatus";

const cc = { name: "cc-switch", port: 15721, online: true, active_provider: "bigmodel", provider_base_url: "https://open.example" } as ToolRow;
const ccOff = { ...cc, online: false, active_provider: null, provider_base_url: null } as ToolRow;
const cpp = { name: "codex-plus", port: 57321, online: true, active_provider: "volces", provider_base_url: "https://ark.example" } as ToolRow;
// 真实数据面：两个 dossier 工具都会出现在 tools 数组里（在线与否由端口探测决定）
const bothOnline = [cc, cpp];

const row = (ownership: string, base_url: string | null = null): HarnessRow => ({ base_url, ownership, has_snapshot: false });
const snap = (second_hop: string | null): SnapshotRow => ({ base_url: "https://origin.example", key_ref: "k", model: "m", second_hop, ts: 1 });

describe("toolFor（逐 harness 工具归属，2026-08-26 用户裁决）", () => {
  it("归属以配置文件为准：harness 直指 cc-switch 端口 → cc-switch（即使 codex-plus 也在线）", () => {
    expect(toolFor("claude", row("cc-switch", "http://127.0.0.1:15721"), undefined, bothOnline)?.name).toBe("cc-switch");
  });
  it("codex 直指 codex-plus 端口 → codex-plus，不再被 dossier 顺序截胡成 cc-switch", () => {
    expect(toolFor("codex", row("codex-plus", "http://127.0.0.1:57321/v1"), undefined, bothOnline)?.name).toBe("codex-plus");
  });
  it("CC Switch 路由是逐工具开关：claude 未被路由（other/none）→ 无工具跳，哪怕 cc-switch 在线", () => {
    expect(toolFor("claude", row("other", "https://up.example"), undefined, bothOnline)).toBeNull();
    expect(toolFor("claude", row("none"), undefined, bothOnline)).toBeNull();
  });
  it("接管态（ours）用快照 second_hop：codex 接管自 codex-plus → codex-plus", () => {
    expect(toolFor("codex", row("ours", "http://127.0.0.1:8787"), snap("codex-plus"), bothOnline)?.name).toBe("codex-plus");
  });
  it("接管态快照无 second_hop（一层直连上游）→ 无工具跳", () => {
    expect(toolFor("claude", row("ours", "http://127.0.0.1:8787"), snap(null), bothOnline)).toBeNull();
    expect(toolFor("claude", row("ours", "http://127.0.0.1:8787"), undefined, bothOnline)).toBeNull();
  });
  it("配置文件证据优先于快照：接管中漂移（ownership 变 cc-switch）→ cc-switch", () => {
    expect(toolFor("codex", row("cc-switch", "http://127.0.0.1:15721"), snap("codex-plus"), bothOnline)?.name).toBe("cc-switch");
  });
  it("dossier 不支持该 harness 的工具名不显示（qwen/zcode 永无工具跳）", () => {
    expect(toolFor("qwen-code", row("cc-switch", "http://127.0.0.1:15721"), undefined, bothOnline)).toBeNull();
    expect(toolFor("zcode", row("cc-switch", "http://127.0.0.1:15721"), undefined, bothOnline)).toBeNull();
  });
});

describe("chainHops", () => {
  it("接管态 + 快照 second_hop 工具在线：3 跳（harness → relay → 工具，两层）", () => {
    const hops = chainHops(row("ours", "http://127.0.0.1:8787"), "claude", toolFor("claude", row("ours"), snap("cc-switch"), bothOnline), true, 8787);
    expect(hops.map((h) => h.bypass)).toEqual([false, false, false]);
    expect(hops[1].label).toContain("8787");
    expect(hops[2].label).toContain("cc-switch");
    expect(hops[2].arrow).toContain("relay（两层）");
  });
  it("接管态 + 一层直连（无 second_hop）：3 跳，第三跳直连真实上游", () => {
    const hops = chainHops(row("ours", "http://127.0.0.1:8787"), "claude", null, true, 8787);
    expect(hops[2].label).toContain("直连真实上游");
    expect(hops[2].arrow).toContain("relay（一层直连）");
  });
  it("接管态 + 工具离线：4 跳（relay 回落一层直连，route_fallback 等效）", () => {
    const hops = chainHops(row("ours", "http://127.0.0.1:8787"), "claude", ccOff, true, 8787);
    expect(hops[1].bypass).toBe(false);
    expect(hops[2].bypass).toBe(true);
    expect(hops[3].arrow).toContain("回落一层直连");
  });
  it("harness 直指工具端口（路由关/漂移）：本代理置灰旁路，工具是真实下一跳", () => {
    const hops = chainHops(row("cc-switch", "http://127.0.0.1:15721"), "claude", cc, false, 8787);
    expect(hops[1].bypass).toBe(true); // vision-relay 不在链上
    expect(hops[2].bypass).toBe(false);
    expect(hops[2].arrow).toContain("直连 :15721");
  });
  it("路由开但未接管该 harness（other）：本代理也置灰，不谎称经代理", () => {
    const hops = chainHops(row("other", "https://up.example"), "claude", null, true, 8787);
    expect(hops[1].bypass).toBe(true);
    expect(hops[2].arrow).toContain("直连真实上游");
  });
  it("harness 直指离线工具：链断在工具处，不再虚构「回落真实上游」", () => {
    const hops = chainHops(row("cc-switch", "http://127.0.0.1:15721"), "claude", ccOff, false, 8787);
    expect(hops).toHaveLength(3);
    expect(hops[2].bypass).toBe(false);
    expect(hops[2].sub).toContain("不可达");
  });
  it("no tool harness is single-hop direct", () => {
    const hops = chainHops(row("ours", "http://127.0.0.1:8787"), "qwen-code", null, true, 8787);
    expect(hops).toHaveLength(3);
  });
});

describe("zcode label", () => {
  it("labels zcode and has no routing tool", () => {
    expect(harnessLabel("zcode")).toBe("⚡ Zcode");
    expect(toolFor("zcode", row("none"), undefined, [cc])).toBeNull();
  });
});
