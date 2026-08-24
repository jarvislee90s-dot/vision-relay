// 样式护栏：styles.css 必须被 main.tsx 导入。
// 背景（2026-08-24 真实事故）：styles.css 忠实移植了 mockup 却从未被 import，
// GUI 以"无样式裸 HTML"跑完整个 M2——jsdom/tsc/vite 三层门禁都不报错，
// 只有产物检查（dist 无 .css）或人工视觉核对能发现。本护栏把这类回归挡在 CI。
// 实现说明：用 vite 原生 import.meta.glob(?raw) 读源码，避免引入 @types/node。
import { describe, expect, it } from "vitest";

const mainSrc = (import.meta.glob("./main.tsx", { query: "?raw", import: "default", eager: true }) as Record<
  string,
  string
>)["./main.tsx"];

describe("stylesheet wiring guard", () => {
  it("main.tsx imports ./styles.css（否则 GUI 完全无样式）", () => {
    expect(mainSrc).toContain('import "./styles.css"');
  });
});
