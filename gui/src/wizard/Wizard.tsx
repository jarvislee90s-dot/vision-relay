import { useEffect, useState } from "react";
import { core } from "../core";

interface Triple {
  harness: string;
  provider: string;
  model: string;
  value: string | null;
  source: string | null;
  probe_cached: string | null;
}

export function Wizard(p: { onDone: () => void }) {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState({
    model: "qwen-vl-max",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key: "",
  });
  const [rows, setRows] = useState<Triple[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (step === 2) core<{ models: Triple[] }>("models-scan").then((d) => setRows(d.models)).catch(() => {});
  }, [step]);

  const saveVlm = async () => {
    setErr(null);
    try {
      await core("vlm-set", {
        stdin: { vlm: { model: form.model, base_url: form.base_url, ...(form.api_key ? { api_key: form.api_key } : {}) } },
      });
      setStep(2);
    } catch (e) {
      setErr(String(e));
    }
  };
  const finish = async (reviewed: boolean) => {
    if (reviewed) {
      await core("models-set", {
        stdin: rows.map((r) => ({ harness: r.harness, provider: r.provider, model: r.model, value: r.value })),
      });
    } else {
      await core("models-set", { stdin: [] }); // 跳过 = 仅置首次确认标志
    }
    p.onDone();
  };

  return (
    <div className="modal show">
      <div className="modal-box" style={{ width: 680 }}>
        <div className="steps">
          <div className={"step " + (step === 1 ? "on" : "done")}>{step === 1 ? "① 配置 VLM（必填）" : "① 配置 VLM ✓"}</div>
          <div className={"step " + (step === 2 ? "on" : "")}>② 过目模型能力（可跳过）</div>
        </div>
        {step === 1 && (
          <div className="card">
            <div className="field">
              <label>模型名称</label>
              <input className="input" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} />
            </div>
            <div className="field">
              <label>base URL</label>
              <input className="input" style={{ minWidth: 380 }} value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
            </div>
            <div className="field">
              <label>API key</label>
              <input className="input" type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
            </div>
            {err && <div className="alert-err">{err}</div>}
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button className="btn primary lg" disabled={!form.api_key || !form.model} onClick={saveVlm}>
                下一步：过目模型能力 →
              </button>
            </div>
          </div>
        )}
        {step === 2 && (
          <div className="card">
            <div className="alert-warn">
              ⚠ 标错两个方向的代价：视觉模型标成纯文本 → 每张图都白走 VLM 转述（费 token + 降质）；纯文本标成视觉 → 请求报错。
            </div>
            <table>
              <thead>
                <tr>
                  <th>harness · provider</th>
                  <th>模型</th>
                  <th>标注</th>
                  <th>实测</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 12).map((r, i) => (
                  <tr key={i}>
                    <td>
                      {r.harness} · {r.provider}
                    </td>
                    <td>{r.model}</td>
                    <td>{r.value === "image" ? "支持图片" : r.value === "text_only" ? "纯文本" : "未标注"}</td>
                    <td>{r.probe_cached ?? "未测"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="row" style={{ justifyContent: "space-between", marginTop: 10 }}>
              <button className="btn" onClick={() => setStep(1)}>← 上一步</button>
              <div className="row">
                <button className="btn" onClick={() => finish(false)}>跳过（按默认）</button>
                <button className="btn primary lg" onClick={() => finish(true)}>完成 → 开启路由</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
