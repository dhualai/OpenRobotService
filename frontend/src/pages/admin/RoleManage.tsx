// 角色管理 - 从 BackgroundService tmp 迁移
import { useState, useEffect } from 'react';
import { Button, Toast, Loading } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

interface Role { id: string; name: string; description: string; permissions: string[]; }

export default function RoleManage() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(true);
  const request = createRequest(API_CONFIG.USER_CENTER.BASE_URL, 'UserCenter');

  useEffect(() => {
    request<Role[]>('/auth/roles/').then(setRoles).catch((e) => Toast({ message: String(e), theme: 'error' })).finally(() => setLoading(false));
  }, []);

  if (loading) return <Loading text="加载角色列表..." />;

  return (
    <div style={{ padding: 16 }}>
      {roles.map((role) => (
        <div key={role.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          <div style={{ fontWeight: 500, marginBottom: 4 }}>{role.name}</div>
          <div style={{ fontSize: 13, color: '#666', marginBottom: 8 }}>{role.description}</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {(role.permissions || []).map((p) => (
              <span key={p} style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, background: '#ecf2fe', color: '#0052d9' }}>{p}</span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
