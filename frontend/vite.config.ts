/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// ── dev 代理目标：业务后端与 AI 服务已拆分（与 deploy/nginx/conf/conf.d/app_gateway.conf 对齐）─────
//    业务后端：backend/main.py @8400（auth/admin/tasks/call/wechat）
//    AI 服务：  ai/run.py      @8401（/api/ai/*，含 SSE 流式）
//    本地需同时启动两者；可用环境变量覆盖目标地址：
//      VITE_DEV_BACKEND_TARGET=http://localhost:8400 VITE_DEV_AI_TARGET=http://localhost:8401 npm run dev
const DEV_BACKEND_TARGET = process.env.VITE_DEV_BACKEND_TARGET || 'http://localhost:8400';
const DEV_AI_TARGET = process.env.VITE_DEV_AI_TARGET || 'http://localhost:8401';

// ── 构建基础路径（生产环境前缀）─────────────────────────────────────────────────
//    dev 不设（默认 '/'）；构建时通过 CLI --base 指定（见 package.json build:test/build:prod）：
//      vite build --base=/t/app/   → 测试环境，nginx 以 /t/app/ 托管、/t/api/* 分发
//      vite build --base=/p/app/   → 生产环境，nginx 以 /p/app/ 托管、/p/api/* 分发
//    前端 API 前缀由 src/config/api.ts 据 base 自动推导，无需在此重复配置。
const APP_BASE = process.env.VITE_APP_BASE || '/';

export default defineConfig(({ command }) => {
  // 仅 dev(serve) 生效的代理；build 不启动 dev server，生产走 nginx 分发
  const proxy = command === 'serve' ? {
    // AI 服务（/api/ai/*，含 SSE 流式）→ 8401；须排在 /api 之前，保证最长前缀优先命中
    '/api/ai': {
      target: DEV_AI_TARGET,
      changeOrigin: true,
      // SSE：关闭缓冲，token 增量直通
      ws: false,
    },
    // 业务后端（auth/admin/tasks/call/wechat）→ 8400
    '/api': {
      target: DEV_BACKEND_TARGET,
      changeOrigin: true,
      // 开启 WebSocket 转发，使 dev 下评论区实时 WS（/api/tasks/{id}/ws）可连后端 8400；
      // 生产不走 vite，由 nginx 的 Upgrade 透传处理（见 deploy/nginx/conf/nginx.conf）。
      ws: true,
    },
  } : {};

  return {
    base: APP_BASE,
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      proxy,
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            echarts: ['echarts', 'echarts-for-react'],
            tdesign: ['tdesign-mobile-react'],
            react: ['react', 'react-dom', 'react-router-dom'],
          },
        },
      },
    },
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      css: true,
      env: {
        VITE_DISABLE_AUTH_GUARD: 'false',
      },
      coverage: {
        provider: 'v8',
        reporter: ['text', 'json', 'html'],
        include: ['src/**/*.{ts,tsx}'],
        exclude: ['src/test/**', 'src/vite-env.d.ts', 'src/**/*.d.ts'],
      },
    },
  };
});
