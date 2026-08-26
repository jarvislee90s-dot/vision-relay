// zcode 重启三选弹窗（spec §7.2）：选项①连带重启（默认）②取消本次操作 ③稍后自行重启。
export interface ZcodeChoice { label: string; kind: "restart" | "abort" | "later" }
export function ZcodeDialog(p: { title: string; desc: string; choices: ZcodeChoice[]; onChoose: (kind: ZcodeChoice["kind"]) => void }) {
  return (
    <div className="modal-backdrop" style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.35)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50 }}>
      <div className="card" style={{ maxWidth: 460, margin: 16 }} role="dialog" aria-label={p.title}>
        <h3 style={{ marginTop: 0 }}>{p.title}</h3>
        <p className="dim">{p.desc}</p>
        <div className="row" style={{ justifyContent: "flex-end", gap: 8, flexWrap: "wrap" }}>
          {p.choices.map((c) => (
            <button key={c.kind} className={"btn" + (c.kind === "restart" ? " primary" : "")} onClick={() => p.onChoose(c.kind)}>{c.label}</button>
          ))}
        </div>
      </div>
    </div>
  );
}
