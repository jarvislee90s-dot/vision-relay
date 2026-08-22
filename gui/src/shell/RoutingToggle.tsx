import { useState } from "react";
import { startService, stopService } from "../core";
export function RoutingToggle(props: { on: boolean; onChangeDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const toggle = async () => {
    setBusy(true);
    try {
      if (props.on) await stopService();
      else await startService();
      setTimeout(props.onChangeDone, 1200);
    } finally { setBusy(false); }
  };
  return (
    <div className="switch">
      <span className="dim">路由关闭</span>
      <div className={"track" + (props.on ? "" : " off")} onClick={busy ? undefined : toggle}>
        <div className="knob" />
      </div>
      <b style={{ color: props.on ? "#059669" : "#6b7280" }}>{props.on ? "路由开启" : "路由已关"}</b>
    </div>
  );
}
