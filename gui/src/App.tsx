import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { initialLang, t, Lang } from "./i18n";
import { useStatus } from "./shell/useStatus";
import { CloseGuard } from "./shell/CloseGuard";
import { Overview } from "./pages/Overview";
import { ModelsPage } from "./pages/Models";
import { VisionLogPage } from "./pages/VisionLog";
import { EventsPage } from "./pages/Events";
import { SettingsPage } from "./pages/Settings";

export default function App() {
  const [page, setPage] = useState("overview");
  const [lang, setLang] = useState<Lang>(initialLang());
  const { status, error, refresh } = useStatus();
  const [showDiag, setShowDiag] = useState(false);
  useEffect(() => {
    // 托盘菜单事件：toggle -> 回总览；diag -> 弹诊断报告
    let un: (() => void) | undefined;
    listen("tray", (e) => {
      if (e.payload === "toggle") setPage("overview");
      if (e.payload === "diag") setShowDiag(true);
    }).then((f) => { un = f; });
    return () => { if (un) un(); };
  }, []);
  const nav: [string, string][] = [["overview","overview"],["models","models"],["visionlog","visionlog"],["events","events"],["settings","settings"]];
  return (
    <div className="app">
      <div className="side">
        <div className="logo">👁 vision-relay</div>
        {nav.map(([id, key]) => (
          <div key={id} className={"side-item" + (page === id ? " active" : "")} onClick={() => setPage(id)}>
            {t(lang, key as never)}
          </div>
        ))}
      </div>
      <div className="main">
        {error && <div className="alert-err">核心不可用：{error}</div>}
        {page === "overview" && <Overview status={status} refresh={refresh} lang={lang} showDiag={showDiag} setShowDiag={setShowDiag} />}
        {page === "models" && <ModelsPage lang={lang} refresh={refresh} />}
        {page === "visionlog" && <VisionLogPage lang={lang} />}
        {page === "events" && <EventsPage lang={lang} />}
        {page === "settings" && <SettingsPage lang={lang} status={status} refresh={refresh} setLang={setLang} />}
      </div>
      <CloseGuard />
    </div>
  );
}
