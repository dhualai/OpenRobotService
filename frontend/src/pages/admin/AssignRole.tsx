// 分配角色 —— 为用户在指定项目中分配角色
// 基于接口文档 POST /api/admin/users/{username}/roles，三个下拉：用户 / 角色 / 项目
import { useState, useEffect, useCallback } from 'react';
import { Button, Toast, Loading, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface UserItem { id: string; username: string; name?: string; }
interface RoleItem { id: string; name: string; }
interface ProjectItem { id?: string; project_code: string; name: string; }

type PickerKey = 'user' | 'role' | 'project' | null;

export default function AssignRole() {
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const [users, setUsers] = useState<UserItem[]>([]);
  const [roles, setRoles] = useState<RoleItem[]>([]);
  const [projects, setProjects] = useState<ProjectItem[]>([]);
  const [loading, setLoading] = useState(true);

  const [selectedUser, setSelectedUser] = useState<UserItem | null>(null);
  const [selectedRole, setSelectedRole] = useState<RoleItem | null>(null);
  const [selectedProject, setSelectedProject] = useState<ProjectItem | null>(null);
  const [activePicker, setActivePicker] = useState<PickerKey>(null);
  const [submitting, setSubmitting] = useState(false);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [usersData, rolesData, projectsData] = await Promise.all([
        request<UserItem[]>('/users/?limit=1000'),
        request<RoleItem[]>('/roles/'),
        request<ProjectItem[]>('/projects/?limit=1000&include_analysis=false'),
      ]);
      setUsers(normalizeList<UserItem>(usersData));
      setRoles(normalizeList<RoleItem>(rolesData));
      setProjects(normalizeList<ProjectItem>(projectsData));
    } catch (err) {
      Toast({ message: `加载数据失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  const handleSubmit = async () => {
    if (!selectedUser) { Toast({ message: '请选择用户', theme: 'warning' }); return; }
    if (!selectedRole) { Toast({ message: '请选择角色', theme: 'warning' }); return; }
    if (!selectedProject) { Toast({ message: '请选择项目', theme: 'warning' }); return; }
    setSubmitting(true);
    try {
      await request(`/users/${encodeURIComponent(selectedUser.username)}/roles`, {
        method: 'POST',
        body: JSON.stringify({ project_id: selectedProject.project_code, role_ids: [selectedRole.id] }),
      });
      Toast({ message: '角色分配成功', theme: 'success' });
      setSelectedUser(null);
      setSelectedRole(null);
      setSelectedProject(null);
    } catch (err) {
      Toast({ message: `分配失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <Loading text="加载中..." />;

  return (
    <div className="admin-view">
      <div style={{ padding: 16 }}>
        <div style={{ background: '#fff', borderRadius: 12, padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <PickerField
            label="用户"
            value={selectedUser ? (selectedUser.name || selectedUser.username) : ''}
            placeholder="请选择用户"
            onClick={() => setActivePicker('user')}
          />
          <PickerField
            label="角色"
            value={selectedRole?.name || ''}
            placeholder="请选择角色"
            onClick={() => setActivePicker('role')}
          />
          <PickerField
            label="项目"
            value={selectedProject?.name || ''}
            placeholder="请选择项目"
            onClick={() => setActivePicker('project')}
          />
        </div>

        <Button theme="primary" block style={{ marginTop: 20 }} loading={submitting} onClick={handleSubmit}>
          保存
        </Button>
      </div>

      {/* 用户选择弹窗 */}
      <Popup visible={activePicker === 'user'} onClose={() => setActivePicker(null)} placement="bottom" showOverlay>
        <PickerList
          title="选择用户"
          items={users.map((u) => ({ key: u.id, label: u.name || u.username, sub: u.username }))}
          onSelect={(key) => {
            const u = users.find((x) => x.id === key);
            if (u) setSelectedUser(u);
            setActivePicker(null);
          }}
        />
      </Popup>

      {/* 角色选择弹窗 */}
      <Popup visible={activePicker === 'role'} onClose={() => setActivePicker(null)} placement="bottom" showOverlay>
        <PickerList
          title="选择角色"
          items={roles.map((r) => ({ key: r.id, label: r.name }))}
          onSelect={(key) => {
            const r = roles.find((x) => x.id === key);
            if (r) setSelectedRole(r);
            setActivePicker(null);
          }}
        />
      </Popup>

      {/* 项目选择弹窗 */}
      <Popup visible={activePicker === 'project'} onClose={() => setActivePicker(null)} placement="bottom" showOverlay>
        <PickerList
          title="选择项目"
          items={projects.map((p) => ({ key: p.project_code, label: p.name, sub: p.project_code }))}
          onSelect={(key) => {
            const p = projects.find((x) => x.project_code === key);
            if (p) setSelectedProject(p);
            setActivePicker(null);
          }}
        />
      </Popup>
    </div>
  );
}

function PickerField({ label, value, placeholder, onClick }: { label: string; value: string; placeholder: string; onClick: () => void }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <label style={{ fontSize: 12, color: '#999' }}>{label}</label>
      <div
        onClick={onClick}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          fontSize: 14, background: '#f8fafc', borderRadius: 8, padding: '12px 14px', cursor: 'pointer',
        }}
      >
        <span style={{ color: value ? '#333' : '#bbb' }}>{value || placeholder}</span>
        <span style={{ color: '#999' }}>›</span>
      </div>
    </div>
  );
}

function PickerList({ title, items, onSelect }: { title: string; items: { key: string; label: string; sub?: string }[]; onSelect: (key: string) => void }) {
  return (
    <div style={{ padding: 20, maxHeight: '60vh', overflow: 'auto' }}>
      <h4 style={{ marginBottom: 12 }}>{title}</h4>
      {items.map((item) => (
        <div
          key={item.key}
          onClick={() => onSelect(item.key)}
          style={{
            padding: '12px 4px', borderBottom: '1px solid #f5f5f5', cursor: 'pointer',
            display: 'flex', flexDirection: 'column', gap: 2,
          }}
        >
          <span style={{ fontSize: 14, fontWeight: 500 }}>{item.label}</span>
          {item.sub && <span style={{ fontSize: 12, color: '#999' }}>{item.sub}</span>}
        </div>
      ))}
      {items.length === 0 && (
        <div style={{ textAlign: 'center', padding: 30, color: '#999' }}>暂无数据</div>
      )}
    </div>
  );
}
