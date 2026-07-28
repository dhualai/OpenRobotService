// 鉴权失效统一处理：弹出"需要登录"提示 → 登出（清 token）→ 跳转登录页（携带来源页）。
// 供 API 层（AI 模块 401）与 UI 层（ChatPanel 无 token）共同调用，保证"一出现需要登录就跳转"。
import { useAuthStore } from '@/stores/auth';
import { isLoggingOut } from '@/api/client';
import { Toast } from 'tdesign-mobile-react';

let _kicking = false;

/** 是否正在跳转登录页（供调用方抑制重复错误提示） */
export function isKickingToLogin(): boolean {
  return _kicking;
}

/**
 * 鉴权失效 / 需要登录的统一入口。
 * 多次调用只生效一次（避免并发 401 重复弹窗/跳转）。
 */
export function kickToLogin(reason = '请先登录'): void {
  // 登出流程中（用户主动登出已设 loggingOut=true 并会 navigate('/login?reason=logout')）：
  // 不抢跳转。否则这里 600ms 后整页跳 /login?from=...（无 reason），整页重载使 loggingOut 复位，
  // Login 页因 reason 非 logout 再跳微信静默授权=自动登录，覆盖 SPA 把页面带回摇人页。
  if (isLoggingOut()) return;
  if (_kicking) return;
  _kicking = true;
  try {
    Toast({ message: reason, theme: 'warning' });
  } catch {
    /* Toast 在某些环境未就绪，忽略；跳转照常进行 */
  }
  useAuthStore.getState().logout();
  const from = window.location.pathname + window.location.search;
  // 留 600ms 让用户看到提示，再跳转
  setTimeout(() => {
    window.location.assign(`/login?from=${encodeURIComponent(from)}`);
  }, 600);
}
