// 分配角色 —— 双模式批量授权
// 模式一（按项目授权）：选项目 → 多选用户 → 批量授权 / 批量移除角色（含单人编辑）
// 模式二（按用户授权）：多选用户 + 多选角色 → 多选项目 → 一次性把「用户×角色」笛卡尔积授权到所有勾选项目
// 接口：
//   GET  /users/?limit=1000            用户列表（含 roles: { project_id: [role_id] }）
//   GET  /roles/                       角色列表
//   GET  /projects/                    项目列表
//   POST /users/project/assign-roles   批量授权（用户 × 角色 笛卡尔积）
//   POST /users/{username}/roles/remove 批量移除
// 样式参考 macaron assign-roles 页：入口方式卡 + 蓝条小节 + 选择芯片/复选行 + 双按钮操作区。
import { useState, useEffect, useCallback, useMemo } from 'react';
import type { ReactNode } from 'react';
import { Toast, Loading, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import {
  MacFolderClosed, MacUserRound, MacChevronLeft, MacChevronRight, MacCheck, MacSearch,
} from '@/shared/components/macaronIcons';

interface UserItem {
  id: string;
  username: string;
  name?: string;
  roles?: Record<string, string[]>; // project_id -> role_id[]
}
interface RoleItem { id: string; name: string; role_type?: string; }
interface ProjectItem { id?: string; project_code: string; name: string; }

type ApiRequest = ReturnType<typeof createRequest>;
type View = 'entry' | 'project' | 'user';

type FilterMode = 'all' | 'authorized' | 'unauthorized';

/** 自绘勾选圆点（18px 圆 + 白色对勾，对照原型 Row 的选中样式） */
function ChoiceDot({ checked }: { checked: boolean }) {
  return (
    <span
      className="mac-choice__dot"
      style={{
        borderColor: checked ? 'var(--mac-blue-2)' : undefined,
        background: checked ? 'var(--mac-blue-2)' : '#fff',
      }}
    >
      {checked && <MacCheck size={12} />}
    </span>
  );
}

export default function AssignRole() {
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const [view, setView] = useState<View>('entry');
  const [users, setUsers] = useState<UserItem[]>([]);
  const [roles, setRoles] = useState<RoleItem[]>([]);
  const [projects, setProjects] = useState<ProjectItem[]>([]);
  const [loading, setLoading] = useState(true);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [usersData, rolesData, projectsData] = await Promise.all([
        request<UserItem[]>('/users/?limit=1000'),
        request<RoleItem[]>('/roles/'),
        request<ProjectItem[]>('/projects/?limit=1000&include_analysis=false'),
      ]);
      setUsers(normalizeList<UserItem>(usersData));
      setRoles(normalizeList<RoleItem>(rolesData).filter((r) => r.role_type === 'project'));
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
      // client.ts 会按 URL 缓存 GET 响应，授权后必须跳过缓存才能拿到最新角色
      const usersData = await request<UserItem[]>('/users/?limit=1000', { skipCache: true });
      setUsers(normalizeList<UserItem>(usersData));
    } catch (err) {
      Toast({ message: `刷新用户失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  }, []);

  // 入口选择屏：无需等待数据加载即可展示
  if (view === 'entry') {
    return (
      <div className="admin-view" style={{ padding: '24px 16px' }}>
        <header style={{ textAlign: 'center', margin: '8px 0 16px' }}>
          <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: 'var(--mac-fg)' }}>请选择授权方式</h1>
          <p style={{ margin: '6px 0 0', fontSize: 11.5, color: 'var(--mac-muted-fg)' }}>
            两种方式均可完成批量授权，按需选择
          </p>
        </header>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <ModeEntryCard
            icon={<MacFolderClosed size={18} />}
            title="项目优先"
            desc="先选一个项目，再批量给人员授权 / 移除角色"
            onClick={() => setView('project')}
          />
          <ModeEntryCard
            icon={<MacUserRound size={18} />}
            title="用户优先"
            desc="先选若干用户和角色，再批量授权到多个项目"
            onClick={() => setView('user')}
          />
        </div>
      </div>
    );
  }

  if (loading) return <Loading text="加载中..." />;

  return (
    <div className="admin-view">
      {/* 返回入口 */}
      <div style={{ padding: '8px 16px 0' }}>
        <button type="button" className="mac-back-link" onClick={() => setView('entry')}>
          <MacChevronLeft size={14} />
          返回选择授权方式
        </button>
      </div>

      {view === 'project' ? (
        <ProjectFirstAssign
          users={users}
          roles={roles}
          projects={projects}
          request={request}
          reloadUsers={reloadUsers}
        />
      ) : (
        <UserFirstAssign
          users={users}
          roles={roles}
          projects={projects}
          request={request}
          reloadUsers={reloadUsers}
        />
      )}
    </div>
  );
}

// ============================================================
// 模式一：按项目授权（原有流程）
// 流程：选项目 → 列表展示每个用户在该项目下已有的角色 → 多选用户 → 批量授权 / 批量移除
// ============================================================
function ProjectFirstAssign({
  users, roles, projects, request, reloadUsers,
}: {
  users: UserItem[];
  roles: RoleItem[];
  projects: ProjectItem[];
  request: ApiRequest;
  reloadUsers: () => Promise<void>;
}) {
  const [selectedProject, setSelectedProject] = useState<ProjectItem | null>(null);
  const [projectPickerVisible, setProjectPickerVisible] = useState(false);

  const [keyword, setKeyword] = useState('');
  const [filterMode, setFilterMode] = useState<FilterMode>('all');
  const [selectedUserIds, setSelectedUserIds] = useState<Set<string>>(new Set());

  // 当前选中的单个角色（对照原型：先选人员，再选一个角色，下方「移除角色 / 批量授权」两个按钮）
  const [selectedRoleId, setSelectedRoleId] = useState<string | null>(null);

  // 单人角色编辑弹窗
  const [editingUser, setEditingUser] = useState<UserItem | null>(null);
  const [editingRoleIds, setEditingRoleIds] = useState<Set<string>>(new Set());

  const [submitting, setSubmitting] = useState(false);

  const roleNameMap = useMemo(() => {
    const m = new Map<string, string>();
    roles.forEach((r) => m.set(r.id, r.name));
    return m;
  }, [roles]);

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

  // 批量授权：一次请求，用户 × 选中角色
  const handleBatchGrant = async () => {
    if (!selectedProject || !selectedRoleId) return;
    setSubmitting(true);
    try {
      const organization_ids: { user_name: string; role_id: string }[] = [];
      selectedUsers.forEach((u) => {
        organization_ids.push({ user_name: u.username, role_id: selectedRoleId });
      });
      await request('/users/project/assign-roles', {
        method: 'POST',
        body: JSON.stringify({ project_id: selectedProject.project_code, organization_ids }),
      });
      Toast({ message: `已为 ${selectedUsers.length} 人授权「${roleNameMap.get(selectedRoleId) || ''}」`, theme: 'success' });
      setSelectedUserIds(new Set());
      setSelectedRoleId(null);
      await reloadUsers();
    } catch (err) {
      Toast({ message: `授权失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmitting(false);
    }
  };

  // 批量移除：逐用户移除选中角色，统计结果
  const handleBatchRevoke = async () => {
    if (!selectedProject || !selectedRoleId) return;
    setSubmitting(true);
    try {
      const results = await Promise.allSettled(
        selectedUsers.map((u) =>
          request(`/users/${encodeURIComponent(u.username)}/roles/remove`, {
            method: 'POST',
            body: JSON.stringify({
              project_id: selectedProject.project_code,
              role_ids: [selectedRoleId],
            }),
          }),
        ),
      );
      const okCount = results.filter((r) => r.status === 'fulfilled').length;
      const failCount = results.length - okCount;
      Toast({
        message: failCount > 0
          ? `移除完成：成功 ${okCount} 人，失败 ${failCount} 人`
          : `已为 ${okCount} 人移除「${roleNameMap.get(selectedRoleId) || ''}」`,
        theme: failCount > 0 ? 'warning' : 'success',
      });
      setSelectedUserIds(new Set());
      setSelectedRoleId(null);
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

  const selectedCount = selectedUserIds.size;

  return (
    <div>
      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* 项目选择 */}
        <section className="mac-card mac-card--pad">
          <p style={{ margin: 0, fontSize: 11.5, color: 'var(--mac-muted-fg)' }}>项目</p>
          <button
            type="button"
            className="mac-selector mac-selector--soft"
            style={{ marginTop: 8 }}
            onClick={() => setProjectPickerVisible(true)}
          >
            <span className="mac-selector__body">
              {selectedProject ? (
                <span className="mac-selector__name">{selectedProject.name}</span>
              ) : (
                <span className="mac-selector__placeholder">请先选择项目</span>
              )}
            </span>
            <span className="mac-selector__chevron"><MacChevronRight size={16} /></span>
          </button>
        </section>

        {selectedProject && (
          <>
            {/* 搜索 + 状态筛选 */}
            <section className="mac-card mac-card--pad" style={{ padding: 12 }}>
              <div className="mac-search">
                <MacSearch size={16} />
                <input
                  className="mac-search__input"
                  value={keyword}
                  onChange={(e) => setKeyword(e.target.value)}
                  placeholder="搜索姓名 / 用户名"
                />
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                {([
                  ['all', '全部'],
                  ['authorized', '已授权'],
                  ['unauthorized', '未授权'],
                ] as [FilterMode, string][]).map(([mode, label]) => (
                  <button
                    key={mode}
                    type="button"
                    className={`mac-filter-chip ${filterMode === mode ? 'is-active' : ''}`}
                    onClick={() => setFilterMode(mode)}
                  >
                    {filterMode === mode && <MacCheck size={12} />}
                    {label}
                  </button>
                ))}
              </div>
            </section>

            {/* 全选栏 */}
            <div
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 4px 0' }}
            >
              <button
                type="button"
                style={{
                  display: 'flex', alignItems: 'center', gap: 8, border: 'none', background: 'none',
                  cursor: 'pointer', fontFamily: 'inherit', fontSize: 13, color: 'var(--mac-fg)', padding: 0,
                }}
                onClick={toggleSelectAll}
              >
                <ChoiceDot checked={allFilteredSelected} />
                全选（{filteredUsers.length} 人）
              </button>
              <span style={{ fontSize: 11, color: 'var(--mac-muted-fg)' }}>已选 {selectedCount} 人</span>
            </div>

            {/* 用户列表 */}
            <section className="mac-card" style={{ overflow: 'hidden' }}>
              {filteredUsers.map((u) => {
                const ownedRoleIds = userRolesInProject(u);
                const checked = selectedUserIds.has(u.id);
                return (
                  <div key={u.id} className={`mac-select-row ${checked ? 'is-checked' : ''}`}>
                    <span style={{ paddingTop: 2, cursor: 'pointer' }} onClick={() => toggleUser(u.id)}>
                      <ChoiceDot checked={checked} />
                    </span>
                    <div className="mac-select-row__body" onClick={() => toggleUser(u.id)}>
                      <span className="mac-select-row__name">{u.name || u.username}</span>
                      <span className="mac-select-row__meta">{u.username}</span>
                      <div className="mac-select-row__chips">
                        {ownedRoleIds.length > 0 ? (
                          ownedRoleIds.map((rid) => (
                            <span key={rid} className="mac-chip mac-chip--blue">
                              {roleNameMap.get(rid) || rid}
                            </span>
                          ))
                        ) : (
                          <span style={{ fontSize: 11, color: 'var(--mac-muted-fg)' }}>未授权</span>
                        )}
                      </div>
                    </div>
                    <button
                      type="button"
                      className="mac-btn mac-btn--ghost"
                      onClick={() => openUserEditor(u)}
                    >
                      编辑
                    </button>
                  </div>
                );
              })}
              {filteredUsers.length === 0 && (
                <div className="mac-empty">暂无匹配用户</div>
              )}
            </section>

            {/* 选择角色（对照原型：单选角色芯片） */}
            <div className="mac-section-title" style={{ marginTop: 8 }}>
              <span className="mac-section-title__bar" />
              <h2 className="mac-section-title__text">选择角色</h2>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, padding: '0 4px' }}>
              {roles.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  className={`mac-filter-chip ${selectedRoleId === r.id ? 'is-active' : ''}`}
                  onClick={() => setSelectedRoleId(selectedRoleId === r.id ? null : r.id)}
                >
                  {selectedRoleId === r.id && <MacCheck size={12} />}
                  {r.name}
                </button>
              ))}
            </div>

            {/* 操作按钮（对照原型 Actions：移除角色 / 批量授权） */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 16 }}>
              <button
                type="button"
                className="mac-btn mac-btn--lg mac-btn--outline"
                disabled={!selectedProject || selectedCount === 0 || !selectedRoleId || submitting}
                onClick={handleBatchRevoke}
              >
                移除角色
              </button>
              <button
                type="button"
                className="mac-btn mac-btn--lg mac-btn--primary"
                disabled={!selectedProject || selectedCount === 0 || !selectedRoleId || submitting}
                onClick={handleBatchGrant}
              >
                {submitting ? '提交中...' : '批量授权'}
              </button>
            </div>
          </>
        )}
      </div>

      {/* 项目选择弹窗 */}
      <Popup visible={projectPickerVisible} onClose={() => setProjectPickerVisible(false)} placement="bottom" showOverlay>
        <PickerList
          key={`project-picker-${projectPickerVisible}`}
          title="选择项目"
          searchable
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

      {/* 单人角色编辑弹窗 */}
      <Popup visible={editingUser !== null} onClose={() => setEditingUser(null)} placement="bottom" showOverlay>
        <div className="mac-sheet" style={{ maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
          <h4 className="mac-sheet__title" style={{ marginBottom: 4 }}>
            编辑角色：{editingUser?.name || editingUser?.username}
          </h4>
          <p className="mac-note" style={{ textAlign: 'left', marginBottom: 12 }}>
            项目「{selectedProject?.name}」，勾选即授权，取消即移除
          </p>
          <div style={{ overflow: 'auto', flex: 1 }}>
            {roles.map((r) => (
              <button
                key={r.id}
                type="button"
                className={`mac-choice ${editingRoleIds.has(r.id) ? 'is-active' : ''}`}
                onClick={() =>
                  setEditingRoleIds((prev) => {
                    const next = new Set(prev);
                    if (next.has(r.id)) next.delete(r.id);
                    else next.add(r.id);
                    return next;
                  })
                }
              >
                <span className="mac-choice__dot">
                  {editingRoleIds.has(r.id) && <MacCheck size={12} />}
                </span>
                <span className="mac-choice__label">{r.name}</span>
              </button>
            ))}
          </div>
          <button
            type="button"
            className="mac-btn mac-btn--primary mac-btn--block"
            style={{ marginTop: 16 }}
            disabled={submitting}
            onClick={handleSaveUserEditor}
          >
            {submitting ? '保存中...' : '保存'}
          </button>
        </div>
      </Popup>
    </div>
  );
}

