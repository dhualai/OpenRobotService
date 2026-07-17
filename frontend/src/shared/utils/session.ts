// 鉴权失效统一处理：弹出"需要登录"提示 → 登出（清 token）→ 跳转登录页（携带来源页）。
// 供 API 层（AI 模块 401）与 UI 层（ChatPanel 无 token）共同调用，保证"一出现需要登录就跳转"。
import { useAuthStore } from '@/stores/auth';
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
