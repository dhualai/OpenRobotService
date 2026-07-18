/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// 测试服务器地址：dev（serve）走此地址；build 时 proxy target 置空。
// 注：proxy 仅对 `vite dev` 生效，`vite build` 不启动 dev server，target 在构建期无实际作用，
// 这里按需求在 build 时显式置空，避免硬编码后端地址。
const TEST_API_TARGET = 'http://localhost:8400';

export default defineConfig(({ command }) => {
  // serve = npm run dev；build = npm run build
  const apiTarget = command === 'serve' ? TEST_API_TARGET : '';

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      proxy: {
        // 若需将「认证 / AI 模块」指向本地开发后端，把下面两条的 target 改为本地地址即可，
        // 不影响其余 /api 仍走测试服务器。
        '/api/auth': { target: apiTarget, changeOrigin: true },
        '/api/ai': { target: apiTarget, changeOrigin: true },
        '/api': { target: apiTarget, changeOrigin: true },
      },
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