// ============================================================
// 模式二：按用户授权
// 流程：多选用户 + 多选角色 + 多选项目 → 一次性把「用户×角色」笛卡尔积授权到所有勾选项目
// ============================================================
function UserFirstAssign({
  users, roles, projects, request, reloadUsers,
}: {
  users: UserItem[];
  roles: RoleItem[];
  projects: ProjectItem[];
  request: ApiRequest;
  reloadUsers: () => Promise<void>;
}) {
  const [keyword, setKeyword] = useState('');
  const [roleKeyword, setRoleKeyword] = useState('');
  const [projectKeyword, setProjectKeyword] = useState('');
  const [selectedUserIds, setSelectedUserIds] = useState<Set<string>>(new Set());
  const [selectedRoleIds, setSelectedRoleIds] = useState<Set<string>>(new Set());
  const [selectedProjectCodes, setSelectedProjectCodes] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);

  const filteredUsers = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    if (!kw) return users;
    return users.filter((u) => {
      const text = `${u.name || ''} ${u.username}`.toLowerCase();
      return text.includes(kw);
    });
  }, [users, keyword]);

  const filteredRoles = useMemo(() => {
    const kw = roleKeyword.trim().toLowerCase();
    if (!kw) return roles;
    return roles.filter((r) => r.name.toLowerCase().includes(kw));
  }, [roles, roleKeyword]);

  const filteredProjects = useMemo(() => {
    const kw = projectKeyword.trim().toLowerCase();
    if (!kw) return projects;
    return projects.filter((p) => {
      const text = `${p.name} ${p.project_code}`.toLowerCase();
      return text.includes(kw);
    });
  }, [projects, projectKeyword]);

  const toggleUser = (id: string) => {
    setSelectedUserIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleRole = (id: string) => {
    setSelectedRoleIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleProject = (code: string) => {
    setSelectedProjectCodes((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  };

  const allUsersSelected = filteredUsers.length > 0 && filteredUsers.every((u) => selectedUserIds.has(u.id));
  const toggleSelectAllUsers = () => {
    setSelectedUserIds((prev) => {
      const next = new Set(prev);
      if (allUsersSelected) filteredUsers.forEach((u) => next.delete(u.id));
      else filteredUsers.forEach((u) => next.add(u.id));
      return next;
    });
  };

  const allRolesSelected = filteredRoles.length > 0 && filteredRoles.every((r) => selectedRoleIds.has(r.id));
  const toggleSelectAllRoles = () => {
    setSelectedRoleIds((prev) => {
      const next = new Set(prev);
      if (allRolesSelected) filteredRoles.forEach((r) => next.delete(r.id));
      else filteredRoles.forEach((r) => next.add(r.id));
      return next;
    });
  };

  const allProjectsSelected = filteredProjects.length > 0 && filteredProjects.every((p) => selectedProjectCodes.has(p.project_code));
  const toggleSelectAllProjects = () => {
    setSelectedProjectCodes((prev) => {
      const next = new Set(prev);
      if (allProjectsSelected) filteredProjects.forEach((p) => next.delete(p.project_code));
      else filteredProjects.forEach((p) => next.add(p.project_code));
      return next;
    });
  };

  const canSubmit =
    selectedUserIds.size > 0 && selectedRoleIds.size > 0 && selectedProjectCodes.size > 0 && !submitting;

  // 逐项目调用，每个项目都把「用户×角色」笛卡尔积授权
  const handleAssign = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const selectedUsers = users.filter((u) => selectedUserIds.has(u.id));
      const organization_ids: { user_name: string; role_id: string }[] = [];
      selectedUsers.forEach((u) => {
        selectedRoleIds.forEach((rid) => {
          organization_ids.push({ user_name: u.username, role_id: rid });
        });
      });
      const projectCodes = Array.from(selectedProjectCodes);
      const results = await Promise.allSettled(
        projectCodes.map((code) =>
          request('/users/project/assign-roles', {
            method: 'POST',
            body: JSON.stringify({ project_id: code, organization_ids }),
          }),
        ),
      );
      const okCount = results.filter((r) => r.status === 'fulfilled').length;
      const failCount = results.length - okCount;
      Toast({
        message: failCount > 0
          ? `授权完成：成功 ${okCount} 个项目，失败 ${failCount} 个`
          : `已为 ${selectedUsers.length} 人 × ${selectedRoleIds.size} 角色，授权到 ${okCount} 个项目`,
        theme: failCount > 0 ? 'warning' : 'success',
      });
      if (failCount === 0) {
        setSelectedUserIds(new Set());
        setSelectedRoleIds(new Set());
        setSelectedProjectCodes(new Set());
      }
      await reloadUsers();
    } catch (err) {
      Toast({ message: `授权失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmitting(false);
    }
  };

  // 批量移除：逐用户 × 逐项目移除选中角色，统计结果
  const handleRemove = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const selectedUsers = users.filter((u) => selectedUserIds.has(u.id));
      const roleIds = Array.from(selectedRoleIds);
      const projectCodes = Array.from(selectedProjectCodes);
      const results = await Promise.allSettled(
        selectedUsers.flatMap((u) =>
          projectCodes.map((code) =>
            request(`/users/${encodeURIComponent(u.username)}/roles/remove`, {
              method: 'POST',
              body: JSON.stringify({ project_id: code, role_ids: roleIds }),
            }),
          ),
        ),
      );
      const okCount = results.filter((r) => r.status === 'fulfilled').length;
      const failCount = results.length - okCount;
      Toast({
        message: failCount > 0
          ? `移除完成：成功 ${okCount} 项，失败 ${failCount} 项`
          : `已在 ${projectCodes.length} 个项目为 ${selectedUsers.length} 人移除 ${roleIds.length} 个角色`,
        theme: failCount > 0 ? 'warning' : 'success',
      });
      if (failCount === 0) {
        setSelectedUserIds(new Set());
        setSelectedRoleIds(new Set());
        setSelectedProjectCodes(new Set());
      }
      await reloadUsers();
    } catch (err) {
      Toast({ message: `移除失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ paddingBottom: 16 }}>
      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* 选择用户 */}
        <PickCard title="选择用户" count={selectedUserIds.size}>
          <div className="mac-search" style={{ margin: '0 16px 8px' }}>
            <MacSearch size={16} />
            <input
              className="mac-search__input"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="搜索姓名 / 用户名"
            />
          </div>
          <button
            type="button"
            style={{
              display: 'flex', alignItems: 'center', gap: 8, border: 'none', background: 'none',
              cursor: 'pointer', fontFamily: 'inherit', fontSize: 13, color: 'var(--mac-muted-fg)',
              padding: '8px 16px', width: '100%', textAlign: 'left',
            }}
            onClick={toggleSelectAllUsers}
          >
            <ChoiceDot checked={allUsersSelected} />
            全选（{filteredUsers.length} 人）
          </button>
          <div style={{ maxHeight: 260, overflow: 'auto' }}>
            {filteredUsers.map((u) => {
              const checked = selectedUserIds.has(u.id);
              return (
                <button
                  key={u.id}
                  type="button"
                  className={`mac-choice ${checked ? 'is-active' : ''}`}
                  style={{ padding: '8px 16px', borderBottom: '1px solid rgba(232,234,234,0.6)' }}
                  onClick={() => toggleUser(u.id)}
                >
                  <span className="mac-choice__dot">
                    {checked && <MacCheck size={12} />}
                  </span>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span className="mac-choice__label">{u.name || u.username}</span>
                    <span style={{ fontSize: 11, color: 'var(--mac-muted-fg)', marginLeft: 8 }}>{u.username}</span>
                  </span>
                </button>
              );
            })}
            {filteredUsers.length === 0 && (
              <div className="mac-empty">暂无匹配用户</div>
            )}
          </div>
        </PickCard>

        {/* 选择角色 */}
        <PickCard title="选择角色" count={selectedRoleIds.size}>
          <div className="mac-search" style={{ margin: '0 16px 8px' }}>
            <MacSearch size={16} />
            <input
              className="mac-search__input"
              value={roleKeyword}
              onChange={(e) => setRoleKeyword(e.target.value)}
              placeholder="搜索角色名称"
            />
          </div>
          <button
            type="button"
            style={{
              display: 'flex', alignItems: 'center', gap: 8, border: 'none', background: 'none',
              cursor: 'pointer', fontFamily: 'inherit', fontSize: 13, color: 'var(--mac-muted-fg)',
              padding: '8px 16px', width: '100%', textAlign: 'left',
            }}
            onClick={toggleSelectAllRoles}
          >
            <ChoiceDot checked={allRolesSelected} />
            全选（{filteredRoles.length} 个角色）
          </button>
          <div style={{ maxHeight: 200, overflow: 'auto' }}>
            {filteredRoles.map((r) => {
              const checked = selectedRoleIds.has(r.id);
              return (
                <button
                  key={r.id}
                  type="button"
                  className={`mac-choice ${checked ? 'is-active' : ''}`}
                  style={{ padding: '8px 16px', borderBottom: '1px solid rgba(232,234,234,0.6)' }}
                  onClick={() => toggleRole(r.id)}
                >
                  <span className="mac-choice__dot">
                    {checked && <MacCheck size={12} />}
                  </span>
                  <span className="mac-choice__label">{r.name}</span>
                </button>
              );
            })}
            {filteredRoles.length === 0 && (
              <div className="mac-empty">暂无匹配角色</div>
            )}
          </div>
        </PickCard>

        {/* 选择项目 */}
        <PickCard title="选择项目" count={selectedProjectCodes.size}>
          <div className="mac-search" style={{ margin: '0 16px 8px' }}>
            <MacSearch size={16} />
            <input
              className="mac-search__input"
              value={projectKeyword}
              onChange={(e) => setProjectKeyword(e.target.value)}
              placeholder="搜索项目名称 / 编码"
            />
          </div>
          <button
            type="button"
            style={{
              display: 'flex', alignItems: 'center', gap: 8, border: 'none', background: 'none',
              cursor: 'pointer', fontFamily: 'inherit', fontSize: 13, color: 'var(--mac-muted-fg)',
              padding: '8px 16px', width: '100%', textAlign: 'left',
            }}
            onClick={toggleSelectAllProjects}
          >
            <ChoiceDot checked={allProjectsSelected} />
            全选（{filteredProjects.length} 个项目）
          </button>
          <div style={{ maxHeight: 260, overflow: 'auto' }}>
            {filteredProjects.map((p) => {
              const checked = selectedProjectCodes.has(p.project_code);
              return (
                <button
                  key={p.project_code}
                  type="button"
                  className={`mac-choice ${checked ? 'is-active' : ''}`}
                  style={{ padding: '8px 16px', borderBottom: '1px solid rgba(232,234,234,0.6)' }}
                  onClick={() => toggleProject(p.project_code)}
                >
                  <span className="mac-choice__dot">
                    {checked && <MacCheck size={12} />}
                  </span>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span className="mac-choice__label">{p.name}</span>
                    <span style={{ display: 'block', fontSize: 11, color: 'var(--mac-muted-fg)', marginTop: 2 }}>{p.project_code}</span>
                  </span>
                </button>
              );
            })}
            {filteredProjects.length === 0 && (
              <div className="mac-empty">暂无匹配项目</div>
            )}
          </div>
        </PickCard>
      </div>

      {/* 操作按钮（对照原型 Actions：移除角色 / 批量授权） */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, padding: '0 16px' }}>
        <button
          type="button"
          className="mac-btn mac-btn--lg mac-btn--outline"
          disabled={!canSubmit}
          onClick={handleRemove}
        >
          移除角色
        </button>
        <button
          type="button"
          className="mac-btn mac-btn--lg mac-btn--primary"
          disabled={!canSubmit}
          onClick={handleAssign}
        >
          {submitting ? '提交中...' : '批量授权'}
        </button>
      </div>
    </div>
  );
}

