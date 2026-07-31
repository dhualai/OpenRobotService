// 路由守卫 Hook + 组件
import { useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuthStore, isManualLogout } from '@/stores/auth';
import { isLoggingOut } from '@/api/client';
import { checkUrlTokens, buildWechatAuthUrl, buildStateFromPath } from '@/shared/utils/url';
import { WECHAT_CONFIG } from '@/config/wechat';

const DISABLE_AUTH_GUARD = import.meta.env.VITE_DISABLE_AUTH_GUARD === 'true';

// 未登录时的跳转策略：
//   - 启用微信登录（VITE_WECHAT_LOGIN_ENABLED=true）→ 跳微信 OAuth 授权
//   - 未启用（测试环境默认）→ 跳 /login 账密登录页，无需跳转微信
//   - 登出流程中（loggingOut）或同一会话内已手动登出（manual_logout）→ 一律跳 /login?reason=logout，
//     禁止微信静默 OAuth 自动登录，把用户固定在登录页（由用户手动重新登录）
function redirectUnauthenticated(
  navigate: ReturnType<typeof useNavigate>,
  target: string,
): void {
  if (isLoggingOut() || isManualLogout()) {
    navigate('/login?reason=logout', { replace: true });
    return;
  }
  if (WECHAT_CONFIG.loginEnabled) {
    // state 携带完整目标地址（origin + 部署前缀 + 路由路径）的 base64url 编码，
    // 后端 /wechat/callback 解码后原样回跳，避免丢失 /p/app 前缀。
    const state = buildStateFromPath(target);
    window.location.href = buildWechatAuthUrl(state);
    return;
  }
  navigate(`/login?from=${encodeURIComponent(target)}`, { replace: true });
}

export function useAuthGuard(requireAdmin = false) {
  const { isLoggedIn, isLoading, isAdmin } = useAuthStore();
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    if (isLoading) return;

    if (DISABLE_AUTH_GUARD) return;

    // 先尝试从 URL 参数中恢复 token
    const restored = checkUrlTokens();

    if (!isLoggedIn && !restored) {
      // 与 AuthGuard 组件保持一致：按配置决定跳微信 OAuth 或账密页
      redirectUnauthenticated(navigate, location.pathname);
      return;
    }

    if (requireAdmin && !isAdmin) {
      navigate('/no-permission', { replace: true });
    }
  }, [isLoggedIn, isLoading, isAdmin, requireAdmin, navigate, location.pathname]);

  return { isLoggedIn, isLoading, isAdmin };
}

// AuthGuard 组件
import type { ReactNode } from 'react';

interface AuthGuardProps {
  children: ReactNode;
  requireAdmin?: boolean;
}

export function AuthGuard({ children, requireAdmin = false }: AuthGuardProps) {
  const { isLoggedIn, isLoading, isAdmin } = useAuthStore();
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    if (isLoading) return;
    if (DISABLE_AUTH_GUARD) return;

    // 先尝试从 URL 参数中恢复 token
    const restored = checkUrlTokens();

    if (!isLoggedIn && !restored) {
      // 仅用 pathname 作为 state，避免 query 参数污染后端回跳时的 token 拼接
      redirectUnauthenticated(navigate, location.pathname);
      return;
    }
    if (requireAdmin && !isAdmin) {
      navigate('/no-permission', { replace: true });
    }
  }, [isLoggedIn, isLoading, isAdmin, requireAdmin, navigate, location.pathname]);

  if (DISABLE_AUTH_GUARD) return <>{children}</>;
  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', color: '#0052d9' }}>
        加载中...
      </div>
    );
  }
  if (!isLoggedIn) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', color: '#0052d9' }}>
        正在跳转登录...
      </div>
    );
  }
  return <>{children}</>;
}
