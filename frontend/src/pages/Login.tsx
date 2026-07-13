import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Form, FormItem, Input, Button, Toast, Navbar } from 'tdesign-mobile-react';
import { useAuthStore } from '@/stores/auth';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

export default function Login() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: unknown) => {
    if (e && typeof (e as { preventDefault?: () => void }).preventDefault === 'function') {
      (e as { preventDefault: () => void }).preventDefault();
    }
    setLoading(true);
    try {
      const request = createRequest(API_CONFIG.AUTH.BASE_URL, '认证服务');
      const data = await request<{
        access_token: string;
        refresh_token: string;
        expires_in: number;
      }>('/login', {
        method: 'POST',
        body: JSON.stringify({ username: 'debug', password: 'debug' }),
        skipAuth: true,
      });
      login(data, 'debug用户');
      Toast({ message: '登录成功', theme: 'success' });
      navigate('/call', { replace: true });
    } catch (err) {
      Toast({ message: `登录失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page-container" style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', minHeight: '100vh' }}>
      <Navbar title="摇人吧" fixed leftArrow onLeftClick={() => navigate(-1)} />
      <div style={{ maxWidth: 400, margin: '80px auto 0', width: '100%' }}>
        <h2 style={{ textAlign: 'center', marginBottom: 32, color: '#0052d9' }}>登录 OpenRobotService</h2>
        <Form onSubmit={handleSubmit}>
          <FormItem label="用户名" name="username">
            <Input placeholder="请输入用户名" defaultValue="debug" />
          </FormItem>
          <FormItem label="密码" name="password">
            <Input type="password" placeholder="请输入密码" defaultValue="debug" />
          </FormItem>
          <FormItem>
            <Button theme="primary" type="submit" block loading={loading}>
              登录
            </Button>
          </FormItem>
        </Form>
        <p style={{ textAlign: 'center', color: '#999', fontSize: 13, marginTop: 16 }}>
          Debug模式：用户名和密码均为 debug
        </p>
      </div>
    </div>
  );
}
