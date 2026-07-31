// 权限管理 - 增强版：创建/编辑/删除权限 + 启用开关 + 搜索
// 基于接口文档 /api/admin/permissions
import { useState, useEffect, useCallback } from 'react';
import { Button, Switch, Toast, Loading, Dialog, Input, Popup, Form, FormItem } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface Permission { id: string; code: string; name: string; resource_type: string; action: string; enabled: boolean; }

const EMPTY_FORM = { name: '', resource_type: '', action: '', code: '' };

export default function PermissionManage() {
  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [loading, setLoading] = useState(true);

  // 搜索关键字
  const [keyword, setKeyword] = useState('');

  // 新建/编辑权限
  const [dialogVisible, setDialogVisible] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<{ code: string; name: string; resource_type: string; action: string }>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  // 用于强制 Form 重新挂载，使 initialData 在每次打开时重新读取
  const [formKey, setFormKey] = useState(0);

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

  const openCreate = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormKey((k) => k + 1);
    setDialogVisible(true);
  };

  const openEdit = (perm: Permission) => {
    setEditingId(perm.id);
    setForm({ code: perm.code, name: perm.name, resource_type: perm.resource_type, action: perm.action });
    setFormKey((k) => k + 1);
    setDialogVisible(true);
  };

  // 创建/更新权限
  const handleSave = async () => {
    if (!form.name.trim()) { Toast({ message: '请输入权限名称', theme: 'warning' }); return; }
    if (!form.code.trim()) { Toast({ message: '请输入权限编码', theme: 'warning' }); return; }
    if (!form.resource_type.trim()) { Toast({ message: '请输入资源类型', theme: 'warning' }); return; }
    setSubmitting(true);
    try {
      if (editingId) {
        await request(`/permissions/${editingId}`, { method: 'PUT', body: JSON.stringify(form) });
        Toast({ message: '权限已更新', theme: 'success' });
      } else {
        await request('/permissions/', { method: 'POST', body: JSON.stringify(form) });
        Toast({ message: '权限已创建', theme: 'success' });
      }
      setDialogVisible(false);
      setForm(EMPTY_FORM);
      setEditingId(null);
      fetchPermissions();
    } catch (err) {
      Toast({ message: `${editingId ? '更新' : '创建'}失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
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

  // 模糊搜索：按 name 或 code
  const lowerKey = keyword.trim().toLowerCase();
  const filtered = lowerKey
    ? permissions.filter((p) =>
        p.name.toLowerCase().includes(lowerKey) || (p.code || '').toLowerCase().includes(lowerKey))
    : permissions;

  return (
    <div style={{ padding: 16 }}>
      <Button theme="primary" block style={{ marginBottom: 12 }} onClick={openCreate}>
        新建权限
      </Button>

      <Input
        value={keyword}
        onChange={(v) => setKeyword(String(v))}
        placeholder="搜索权限名称或编码"
        clearable
        style={{ marginBottom: 12 }}
      />

      {filtered.map((perm) => (
        <div
          key={perm.id}
          onClick={() => openEdit(perm)}
          style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 500 }}>{perm.name}</div>
              <div style={{ fontSize: 13, color: '#999', marginTop: 2, wordBreak: 'break-all' }}>{perm.code}</div>
              <div style={{ fontSize: 13, color: '#666', marginTop: 2 }}>{perm.resource_type} · {perm.action}</div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
              <Switch value={perm.enabled} onChange={() => togglePermission(perm)} />
              <Button size="small" theme="danger" variant="outline" onClick={() => handleDelete(perm)}>删除</Button>
            </div>
          </div>
        </div>
      ))}

      {filtered.length === 0 && (
        <div style={{ textAlign: 'center', color: '#999', padding: 20 }}>暂无权限数据</div>
      )}

      {/* 新建/编辑权限弹窗 */}
      <Popup visible={dialogVisible} onClose={() => setDialogVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20 }}>
          <h4 style={{ marginBottom: 16 }}>{editingId ? '编辑权限' : '新建权限'}</h4>
          <Form key={formKey} initialData={form}>
            <FormItem label="权限编码" name="code">
              <Input value={form.code} onChange={(v) => setForm((p) => ({ ...p, code: String(v) }))} placeholder="如 backend:permission:base:read" clearable />
            </FormItem>
            <FormItem label="权限名称" name="name">
              <Input value={form.name} onChange={(v) => setForm((p) => ({ ...p, name: String(v) }))} placeholder="如 基础权限" clearable />
            </FormItem>
            <FormItem label="资源类型" name="resource_type">
              <Input value={form.resource_type} onChange={(v) => setForm((p) => ({ ...p, resource_type: String(v) }))} placeholder="如 backend" clearable />
            </FormItem>
            <FormItem label="操作" name="action">
              <Input value={form.action} onChange={(v) => setForm((p) => ({ ...p, action: String(v) }))} placeholder="如 read, write" clearable />
            </FormItem>
            <FormItem>
              <div style={{ display: 'flex', gap: 8 }}>
                <Button theme="default" block onClick={() => setDialogVisible(false)}>取消</Button>
                <Button theme="primary" block onClick={handleSave} loading={submitting}>{editingId ? '保存' : '创建'}</Button>
              </div>
            </FormItem>
          </Form>
        </div>
      </Popup>
    </div>
  );
}
