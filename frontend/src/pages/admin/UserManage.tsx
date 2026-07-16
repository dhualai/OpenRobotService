// 用户管理 - 增强版：启用/禁用 + 角色分配
// 基于接口文档 /api/admin/users + /api/admin/users/{username}/roles
import { useState, useEffect, useCallback } from 'react';
import { Button, Toast, Loading, Dialog, Input, Popup, Checkbox } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface User { id: string; username: string; email: string; role: string; is_active: boolean; }
interface Role { id: string; name: string; description?: string; }

export default function UserManage() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  // 角色分配
  const [roleVisible, setRoleVisible] = useState(false);
  const [selectedUser, setSelectedUser] = useState<string | null>(null);
  const [allRoles, setAllRoles] = useState<Role[]>([]);
  const [userRoles, setUserRoles] = useState<string[]>([]);
  const [roleLoading, setRoleLoading] = useState(false);
  const [roleSaving, setRoleSaving] = useState(false);

  // 搜索
  const [keyword, setKeyword] = useState('');

  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    try {
      const params = keyword ? `?keyword=${encodeURIComponent(keyword)}` : '';
      const data = await request<User[]>(`/users/${params}`);
      setUsers(normalizeList<User>(data));
    } catch (e) { Toast({ message: String(e), theme: 'error' }); }
    finally { setLoading(false); }
  }, [keyword]);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  const toggleActive = async (user: User) => {
    try {
      await request(`/users/${user.username}`, { method: 'PUT', body: JSON.stringify({ is_active: !user.is_active }) });
      Toast({ message: '状态已更新', theme: 'success' });
      fetchUsers();
    } catch (e) { Toast({ message: String(e), theme: 'error' }); }
  };

  // 添加搜索
  const handleSearch = () => { fetchUsers(); };

  // 打开角色分配弹窗
  const openRoleAssign = async (user: User) => {
    setSelectedUser(user.username);
    setRoleVisible(true);
    setRoleLoading(true);
    try {
      // 加载所有角色
      const roles = await request<Role[]>('/roles/');
      setAllRoles(normalizeList<Role>(roles));
      // 加载用户当前角色（通过 detail 接口）
      try {
        const detail = await request<{ roles?: { project_backend?: string[] } }>(`/users/${user.username}/detail`);
        setUserRoles(detail.roles?.project_backend || []);
      } catch {
        setUserRoles([]);
      }
    } catch (e) {
      Toast({ message: String(e), theme: 'error' });
    } finally { setRoleLoading(false); }
  };

  // 切换用户角色
  const toggleUserRole = (roleName: string) => {
    setUserRoles((prev) =>
      prev.includes(roleName) ? prev.filter((r) => r !== roleName) : [...prev, roleName]
    );
  };

  // 保存角色分配
  const handleSaveRoles = async () => {
    if (!selectedUser) return;
    setRoleSaving(true);
    try {
      await request(`/users/${selectedUser}/roles`, {
        method: 'POST',
        body: JSON.stringify({ roles: userRoles }),
      });
      Toast({ message: '角色已更新', theme: 'success' });
      setRoleVisible(false);
      fetchUsers();
    } catch (err) {
      Toast({ message: `保存角色失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally { setRoleSaving(false); }
  };

  if (loading) return <Loading text="加载用户列表..." />;

  return (
    <div style={{ padding: 16 }}>
      {/* 搜索栏 */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <Input
          value={keyword}
          onChange={(v) => setKeyword(String(v))}
          placeholder="搜索用户…"
          clearable
          style={{ flex: 1 }}
        />
        <Button size="small" theme="primary" onClick={handleSearch}>搜索</Button>
      </div>

      {users.map((user) => (
        <div key={user.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 500 }}>{user.username}</div>
              <div style={{ fontSize: 13, color: '#666' }}>{user.email} · {user.role}</div>
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <Button size="small" variant="outline" onClick={() => openRoleAssign(user)}>角色</Button>
              <Button size="small" theme={user.is_active ? 'primary' : 'danger'} variant="outline" onClick={() => toggleActive(user)}>
                {user.is_active ? '已启用' : '已禁用'}
              </Button>
            </div>
          </div>
        </div>
      ))}

      {/* 角色分配弹窗 */}
      <Popup visible={roleVisible} onClose={() => setRoleVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '60vh', overflow: 'auto' }}>
          <h4 style={{ marginBottom: 16 }}>分配角色 - {selectedUser}</h4>
          {roleLoading ? <Loading text="加载角色..." /> : (
            <div style={{ marginBottom: 16 }}>
              {allRoles.map((role) => (
                <div
                  key={role.id}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 0', borderBottom: '1px solid #f5f5f5',
                  }}
                  onClick={() => toggleUserRole(role.name)}
                >
                  <Checkbox checked={userRoles.includes(role.name)} />
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 500 }}>{role.name}</div>
                    <div style={{ fontSize: 12, color: '#999' }}>{role.description || ''}</div>
                  </div>
                </div>
              ))}
              {allRoles.length === 0 && (
                <div style={{ textAlign: 'center', padding: 20, color: '#999' }}>暂无角色</div>
              )}
            </div>
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <Button theme="default" block onClick={() => setRoleVisible(false)}>取消</Button>
            <Button theme="primary" block onClick={handleSaveRoles} loading={roleSaving}>保存</Button>
          </div>
        </div>
      </Popup>
    </div>
  );
}
