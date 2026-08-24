import { useEffect, useState } from "react";
import { core, setCorePath } from "../core";
import type { StatusData } from "../shell/useStatus";
import { Lang } from "../i18n";

interface VlmForm { model: string; base_url: string; api_key: string; format: string }
const EMPTY: VlmForm = { model: "", base_url: "", api_key: "", format: "chat" };
const HARNESSES = ["claude", "codex", "qwen-code"];

// 大写真组件（非 field() 闭包 helper）：useState 逐实例、跨重渲染存活；卸载即重置。
// 显隐翻转只走 onSet（不 touch），用户输入才 onEdit（touch）——reveal 不产生脏状态。
// 只读不覆盖用户输入（评审修正）：字段已有内容时不填真 key，仅切可见性；隐藏时
// 仅当内容未被编辑（仍等于揭示出的 key）才还原为空态=不修改，否则保留用户编辑。
function SecretField(p: {
  label: string;                    // 展示文案，如 "API key" | "claude · API key"
  ariaKey: string;                  // aria-label 用，如 "API key" | "claude 的 API key"
  value: string;
  onSet: (v: string) => void;       // 写值（不 touch：显隐翻转不动脏计数）
  onEdit: () => void;               // 用户输入才 touch
  onReveal: () => Promise<string>;  // 取该作用域真 key（""=未配置）
  width?: number;
}) {
  const [shown, setShown] = useState(false);
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState<string | null>(null); // 揭示出的真 key（隐藏时据此判断是否被编辑）

  const reveal = async () => {
    setBusy(true);
    try {
      const before = p.value;
      const real = await p.onReveal();
      setRevealed(real || null);
      // 空字段且取回期间未被改动 → 填入真 key（不 touch）；字段已有输入则绝不覆盖（用户编辑胜出）
      if (real && before === "" && p.value === before) p.onSet(real);
      // 有内容（真 key 或用户输入）才切可见；无 real 且字段空 → 无可显，保持隐藏
      if (real || p.value !== "") setShown(true);
    } catch (e) {
      window.alert("获取 key 失败：" + (e instanceof Error ? e.message : String(e)));
    } finally {
      setBusy(false);
    }
  };

  const hide = () => {
    // 内容仍是被揭示的真 key（未编辑）→ 还原空态=不修改；用户改过则保留其编辑，仅切回密码态
    if (revealed !== null && p.value === revealed) p.onSet("");
    setRevealed(null);
    setShown(false);
  };

  return (
    <div className="field">
      <label>{p.label}</label>
      <div className="row" style={{ gap: 6 }}>
        <input
          className="input"
          type={shown ? "text" : "password"}
          value={p.value}
          onChange={(e) => { p.onSet(e.target.value); p.onEdit(); }}
          style={{ minWidth: p.width ?? 160 }}
        />
        <button
          type="button"
          className="btn"
          aria-label={shown ? `隐藏 ${p.ariaKey}` : `显示 ${p.ariaKey}`}
          disabled={busy}
          onClick={async () => {
            if (shown) { hide(); return; }
            await reveal();
          }}
        >
          {shown ? "🙈" : "👁"}
        </button>
      </div>
    </div>
  );
}

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
  const [testImg, setTestImg] = useState<{ file: File; base64: string; url: string } | null>(null);
  const [testImgReading, setTestImgReading] = useState(false); // FileReader 读取中禁用测试按钮，防止此刻点击发出默认/旧图请求
  const [testImgErr, setTestImgErr] = useState<string | null>(null);

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

  const readAsDataURL = (f: File) =>
    new Promise<string>((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result));
      r.onerror = () => reject(new Error("read failed"));
      r.readAsDataURL(f);
    });

  const onPickImage = async (f: File) => {
    setTestImgErr(null);
    if (!["image/png", "image/jpeg", "image/webp", "image/gif"].includes(f.type)) {
      setTestImgErr("仅支持 PNG、JPEG、WebP 或 GIF");
      return;
    }
    if (f.size > 10 * 1024 * 1024) {
      setTestImgErr("图片不能超过 10 MiB");
      return;
    }
    setTestImgReading(true);
    try {
      const dataUrl = await readAsDataURL(f);
      setTestImg({ file: f, base64: dataUrl.split(",")[1] ?? "", url: dataUrl });
    } catch {
      setTestImgErr("图片读取失败");
    } finally {
      setTestImgReading(false);
    }
  };

  const runTest = async () => {
    setTestBusy(true); setTestOut(null);
    try {
      const stdin: Record<string, unknown> = {
        mode: testMode.startsWith("tier1") ? "tier1" : "tier2", // 四模式(tier1/tier1c/tier2/tier2c)→契约 mode=tier1|tier2
        question: testQ || null,
        custom_prompt: testMode.endsWith("c") ? (testCustom || null) : null, // 自选提示词仅 c 模式走 custom_prompt，否则 null
      };
      if (testImg) {
        stdin.image_base64 = testImg.base64;
        stdin.media_type = testImg.file.type;
      }
      const d = await core<{ desc: string; duration_ms: number; model: string }>("vlm-test", { stdin });
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
        <SecretField label="API key" ariaKey="API key" value={vlm.api_key}
          onSet={(v) => setVlm({ ...vlm, api_key: v })} onEdit={touch}
          onReveal={async () => (await core<{ vlm: { api_key: string } }>("vlm-secret")).vlm.api_key} />
        <div className="dim small" style={{ marginLeft: 92 }}>留空 = 不修改（👁 可点按显示已保存的 key；离开本页自动恢复隐藏）</div>
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
            <SecretField label={`${h} · API key`} ariaKey={`${h} 的 API key`} value={groups[h]!.api_key}
              onSet={(v) => setGroups({ ...groups, [h]: { ...groups[h]!, api_key: v } })} onEdit={touch}
              onReveal={async () => (await core<{ vlm_by_harness: Record<string, { api_key: string }> }>("vlm-secret")).vlm_by_harness[h]?.api_key ?? ""} />
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
          <button className="btn green" disabled={testBusy || testImgReading} onClick={runTest}>{testBusy ? "测试中…" : "开始测试"}</button>
        </div>
        <div className="field" style={{ marginTop: 8 }}>
          <label>自定义测试图（可选，走同一 VLM 调用路径）</label>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" aria-label="自定义图片"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) onPickImage(f); }} />
            {testImg && (
              <>
                <img src={testImg.url} alt="自定义测试图片预览" style={{ height: 48, border: "1px solid #ddd", borderRadius: 4 }} />
                <span className="dim small">{testImg.file.name} · {testImg.file.type}</span>
                <button type="button" className="btn" onClick={() => setTestImg(null)}>清除图片</button>
              </>
            )}
          </div>
          {testImgErr && <div className="err small" style={{ marginTop: 4 }}>{testImgErr}</div>}
        </div>
        {testOut && <div className="mono codebox" style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>{testOut}</div>}
        <div className="dim small" style={{ marginTop: 4 }}>未选自定义测试图时使用 1×1 最小图（验证连通与提示词）。</div>
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
