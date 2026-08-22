import { useEffect, useMemo, useState } from "react";
import { core } from "../core";

interface EventRow { ts: number; type: string; harness: string | null; [k: string]: unknown }
const TYPES = ["all", "reclaim", "absorb", "auto_fix", "auto_annotate", "relay_added"];

export function EventsPage(p: { lang: string }) {
  void p;
  const [rows, setRows] = useState<EventRow[]>([]);
  const [filter, setFilter] = useState("all");
  useEffect(() => {
    const load = () => core<EventRow[]>("events").then((rows) => setRows(rows.slice().reverse())).catch(() => {});
    load(); const t = setInterval(load, 8000); return () => clearInterval(t);
  }, []);
  const exportAll = async () => {
    try {
      const rows = await core<EventRow[]>("events", { args: ["--limit", "0"] });
      const blob = new Blob([rows.map((r) => JSON.stringify(r)).join("\n") + "\n"], { type: "application/x-ndjson" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `vision-relay-events-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.jsonl`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      window.alert(String(e));
    }
  };
  const shown = useMemo(() => (filter === "all" ? rows : rows.filter((r) => r.type === filter)), [rows, filter]);
  const label: Record<string, string> = { reclaim: "自动抢回", absorb: "自动吸收", auto_fix: "自动修复", auto_annotate: "自动标注", relay_added: "生成转发" };
  return (
    <div className="card" style={{ padding: 0 }}>
      <div className="row between" style={{ padding: "12px 14px 0" }}>
        <h3>自动动作全程留痕</h3>
        <div className="row">
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            {TYPES.map((t) => <option key={t} value={t}>{t === "all" ? "全部" : label[t] ?? t}</option>)}
          </select>
          <button className="btn" onClick={exportAll}>⬇ 导出</button>
        </div>
      </div>
      <table>
        <thead><tr><th>时间</th><th>harness</th><th>类型</th><th>内容</th></tr></thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={i}>
              <td className="mono">{new Date(r.ts * 1000).toLocaleString()}</td>
              <td>{r.harness ?? "—"}</td>
              <td><span className="tag warn">{label[r.type] ?? r.type}</span></td>
              <td className="small dim">{JSON.stringify({ ...r, ts: undefined, type: undefined, harness: undefined })}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
