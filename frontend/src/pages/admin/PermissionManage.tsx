// 权限管理 - 从 BackgroundService tmp 迁移
import { useState, useEffect } from 'react';
import { Switch, Toast, Loading } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

interface Permission { id: string; name: string; resource: string; action: string; enabled: boolean; }

export default function PermissionManage() {
  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [loading, setLoading] = useState(true);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const fetchPermissions = async () => {
    setLoading(true);
    try { const data = await request<Permission[]>('/permissions/'); setPermissions(data || []); } catch (e) { Toast({ message: String(e), theme: 'error' }); } finally { setLoading(false); }
  };

  useEffect(() => { fetchPermissions(); }, []);

  const togglePermission = async (perm: Permission) => {
    try {
      await request(`/permissions/${perm.id}`, { method: 'PUT', body: JSON.stringify({ enabled: !perm.enabled }) });
      setPermissions((prev) => prev.map((p) => p.id === perm.id ? { ...p, enabled: !p.enabled } : p));
    } catch (e) { Toast({ message: String(e), theme: 'error' }); }
  };

  if (loading) return <Loading text="加载权限列表..." />;

  return (
    <div style={{ padding: 16 }}>
      {permissions.map((perm) => (
        <div key={perm.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 500 }}>{perm.name}</div>
              <div style={{ fontSize: 13, color: '#666' }}>{perm.resource} · {perm.action}</div>
            </div>
            <Switch value={perm.enabled} onChange={() => togglePermission(perm)} />
          </div>
        </div>
      ))}
    </div>
  );
}
