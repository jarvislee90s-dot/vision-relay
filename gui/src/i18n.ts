export type Lang = "zh" | "en";
const dict = {
  zh: { overview: "总览", models: "模型能力", visionlog: "识图记录", events: "事件日志", settings: "设置",
        routingOn: "路由开启", routingOff: "路由已关", routingOffLabel: "路由关闭", refresh: "刷新", diag: "诊断报告",
        coreUnavailable: "核心不可用" },
  en: { overview: "Overview", models: "Models", visionlog: "Vision Log", events: "Events", settings: "Settings",
        routingOn: "Routing ON", routingOff: "Routing OFF", routingOffLabel: "Routing", refresh: "Refresh", diag: "Diagnostics",
        coreUnavailable: "Core unavailable" },
} as const;
export function initialLang(): Lang {
  const saved = localStorage.getItem("vr.lang");
  if (saved === "zh" || saved === "en") return saved;
  return navigator.language.startsWith("zh") ? "zh" : "en";
}
export function t(lang: Lang, key: keyof typeof dict.zh): string {
  return dict[lang][key] ?? dict.zh[key];
}
