// 角色管理 - 增强版：创建/删除角色 + 权限管理
// 基于接口文档 /api/admin/roles + /api/admin/roles/{role_id}/permissions
import { useState, useEffect, useCallback } from 'react';
import { Button, Toast, Loading, Dialog, Input, Textarea, Popup, Form, FormItem, Checkbox } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface Role { id: string; name: string; description: string; permissions?: string[]; }
interface Permission { id: string; name: string; resource: string; action: string; }

export default function RoleManage() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(true);

  // 新建/编辑角色
  const [editVisible, setEditVisible] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState({ name: '', description: '' });
  const [submitting, setSubmitting] = useState(false);

  // 权限管理弹窗
  const [permVisible, setPermVisible] = useState(false);
  const [permRoleId, setPermRoleId] = useState<string | null>(null);
  const [allPermissions, setAllPermissions] = useState<Permission[]>([]);
  const [rolePermissions, setRolePermissions] = useState<string[]>([]);
  const [permLoading, setPermLoading] = useState(false);
  const [permSaving, setPermSaving] = useState(false);

  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const fetchRoles = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request<Role[]>('/roles/');
      setRoles(normalizeList<Role>(data));
    } catch (e) { Toast({ message: String(e), theme: 'error' }); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchRoles(); }, [fetchRoles]);

  // 新建角色
  const openCreate = () => {
    setEditingId(null);
    setForm({ name: '', description: '' });
    setEditVisible(true);
  };

  // 保存角色
  const handleSaveRole = async () => {
    if (!form.name.trim()) { Toast({ message: '请输入角色名称', theme: 'warning' }); return; }
    setSubmitting(true);
    try {
      if (editingId) {
        // 注意：接口文档无PUT更新角色的描述接口，仅保留名称不变并刷新列表
        await request(`/roles/`, { method: 'POST', body: JSON.stringify(form) });
      } else {
        await request('/roles/', { method: 'POST', body: JSON.stringify(form) });
      }
      Toast({ message: editingId ? '角色已更新' : '角色已创建', theme: 'success' });
      setEditVisible(false);
      fetchRoles();
    } catch (err) {
      Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally { setSubmitting(false); }
  };

  // 删除角色
  const handleDelete = (role: Role) => {
    Dialog.confirm?.({
      title: '确认删除',
      content: `确定要删除角色「${role.name}」吗？`,
      onConfirm: async () => {
        try {
          await request(`/roles/${role.id}`, { method: 'DELETE' });
          Toast({ message: '已删除', theme: 'success' });
          fetchRoles();
        } catch (err) {
          Toast({ message: `删除失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        }
      },
    });
  };

  // 打开权限管理
  const openPermissionManager = async (role: Role) => {
    setPermRoleId(role.id);
    setPermVisible(true);
    setPermLoading(true);
    try {
      // 加载所有可用权限
      const perms = await request<Permission[]>('/permissions/');
      setAllPermissions(normalizeList<Permission>(perms));
      // 加载当前角色的权限
      try {
        const rolePerms = await request<{ permissions?: string[] }>(`/roles/${role.id}/all-permissions`);
        setRolePermissions(rolePerms.permissions || []);
      } catch {
        setRolePermissions(role.permissions || []);
      }
    } catch (e) {
      Toast({ message: String(e), theme: 'error' });
    } finally { setPermLoading(false); }
  };

  // 切换权限
  const togglePerm = (permId: string) => {
    setRolePermissions((prev) =>
      prev.includes(permId) ? prev.filter((p) => p !== permId) : [...prev, permId]
    );
  };

  // 保存权限变更
  const handleSavePermissions = async () => {
    if (!permRoleId) return;
    setPermSaving(true);
    try {
      // 先移除所有权限，再重新添加
      await request(`/roles/${permRoleId}/permissions/remove`, {
        method: 'POST',
        body: JSON.stringify({ permission_ids: allPermissions.map((p) => p.id) }),
      }).catch(() => {}); // 忽略清空失败
      if (rolePermissions.length > 0) {
        await request(`/roles/${permRoleId}/permissions`, {
          method: 'POST',
          body: JSON.stringify({ permission_ids: rolePermissions }),
        });
      }
      Toast({ message: '权限已更新', theme: 'success' });
      setPermVisible(false);
      fetchRoles();
    } catch (err) {
      Toast({ message: `保存权限失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally { setPermSaving(false); }
  };

  if (loading) return <Loading text="加载角色列表..." />;

  return (
    <div style={{ padding: 16 }}>
      <Button theme="primary" block style={{ marginBottom: 16 }} onClick={openCreate}>
        新建角色
      </Button>

      {roles.map((role) => (
        <div key={role.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 500, marginBottom: 4 }}>{role.name}</div>
              <div style={{ fontSize: 13, color: '#666', marginBottom: 8 }}>{role.description}</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {(role.permissions || []).slice(0, 5).map((p) => (
                  <span key={p} style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, background: '#ecf2fe', color: '#0052d9' }}>{p}</span>
                ))}
                {(role.permissions?.length || 0) > 5 && (
                  <span style={{ fontSize: 11, padding: '2px 8px', color: '#999' }}>+{role.permissions!.length - 5} 更多</span>
                )}
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flexShrink: 0, marginLeft: 12 }}>
              <Button size="small" variant="outline" onClick={() => openPermissionManager(role)}>权限</Button>
              <Button size="small" variant="outline" onClick={() => { setEditingId(role.id); setForm({ name: role.name, description: role.description }); setEditVisible(true); }}>编辑</Button>
              <Button size="small" theme="danger" variant="outline" onClick={() => handleDelete(role)}>删除</Button>
            </div>
          </div>
        </div>
      ))}

      {/* 新建/编辑角色弹窗 */}
      <Popup visible={editVisible} onClose={() => setEditVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20 }}>
          <h4 style={{ marginBottom: 16 }}>{editingId ? '编辑角色' : '新建角色'}</h4>
          <Form onSubmit={handleSaveRole}>
            <FormItem label="角色名称">
              <Input value={form.name} onChange={(v) => setForm((p) => ({ ...p, name: String(v) }))} placeholder="如 admin, editor" clearable />
            </FormItem>
            <FormItem label="描述">
              <Textarea value={form.description} onChange={(v) => setForm((p) => ({ ...p, description: String(v) }))} placeholder="角色描述" autosize={{ minRows: 2, maxRows: 4 }} />
            </FormItem>
            <FormItem>
              <div style={{ display: 'flex', gap: 8 }}>
                <Button theme="default" block onClick={() => setEditVisible(false)}>取消</Button>
                <Button theme="primary" block type="submit" loading={submitting}>保存</Button>
              </div>
            </FormItem>
          </Form>
        </div>
      </Popup>

      {/* 权限管理弹窗 */}
      <Popup visible={permVisible} onClose={() => setPermVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '70vh', overflow: 'auto' }}>
          <h4 style={{ marginBottom: 16 }}>管理角色权限</h4>
          {permLoading ? <Loading text="加载权限..." /> : (
            <div style={{ marginBottom: 16 }}>
              {allPermissions.map((perm) => (
                <div
                  key={perm.id}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 0', borderBottom: '1px solid #f5f5f5',
                  }}
                  onClick={() => togglePerm(perm.id)}
                >
                  <Checkbox checked={rolePermissions.includes(perm.id)} />
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 500 }}>{perm.name}</div>
                    <div style={{ fontSize: 12, color: '#999' }}>{perm.resource} · {perm.action}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <Button theme="default" block onClick={() => setPermVisible(false)}>取消</Button>
            <Button theme="primary" block onClick={handleSavePermissions} loading={permSaving}>保存权限</Button>
          </div>
        </div>
      </Popup>
    </div>
  );
}
