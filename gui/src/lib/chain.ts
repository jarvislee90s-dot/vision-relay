import type { HarnessRow, SnapshotRow, ToolRow } from "../shell/useStatus";

export interface Hop { label: string; arrow: string; bypass: boolean; sub?: string }

// 与后端 TOOL_DOSSIERS.harnesses / _TEMPLATES 对齐的工具-harness 矩阵。
// 仅作显示门控（qwen/zcode 永无工具跳）；归属判定不依赖它——见 toolFor。
const TOOL_HARNESSES: Record<string, string[]> = { "cc-switch": ["claude", "codex"], "codex-plus": ["codex"] };

// 逐 harness 工具归属（2026-08-26 用户裁决；契约由 chain.test.ts 钉死）：
//   ① 配置文件证据（ownership = cc-switch/codex-plus，即 base_url 端口指纹）——
//      CC Switch / Codex++ 的路由是逐工具开关，端口在线 ≠ 路由了这个 harness，
//      唯一证据是该 harness 自己配置文件指向的端口（15721→CC Switch / 57321→Codex++）；
//   ② 接管态（ownership = ours）配置文件已指本代理，无从判断——用接管快照 second_hop
//      （接管时刻从配置文件记录的归属）；
//   ③ 其余（other/none 直连、qwen/zcode）无工具跳。
// 弃用旧「dossier 全局矩阵 + 工具在线」推断：那是 relay 层语义，会把 claude/codex
// 全局标成同一个工具（实测事故：claude 未开路由却显示直连 cc-switch :15721）。
export function toolFor(
  harness: string,
  row: HarnessRow,
  snap: SnapshotRow | undefined,
  tools: ToolRow[],
): ToolRow | null {
  let name: string | null;
  if (row.ownership === "ours") {
    name = snap?.second_hop ?? null; // ② 接管态：接管快照记录的归属
  } else if (row.ownership === "other" || row.ownership === "none") {
    name = null; // ③ 直连/未配置：无工具跳
  } else {
    name = row.ownership; // ① 配置文件直指工具端口
  }
  if (!name || !(TOOL_HARNESSES[name] ?? []).includes(harness)) return null;
  return tools.find((t) => t.name === name) ?? null;
}

// 链路状态机（纯函数）。契约（由 chain.test.ts 钉死）：
//   - 本代理节点仅在「路由开 且 接管态（ownership=ours）」为真实路径，其余一律置灰「已旁路」
//     （harness 直指工具端口 = 漂移/直连态，不经本代理）；
//   - 接管态：工具在线 3 跳（harness → relay → 工具，两层）；工具离线 4 跳
//     （工具旁路 → 真实上游，= route_fallback 的回落一层直连）；无工具 3 跳一层直连；
//   - harness 直指工具端口：3 跳，工具是真实下一跳（本代理已旁路）；工具离线则
//     链断在工具处，如实标注「不可达」，不虚构回落路径。
export function chainHops(row: HarnessRow, harness: string, tool: ToolRow | null, routingOn: boolean, port: number): Hop[] {
  const relayInPath = routingOn && row.ownership === "ours";
  const relayHop: Hop = { label: `👁 vision-relay :${port}`, arrow: "↓ base_url", bypass: !relayInPath };
  const first: Hop = { label: harnessLabel(harness), arrow: "", bypass: false };
  if (!tool) {
    return [
      first,
      relayHop,
      { label: "☁️ 直连真实上游", arrow: relayInPath ? "↓ relay（一层直连）" : "↓ 直连真实上游", bypass: false },
    ];
  }
  // M3：无 baseURL（"" 或读不到 null）的供应商不隐身——名字照常显示，地址占位「未接线」
  const sub = tool.active_provider ? `供应商 ${tool.active_provider}（${tool.provider_base_url || "未接线"}）` : undefined;
  if (tool.online) {
    return [
      first,
      relayHop,
      { label: `🔀 ${tool.name} :${tool.port}`, arrow: relayInPath ? "↓ relay（两层）" : `↓ 直连 :${tool.port}`, bypass: false, sub },
    ];
  }
  if (!relayInPath) {
    // harness 直指离线工具：不经本代理，无回落可言——链断在工具处
    return [
      first,
      relayHop,
      { label: `🔀 ${tool.name} :${tool.port}`, arrow: `↓ 直连 :${tool.port}`, bypass: false, sub: sub ? `${sub} · 工具离线，请求不可达` : "工具离线，请求不可达" },
    ];
  }
  // 接管态 + 工具离线：relay 回落一层直连真实上游（route_fallback 等效）
  return [
    first,
    relayHop,
    { label: `🔀 ${tool.name} :${tool.port}`, arrow: "↓ relay", bypass: true, sub },
    { label: "☁️ 真实上游", arrow: "↓ relay（回落一层直连）", bypass: false, sub: tool.provider_base_url ?? "由工具决定（未知）" },
  ];
}

export function harnessLabel(h: string): string {
  return { claude: "🤖 Claude Code", codex: "💻 Codex", "qwen-code": "❓ Qwen Code", zcode: "⚡ Zcode" }[h] ?? h;
}
