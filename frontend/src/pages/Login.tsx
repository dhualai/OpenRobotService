// 登录页 —— 机器人开源平台风格（移动端 H5）
// 未登录时由 AuthGuard 跳转至此（携带 ?from=原路径，登录成功回到来源页）。
import { useState, useEffect, type FormEvent } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Toast } from 'tdesign-mobile-react';
import { RobotIcon } from 'tdesign-icons-react';
import { useAuthStore } from '@/stores/auth';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { WECHAT_CONFIG } from '@/config/wechat';
import { buildWechatAuthUrl, buildStateFromPath } from '@/shared/utils/url';

/* ---------- 图标 ---------- */
/* 机器人徽标用 tdesign-icons-react（描边几何风，硬朗）；其余表单图标为内联 SVG。 */

function UserIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 20c0-4 4-6.2 8-6.2s8 2.2 8 6.2" />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="4.5" y="10" width="15" height="10" rx="2.6" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
      <circle cx="12" cy="15" r="1.5" fill="currentColor" stroke="none" />
    </svg>
  );
}

function EyeIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 3l18 18" />
      <path d="M10.6 6.1A10.9 10.9 0 0 1 12 6c6.4 0 10 6 10 6a16.2 16.2 0 0 1-3.4 4" />
      <path d="M6.2 6.2A16.2 16.2 0 0 0 2 12s3.6 6 10 6a10.9 10.9 0 0 0 4.2-.8" />
      <path d="M9.5 9.5a3 3 0 0 0 4.2 4.2" />
    </svg>
  );
}

export default function Login() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  // 登录成功后回到来源页（AuthGuard 未登录跳转时携带），缺省进工作台
  const from = params.get('from') || '/call';
  const login = useAuthStore((s) => s.login);
  const isLoggedIn = useAuthStore((s) => s.isLoggedIn);
  // debug=true 时强制显示账密登录表单（便于后台人员登录）；否则走微信登录
  const debugMode = params.get('debug') === 'true';

  // 与守卫页面保持一致的跳转策略：
  // 已登录 → 直接回来源页；启用微信登录且非 debug → 跳微信授权；其余 → 显示账密表单
  useEffect(() => {
    if (isLoggedIn) {
      navigate(from, { replace: true });
      return;
    }
    if (WECHAT_CONFIG.loginEnabled && !debugMode) {
      // 携带完整来源地址（含部署前缀）的 base64url state，后端解码后原样回跳
      const state = buildStateFromPath(from);
      window.location.href = buildWechatAuthUrl(state);
    }
  }, [isLoggedIn, debugMode, from, navigate]);

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPwd, setShowPwd] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) {
      Toast({ message: '请输入账号和密码', theme: 'warning' });
      return;
    }
    setLoading(true);
    try {
      const request = createRequest(API_CONFIG.AUTH.BASE_URL, '认证服务');
      const data = await request<{ access_token: string; refresh_token: string; expires_in: number }>(
        '/login',
        { method: 'POST', body: JSON.stringify({ username: username.trim(), password }), skipAuth: true },
      );
      login(data, username.trim());
      Toast({ message: '登录成功', theme: 'success' });
      navigate(from, { replace: true });
    } catch (err) {
      Toast({ message: `登录失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-card__brand">
          <div className="login-logo"><RobotIcon size={42} /></div>
          <h1 className="login-card__title">OpenRobotService</h1>
          <p className="login-card__subtitle">机器人开源平台 · 摇人吧</p>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="login-field">
            <span className="login-field__icon"><UserIcon /></span>
            <input
              className="login-field__input"
              type="text"
              placeholder="请输入账号"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
            />
          </div>

          <div className="login-field">
            <span className="login-field__icon"><LockIcon /></span>
            <input
              className="login-field__input"
              type={showPwd ? 'text' : 'password'}
              placeholder="请输入密码"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
            <button
              type="button"
              className="login-field__eye"
              onClick={() => setShowPwd((v) => !v)}
              aria-label={showPwd ? '隐藏密码' : '显示密码'}
            >
              {showPwd ? <EyeOffIcon /> : <EyeIcon />}
            </button>
          </div>

          <button className="login-btn" type="submit" disabled={loading}>
            {loading ? <span className="login-btn__spinner" /> : '登录'}
          </button>
        </form>

        <p className="login-card__foot">© OpenRobotService · 服务号 H5</p>
      </div>
    </div>
  );
}
