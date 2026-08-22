import { invoke } from "@tauri-apps/api/core";

export const CONTRACT_VERSION = 1;

export interface Envelope<T = unknown> {
  contract_version: number;
  ok: boolean;
  data: T;
}

export function parseEnvelope<T>(raw: unknown): T {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) throw new Error("core output is not JSON");
  const e = raw as Envelope;
  if (e.contract_version !== CONTRACT_VERSION)
    throw new Error(`contract mismatch: core=${e.contract_version}, gui=${CONTRACT_VERSION}（请升级另一侧）`);
  if (!e.ok) throw new Error((e.data as { error?: string })?.error ?? "core verb failed");
  if (!("data" in e)) throw new Error("envelope missing data");
  return e.data as T;
}

let corePath: string | null = null;

export async function detectCore(explicit?: string | null): Promise<string> {
  if (explicit) {
    corePath = explicit;
    return explicit;
  }
  if (corePath) return corePath;
  const found = await invoke<string | null>("which_core");
  if (!found) throw new Error("找不到 vision-relay 核心：请在设置里指定路径，或确认已 pip install 并在 PATH");
  corePath = found;
  return found;
}

export function setCorePath(p: string | null): void {
  corePath = p;
}

export async function core<T = unknown>(verb: string, opts: { args?: string[]; stdin?: unknown } = {}): Promise<T> {
  const path = await detectCore();
  const args = [verb, "--json", ...(opts.args ?? [])];
  const stdin = opts.stdin !== undefined ? JSON.stringify(opts.stdin) : null;
  const out = await invoke<string>("run_core", { corePath: path, args, stdin });
  return parseEnvelope<T>(JSON.parse(out));
}

export async function startService(): Promise<void> {
  const path = await detectCore();
  await invoke("start_core_detached", { corePath: path });
}

export async function stopService(): Promise<void> {
  // stop 是生命周期命令（不加载配置、PID 直杀），输出人类可读文本而非 envelope——直接跑子进程、忽略输出
  const path = await detectCore();
  await invoke("run_core", { corePath: path, args: ["stop"], stdin: null });
}
