import React from "react";
import ReactDOM from "react-dom/client";
import "./styles.css"; // 唯一的全局样式入口（mockup 移植）——漏掉它 GUI 就是无样式裸 HTML（2026-08-24 事故根因）
import App from "./App";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
