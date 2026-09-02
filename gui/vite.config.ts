import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      // tauri dev 时 cargo 并发写 target/（dll 被锁），Windows 上 fs.watch 抛
      // EBUSY 且 chokidar 不吞 → Node 崩溃、beforeDevCommand 失败。Rust 侧
      // 由 tauri CLI 自己监听重启，vite 无需盯 src-tauri（官方脚手架同款配置）。
      ignored: ["**/src-tauri/**"],
    },
  },
});
