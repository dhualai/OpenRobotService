// 分配角色 —— 以项目为中心的批量授权
// 流程：选项目 → 列表展示每个用户在该项目下已有的角色（状态可见）→ 多选用户 → 批量授权 / 批量移除
// 接口：
//   GET  /users/?limit=1000            用户列表（含 roles: { project_id: [role_id] }）
//   GET  /roles/                       角色列表
//   GET  /projects/                    项目列表
//   POST /users/project/assign-roles   批量授权（用户 × 角色 笛卡尔积）
//   POST /users/{username}/roles/remove 批量移除
import { useState, useEffect, useCallback, useMemo } from 'react';
import { Button, Toast, Loading, Popup, Tag, Input } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface UserItem {
  id: string;
  username: string;
  name?: string;
  roles?: Record<string, string[]>; // project_id -> role_id[]
}
interface RoleItem { id: string; name: string; }
interface ProjectItem { id?: string; project_code: string; name: string; }

type FilterMode = 'all' | 'authorized' | 'unauthorized';
type RolePickerMode = 'assign' | 'remove' | null;

export default function AssignRole() {
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const [users, setUsers] = useState<UserItem[]>([]);
  const [roles, setRoles] = useState<RoleItem[]>([]);
  const [projects, setProjects] = useState<ProjectItem[]>([]);
  const [loading, setLoading] = useState(true);

  const [selectedProject, setSelectedProject] = useState<ProjectItem | null>(null);
  const [projectPickerVisible, setProjectPickerVisible] = useState(false);

  const [keyword, setKeyword] = useState('');
  const [filterMode, setFilterMode] = useState<FilterMode>('all');
  const [selectedUserIds, setSelectedUserIds] = useState<Set<string>>(new Set());

  // 批量角色选择弹窗（授权 / 移除共用）
  const [rolePickerMode, setRolePickerMode] = useState<RolePickerMode>(null);
  const [checkedRoleIds, setCheckedRoleIds] = useState<Set<string>>(new Set());

  // 单人角色编辑弹窗
  const [editingUser, setEditingUser] = useState<UserItem | null>(null);
  const [editingRoleIds, setEditingRoleIds] = useState<Set<string>>(new Set());

  const [submitting, setSubmitting] = useState(false);

  const roleNameMap = useMemo(() => {
    const m = new Map<string, string>();
    roles.forEach((r) => m.set(r.id, r.name));
    return m;
  }, [roles]);

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

  const reloadUsers = useCallback(async () => {
    try {
      const usersData = await request<UserItem[]>('/users/?limit=1000');
      setUsers(normalizeList<UserItem>(usersData));
    } catch (err) {
      Toast({ message: `刷新用户失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  }, []);

  // 某用户在当前项目下已有的角色 id 列表
  const userRolesInProject = useCallback(
    (u: UserItem): string[] => {
      if (!selectedProject || !u.roles) return [];
      return u.roles[selectedProject.project_code] || [];
    },
    [selectedProject],
  );

  const filteredUsers = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    return users.filter((u) => {
      if (kw) {
        const text = `${u.name || ''} ${u.username}`.toLowerCase();
        if (!text.includes(kw)) return false;
      }
      const hasRole = userRolesInProject(u).length > 0;
      if (filterMode === 'authorized') return hasRole;
      if (filterMode === 'unauthorized') return !hasRole;
      return true;
    });
  }, [users, keyword, filterMode, userRolesInProject]);

  const allFilteredSelected =
    filteredUsers.length > 0 && filteredUsers.every((u) => selectedUserIds.has(u.id));

  const toggleUser = (id: string) => {
    setSelectedUserIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    setSelectedUserIds((prev) => {
      const next = new Set(prev);
      if (allFilteredSelected) filteredUsers.forEach((u) => next.delete(u.id));
      else filteredUsers.forEach((u) => next.add(u.id));
      return next;
    });
  };

  const selectedUsers = useMemo(
    () => users.filter((u) => selectedUserIds.has(u.id)),
    [users, selectedUserIds],
  );

  const openRolePicker = (mode: Exclude<RolePickerMode, null>) => {
    if (selectedUserIds.size === 0) {
      Toast({ message: '请先勾选用户', theme: 'warning' });
      return;
    }
    // 移除模式下默认勾选所有被选用户在该项目下已有的角色
    if (mode === 'remove') {
      const union = new Set<string>();
      selectedUsers.forEach((u) => userRolesInProject(u).forEach((rid) => union.add(rid)));
      setCheckedRoleIds(union);
    } else {
      setCheckedRoleIds(new Set());
    }
    setRolePickerMode(mode);
  };

  // 批量授权：一次请求，用户 × 角色 笛卡尔积
  const handleBatchAssign = async () => {
    if (!selectedProject) return;
    if (checkedRoleIds.size === 0) {
      Toast({ message: '请勾选要授权的角色', theme: 'warning' });
      return;
    }
    setSubmitting(true);
    try {
      const organization_ids: { user_name: string; role_id: string }[] = [];
      selectedUsers.forEach((u) => {
        checkedRoleIds.forEach((rid) => {
          organization_ids.push({ user_name: u.username, role_id: rid });
        });
      });
      await request('/users/project/assign-roles', {
        method: 'POST',
        body: JSON.stringify({ project_id: selectedProject.project_code, organization_ids }),
      });
      Toast({ message: `已为 ${selectedUsers.length} 人授权 ${checkedRoleIds.size} 个角色`, theme: 'success' });
      setRolePickerMode(null);
      setSelectedUserIds(new Set());
      await reloadUsers();
    } catch (err) {
      Toast({ message: `授权失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmitting(false);
    }
  };

  // 批量移除：逐用户调用移除接口，逐个统计结果
  const handleBatchRemove = async () => {
    if (!selectedProject) return;
    if (checkedRoleIds.size === 0) {
      Toast({ message: '请勾选要移除的角色', theme: 'warning' });
      return;
    }
    setSubmitting(true);
    try {
      const results = await Promise.allSettled(
        selectedUsers.map((u) =>
          request(`/users/${encodeURIComponent(u.username)}/roles/remove`, {
            method: 'POST',
            body: JSON.stringify({
              project_id: selectedProject.project_code,
              role_ids: Array.from(checkedRoleIds),
            }),
          }),
        ),
      );
      const okCount = results.filter((r) => r.status === 'fulfilled').length;
      const failCount = results.length - okCount;
      Toast({
        message: failCount > 0 ? `移除完成：成功 ${okCount} 人，失败 ${failCount} 人` : `已为 ${okCount} 人移除角色`,
        theme: failCount > 0 ? 'warning' : 'success',
      });
      setRolePickerMode(null);
      setSelectedUserIds(new Set());
      await reloadUsers();
    } finally {
      setSubmitting(false);
    }
  };

  // 打开单人编辑弹窗
  const openUserEditor = (u: UserItem) => {
    setEditingUser(u);
    setEditingRoleIds(new Set(userRolesInProject(u)));
  };

  // 单人保存：对比差集，新增走授权、取消走移除
  const handleSaveUserEditor = async () => {
    if (!editingUser || !selectedProject) return;
    const before = new Set(userRolesInProject(editingUser));
    const added = Array.from(editingRoleIds).filter((id) => !before.has(id));
    const removed = Array.from(before).filter((id) => !editingRoleIds.has(id));
    if (added.length === 0 && removed.length === 0) {
      setEditingUser(null);
      return;
    }
    setSubmitting(true);
    try {
      const tasks: Promise<unknown>[] = [];
      if (added.length > 0) {
        tasks.push(
          request(`/users/${encodeURIComponent(editingUser.username)}/roles`, {
            method: 'POST',
            body: JSON.stringify({ project_id: selectedProject.project_code, role_ids: added }),
          }),
        );
      }
      if (removed.length > 0) {
        tasks.push(
          request(`/users/${encodeURIComponent(editingUser.username)}/roles/remove`, {
            method: 'POST',
            body: JSON.stringify({ project_id: selectedProject.project_code, role_ids: removed }),
          }),
        );
      }
      await Promise.all(tasks);
      Toast({ message: '保存成功', theme: 'success' });
      setEditingUser(null);
      await reloadUsers();
    } catch (err) {
      Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <Loading text="加载中..." />;

  const selectedCount = selectedUserIds.size;

  return (
    <div className="admin-view" style={{ paddingBottom: selectedCount > 0 ? 72 : 0 }}>
      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* 项目选择 */}
        <div style={{ background: '#fff', borderRadius: 12, padding: 16 }}>
          <PickerField
            label="项目"
            value={selectedProject?.name || ''}
            placeholder="请先选择项目"
            onClick={() => setProjectPickerVisible(true)}
          />
        </div>

        {selectedProject && (
          <>
            {/* 搜索 + 状态筛选 */}
            <div style={{ background: '#fff', borderRadius: 12, padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <Input
                value={keyword}
                onChange={(v) => setKeyword(String(v))}
                placeholder="搜索姓名 / 用户名"
                clearable
              />
              <div style={{ display: 'flex', gap: 8 }}>
                {([
                  ['all', '全部'],
                  ['authorized', '已授权'],
                  ['unauthorized', '未授权'],
                ] as [FilterMode, string][]).map(([mode, label]) => (
                  <Tag
                    key={mode}
                    theme={filterMode === mode ? 'primary' : 'default'}
                    variant={filterMode === mode ? 'dark' : 'light'}
                    onClick={() => setFilterMode(mode)}
                    style={{ cursor: 'pointer', padding: '4px 12px' }}
                  >
                    {label}
                  </Tag>
                ))}
              </div>
            </div>

            {/* 全选栏 */}
            <div
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '4px 4px 0',
              }}
            >
              <div
                onClick={toggleSelectAll}
                style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 13, color: '#666' }}
              >
                <CheckDot checked={allFilteredSelected} />
                全选（{filteredUsers.length} 人）
              </div>
              <span style={{ fontSize: 12, color: '#999' }}>已选 {selectedCount} 人</span>
            </div>

            {/* 用户列表 */}
            <div style={{ background: '#fff', borderRadius: 12, overflow: 'hidden' }}>
              {filteredUsers.map((u) => {
                const ownedRoleIds = userRolesInProject(u);
                const checked = selectedUserIds.has(u.id);
                return (
                  <div
                    key={u.id}
                    style={{
                      display: 'flex', alignItems: 'flex-start', gap: 10,
                      padding: '12px 14px', borderBottom: '1px solid #f5f5f5',
                      background: checked ? '#f0f7ff' : '#fff',
                    }}
                  >
                    <div style={{ paddingTop: 2 }} onClick={() => toggleUser(u.id)}>
                      <CheckDot checked={checked} />
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }} onClick={() => toggleUser(u.id)}>
                      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                        <span style={{ fontSize: 14, fontWeight: 500 }}>{u.name || u.username}</span>
                        <span style={{ fontSize: 12, color: '#999' }}>{u.username}</span>
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
                        {ownedRoleIds.length > 0 ? (
                          ownedRoleIds.map((rid) => (
                            <Tag key={rid} theme="success" variant="light" size="small">
                              {roleNameMap.get(rid) || rid}
                            </Tag>
                          ))
                        ) : (
                          <span style={{ fontSize: 12, color: '#ccc' }}>未授权</span>
                        )}
                      </div>
                    </div>
                    <Button
                      size="small"
                      variant="outline"
                      theme="primary"
                      onClick={() => openUserEditor(u)}
                    >
                      编辑
                    </Button>
                  </div>
                );
              })}
              {filteredUsers.length === 0 && (
                <div style={{ textAlign: 'center', padding: 30, color: '#999' }}>暂无匹配用户</div>
              )}
            </div>
          </>
        )}
      </div>

      {/* 底部批量操作栏 */}
      {selectedProject && selectedCount > 0 && (
        <div
          style={{
            position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 100,
            background: '#fff', boxShadow: '0 -2px 12px rgba(0,0,0,0.08)',
            padding: '10px 16px calc(10px + env(safe-area-inset-bottom))',
            display: 'flex', alignItems: 'center', gap: 10,
          }}
        >
          <span style={{ flex: 1, fontSize: 13, color: '#666' }}>已选 {selectedCount} 人</span>
          <Button size="small" theme="primary" onClick={() => openRolePicker('assign')}>
            批量授权
          </Button>
          <Button size="small" theme="danger" variant="outline" onClick={() => openRolePicker('remove')}>
            批量移除
          </Button>
        </div>
      )}

      {/* 项目选择弹窗 */}
      <Popup visible={projectPickerVisible} onClose={() => setProjectPickerVisible(false)} placement="bottom" showOverlay>
        <PickerList
          title="选择项目"
          items={projects.map((p) => ({ key: p.project_code, label: p.name, sub: p.project_code }))}
          onSelect={(key) => {
            const p = projects.find((x) => x.project_code === key);
            if (p) {
              setSelectedProject(p);
              setSelectedUserIds(new Set());
            }
            setProjectPickerVisible(false);
          }}
        />
      </Popup>

      {/* 批量角色选择弹窗（授权 / 移除） */}
      <Popup visible={rolePickerMode !== null} onClose={() => setRolePickerMode(null)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
          <h4 style={{ marginBottom: 4 }}>
            {rolePickerMode === 'assign' ? '批量授权角色' : '批量移除角色'}
          </h4>
          <p style={{ fontSize: 12, color: '#999', marginBottom: 12 }}>
            将对已选 {selectedCount} 人在「{selectedProject?.name}」中{rolePickerMode === 'assign' ? '添加' : '移除'}勾选的角色
          </p>
          <div style={{ overflow: 'auto', flex: 1 }}>
            {roles.map((r) => (
              <div
                key={r.id}
                onClick={() =>
                  setCheckedRoleIds((prev) => {
                    const next = new Set(prev);
                    if (next.has(r.id)) next.delete(r.id);
                    else next.add(r.id);
                    return next;
                  })
                }
                style={{
                  padding: '10px 4px', borderBottom: '1px solid #f5f5f5', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 8, fontSize: 14,
                }}
              >
                <CheckDot checked={checkedRoleIds.has(r.id)} />
                {r.name}
              </div>
            ))}
          </div>
          <Button
            theme={rolePickerMode === 'assign' ? 'primary' : 'danger'}
            block
            style={{ marginTop: 16 }}
            loading={submitting}
            onClick={rolePickerMode === 'assign' ? handleBatchAssign : handleBatchRemove}
          >
            确认{rolePickerMode === 'assign' ? '授权' : '移除'}（{checkedRoleIds.size} 个角色）
          </Button>
        </div>
      </Popup>

      {/* 单人角色编辑弹窗 */}
      <Popup visible={editingUser !== null} onClose={() => setEditingUser(null)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
          <h4 style={{ marginBottom: 4 }}>
            编辑角色：{editingUser?.name || editingUser?.username}
          </h4>
          <p style={{ fontSize: 12, color: '#999', marginBottom: 12 }}>
            项目「{selectedProject?.name}」，勾选即授权，取消即移除
          </p>
          <div style={{ overflow: 'auto', flex: 1 }}>
            {roles.map((r) => (
              <div
                key={r.id}
                onClick={() =>
                  setEditingRoleIds((prev) => {
                    const next = new Set(prev);
                    if (next.has(r.id)) next.delete(r.id);
                    else next.add(r.id);
                    return next;
                  })
                }
                style={{
                  padding: '10px 4px', borderBottom: '1px solid #f5f5f5', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 8, fontSize: 14,
                }}
              >
                <CheckDot checked={editingRoleIds.has(r.id)} />
                {r.name}
              </div>
            ))}
          </div>
          <Button theme="primary" block style={{ marginTop: 16 }} loading={submitting} onClick={handleSaveUserEditor}>
            保存
          </Button>
        </div>
      </Popup>
    </div>
  );
}

// 自绘勾选圆点：避免 tdesign Checkbox 默认白色背景块在卡片上显得突兀
function CheckDot({ checked }: { checked: boolean }) {
  return (
    <span
      style={{
        width: 18, height: 18, borderRadius: '50%', flexShrink: 0,
        border: checked ? 'none' : '1.5px solid #ccc',
        background: checked ? '#0052d9' : 'transparent',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        color: '#fff', fontSize: 12, lineHeight: 1, boxSizing: 'border-box',
      }}
    >
      {checked ? '✓' : ''}
    </span>
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
