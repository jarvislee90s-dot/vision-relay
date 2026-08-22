import { useEffect, useMemo, useState } from "react";
import { core, openPath } from "../core";
import { RoutingToggle } from "../shell/RoutingToggle";
import { chainHops, toolFor } from "../lib/chain";
import type { StatusData } from "../shell/useStatus";
import { t, Lang } from "../i18n";

interface EventRow { ts: number; type: string; harness: string | null; [k: string]: unknown }
interface DiagReport { actions: { type: string; harness?: string; fix?: string; ok?: boolean }[]; needs_you: { type: string; harness?: string; hint?: string }[]; observed: StatusData }

export function Overview(p: { status: StatusData | null; refresh: () => void; lang: Lang; showDiag: boolean; setShowDiag: (b: boolean) => void }) {
  const [events, setEvents] = useState<EventRow[]>([]);
  const [diag, setDiag] = useState<DiagReport | null>(null);
  const [drawer, setDrawer] = useState<string | null>(null);
  const loadEvents = async () => { try { const rows = await core<EventRow[]>("events"); setEvents(rows.slice(-20).reverse()); } catch { /* 被动 */ } };
  useEffect(() => { loadEvents(); const t = setInterval(loadEvents, 8000); return () => clearInterval(t); }, []);
  useEffect(() => { if (p.showDiag) runDiag(); }, [p.showDiag]);
  const runDiag = async () => {
    try {
      const d = await core<DiagReport>("diagnose");
      setDiag(d); p.refresh(); loadEvents();
    } catch (e) {
      console.error("诊断失败", e);
      p.setShowDiag(false);
    }
  };
  const auto = useMemo(() => events.filter((e) => ["reclaim", "absorb", "auto_fix", "auto_annotate"].includes(e.type)), [events]);
  const needsYou = diag?.needs_you ?? autoNeedsYou(p.status);
  if (!p.status) return <div className="card">加载中…（核心不可用见顶部错误）</div>;
  const s = p.status;
  return (
    <>
      <div className="card row between">
        <div>
          <div className="row">
            <span className={"dot " + (s.service_alive ? "g" : "r")} />
            <b style={{ fontSize: 15 }}>{s.service_alive ? "服务运行中" : "服务已停止"}</b>
            <span className="dim">127.0.0.1:{s.bind_port} · 自动对账中</span>
          </div>
        </div>
        <RoutingToggle on={s.routing_on && s.service_alive} onChangeDone={p.refresh} lang={p.lang} />
        <button className="btn lg" onClick={p.refresh}>🔄 {t(p.lang, "refresh")}</button>
        <button className="btn lg" onClick={() => p.setShowDiag(true)}>📋 {t(p.lang, "diag")}</button>
      </div>
      <div className="cols3">
        {Object.keys(s.harnesses).map((h) => {
          const tool = toolFor(h, s.tools);
          const hops = chainHops(s.harnesses[h], h, tool, s.routing_on && s.service_alive, s.bind_port);
          const snap = s.snapshots[h];
          const cfgPath = s.harnesses[h].config_path;
          return (
            <div className="card" key={h}>
              <div className="row between"><b>{h}</b><span className={"tag " + (s.harnesses[h].ownership === "ours" ? "ok" : "gray")}>{s.harnesses[h].ownership === "ours" ? "✓ 已接管" : s.harnesses[h].ownership}</span></div>
              <div className="chain">
                {hops.map((hop, i) => (
                  <div key={i}>
                    {hop.arrow && <div className="arrow">{hop.arrow}</div>}
                    <div className={"hop" + (hop.bypass ? " bypass" : "")}>{hop.label}{hop.bypass ? <span className="bz">已旁路</span> : null}</div>
                    {hop.sub && <div className="small dim">{hop.sub}</div>}
                  </div>
                ))}
              </div>
              <a href="#" className="small dim" onClick={(e) => { e.preventDefault(); setDrawer(drawer === h ? null : h); }}>
                {drawer === h ? "▾" : "▸"} 详情：快照 · relay · 停用
              </a>
              {drawer === h && (
                <table>
                  <tbody>
                    <tr><td className="dim small">配置文件</td><td className="small mono">
                      {cfgPath ?? "—"}{" "}
                      {cfgPath && (
                        <button className="btn" onClick={() => openPath(cfgPath).catch((e) => window.alert(String(e)))}>打开</button>
                      )}
                    </td></tr>
                    <tr><td className="dim small">接管快照</td><td className="mono small">{snap ? `${snap.base_url} · ${snap.key_ref} · ${snap.model}` : "无"}</td></tr>
                    {s.relays.filter((r) => r.via || r.name.startsWith("direct-")).slice(0, 4).map((r) => (
                      <tr key={r.name}><td className="dim small">relay</td><td className="small">
                        {r.name} → {r.base_url} {r.suppressed ? <span className="tag gray">已停用</span> : null}
                        {!r.has_key && r.name.startsWith("direct-") ? <button className="btn" onClick={() => fillKey(r.name, p.refresh)}>🔑 补填 key</button> : null}
                        <button className="btn" onClick={() => toggleRelay(r.name, r.suppressed, p.refresh)}>{r.suppressed ? "恢复" : "停用转发"}</button>
                      </td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          );
        })}
      </div>
      <div className="card">
        <h3>自动处理（无需你操作）</h3>
        {auto.length === 0 && <div className="dim small" style={{ padding: 4 }}>最近无自动动作</div>}
        {auto.slice(0, 5).map((e, i) => (
          <div className="alert-ok row between" key={i}>
            <span>✅ {new Date(e.ts * 1000).toLocaleTimeString()} {e.harness ?? ""} {eventText(e)}</span>
          </div>
        ))}
      </div>
      <div className="card">
        <h3>⚠ 需要你</h3>
        {needsYou.length === 0 && <div className="dim small" style={{ padding: 4 }}>无待办</div>}
        {needsYou.map((n, i) => (
          <div className="alert-err row between" key={i}><span>🔑 {n.harness ?? ""} {n.hint ?? ""}</span></div>
        ))}
      </div>
      {p.showDiag && (
        <div className="modal show" onClick={() => p.setShowDiag(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="row between"><b style={{ fontSize: 15 }}>📋 诊断报告（自动运行，只读）</b><button className="btn" onClick={() => p.setShowDiag(false)}>✕</button></div>
            {diag ? (
              <>
                <div className="alert-ok">✅ 已自动修复：{diag.actions.map((a) => `${a.harness ?? ""} ${a.type}${a.fix ? `(${a.fix})` : ""}`).join(" · ") || "（本次无）"}</div>
                <table><tbody>
                  <tr><td>✓ 服务进程 / 端口</td><td style={{ textAlign: "right", color: "#059669" }}>{diag.observed.service_alive ? "运行中" : "未运行"}</td></tr>
                  {diag.observed.tools.map((t) => (
                    <tr key={t.name}><td>✓ 工具 {t.name} :{t.port}</td><td style={{ textAlign: "right", color: t.online ? "#059669" : "#b91c1c" }}>{t.online ? `在线${t.active_provider ? " · " + t.active_provider : ""}` : "离线"}</td></tr>
                  ))}
                </tbody></table>
                {diag.needs_you.map((n, i) => <div className="alert-err" key={i}>⚠ {n.harness} {n.hint}</div>)}
              </>
            ) : (
              <div className="dim small" style={{ padding: 8 }}>诊断中…</div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

function autoNeedsYou(s: StatusData | null) {
  if (!s) return [];
  return s.relays.filter((r) => r.name.startsWith("direct-") && !r.has_key)
    .map((r) => ({ type: "missing_key", harness: r.name, hint: "直连上游缺 API key" }));
}
function eventText(e: EventRow): string {
  const m: Record<string, string> = { reclaim: "漂移已自动抢回", absorb: "新上游已吸收接管", auto_fix: "已自动修复", auto_annotate: "新模型已自动标注", relay_added: "已生成工具转发" };
  return m[e.type] ?? e.type;
}
async function fillKey(name: string, refresh: () => void): Promise<void> {
  const key = window.prompt(`为 ${name} 粘贴 API key（不会回显）：`);
  if (!key) return;
  try {
    await core("relay-set", { stdin: { name, api_key: key } });
    refresh();
  } catch (e) {
    console.error("fillKey 失败", e);
    window.alert(e instanceof Error ? e.message : String(e));
  }
}
async function toggleRelay(name: string, suppressed: boolean, refresh: () => void): Promise<void> {
  try {
    await core("relay-set", { stdin: { name, suppressed: !suppressed } });
    refresh();
  } catch (e) {
    console.error("toggleRelay 失败", e);
    window.alert(e instanceof Error ? e.message : String(e));
  }
}