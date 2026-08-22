import type { HarnessRow, ToolRow } from "../shell/useStatus";

export interface Hop { label: string; arrow: string; bypass: boolean; sub?: string }

// 链路状态机（纯函数）：按 路由开关 / 工具存在性 / 工具在线 推导跳数与旁路标记。
// 契约（由 chain.test.ts 钉死）：
//   - 无工具：3 跳（harness → relay → ☁️ 直连真实上游）
//   - 工具在线：3 跳（harness → relay → 🔀 工具，工具为终点，不旁路）
//   - 工具离线：4 跳（harness → relay → 🔀 工具(旁路) → ☁️ 真实上游）
//   - relay 在路由关闭时旁路；工具仅在离线时旁路。
export function chainHops(_row: HarnessRow, harness: string, tool: ToolRow | null, routingOn: boolean, port: number): Hop[] {
  const relayHop: Hop = { label: `👁 vision-relay :${port}`, arrow: "↓ base_url", bypass: !routingOn };
  if (!tool) {
    return [
      { label: harnessLabel(harness), arrow: "", bypass: false },
      relayHop,
      { label: "☁️ 直连真实上游", arrow: routingOn ? "↓ relay（一层直连）" : "↓ 直连真实上游", bypass: false },
    ];
  }
  const sub = tool.active_provider ? `供应商 ${tool.active_provider}（${tool.provider_base_url ?? "未知"}）` : undefined;
  if (tool.online) {
    // 工具在线：工具负责把请求继续转发到真实上游，链止于工具
    return [
      { label: harnessLabel(harness), arrow: "", bypass: false },
      relayHop,
      { label: `🔀 ${tool.name} :${tool.port}`, arrow: routingOn ? "↓ relay（两层）" : `↓ 直连 :${tool.port}`, bypass: false, sub },
    ];
  }
  // 工具离线：旁路工具，链止于真实上游
  return [
    { label: harnessLabel(harness), arrow: "", bypass: false },
    relayHop,
    { label: `🔀 ${tool.name} :${tool.port}`, arrow: routingOn ? "↓ relay" : `↓ 直连 :${tool.port}`, bypass: true, sub },
    { label: "☁️ 真实上游", arrow: routingOn ? "↓ relay（回落一层直连）" : "↓ 直连真实上游", bypass: false, sub: tool.provider_base_url ?? "由工具决定（未知）" },
  ];
}

export function harnessLabel(h: string): string {
  return { claude: "🤖 Claude Code", codex: "💻 Codex", "qwen-code": "❓ Qwen Code" }[h] ?? h;
}

export function toolFor(harness: string, tools: ToolRow[]): ToolRow | null {
  return tools.find((t) => (t.name === "cc-switch" ? ["claude", "codex"] : ["codex"]).includes(harness)) ?? null;
}
