import { useNavigate } from 'react-router-dom';
import { Button, Navbar } from 'tdesign-mobile-react';

export default function NoPermission() {
  const navigate = useNavigate();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '100vh', padding: 32 }}>
      <div style={{ fontSize: 64, marginBottom: 16 }}>🔒</div>
      <h2 style={{ marginBottom: 8, color: '#333' }}>权限不足</h2>
      <p style={{ color: '#999', marginBottom: 32, textAlign: 'center' }}>
        您没有访问此页面的权限，请联系管理员
      </p>
      <Button theme="primary" onClick={() => navigate('/call', { replace: true })}>
        返回首页
      </Button>
    </div>
  );
}
