/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  readonly VITE_DISABLE_AUTH_GUARD: string;
  readonly VITE_WECHAT_APP_ID: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
