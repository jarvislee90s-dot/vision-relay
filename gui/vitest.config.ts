import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// 仅测试配置：环境由各测试文件头部的 `// @vitest-environment jsdom` 声明，
// 纯逻辑测试保持默认 node 环境不变（见计划 Task 1）。
export default defineConfig({
  plugins: [react()],
  test: {
    setupFiles: ["./src/test/setup.ts"],
  },
});
