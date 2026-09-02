import { useCallback, useEffect, useRef, useState } from "react";
import { core } from "../core";
export interface HarnessRow { base_url: string | null; ownership: string; has_snapshot: boolean; config_path?: string }
export interface ToolRow { name: string; port: number; online: boolean; active_provider: string | null; provider_base_url: string | null }
export interface RelayRow { name: string; protocol: string; base_url: string; via: string | null; models: string[]; suppressed: boolean; has_key: boolean; harness?: string | null }
export interface SnapshotRow { base_url: string; key_ref: string; model: string; second_hop: string | null; ts: number }
export interface StatusData {
  service_alive: boolean; routing_on: boolean; bind_port: number;
  harnesses: Record<string, HarnessRow>;
  tools: ToolRow[]; relays: RelayRow[];
  snapshots: Record<string, SnapshotRow>;
  vlm: { model: string; base_url: string; format: string; configured: boolean; custom_prompts: boolean; groups: string[] };
  setup_state: { has_config: boolean; capability_confirmed: boolean; vlm_configured: boolean };
  first_run: boolean;
  auto_wire?: boolean;
  zcode_runtime?: { running: boolean; needs_restart: boolean };
}
export function useStatus(intervalMs = 5000) {
  const [status, setStatus] = useState<StatusData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const refresh = useCallback(async () => {
    try { setStatus(await core<StatusData>("status")); setError(null); }
    catch (e) { setError(String(e)); }
  }, []);
  useEffect(() => {
    refresh();
    timer.current = window.setInterval(refresh, intervalMs);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [refresh, intervalMs]);
  return { status, error, refresh };
}
