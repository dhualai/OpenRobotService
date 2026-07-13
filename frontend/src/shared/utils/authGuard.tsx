// 路由守卫 Hook + 组件
import { useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '@/stores/auth';
import { getUrlParams, buildWechatAuthUrl, checkUrlTokens } from '@/shared/utils/url';

const DISABLE_AUTH_GUARD = import.meta.env.VITE_DISABLE_AUTH_GUARD === 'true';

export function useAuthGuard(requireAdmin = false) {
  const { isLoggedIn, isLoading, isAdmin } = useAuthStore();
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    if (isLoading) return;

    if (DISABLE_AUTH_GUARD) return;

    if (!isLoggedIn) {
      if (getUrlParams()) {
        navigate('/login', { replace: true });
      } else {
        const authUrl = buildWechatAuthUrl(location.pathname);
        window.location.href = authUrl;
      }
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

    checkUrlTokens();

    if (!isLoggedIn) {
      if (getUrlParams()) {
        navigate('/login', { replace: true });
      } else {
        const authUrl = buildWechatAuthUrl(location.pathname);
        window.location.href = authUrl;
      }
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
        正在跳转认证...
      </div>
    );
  }
  return <>{children}</>;
}
