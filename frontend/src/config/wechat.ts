// 微信相关可配置项集中管理
//
// 目的：让「微信网页授权登录跳转」与「微信 JS-SDK」在不同环境可开关，
// 测试环境无需真正跳转微信即可调试（默认全部关闭）。
//
// 环境变量（Vite 约定 VITE_ 前缀，写在 .env.local / .env.[mode] 中）：
//   VITE_WECHAT_LOGIN_ENABLED   'true' 启用微信 OAuth 登录跳转；否则未登录跳账密登录页（默认关闭）
//   VITE_WECHAT_JSSDK_ENABLED   'true' 启用微信 JS-SDK 初始化（默认关闭）
//   VITE_WECHAT_APP_ID          微信公众号 AppID（启用登录/JS-SDK 时必填）
//   VITE_WECHAT_OAUTH_SCOPE     授权作用域，snsapi_base（静默）| snsapi_userinfo（默认 snsapi_base）
//   VITE_WECHAT_REDIRECT_PATH   OAuth 回调路径（默认 /api/wechat/callback）

const flag = (v: string | undefined): boolean => v === 'true';

export const WECHAT_CONFIG = {
  /** 是否启用微信网页授权登录跳转；false 时未登录跳 /login 账密页（测试环境友好） */
  loginEnabled: flag(import.meta.env.VITE_WECHAT_LOGIN_ENABLED),
  /** 是否启用微信 JS-SDK 初始化 */
  jsSdkEnabled: flag(import.meta.env.VITE_WECHAT_JSSDK_ENABLED),
  /** 微信公众号 AppID */
  appId: import.meta.env.VITE_WECHAT_APP_ID || '',
  /** 授权作用域：snsapi_base（静默，仅拿 openid）| snsapi_userinfo（可拿昵称头像） */
  oauthScope: import.meta.env.VITE_WECHAT_OAUTH_SCOPE || 'snsapi_base',
  /** OAuth 回调路径（拼接在 window.location.origin 之后，作为 redirect_uri 的兜底推导） */
  redirectPath: import.meta.env.VITE_WECHAT_REDIRECT_PATH || '/api/wechat/callback',
  /** 完整 OAuth 回调地址（可选）。填了则直接作为 redirect_uri；留空则自动用 origin + redirectPath 推导 */
  redirectUri: import.meta.env.VITE_WECHAT_REDIRECT_URI || '',
} as const;

export default WECHAT_CONFIG;
