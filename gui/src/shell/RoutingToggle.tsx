import { useState } from "react";
import { core, startService, stopService } from "../core";
import { t, Lang } from "../i18n";
import type { StatusData } from "./useStatus";
import { ZcodeDialog } from "./ZcodeDialog";

export function RoutingToggle(props: { on: boolean; onChangeDone: () => void; lang: Lang; status: StatusData | null }) {
  const [busy, setBusy] = useState(false);
  const [dlg, setDlg] = useState<null | "on" | "off">(null);
  // zcode 在受管清单且进程在跑 → 先弹三选（spec §7.2）；否则直接执行
  const zcodeLive = props.status?.zcode_runtime?.running && "zcode" in (props.status?.harnesses ?? {});
  const doToggle = async (restart: boolean) => {
    setBusy(true);
    try {
      if (props.on) await stopService();
      else await startService();
      if (restart && zcodeLive) await core("zcode-restart");
      setTimeout(props.onChangeDone, 1200);
    } finally { setBusy(false); }
  };
  const toggle = async () => {
    if (zcodeLive) { setDlg(props.on ? "off" : "on"); return; }
    await doToggle(false);
  };
  const act = dlg === "on" ? "开启" : "关闭";
  return (
    <>
      <div className="switch">
        <span className="dim">{t(props.lang, "routingOffLabel")}</span>
        <div className={"track" + (props.on ? "" : " off")} onClick={busy ? undefined : toggle}>
          <div className="knob" />
        </div>
        <b style={{ color: props.on ? "#059669" : "#6b7280" }}>{props.on ? t(props.lang, "routingOn") : t(props.lang, "routingOff")}</b>
      </div>
      {dlg && (
        <ZcodeDialog
          title={`⚡ ${act}路由与 zcode`}
          desc={`zcode 正在运行，配置改动需重启才能生效。${dlg === "off" ? "重启前 zcode 的请求将失败。" : ""}`}
          choices={[
            { label: `${act}路由并重启 zcode`, kind: "restart" },
            { label: `不${act}路由`, kind: "abort" },
            { label: `${act}路由，稍后自行重启`, kind: "later" },
          ]}
          onChoose={(kind) => {
            setDlg(null);
            if (kind === "abort") return;
            void doToggle(kind === "restart");
          }}
        />
      )}
    </>
  );
}
