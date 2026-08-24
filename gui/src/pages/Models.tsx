import { useEffect, useState } from "react";
import { core } from "../core";

interface Triple { harness: string; provider: string; model: string; value: string | null; source: string | null; probe_cached: string | null }
type Draft = Record<string, string | null>;

export function ModelsPage(p: { lang: string; refresh: () => void }) {
  const [rows, setRows] = useState<Triple[]>([]);
  const [draft, setDraft] = useState<Draft>({});
  const [busy, setBusy] = useState("");

  // 只刷新行数据、保留未保存的 draft（retest/probeAll 用；save 成功后才整体重置）
  const refreshRows = async () => { setRows((await core<{ models: Triple[] }>("models-scan")).models); };
  const load = async () => { await refreshRows(); setDraft({}); };
  useEffect(() => { load().catch((e) => { console.error(e); window.alert(String(e)); }); }, []);

  const key = (r: Triple) => r.harness + "|" + r.provider + "|" + r.model;
  const effective = (r: Triple) => (key(r) in draft ? draft[key(r)] : r.value);
  const dirty = rows.filter((r) => key(r) in draft && draft[key(r)] !== r.value);

  const save = async () => {
    try {
      await core("models-set", { stdin: dirty.map((r) => ({ harness: r.harness, provider: r.provider, model: r.model, value: draft[key(r)] })) });
      await load(); p.refresh();
    } catch (e) { console.error(e); window.alert(String(e)); }
  };
  const retest = async (r: Triple) => {
    setBusy(r.model);
    try {
      const d = await core<{ result: string | null; target_found: boolean; reason: string | null }>("probe", { args: ["--harness", r.harness, "--provider", r.provider, "--model", r.model] });
      if (d.result === null)
        window.alert(d.target_found === false ? (d.reason ?? "探测目标不可达") : "已探测但无结论（超时/鉴权/回答含糊），不下判定");
      await refreshRows();
    } catch (e) { console.error(e); window.alert(String(e)); } finally { setBusy(""); }
  };
  const probeAll = async () => {
    setBusy("all");
    try {
      const d = await core<{ probed: number; results: { result: string | null; target_found: boolean; reason: string | null }[] }>("probe", { args: ["--all-untested"] });
      if (d.probed === 0) window.alert("没有待探测的模型（全部已有缓存结论）");
      else if (d.results.every((x) => x.result === null)) {
        const first = d.results.find((x) => x.target_found === false);
        window.alert(`已探测 ${d.probed} 个，均无结论${first ? "：" + (first.reason ?? "目标不可达") : "（超时/鉴权/回答含糊）"}`);
      }
      await refreshRows();
    } catch (e) { console.error(e); window.alert(String(e)); } finally { setBusy(""); }
  };
  const fetchList = async () => {
    try {
      const d = await core<{ providers: Record<string, string[]>; errors: Record<string, string> }>("models-fetch");
      const ids = Object.values(d.providers).flat();
      window.alert(ids.length ? "拉到 " + ids.length + " 个模型 ID（能力以探针/目录为准）：\n" + ids.slice(0, 30).join("\n") + (ids.length > 30 ? "\n…" : "") : "未拉到清单：" + JSON.stringify(d.errors));
    } catch (e) { console.error(e); window.alert(String(e)); }
  };
  const cycle = (v: string | null) => (v === null ? "text_only" : v === "text_only" ? "image" : null);

  return (
    <>
      <div className="alert-ok">
        ✅ 按 (harness · provider · 模型) 三元组标注，记录只有输入模态一个字段（纯文本 / 支持图片 / 未标注）。只在猜错时改；未标注模型运行时按设置开关（默认走识图）。
      </div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <button className="btn" disabled={!!busy} onClick={probeAll}>{busy === "all" ? "探测中…" : "🔍 探测全部未测"}</button>
        <button className="btn" onClick={fetchList}>⬇ 从上游拉取模型清单（可选）</button>
      </div>
      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>harness</th><th>provider</th><th>模型</th><th>当前标注</th><th>依据</th><th>实测</th><th>操作</th></tr></thead>
          <tbody>
            {rows.map((r, i) => {
              const v = effective(r);
              return (
                <tr key={i} style={{ background: key(r) in draft ? "#fffbeb" : undefined }}>
                  <td>{r.harness}</td><td>{r.provider}</td><td>{r.model}</td>
                  <td>{v === null ? <span className="tag gray">未标注（空）</span> : v === "image" ? <span className="cap image">支持图片</span> : <span className="cap text">纯文本</span>}</td>
                  <td className="dim small">{r.source ?? "—"}{r.probe_cached ? "（缓存 " + r.probe_cached + "）" : ""}</td>
                  <td className="small">{r.probe_cached === "image" ? "✓ 答对" : r.probe_cached === "text_only" ? "✗ 报错/吞图" : <span className="tag gray">未测</span>}</td>
                  <td>
                    <a href="#" onClick={(e) => { e.preventDefault(); setDraft({ ...draft, [key(r)]: cycle(v) }); }}>切换</a>
                    {" · "}<a href="#" onClick={(e) => { e.preventDefault(); retest(r); }}>{busy === r.model ? "探测中…" : "重测"}</a>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <span className="dim small">{dirty.length ? dirty.length + " 处未保存" : "无未保存修改"}</span>
        <button className="btn primary" disabled={!dirty.length} onClick={save}>保存修改</button>
      </div>
    </>
  );
}
