import { useEffect, useState } from "react";
import { core, setCorePath } from "../core";
import type { StatusData } from "../shell/useStatus";
import { Lang } from "../i18n";

interface VlmForm { model: string; base_url: string; api_key: string; format: string }
const EMPTY: VlmForm = { model: "", base_url: "", api_key: "", format: "chat" };
const HARNESSES = ["claude", "codex", "qwen-code"];

export function SettingsPage(p: { lang: string; status: StatusData | null; refresh: () => void; setLang: (l: Lang) => void }) {
  const [vlm, setVlm] = useState<VlmForm>(EMPTY);
  const [groups, setGroups] = useState<Record<string, VlmForm | null>>({});
  const [prompts, setPrompts] = useState<{ t1: string; t2: string }>({ t1: "", t2: "" });
  const [unknownDefault, setUnknownDefault] = useState("text_only");
  const [logCfg, setLogCfg] = useState({ enabled: true, retention_days: 7 });
  const [corePath, setCorePathInput] = useState("");
  const [dirtyCount, setDirtyCount] = useState(0);
  const [testOut, setTestOut] = useState<string | null>(null);
  const [testBusy, setTestBusy] = useState(false);
  const [testMode, setTestMode] = useState("tier1");
  const [testCustom, setTestCustom] = useState("");
  const [testQ, setTestQ] = useState("");

  useEffect(() => {
    core<Record<string, unknown>>("config").then((c) => {
      const v = c.vlm as Record<string, string>;
      setVlm({ model: v.model ?? "", base_url: v.base_url ?? "", api_key: "", format: v.format ?? "chat" });
      const g: Record<string, VlmForm | null> = {};
      for (const h of HARNESSES) g[h] = null;
      for (const [h, over] of Object.entries((c.vlm_by_harness ?? {}) as Record<string, Record<string, string>>)) {
        g[h] = { model: over.model ?? "", base_url: over.base_url ?? "", api_key: "", format: over.format ?? "" };
      }
      setGroups(g);
      setPrompts({ t1: v.custom_tier1 ?? "", t2: v.custom_tier2 ?? "" });
      const r = c.routing as Record<string, unknown>;
      setUnknownDefault((r.unknown_default as string) ?? "text_only");
      const vl = c.vision_log as Record<string, unknown>;
      setLogCfg({ enabled: vl.enabled !== false, retention_days: (vl.retention_days as number) ?? 7 });
    }).catch(() => {});
  }, []);

  const touch = () => setDirtyCount((n) => n + 1);

  const save = async () => {
    try {
      const payload: Record<string, unknown> = {
        vlm: { model: vlm.model, base_url: vlm.base_url, format: vlm.format, ...(vlm.api_key ? { api_key: vlm.api_key } : {}) },
        vlm_by_harness: Object.fromEntries(
          HARNESSES.filter((h) => groups[h]).map((h) => {
            const g = groups[h]!;
            return [h, g ? { model: g.model, base_url: g.base_url, ...(g.api_key ? { api_key: g.api_key } : {}) } : null];
          }),
        ),
      };
      if (prompts.t1) payload.custom_tier1 = prompts.t1; else payload.custom_tier1 = null;
      if (prompts.t2) payload.custom_tier2 = prompts.t2; else payload.custom_tier2 = null;
      await core("vlm-set", { stdin: payload });
      await core("settings-set", { stdin: { routing: { unknown_default: unknownDefault }, vision_log: logCfg } });
      if (corePath) setCorePath(corePath);
      setDirtyCount(0); p.refresh();
    } catch (e) {
      window.alert("保存失败：" + (e instanceof Error ? e.message : String(e)));
    }
  };

  const runTest = async () => {
    setTestBusy(true); setTestOut(null);
    try {
      const d = await core<{ desc: string; duration_ms: number; model: string }>("vlm-test", {
        stdin: {
          mode: testMode.startsWith("tier1") ? "tier1" : "tier2", // 四模式(tier1/tier1c/tier2/tier2c)→契约 mode=tier1|tier2
          question: testQ || null,
          custom_prompt: testMode.endsWith("c") ? (testCustom || null) : null, // 自选提示词仅 c 模式走 custom_prompt，否则 null
        },
      });
      setTestOut(`✅ ${d.model} · ${d.duration_ms}ms\n${d.desc}`);
    } catch (e) {
      setTestOut(`❌ ${String(e)}`);
    } finally { setTestBusy(false); }
  };

  const field = (label: string, value: string, onChange: (v: string) => void, type = "text", width = 320) => (
    <div className="field" key={label}>
      <label>{label}</label>
      <input className="input" type={type} value={value} onChange={(e) => { onChange(e.target.value); touch(); }} style={{ minWidth: width }} />
    </div>
  );

  return (
    <>
      <div className="card">
        <h3>🔍 VLM（唯一必配）{p.status?.vlm.configured ? <span className="tag ok">已配置</span> : <span className="tag err">未配置</span>}</h3>
        {field("模型名称", vlm.model, (v) => setVlm({ ...vlm, model: v }))}
        {field("base URL", vlm.base_url, (v) => setVlm({ ...vlm, base_url: v }))}
        {field("API key", vlm.api_key, (v) => setVlm({ ...vlm, api_key: v }), "password", 160)}
        <div className="dim small" style={{ marginLeft: 92 }}>留空 = 不修改（已保存的 key 不回显）</div>
      </div>

      <div className="card">
        <h3>按 harness 分组</h3>
        <table><tbody>
          {HARNESSES.map((h) => (
            <tr key={h}>
              <td>{h}</td>
              <td>{groups[h] ? <span className="tag gray">自定义</span> : <span className="tag ok">跟随全局 ✓</span>}</td>
              <td style={{ textAlign: "right" }}>
                <a href="#" onClick={(e) => { e.preventDefault(); setGroups({ ...groups, [h]: groups[h] ? null : { ...EMPTY } }); touch(); }}>
                  {groups[h] ? "改回跟随全局" : "单独配置"}
                </a>
              </td>
            </tr>
          ))}
        </tbody></table>
        {HARNESSES.filter((h) => groups[h]).map((h) => (
          <div className="card" key={h} style={{ margin: "8px 0 0" }}>
            {field(`${h} · 模型`, groups[h]!.model, (v) => setGroups({ ...groups, [h]: { ...groups[h]!, model: v } }))}
            {field(`${h} · base URL`, groups[h]!.base_url, (v) => setGroups({ ...groups, [h]: { ...groups[h]!, base_url: v } }))}
            {field(`${h} · API key`, groups[h]!.api_key, (v) => setGroups({ ...groups, [h]: { ...groups[h]!, api_key: v } }), "password", 160)}
          </div>
        ))}
      </div>

      <div className="card">
        <h3>外观</h3>
        <div className="field">
          <label>语言</label>
          {(["system", "zh", "en"] as const).map((l) => (
            <span key={l} className={"tag" + ((localStorage.getItem("vr.lang") ?? "system") === l ? " info" : " gray")} style={{ cursor: "pointer" }}
              onClick={() => { localStorage.setItem("vr.lang", l === "system" ? "" : l); p.setLang(l === "system" ? (navigator.language.startsWith("zh") ? "zh" : "en") : (l as Lang)); touch(); }}>
              {l === "system" ? "跟随系统" : l === "zh" ? "中文" : "English"}
            </span>
          ))}
        </div>
        <div className="field">
          <label>核心路径</label>
          <input className="input" value={corePath} placeholder="（自动探测 PATH；失败时手动指定）" onChange={(e) => { setCorePathInput(e.target.value); touch(); }} style={{ minWidth: 320 }} />
        </div>
      </div>

      <details className="card">
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>📝 识图提示词（高级，默认内置）▸</summary>
        <div className="field" style={{ alignItems: "flex-start", marginTop: 10 }}>
          <label>Tier1 全面</label>
          <textarea className="prompt" value={prompts.t1} onChange={(e) => { setPrompts({ ...prompts, t1: e.target.value }); touch(); }} />
        </div>
        <div className="field" style={{ alignItems: "flex-start" }}>
          <label>Tier2 聚焦</label>
          <textarea className="prompt" value={prompts.t2} onChange={(e) => { setPrompts({ ...prompts, t2: e.target.value }); touch(); }} />
        </div>
        <div className="row">
          <button className="btn" onClick={() => { setPrompts({ t1: "", t2: "" }); touch(); }}>↩ 恢复默认</button>
          <span className="dim small">当前：{prompts.t1 || prompts.t2 ? "自定义" : "默认"}</span>
        </div>
      </details>

      <details className="card">
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>服务与高级（默认值）▸</summary>
        <table style={{ marginTop: 8 }}><tbody>
          <tr><td className="dim" style={{ width: 130 }}>监听地址</td><td>127.0.0.1:8787（默认）</td></tr>
          <tr><td className="dim">管理的 harness</td><td>自动检测已安装者</td></tr>
          <tr><td className="dim">未标注模型默认</td>
            <td>
              <label><input type="radio" checked={unknownDefault === "text_only"} onChange={() => { setUnknownDefault("text_only"); touch(); }} /> 按纯文本走 VLM 转述（安全，默认）</label>
              <label style={{ marginLeft: 12 }}><input type="radio" checked={unknownDefault === "image"} onChange={() => { setUnknownDefault("image"); touch(); }} /> 直通（省 token，真纯文本会报错）</label>
            </td></tr>
          <tr><td className="dim">识图留痕</td>
            <td>
              <label><input type="checkbox" checked={logCfg.enabled} onChange={(e) => { setLogCfg({ ...logCfg, enabled: e.target.checked }); touch(); }} /> 记录</label>
              　留存 <input className="input" value={logCfg.retention_days} onChange={(e) => { const n = Math.floor(Number(e.target.value)); setLogCfg({ ...logCfg, retention_days: n >= 1 ? n : 7 }); touch(); }} style={{ width: 56 }} /> 天 · 仅存本机
            </td></tr>
        </tbody></table>
      </details>

      <div className="card">
        <h3>🧪 VLM 测试（与生产同一调用路径）</h3>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <select value={testMode} onChange={(e) => setTestMode(e.target.value)}>
            <option value="tier1">Tier1 · 默认提示词</option>
            <option value="tier1c">Tier1 · 自选提示词</option>
            <option value="tier2">Tier2 · 默认＋问题</option>
            <option value="tier2c">Tier2 · 自选提示词</option>
          </select>
          {testMode.startsWith("tier2") && <input className="input" placeholder="聚焦问题…" value={testQ} onChange={(e) => setTestQ(e.target.value)} />}
          {testMode.endsWith("c") && <input className="input" placeholder="自选提示词…" value={testCustom} onChange={(e) => setTestCustom(e.target.value)} />}
          <button className="btn green" disabled={testBusy} onClick={runTest}>{testBusy ? "测试中…" : "开始测试"}</button>
        </div>
        {testOut && <div className="mono codebox" style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>{testOut}</div>}
        <div className="dim small" style={{ marginTop: 4 }}>测试使用 1×1 最小图（不带用户图片，验证连通与提示词）。</div>
      </div>

      <div className="card row between" style={{ position: "sticky", bottom: 0 }}>
        <span className="dim small">{dirtyCount ? `● ${dirtyCount} 处未保存修改` : "无未保存修改"}——输入类修改统一由这里保存生效</span>
        <div className="row">
          <button className="btn" onClick={() => window.location.reload()}>放弃修改</button>
          <button className="btn primary" disabled={!dirtyCount} onClick={save}>💾 保存设置</button>
        </div>
      </div>
    </>
  );
}
