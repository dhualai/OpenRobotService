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
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
