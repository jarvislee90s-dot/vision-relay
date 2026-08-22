export interface Rec { ts: number; harness: string; session: string | null; [k: string]: unknown }
export interface Group { harness: string; sessions: { session: string | null; records: Rec[] }[] }

export function groupRecords(rows: Rec[]): Group[] {
  const byH = new Map<string, Map<string, Rec[]>>();
  for (const r of [...rows].sort((a, b) => b.ts - a.ts)) {
    const sessions = byH.get(r.harness) ?? new Map();
    const k = r.session ?? "";
    (sessions.get(k) ?? sessions.set(k, []).get(k)!).push(r);
    byH.set(r.harness, sessions);
  }
  return [...byH.entries()].map(([harness, sessions]) => ({
    harness,
    sessions: [...sessions.entries()]
      .sort((a, b) => (a[0] === "" ? 1 : b[0] === "" ? -1 : b[1][0].ts - a[1][0].ts))
      .map(([session, records]) => ({ session: session === "" ? null : session, records })),
  })).sort((a, b) => b.sessions[0].records[0].ts - a.sessions[0].records[0].ts);
}
