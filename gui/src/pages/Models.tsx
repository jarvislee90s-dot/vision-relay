import { useEffect, useRef, useState } from "react";
import { core } from "../core";

interface Triple { harness: string; provider: string; model: string; value: string | null; source: string | null; probe_cached: string | null; is_current: boolean }
type Draft = Record<string, string | null>;
interface ProbeOut { result: string | null; target_found: boolean; reason: string | null }

export function ModelsPage(p: { lang: string; refresh: () => void }) {
  const [rows, setRows] = useState<Triple[]>([]);
  const [draft, setDraft] = useState<Draft>({});
  const [busy, setBusy] = useState(false); // 全局探测锁（重测/批量互斥，防连点）
  const [batchBusy, setBatchBusy] = useState(false); // 批量探测进行中（按钮切「终止探测」）
  const [spinKey, setSpinKey] = useState(""); // 正在探测的行 key（行内 spinner）
  const [status, setStatus] = useState(""); // 轻提示/进度状态行
  const cancelRef = useRef(false); // 批量探测终止标志（正在跑的行会跑完，剩余行跳过）

  // 只刷新行数据、保留未保存的 draft（save 成功后才整体重置）；返回最新行
  // 供探测后清 stale draft 用
  const refreshRows = async (): Promise<Triple[]> => {
    const fresh = (await core<{ models: Triple[] }>("models-scan")).models;
    setRows(fresh);
    return fresh;
  };
  const load = async () => { await refreshRows(); setDraft({}); };
  useEffect(() => { load().catch((e) => { console.error(e); window.alert(String(e)); }); }, []);

  const key = (r: Triple) => r.harness + "|" + r.provider + "|" + r.model;
  const effective = (r: Triple) => (key(r) in draft ? draft[key(r)] : r.value);
  const dirty = rows.filter((r) => key(r) in draft && draft[key(r)] !== r.value);

  // 探测成功后：来源非 user 的行，后端标注已被实测覆盖——清掉该行 draft，否则
  // 未保存草稿会一直盖住新值，「探测后当前标注不更新」直到切页才可见
  // （2026-09-02 回归）。user 来源行保留 draft（用户意图优先，探测只更新实测列）。
  const dropStaleDraft = (r: Triple, fresh: Triple[]) => {
    const fr = fresh.find((x) => key(x) === key(r));
    if (fr && fr.source !== "user") {
      setDraft((d) => { const nd = { ...d }; delete nd[key(r)]; return nd; });
    }
  };

  const save = async () => {
    try {
      await core("models-set", { stdin: dirty.map((r) => ({ harness: r.harness, provider: r.provider, model: r.model, value: draft[key(r)] })) });
      await load(); p.refresh();
    } catch (e) { console.error(e); window.alert(String(e)); }
  };

  const outcome = (r: Triple, d: ProbeOut) =>
    d.result === "image" ? `${r.model}：✓ 支持图片（实测）`
      : d.result === "text_only" ? `${r.model}：✗ 纯文本（实测）`
        : `${r.model}：无结论（超时/鉴权/含糊），不下判定`;

  const probeRow = (r: Triple) =>
    core<ProbeOut>("probe", { args: ["--harness", r.harness, "--provider", r.provider, "--model", r.model] });

  const retest = async (r: Triple) => {
    if (busy) return; // 等待中连点直接忽略
    setBusy(true); setSpinKey(key(r)); setStatus(`${r.model}：探测中…`);
    try {
      const d = await probeRow(r);
      if (d.target_found === false) {
        setStatus(`${r.model}：不可达——${d.reason ?? "探测目标不可达"}`);
        window.alert(d.reason ?? "探测目标不可达"); // 不可达需要用户处理（开工具/配直连），才值得打断
      } else {
        setStatus(outcome(r, d));
      }
      const fresh = await refreshRows();
      dropStaleDraft(r, fresh);
    } catch (e) { console.error(e); window.alert(String(e)); } finally { setBusy(false); setSpinKey(""); }
  };

  // 前端逐行驱动（2026-08-25）：候选=当前激活供应商的无缓存行快照；每行单探测，
  // 进度与行内 spinner 实时可见，实测列逐行刷新；结束弹汇总。
  // 2026-09-02：进行中可「终止探测」——置 cancelRef 跳过剩余行（当前行跑完）。
  const probeAll = async () => {
    if (busy || batchBusy) return;
    const candidates = rows.filter((r) => r.is_current && r.probe_cached === null);
    if (!candidates.length) { setStatus("当前供应商模型均已有实测结论，无待探测项"); return; }
    cancelRef.current = false;
    setBusy(true); setBatchBusy(true);
    const n = { image: 0, text_only: 0, none: 0, unreachable: 0 };
    let firstReason = "";
    let done = 0;
    let stopped = false;
    try {
      for (let i = 0; i < candidates.length; i++) {
        if (cancelRef.current) { stopped = true; break; }
        const r = candidates[i];
        setStatus(`正在探测 ${i + 1}/${candidates.length}：${r.model}…`);
        setSpinKey(key(r));
        try {
          const d = await probeRow(r);
          if (d.target_found === false) { n.unreachable++; if (!firstReason) firstReason = d.reason ?? "目标不可达"; }
          else if (d.result === "image") n.image++;
          else if (d.result === "text_only") n.text_only++;
          else n.none++;
        } catch (e) { console.error(e); n.none++; }
        done = i + 1;
        try {
          const fresh = await refreshRows();
          dropStaleDraft(r, fresh);
        } catch (e) { console.error(e); } // 单行刷新失败不中断整批（下一行探测后自然再刷）
      }
      const stoppedNote = stopped ? `（已手动终止，完成 ${done}/${candidates.length}）` : "";
      const summary = `探测 ${candidates.length} 个：${n.image} 支持图片、${n.text_only} 纯文本、${n.none} 无结论、${n.unreachable} 不可达${stoppedNote}${firstReason ? `\n首个不可达原因：${firstReason}` : ""}`;
      setStatus(summary.replace("\n", " "));
      window.alert(summary);
    } finally { setBusy(false); setBatchBusy(false); setSpinKey(""); }
  };

  const fetchList = async () => {
    try {
      const d = await core<{ providers: Record<string, string[]>; errors: Record<string, string>; skipped: Record<string, string> }>("models-fetch");
      const ids = Object.values(d.providers ?? {}).flat();
      if (ids.length) {
        window.alert("拉到 " + ids.length + " 个模型 ID（能力以探针/目录为准）：\n" + ids.slice(0, 30).join("\n") + (ids.length > 30 ? "\n…" : ""));
        return;
      }
      const skipped = Object.keys(d.skipped ?? {});
      if (skipped.length) window.alert(`${skipped.length} 个 relay 均为工具回环端口（cc-switch/Codex++ 两层），模型清单请在工具自己的界面查看`);
      else if (Object.values(d.errors ?? {}).some(Boolean)) window.alert("未拉到清单：" + JSON.stringify(d.errors));
      else window.alert("上游返回空清单");
    } catch (e) { console.error(e); window.alert(String(e)); }
  };

  const cycle = (v: string | null) => (v === null ? "text_only" : v === "text_only" ? "image" : null);

  const thead = <thead><tr><th>harness</th><th>provider</th><th>模型</th><th>当前标注</th><th>依据</th><th>实测</th><th>操作</th></tr></thead>;

  const renderRow = (r: Triple) => {
    const v = effective(r);
    const k = key(r);
    const probing = spinKey === k;
    return (
      <tr key={k} style={{ background: k in draft ? "#fffbeb" : undefined }}>
        <td>{r.harness}</td><td>{r.provider}</td><td>{r.model}</td>
        <td>{v === null ? <span className="tag gray">未标注（空）</span> : v === "image" ? <span className="cap image">支持图片</span> : <span className="cap text">纯文本</span>}</td>
        <td className="dim small">{r.source ?? "—"}{r.probe_cached ? "（缓存 " + r.probe_cached + "）" : ""}</td>
        <td className="small">{r.probe_cached === "image" ? "✓ 接受" : r.probe_cached === "text_only" ? "✗ 拒图" : <span className="tag gray">未测</span>}</td>
        <td>
          <a href="#" onClick={(e) => { e.preventDefault(); setDraft({ ...draft, [k]: cycle(v) }); }}>切换</a>
          {" · "}
          {r.is_current ? (
            <a href="#" onClick={(e) => { e.preventDefault(); retest(r); }}>{probing ? <><span className="spinner" /> 探测中</> : "重测"}</a>
          ) : (
            <a href="#" title="非当前激活供应商，无可达探测路径" style={{ opacity: 0.4, cursor: "not-allowed" }} onClick={(e) => e.preventDefault()}>重测</a>
          )}
        </td>
      </tr>
    );
  };

  const current = rows.filter((r) => r.is_current);
  const others = rows.filter((r) => !r.is_current);

  return (
    <>
      <div className="alert-ok">
        ✅ 按 (harness · provider · 模型) 三元组标注，记录只有输入模态一个字段（纯文本 / 支持图片 / 未标注）。只在猜错时改；未标注模型运行时按设置开关（默认走识图）。
      </div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <button className="btn" disabled={busy && !batchBusy} onClick={() => (batchBusy ? (cancelRef.current = true) : probeAll())}>{batchBusy ? "⏹ 终止探测" : "🔍 探测全部未测"}</button>
        <button className="btn" onClick={fetchList}>⬇ 从上游拉取模型清单（可选）</button>
      </div>
      {status && <div className="dim small mono" style={{ margin: "6px 0", whiteSpace: "pre-wrap" }}>{status}</div>}
      <div className="card" style={{ padding: 0 }}>
        <table>{thead}<tbody>{current.map(renderRow)}</tbody></table>
      </div>
      {others.length > 0 && (
        <details className="card" style={{ marginTop: 8, padding: 12 }}>
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>未激活供应商（{others.length} 行，不参与探测）▸</summary>
          <table style={{ marginTop: 8 }}>{thead}<tbody>{others.map(renderRow)}</tbody></table>
        </details>
      )}
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <span className="dim small">{dirty.length ? dirty.length + " 处未保存" : "无未保存修改"}</span>
        <button className="btn primary" disabled={!dirty.length} onClick={save}>保存修改</button>
      </div>
    </>
  );
}
