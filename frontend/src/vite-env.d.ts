/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  readonly VITE_DISABLE_AUTH_GUARD: string;
  readonly VITE_WECHAT_APP_ID: string;
  readonly VITE_WECHAT_LOGIN_ENABLED: string;
  readonly VITE_WECHAT_JSSDK_ENABLED: string;
  readonly VITE_WECHAT_OAUTH_SCOPE: string;
  readonly VITE_WECHAT_REDIRECT_PATH: string;
  readonly VITE_WECHAT_REDIRECT_URI: string;
  // 前端轮询间隔（毫秒）：任务列表/角标自动刷新，缺省 10000，见 src/config/poll.ts
  readonly VITE_POLL_INTERVAL_MS: string;
  // 智库（功能拓扑）入口地址：独立服务，作为摇人吧子页面挂在 <base>/kb/
  readonly VITE_KB_PORTAL_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare module '*.png?inline' {
  const src: string;
  export default src;
}
