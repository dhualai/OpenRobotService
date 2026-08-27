/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { compression } from 'vite-plugin-compression2';

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
    plugins: [
      react(),
      // 构建时预压缩静态资源，配合 nginx `gzip_static on` / `brotli_static on` 直接发送预压缩文件，
      // 避免 nginx 实时压缩开销，进一步降低首屏传输体积
      // 注意：vite-plugin-compression2 v2 起选项为 algorithms（数组），旧的 algorithm（单数）会被忽略，
      // 导致每个实例按默认算法把 .gz/.br 各发一份、同名文件重复告警，故只注册一个实例
      compression({ algorithms: ['gzip', 'brotliCompress'], threshold: 1024 }),
    ],
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
      // echarts 按需引入（core + bar/pie + canvas 渲染）后单 chunk 约 555 kB，
      // 已是 tree-shake 后的合理下限（整包原为 1.1MB），阈值放宽到 600 以避免误报。
      chunkSizeWarningLimit: 600,
      rollupOptions: {
        output: {
          // 按模块路径分组而非按包名整包引入：既能合并出稳定 chunk，又不破坏 tree-shaking。
          // 此前 `echarts: ['echarts', ...]` 会把整个 echarts 包强制打入，正是 1.1MB chunk 的来源。
          manualChunks(id: string) {
            // 仅合并首屏明确依赖的少量共享组件为一个 admin-shared chunk，减少首屏 HTTP 请求数；
            // 不合并整个 /src/shared —— 否则会把非首屏大模块（hooks/api/stores）一起拖进首屏，体积暴涨（曾达 746KB）。
            if (
              id.includes('/src/shared/components/macaronBits') ||
              id.includes('/src/shared/components/macaronIcons') ||
              id.includes('/src/shared/components/macaronMonthBars') ||
              id.includes('/src/shared/components/UserAvatarMenu') ||
              id.includes('/src/shared/components/SubscriptionReminder')
            ) {
              return 'admin-shared';
            }
            if (!id.includes('node_modules')) return undefined;
            if (id.includes('/echarts') || id.includes('/zrender')) return 'echarts';
            if (id.includes('/tdesign-mobile-react') || id.includes('/tdesign-icons-react')) return 'tdesign';
            if (id.includes('/pdfjs-dist')) return 'pdfjs';
            if (
              id.includes('/react/') ||
              id.includes('/react-dom/') ||
              id.includes('/react-router') ||
              id.includes('/scheduler/')
            ) {
              return 'react';
            }
            return undefined;
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
