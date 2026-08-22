import { useEffect, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";

export function CloseGuard() {
  const [asking, setAsking] = useState(false);
  const [remember, setRemember] = useState(localStorage.getItem("vr.close") === "stop");

  useEffect(() => {
    const unlisten = getCurrentWindow().onCloseRequested(async (event) => {
      // 总是接管关闭事件（Rust 侧已移除 prevent_close+hide，不 prevent 窗口会被直接销毁）
      event.preventDefault();
      const mode = localStorage.getItem("vr.close");
      if (mode === "ui") {
        // 隐藏到托盘：服务继续，托盘可重新打开
        await getCurrentWindow().hide();
      } else if (mode === "stop") {
        // 停服务并关闭窗口；进程留在托盘，托盘退出可彻底结束
        await import("../core").then((m) => m.stopService().catch(() => {}));
        await getCurrentWindow().destroy();
      } else {
        // 无保存模式：弹确认（窗口仍可见）
        setAsking(true);
      }
    });
    return () => { unlisten.then((f) => f()); };
  }, []);

  if (!asking) return null;
  const decide = async (stopServiceToo: boolean) => {
    setAsking(false);
    if (remember) localStorage.setItem("vr.close", stopServiceToo ? "stop" : "ui");
    if (stopServiceToo) {
      await import("../core").then((m) => m.stopService().catch(() => {}));
      await getCurrentWindow().destroy();
    } else {
      await getCurrentWindow().hide();
    }
  };
  return (
    <div className="modal show">
      <div className="modal-box">
        <b>关闭 vision-relay 控制台？</b>
        <div className="dim" style={{ margin: "10px 0" }}>服务在后台继续运行，可从托盘或本界面重新打开。</div>
        <label><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> 记住我的选择</label>
        <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
          <button className="btn" onClick={() => decide(false)}>仅关闭界面（服务继续）</button>
          <button className="btn red" onClick={() => decide(true)}>关闭界面并停止服务</button>
        </div>
      </div>
    </div>
  );
}