// ============================================================
// 通用辅助组件
// ============================================================

function PickerList({ title, items, onSelect, searchable }: { title: string; items: { key: string; label: string; sub?: string }[]; onSelect: (key: string) => void; searchable?: boolean }) {
  const [keyword, setKeyword] = useState('');

  const filteredItems = useMemo(() => {
    if (!keyword.trim()) return items;
    const kw = keyword.trim().toLowerCase();
    return items.filter((item) => {
      const text = `${item.label} ${item.sub || ''}`.toLowerCase();
      return text.includes(kw);
    });
  }, [items, keyword]);

  return (
    <div className="mac-sheet" style={{ maxHeight: '60vh', display: 'flex', flexDirection: 'column' }}>
      <h4 className="mac-sheet__title">{title}</h4>
      {searchable && (
        <div className="mac-search" style={{ marginBottom: 8 }}>
          <MacSearch size={16} />
          <input
            className="mac-search__input"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="输入关键字搜索项目"
          />
        </div>
      )}
      <div style={{ overflow: 'auto', flex: 1 }}>
        {filteredItems.map((item) => (
          <button
            key={item.key}
            type="button"
            className="mac-list-item"
            onClick={() => onSelect(item.key)}
          >
            <span style={{ minWidth: 0, flex: 1 }}>
              <span className="mac-list-item__name">{item.label}</span>
              {item.sub && <span className="mac-list-item__sub">{item.sub}</span>}
            </span>
          </button>
        ))}
        {filteredItems.length === 0 && (
          <div className="mac-empty">
            {keyword ? '暂无匹配结果' : '暂无数据'}
          </div>
        )}
      </div>
    </div>
  );
}

