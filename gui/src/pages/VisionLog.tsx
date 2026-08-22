import { useEffect, useState } from "react";
import { core } from "../core";
import { groupRecords } from "../lib/grouping";
import type { Rec } from "../lib/grouping";

export function VisionLogPage(p: { lang: string }) {
  void p; // lang 预留：本页当前无文案切换，避免 TS6133
  const [rows, setRows] = useState<Rec[]>([]);
  const [sel, setSel] = useState<Rec | null>(null);
  useEffect(() => { core<Rec[]>("visionlog").then(setRows).catch(() => {}); }, []);
  const groups = groupRecords(rows);
  return (
    <div style={{ display: "flex", gap: 10 }}>
      <div style={{ width: 200, flexShrink: 0 }}>
        <div className="card" style={{ padding: 10 }}>
          <b className="small">按 harness → 会话</b>
          {groups.map((g) => (
            <div key={g.harness} style={{ marginTop: 6, fontSize: 12 }}>
              <div style={{ padding: "5px 8px", background: "#eef2ff", borderRadius: 6, fontWeight: 600 }}>{g.harness} <span className="dim">{g.sessions.reduce((n, s) => n + s.records.length, 0)}</span></div>
              {g.sessions.map((s) => (
                <div key={String(s.session)} className="dim" style={{ padding: "3px 8px 3px 20px", cursor: "pointer" }} onClick={() => setSel(s.records[0])}>
                  └ {s.session === null ? "未识别会话" : String(s.session).slice(0, 8) + "…"} <span className="dim">{s.records.length}</span>
                </div>
              ))}
            </div>
          ))}
          {groups.length === 0 && <div className="dim small" style={{ marginTop: 8 }}>暂无识图记录</div>}
        </div>
      </div>
      <div style={{ flex: 1 }}>
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>时间</th><th>层级</th><th>提示词</th><th>缓存</th><th>耗时</th><th>VLM</th></tr></thead>
            <tbody>
              {rows.slice(0, 50).map((r, i) => (
                <tr key={i} style={{ background: sel === r ? "#f0f9ff" : undefined, cursor: "pointer" }} onClick={() => setSel(r)}>
                  <td>{new Date((r.ts as number) * 1000).toLocaleTimeString()}</td>
                  <td>Tier{r.tier as number}</td>
                  <td className="dim">{r.prompt ? "默认" : "—"}</td>
                  <td>{r.cache_hit ? "命中" : "未命中"}</td>
                  <td>{r.duration_ms as number}ms</td>
                  <td className="dim">{String(r.vlm_model ?? "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {sel && (
          <div className="card">
            <b className="small">▸ 三段明细</b>
            <div style={{ marginTop: 8 }}>
              <div className="small" style={{ color: "#2563eb", fontWeight: 700 }}>① 发给 VLM 的提示词</div>
              <div className="mono codebox">{String(sel.prompt ?? "—")}</div>
              <div className="small" style={{ color: "#7c3aed", fontWeight: 700, margin: "6px 0 3px" }}>② VLM 原始返回</div>
              <div className="mono codebox" style={{ background: "#faf5ff", borderColor: "#e9d5ff" }}>{String(sel.raw ?? "—")}</div>
              <div className="small" style={{ color: "#059669", fontWeight: 700, margin: "6px 0 3px" }}>③ 实际注入对话的文本</div>
              <div className="mono codebox" style={{ background: "#ecfdf5", borderColor: "#a7f3d0", color: "#064e3b" }}>{String(sel.injected ?? "—")}</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
