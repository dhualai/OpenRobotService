// 用户管理 - 从 BackgroundService tmp 迁移
import { useState, useEffect } from 'react';
import { Button, Toast, Loading, Dialog } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

interface User { id: string; username: string; email: string; role: string; is_active: boolean; }

export default function UserManage() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const fetchUsers = async () => {
    setLoading(true);
    try { const data = await request<User[]>('/users/'); setUsers(data || []); } catch (e) { Toast({ message: String(e), theme: 'error' }); } finally { setLoading(false); }
  };

  useEffect(() => { fetchUsers(); }, []);

  const toggleActive = async (user: User) => {
    try {
      await request(`/users/${user.username}`, { method: 'PUT', body: JSON.stringify({ is_active: !user.is_active }) });
      Toast({ message: '状态已更新', theme: 'success' });
      fetchUsers();
    } catch (e) { Toast({ message: String(e), theme: 'error' }); }
  };

  if (loading) return <Loading text="加载用户列表..." />;

  return (
    <div style={{ padding: 16 }}>
      {users.map((user) => (
        <div key={user.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 500 }}>{user.username}</div>
              <div style={{ fontSize: 13, color: '#666' }}>{user.email} · {user.role}</div>
            </div>
            <Button size="small" theme={user.is_active ? 'primary' : 'danger'} variant="outline" onClick={() => toggleActive(user)}>
              {user.is_active ? '已启用' : '已禁用'}
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
