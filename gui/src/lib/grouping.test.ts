import { describe, expect, it } from "vitest";
import { groupRecords } from "./grouping";

const rec = (h: string, session: string | null, ts: number) => ({ ts, harness: h, session, tier: 1, prompt: "p", raw: "r", injected: "i", duration_ms: 1, cache_hit: false, image_hash: "x", vlm_model: "m", question: null });

describe("groupRecords", () => {
  it("groups by harness then session, null session last, desc by ts", () => {
    const g = groupRecords([rec("claude", "s1", 3), rec("claude", null, 1), rec("codex", "s2", 2), rec("claude", "s1", 4)]);
    expect(g.map((x) => x.harness)).toEqual(["claude", "codex"]);
    expect(g[0].sessions.map((s) => s.session)).toEqual(["s1", null]);
    expect(g[0].sessions[0].records.map((r) => r.ts)).toEqual([4, 3]);
  });
});
