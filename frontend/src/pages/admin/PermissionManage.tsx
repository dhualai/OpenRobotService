// 权限管理 - 增强版：创建/删除权限 + 启用开关
// 基于接口文档 /api/admin/permissions
import { useState, useEffect, useCallback } from 'react';
import { Button, Switch, Toast, Loading, Dialog, Input, Popup, Form, FormItem } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface Permission { id: string; name: string; resource: string; action: string; enabled: boolean; }

export default function PermissionManage() {
  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [loading, setLoading] = useState(true);

  // 新建权限
  const [createVisible, setCreateVisible] = useState(false);
  const [form, setForm] = useState({ name: '', resource: '', action: '' });
  const [submitting, setSubmitting] = useState(false);

  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const fetchPermissions = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request<Permission[]>('/permissions/');
      setPermissions(normalizeList<Permission>(data));
    } catch (e) { Toast({ message: String(e), theme: 'error' }); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchPermissions(); }, [fetchPermissions]);

  const togglePermission = async (perm: Permission) => {
    try {
      await request(`/permissions/${perm.id}`, { method: 'PUT', body: JSON.stringify({ enabled: !perm.enabled }) });
      setPermissions((prev) => prev.map((p) => p.id === perm.id ? { ...p, enabled: !p.enabled } : p));
    } catch (e) { Toast({ message: String(e), theme: 'error' }); }
  };

  // 创建权限
  const handleCreate = async () => {
    if (!form.name.trim()) { Toast({ message: '请输入权限名称', theme: 'warning' }); return; }
    setSubmitting(true);
    try {
      await request('/permissions/', { method: 'POST', body: JSON.stringify(form) });
      Toast({ message: '权限已创建', theme: 'success' });
      setCreateVisible(false);
      setForm({ name: '', resource: '', action: '' });
      fetchPermissions();
    } catch (err) {
      Toast({ message: `创建失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally { setSubmitting(false); }
  };

  // 删除权限
  const handleDelete = (perm: Permission) => {
    Dialog.confirm?.({
      title: '确认删除',
      content: `确定要删除权限「${perm.name}」吗？`,
      onConfirm: async () => {
        try {
          await request(`/permissions/${perm.id}`, { method: 'DELETE' });
          Toast({ message: '已删除', theme: 'success' });
          fetchPermissions();
        } catch (err) {
          Toast({ message: `删除失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        }
      },
    });
  };

  if (loading) return <Loading text="加载权限列表..." />;

  return (
    <div style={{ padding: 16 }}>
      <Button theme="primary" block style={{ marginBottom: 16 }} onClick={() => setCreateVisible(true)}>
        新建权限
      </Button>

      {permissions.map((perm) => (
        <div key={perm.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 500 }}>{perm.name}</div>
              <div style={{ fontSize: 13, color: '#666' }}>{perm.resource} · {perm.action}</div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Switch value={perm.enabled} onChange={() => togglePermission(perm)} />
              <Button size="small" theme="danger" variant="outline" onClick={() => handleDelete(perm)}>删除</Button>
            </div>
          </div>
        </div>
      ))}

      {/* 新建权限弹窗 */}
      <Popup visible={createVisible} onClose={() => setCreateVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20 }}>
          <h4 style={{ marginBottom: 16 }}>新建权限</h4>
          <Form onSubmit={handleCreate}>
            <FormItem label="权限名称">
              <Input value={form.name} onChange={(v) => setForm((p) => ({ ...p, name: String(v) }))} placeholder="如 user:read" clearable />
            </FormItem>
            <FormItem label="资源">
              <Input value={form.resource} onChange={(v) => setForm((p) => ({ ...p, resource: String(v) }))} placeholder="如 users" clearable />
            </FormItem>
            <FormItem label="操作">
              <Input value={form.action} onChange={(v) => setForm((p) => ({ ...p, action: String(v) }))} placeholder="如 read, write" clearable />
            </FormItem>
            <FormItem>
              <div style={{ display: 'flex', gap: 8 }}>
                <Button theme="default" block onClick={() => setCreateVisible(false)}>取消</Button>
                <Button theme="primary" block type="submit" loading={submitting}>创建</Button>
              </div>
            </FormItem>
          </Form>
        </div>
      </Popup>
    </div>
  );
}
