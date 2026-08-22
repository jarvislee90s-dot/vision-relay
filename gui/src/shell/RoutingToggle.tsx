import { useState } from "react";
import { startService, stopService } from "../core";
import { t, Lang } from "../i18n";
export function RoutingToggle(props: { on: boolean; onChangeDone: () => void; lang: Lang }) {
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
      <span className="dim">{t(props.lang, "routingOffLabel")}</span>
      <div className={"track" + (props.on ? "" : " off")} onClick={busy ? undefined : toggle}>
        <div className="knob" />
      </div>
      <b style={{ color: props.on ? "#059669" : "#6b7280" }}>{props.on ? t(props.lang, "routingOn") : t(props.lang, "routingOff")}</b>
    </div>
  );
}
