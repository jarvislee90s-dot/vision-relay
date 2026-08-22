import { describe, expect, it } from "vitest";
import { chainHops } from "./chain";

const tool = { name: "cc-switch", port: 15721, online: true, active_provider: "bigmodel", provider_base_url: "https://open.example" };

describe("chainHops", () => {
  it("two-hop when routing on and tool online", () => {
    const hops = chainHops({ base_url: "http://127.0.0.1:8787", ownership: "ours", has_snapshot: true }, "claude", tool, true, 8787);
    expect(hops.map((h) => h.bypass)).toEqual([false, false, false]);
    expect(hops[1].label).toContain("8787");
    expect(hops[2].label).toContain("cc-switch");
  });
  it("relay bypassed when routing off, tool still active", () => {
    const hops = chainHops({ base_url: null, ownership: "none", has_snapshot: false }, "claude", tool, false, 8787);
    expect(hops[0].bypass).toBe(false);
    expect(hops[1].bypass).toBe(true);
    expect(hops[2].bypass).toBe(false);
    expect(hops[2].arrow).toContain("直连");
  });
  it("both bypassed when routing off and tool offline", () => {
    const hops = chainHops({ base_url: null, ownership: "none", has_snapshot: false }, "claude", { ...tool, online: false }, false, 8787);
    expect(hops[1].bypass).toBe(true);
    expect(hops[2].bypass).toBe(true);
    expect(hops[3].arrow).toContain("真实上游");
  });
  it("no tool harness is single-hop direct", () => {
    const hops = chainHops({ base_url: "http://127.0.0.1:8787", ownership: "ours", has_snapshot: true }, "qwen-code", null, true, 8787);
    expect(hops).toHaveLength(3);
  });
});