// 入口选择卡片（对照原型 ModeCard：淡蓝图标块 + 标题/描述 + 箭头）
function ModeEntryCard({ icon, title, desc, onClick }: { icon: ReactNode; title: string; desc: string; onClick: () => void }) {
  return (
    <button type="button" className="admin-entries-card" onClick={onClick}>
      <span
        className="admin-entries-card__icon"
        style={{ background: 'var(--mac-blue-soft)', color: 'var(--mac-blue-2)' }}
      >
        {icon}
      </span>
      <span className="admin-entries-card__body">
        <span className="admin-entries-card__label">{title}</span>
        <span className="admin-entries-card__desc">{desc}</span>
      </span>
      <span style={{ color: 'var(--mac-muted-fg)', display: 'inline-flex', flexShrink: 0 }}>
        <MacChevronRight size={16} />
      </span>
    </button>
  );
}

// 带标题与已选计数的选择卡片（对照原型 PickList 的 surface-card 容器）
function PickCard({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  return (
    <section className="mac-card" style={{ overflow: 'hidden' }}>
      <div
        style={{
          display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
          padding: '16px 16px 0',
        }}
      >
        <h2 style={{ margin: 0, fontSize: 13.5, fontWeight: 700, color: 'var(--mac-fg)' }}>{title}</h2>
        {count > 0 && (
          <span style={{ fontSize: 11, color: 'var(--mac-muted-fg)' }}>已选 {count}</span>
        )}
      </div>
      <div style={{ padding: '12px 0 8px' }}>
        {children}
      </div>
    </section>
  );
}
