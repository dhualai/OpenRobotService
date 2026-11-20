import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // 挂载基路径：作为摇人吧子页面部署时传 VITE_BASE=/t/app/kb/（test）
  // 或 /p/app/kb/（prod）；本地开发留空即 "/"。
  base: process.env.VITE_BASE || "/",
  build: { sourcemap: false },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8767",
    },
  },
});
