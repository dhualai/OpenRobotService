import { useState, useEffect, useCallback, useMemo } from 'react';
import { Button, Toast, Loading, Dialog, Popup, Form, FormItem, Textarea, RadioGroup } from 'tdesign-mobile-react';
import ClearableInput from '@/shared/components/ClearableInput';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { useAuthStore } from '@/stores/auth';

interface User {
  id: string;
  username: string;
  name?: string | null;
  status?: string;
  department?: string | null;
  responsibility_modules?: Record<string, string[]> | null;
  job_level?: number;
  duty_text?: string | null;
  permissions?: string[];
  roles?: Record<string, string[]>;
  projectPermissions?: Record<string, Record<string, string[]>>;
  external_credentials?: Record<string, Record<string, string>>;
  avatar_resource_id?: number | null;
}

interface UserCreateData {
  username: string;
  password: string;
  name?: string;
  department?: string;
  responsibility_modules?: Record<string, string[]>;
  job_level?: number;
  duty_text?: string;
  status?: string;
}

interface UserUpdateData {
  name?: string;
  department?: string;
  responsibility_modules?: Record<string, string[]>;
  job_level?: number;
  duty_text?: string;
  status?: string;
  password?: string;
}

interface ModuleEntry {
  module: string;
  keywords: string[];
}

const JOB_LEVEL_OPTIONS = [
  { label: '一线', value: 1 },
  { label: '管理/审核', value: 2 },
  { label: '仅兜底', value: 3 },
];

const STATUS_OPTIONS = [
  { label: '活跃', value: 'active' },
  { label: '未激活', value: 'inactive' },
];

const modulesToEntries = (mods?: Record<string, string[]> | null): ModuleEntry[] => {
  if (!mods) return [];
  return Object.entries(mods).map(([module, keywords]) => ({
    module,
    keywords: Array.isArray(keywords) ? [...keywords] : [],
  }));
};

const entriesToModules = (entries: ModuleEntry[]): Record<string, string[]> | undefined => {
  const result: Record<string, string[]> = {};
  for (const e of entries) {
    const key = e.module.trim();
    if (key) {
      const kws = e.keywords.map((k) => k.trim()).filter(Boolean);
      if (kws.length > 0) {
        result[key] = kws;
      }
    }
  }
  return Object.keys(result).length > 0 ? result : undefined;
};

export default function UserManage() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  const [editVisible, setEditVisible] = useState(false);
  const [editingUsername, setEditingUsername] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const [form, setForm] = useState<UserCreateData>({
    username: '',
    password: '',
    name: '',
    department: '',
    responsibility_modules: undefined,
    job_level: 1,
    duty_text: '',
    status: 'active',
  });

  const [moduleEntries, setModuleEntries] = useState<ModuleEntry[]>([]);
  const [keywordInputs, setKeywordInputs] = useState<Record<number, string>>({});

  const [keyword, setKeyword] = useState('');

  const [editLoading, setEditLoading] = useState(false);
  const [detailVisible, setDetailVisible] = useState(false);
  const [detailUser, setDetailUser] = useState<User | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // 名称查找表：把详情里的 id/code 解析成可读名称
  const [roleNameMap, setRoleNameMap] = useState<Map<string, string>>(new Map());
  const [projectNameMap, setProjectNameMap] = useState<Map<string, string>>(new Map());
  const [permNameMap, setPermNameMap] = useState<Map<string, string>>(new Map());
  // 系统角色（role_type='system'），用于全局角色编辑弹窗的可选列表
  const [systemRoles, setSystemRoles] = useState<{ id: string; name: string }[]>([]);
  const [globalRoleEditVisible, setGlobalRoleEditVisible] = useState(false);
  const [globalRoleChecked, setGlobalRoleChecked] = useState<Set<string>>(new Set());
  const [globalRoleSaving, setGlobalRoleSaving] = useState(false);
  // 项目角色（role_type='project'）+ 全部项目列表，用于项目角色编辑弹窗
  const [projectRoles, setProjectRoles] = useState<{ id: string; name: string }[]>([]);
  const [allProjects, setAllProjects] = useState<{ id: string; name: string }[]>([]);
  const [projectRoleEditVisible, setProjectRoleEditVisible] = useState(false);
  const [projectRoleChecked, setProjectRoleChecked] = useState<Set<string>>(new Set());
  const [projectRoleSelProject, setProjectRoleSelProject] = useState<string>('');
  const [projectRoleSaving, setProjectRoleSaving] = useState(false);

  const request = useMemo(() => createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin'), []);

  const { hasPermission } = useAuthStore();
  // 仅当当前管理员持有 backend:roles:system 权限时，才允许编辑他人全局角色
  const canManageSystemRoles = hasPermission('backend:role:system');

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request<User[]>('/users/?skip=0&limit=1000');
      setUsers(normalizeList<User>(data));
    } catch (e) {
      Toast({ message: String(e), theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [request]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  // 加载角色 / 项目 / 权限名称查找表（只读、可复用接口缓存）
  useEffect(() => {
    (async () => {
      try {
        const [rolesData, projectsData, permsData] = await Promise.all([
          request<{ id: string; name: string; role_type?: string }[]>('/roles/'),
          request<{ id?: string; project_code: string; name: string }[]>('/projects/?limit=1000&include_analysis=false'),
          request<{ id: string; code: string; name: string }[]>('/permissions/'),
        ]);
        const rMap = new Map<string, string>();
        const sysRoles: { id: string; name: string }[] = [];
        const projRoles: { id: string; name: string }[] = [];
        normalizeList<{ id: string; name: string; role_type?: string }>(rolesData).forEach((r) => {
          rMap.set(r.id, r.name);
          if (r.role_type === 'system') sysRoles.push({ id: r.id, name: r.name });
          if (r.role_type === 'project') projRoles.push({ id: r.id, name: r.name });
        });
        setSystemRoles(sysRoles);
        setProjectRoles(projRoles);
        const pMap = new Map<string, string>();
        const projList: { id: string; name: string }[] = [];
        normalizeList<{ id?: string; project_code: string; name: string }>(projectsData).forEach((p) => {
          pMap.set(p.project_code, p.name);
          if (p.id) {
            pMap.set(p.id, p.name);
            projList.push({ id: p.id, name: p.name });
          }
        });
        setAllProjects(projList);
        const permMap = new Map<string, string>();
        normalizeList<{ id: string; code: string; name: string }>(permsData).forEach((p) => permMap.set(p.code, p.name));
        setRoleNameMap(rMap);
        setProjectNameMap(pMap);
        setPermNameMap(permMap);
      } catch {
        // 查找表加载失败时回退为显示原始 id/code
      }
    })();
  }, [request]);

  const handleSearch = () => {
    setKeyword(keyword.trim());
  };

  const filteredUsers = useMemo(() => {
    if (!keyword) return users;
    const kw = keyword.toLowerCase();
    return users.filter(
      (u) =>
        (u.username && u.username.toLowerCase().includes(kw)) ||
        (u.name && u.name.toLowerCase().includes(kw)) ||
        (u.department && u.department.toLowerCase().includes(kw))
    );
  }, [users, keyword]);

  const openCreate = () => {
    setEditingUsername(null);
    setForm({
      username: '',
      password: '',
      name: '',
      department: '',
      responsibility_modules: undefined,
      job_level: 1,
      duty_text: '',
      status: 'active',
    });
    setModuleEntries([]);
    setKeywordInputs({});
    setEditVisible(true);
  };

  const openEdit = async (user: User) => {
    setEditingUsername(user.username);
    setEditLoading(true);

    try {
      const detail = await request<User>(`/users/${user.username}/detail`);
      setForm({
        username: detail.username,
        password: '',
        name: detail.name || '',
        department: detail.department || '',
        responsibility_modules: detail.responsibility_modules || undefined,
        job_level: detail.job_level ?? 1,
        duty_text: detail.duty_text || '',
        status: detail.status || 'active',
      });
      setModuleEntries(modulesToEntries(detail.responsibility_modules));
    } catch {
      setForm({
        username: user.username,
        password: '',
        name: user.name || '',
        department: user.department || '',
        responsibility_modules: user.responsibility_modules || undefined,
        job_level: user.job_level ?? 1,
        duty_text: user.duty_text || '',
        status: user.status || 'active',
      });
      setModuleEntries(modulesToEntries(user.responsibility_modules));
    }
    setKeywordInputs({});
    setEditLoading(false);
    setEditVisible(true);
  };

  const openDetail = async (user: User) => {
    setDetailUser(user);
    setDetailVisible(true);
    setDetailLoading(true);
    try {
      const detail = await request<User>(`/users/${user.username}/detail`);
      setDetailUser(detail);
    } catch {
      Toast({ message: '加载详情失败', theme: 'error' });
    } finally {
      setDetailLoading(false);
    }
  };

  const reloadDetail = async (username: string) => {
    const detail = await request<User>(`/users/${username}/detail`);
    setDetailUser(detail);
  };

  // 打开全局角色编辑弹窗：用当前已有的全局角色（roles['global']）预勾选
  const openGlobalRoleEditor = (user: User) => {
    setGlobalRoleChecked(new Set(user.roles?.['global'] || []));
    setGlobalRoleEditVisible(true);
  };

  // 保存全局角色：对比差集，新增走 assign-roles（project_id 留空=全局），移除走 DELETE /project/role?project_id=global
  const handleSaveGlobalRoles = async () => {
    if (!detailUser) return;
    const { username, id: userId } = detailUser;
    const before = new Set(detailUser.roles?.['global'] || []);
    const added = Array.from(globalRoleChecked).filter((rid) => !before.has(rid));
    const removed = Array.from(before).filter((rid) => !globalRoleChecked.has(rid));
    if (added.length === 0 && removed.length === 0) {
      setGlobalRoleEditVisible(false);
      return;
    }
    setGlobalRoleSaving(true);
    try {
      const tasks: Promise<unknown>[] = [];
      if (added.length > 0) {
        tasks.push(
          request('/users/project/assign-roles', {
            method: 'POST',
            body: JSON.stringify({
              project_id: '',
              organization_ids: added.map((rid) => ({ user_name: username, role_id: rid })),
            }),
          }),
        );
      }
      removed.forEach((rid) => {
        tasks.push(
          request(
            `/users/project/role?user_id=${encodeURIComponent(userId)}&project_id=global&role_id=${encodeURIComponent(rid)}`,
            { method: 'DELETE' },
          ),
        );
      });
      await Promise.all(tasks);
      Toast({ message: '全局角色已更新', theme: 'success' });
      setGlobalRoleEditVisible(false);
      await reloadDetail(username);
    } catch (err) {
      Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setGlobalRoleSaving(false);
    }
  };

  // 打开项目角色编辑弹窗：直接编辑指定项目的角色，预勾选该项目已有角色
  const openProjectRoleEditor = (user: User, projectId: string) => {
    setProjectRoleSelProject(projectId);
    const existingRoles = user.roles?.[projectId] || [];
    setProjectRoleChecked(new Set(existingRoles));
    setProjectRoleEditVisible(true);
  };

  // 打开项目角色添加弹窗：选择新项目后勾选角色
  const openProjectRoleEditorNew = () => {
    setProjectRoleSelProject('');
    setProjectRoleChecked(new Set());
    setProjectRoleEditVisible(true);
  };

  // 项目选择切换时（仅添加模式），更新勾选状态为该项目的现有角色
  const handleProjectRoleProjectChange = (projectId: string) => {
    setProjectRoleSelProject(projectId);
    const existingRoles = detailUser?.roles?.[projectId] || [];
    setProjectRoleChecked(new Set(existingRoles));
  };

  // 保存项目角色：对比差集，新增走 assign-roles（project_id=实际项目ID），移除走 DELETE
  const handleSaveProjectRoles = async () => {
    if (!detailUser || !projectRoleSelProject) return;
    const { username, id: userId } = detailUser;
    const before = new Set(detailUser.roles?.[projectRoleSelProject] || []);
    const added = Array.from(projectRoleChecked).filter((rid) => !before.has(rid));
    const removed = Array.from(before).filter((rid) => !projectRoleChecked.has(rid));
    if (added.length === 0 && removed.length === 0) {
      setProjectRoleEditVisible(false);
      return;
    }
    setProjectRoleSaving(true);
    try {
      const tasks: Promise<unknown>[] = [];
      if (added.length > 0) {
        tasks.push(
          request('/users/project/assign-roles', {
            method: 'POST',
            body: JSON.stringify({
              project_id: projectRoleSelProject,
              organization_ids: added.map((rid) => ({ user_name: username, role_id: rid })),
            }),
          }),
        );
      }
      removed.forEach((rid) => {
        tasks.push(
          request(
            `/users/project/role?user_id=${encodeURIComponent(userId)}&project_id=${encodeURIComponent(projectRoleSelProject)}&role_id=${encodeURIComponent(rid)}`,
            { method: 'DELETE' },
          ),
        );
      });
      await Promise.all(tasks);
      Toast({ message: '项目角色已更新', theme: 'success' });
      setProjectRoleEditVisible(false);
      await reloadDetail(username);
    } catch (err) {
      Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setProjectRoleSaving(false);
    }
  };

  const handleSave = async () => {
    if (!editingUsername) {
      if (!form.username.trim()) {
        Toast({ message: '请输入用户名', theme: 'warning' });
        return;
      }
      if (!form.password.trim()) {
        Toast({ message: '请输入密码', theme: 'warning' });
        return;
      }
    }

    const modules = entriesToModules(moduleEntries);

    setIsSaving(true);
    try {
      if (editingUsername) {
        const updateData: UserUpdateData = {
          name: form.name || undefined,
          department: form.department || undefined,
          responsibility_modules: modules,
          job_level: form.job_level,
          duty_text: form.duty_text || undefined,
        };
        await request(`/users/${editingUsername}`, {
          method: 'PUT',
          body: JSON.stringify(updateData),
        });
        Toast({ message: '用户已更新', theme: 'success' });
      } else {
        const createData: UserCreateData = {
          username: form.username,
          password: form.password,
          name: form.name || undefined,
          department: form.department || undefined,
          responsibility_modules: modules,
          job_level: form.job_level,
          duty_text: form.duty_text || undefined,
          status: form.status,
        };
        await request('/users/', {
          method: 'POST',
          body: JSON.stringify(createData),
        });
        Toast({ message: '用户已创建', theme: 'success' });
      }
      setEditVisible(false);
      fetchUsers();
    } catch (err) {
      Toast({
        message: `保存失败: ${err instanceof Error ? err.message : ''}`,
        theme: 'error',
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = (user: User) => {
    Dialog.confirm?.({
      title: '确认删除',
      content: `确定要删除用户「${user.username}」吗？此操作不可撤销。`,
      onConfirm: async () => {
        try {
          await request(`/users/${user.username}`, { method: 'DELETE' });
          Toast({ message: '已删除', theme: 'success' });
          fetchUsers();
        } catch (err) {
          Toast({
            message: `删除失败: ${err instanceof Error ? err.message : ''}`,
            theme: 'error',
          });
        }
      },
    });
  };

  const addModule = () => {
    setModuleEntries((prev) => [...prev, { module: '', keywords: [] }]);
  };

  const removeModule = (index: number) => {
    setModuleEntries((prev) => prev.filter((_, i) => i !== index));
    setKeywordInputs((prev) => {
      const next: Record<number, string> = {};
      for (const [k, v] of Object.entries(prev)) {
        const ki = Number(k);
        next[ki > index ? ki - 1 : ki] = v;
      }
      return next;
    });
  };

  const updateModuleName = (index: number, name: string) => {
    setModuleEntries((prev) => prev.map((e, i) => (i === index ? { ...e, module: name } : e)));
  };

  const addKeyword = (index: number) => {
    const val = (keywordInputs[index] || '').trim();
    if (!val) return;
    setModuleEntries((prev) =>
      prev.map((e, i) => (i === index ? { ...e, keywords: [...e.keywords, val] } : e))
    );
    setKeywordInputs((prev) => ({ ...prev, [index]: '' }));
  };

  const removeKeyword = (moduleIndex: number, keywordIndex: number) => {
    setModuleEntries((prev) =>
      prev.map((e, i) =>
        i === moduleIndex
          ? { ...e, keywords: e.keywords.filter((_, ki) => ki !== keywordIndex) }
          : e
      )
    );
  };

  const updateKeywordInput = (index: number, value: string) => {
    setKeywordInputs((prev) => ({ ...prev, [index]: value }));
  };

  const getJobLevelLabel = (level?: number) => {
    const opt = JOB_LEVEL_OPTIONS.find((o) => o.value === level);
    return opt ? opt.label : '未知';
  };

  const getJobLevelColor = (level?: number) => {
    if (level === 1) return '#2ba471';
    if (level === 2) return '#ff7d00';
    if (level === 3) return '#e34d59';
    return '#999';
  };

  if (loading) return <Loading text="加载用户列表..." />;

  return (
    <div style={{ padding: 16 }}>
      <Button theme="primary" block style={{ marginBottom: 16 }} onClick={openCreate}>
        新建用户
      </Button>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <ClearableInput
          value={keyword}
          onChange={(v) => setKeyword(String(v))}
          placeholder="搜索用户名/姓名/部门…"
          style={{ flex: 1 }}
        />
        <Button size="small" theme="primary" onClick={handleSearch}>搜索</Button>
      </div>

      {filteredUsers.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
          {keyword ? '未找到匹配的用户' : '暂无用户，请点击"新建用户"添加'}
        </div>
      ) : (
        filteredUsers.map((user) => (
          <div
            key={user.id}
            onClick={() => openDetail(user)}
            style={{
              background: '#fff',
              borderRadius: 8,
              padding: 14,
              marginBottom: 10,
              boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
              cursor: 'pointer',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'flex-start',
              }}
            >
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{ fontWeight: 500, fontSize: 15 }}>{user.name || user.username}</span>
                  {user.name && user.name !== user.username && (
                    <span style={{ fontSize: 13, color: '#888' }}>@{user.username}</span>
                  )}
                  <span
                    style={{
                      fontSize: 11,
                      padding: '1px 6px',
                      borderRadius: 4,
                      background: user.status === 'active' ? '#e8f5e9' : '#f5f5f5',
                      color: user.status === 'active' ? '#2ba471' : '#999',
                    }}
                  >
                    {user.status === 'active' ? '活跃' : '未激活'}
                  </span>
                </div>

                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 4 }}>
                  <span
                    style={{
                      fontSize: 11,
                      padding: '2px 8px',
                      borderRadius: 4,
                      background: `${getJobLevelColor(user.job_level)}15`,
                      color: getJobLevelColor(user.job_level),
                      border: `1px solid ${getJobLevelColor(user.job_level)}30`,
                    }}
                  >
                    {getJobLevelLabel(user.job_level)}
                  </span>
                  {user.department && (
                    <span
                      style={{
                        fontSize: 11,
                        padding: '2px 8px',
                        borderRadius: 4,
                        background: '#e8f0fe',
                        color: '#0052d9',
                      }}
                    >
                      🏢 {user.department}
                    </span>
                  )}
                </div>

                {user.responsibility_modules && Object.keys(user.responsibility_modules).length > 0 && (
                  <div style={{ marginBottom: 4 }}>
                    {Object.entries(user.responsibility_modules).map(([mod, keywords]) => (
                      <div key={mod} style={{ marginBottom: 3 }}>
                        <span
                          style={{
                            fontSize: 11,
                            fontWeight: 500,
                            padding: '1px 6px',
                            borderRadius: 3,
                            background: '#fff7e6',
                            color: '#d46b08',
                            marginRight: 4,
                          }}
                        >
                          {mod}
                        </span>
                        {keywords.map((kw) => (
                          <span
                            key={kw}
                            style={{
                              fontSize: 11,
                              padding: '1px 5px',
                              borderRadius: 2,
                              background: '#f0f0f0',
                              color: '#666',
                              marginRight: 3,
                            }}
                          >
                            {kw}
                          </span>
                        ))}
                      </div>
                    ))}
                  </div>
                )}

                {user.duty_text && (
                  <div
                    style={{
                      fontSize: 12,
                      color: '#888',
                      marginTop: 4,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                    }}
                  >
                    📋 {user.duty_text}
                  </div>
                )}
              </div>

              <div style={{ display: 'flex', gap: 6, marginLeft: 8, flexShrink: 0 }}>
                <Button size="small" variant="outline" onClick={(e) => { e.stopPropagation(); openEdit(user); }}>
                  编辑
                </Button>
                <Button
                  size="small"
                  theme="danger"
                  variant="outline"
                  onClick={(e) => { e.stopPropagation(); handleDelete(user); }}
                >
                  删除
                </Button>
              </div>
            </div>
          </div>
        ))
      )}

      <Popup visible={editVisible} onClose={() => setEditVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '85vh', overflow: 'auto' }}>
          <h4 style={{ marginBottom: 16 }}>{editingUsername ? '编辑用户' : '新建用户'}</h4>

          {editLoading ? (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Loading text="加载用户信息..." />
            </div>
          ) : (
          <Form initialData={form}>
            {!editingUsername && (
              <>
                <FormItem label="用户名" name="username">
                  <ClearableInput
                    value={form.username}
                    onChange={(v) => setForm((p) => ({ ...p, username: String(v) }))}
                    placeholder="登录账号"
                  />
                </FormItem>

                <FormItem label="密码" name="password">
                  <ClearableInput
                    value={form.password}
                    onChange={(v) => setForm((p) => ({ ...p, password: String(v) }))}
                    placeholder="初始密码"
                    type="password"
                  />
                </FormItem>
              </>
            )}

            <FormItem label="姓名" name="name">
              <ClearableInput
                value={form.name || ''}
                onChange={(v) => setForm((p) => ({ ...p, name: String(v) }))}
                placeholder="真实姓名"
              />
            </FormItem>

            <FormItem label="部门" name="department">
              <ClearableInput
                value={form.department || ''}
                onChange={(v) => setForm((p) => ({ ...p, department: String(v) }))}
                placeholder="部门/团队"
              />
            </FormItem>

            <FormItem label="职级" name="job_level">
              <RadioGroup
                value={form.job_level}
                onChange={(v) => setForm((p) => ({ ...p, job_level: v as number }))}
                options={JOB_LEVEL_OPTIONS}
              />
            </FormItem>

            {!editingUsername && (
              <FormItem label="状态" name="status">
                <RadioGroup
                  value={form.status}
                  onChange={(v) => setForm((p) => ({ ...p, status: v as string }))}
                  options={STATUS_OPTIONS}
                />
              </FormItem>
            )}

            <FormItem label="责任模块" name="responsibility_modules">
              <div style={{ marginBottom: 8 }}>
                {moduleEntries.length === 0 && (
                  <div style={{ fontSize: 12, color: '#bbb', marginBottom: 8 }}>
                    暂未设置，点击下方按钮添加
                  </div>
                )}

                {moduleEntries.map((entry, idx) => (
                  <div
                    key={idx}
                    style={{
                      border: '1px solid #e5e5e5',
                      borderRadius: 6,
                      padding: 10,
                      marginBottom: 10,
                      background: '#fafafa',
                    }}
                  >
                    <div style={{ display: 'flex', gap: 6, marginBottom: 8, alignItems: 'center' }}>
                      <span style={{ fontSize: 12, color: '#666', whiteSpace: 'nowrap', minWidth: 48 }}>
                        模块 {idx + 1}
                      </span>
                      <ClearableInput
                        value={entry.module}
                        onChange={(v) => updateModuleName(idx, String(v))}
                        placeholder="模块名，如：调度USP"
                        style={{ flex: 1 }}
                      />
                      <Button
                        size="small"
                        variant="text"
                        theme="danger"
                        onClick={() => removeModule(idx)}
                      >
                        删除
                      </Button>
                    </div>

                    {entry.keywords.length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 8 }}>
                        {entry.keywords.map((kw, ki) => (
                          <span
                            key={ki}
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: 4,
                              fontSize: 12,
                              padding: '2px 8px',
                              borderRadius: 3,
                              background: '#e8f0fe',
                              color: '#0052d9',
                            }}
                          >
                            {kw}
                            <span
                              style={{ cursor: 'pointer', fontWeight: 'bold' }}
                              onClick={() => removeKeyword(idx, ki)}
                            >
                              ×
                            </span>
                          </span>
                        ))}
                      </div>
                    )}

                    <div style={{ display: 'flex', gap: 6 }}>
                      <ClearableInput
                        value={keywordInputs[idx] || ''}
                        onChange={(v) => updateKeywordInput(idx, String(v))}
                        placeholder="输入职责关键字"
                        style={{ flex: 1 }}
                      />
                      <Button size="small" variant="outline" onClick={() => addKeyword(idx)}>
                        添加
                      </Button>
                    </div>
                  </div>
                ))}

                <Button size="small" variant="outline" onClick={addModule}>
                  + 添加模块
                </Button>
              </div>
            </FormItem>

            <FormItem label="职责画像" name="duty_text">
              <Textarea
                value={form.duty_text || ''}
                onChange={(v) => setForm((p) => ({ ...p, duty_text: String(v) }))}
                placeholder="供 AI 派单匹配参考的职责描述…"
                autosize
                maxlength={500}
              />
            </FormItem>

            <FormItem>
              <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
                <Button theme="default" block onClick={() => setEditVisible(false)}>
                  取消
                </Button>
                <Button theme="primary" block onClick={handleSave} loading={isSaving}>
                  保存
                </Button>
              </div>
            </FormItem>
          </Form>
          )}
        </div>
      </Popup>

      <Popup visible={detailVisible} onClose={() => setDetailVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '85vh', overflow: 'auto' }}>
          <h4 style={{ marginBottom: 16 }}>用户详情</h4>

          {detailLoading ? (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Loading text="加载详情..." />
            </div>
          ) : detailUser ? (
            <>
              <div style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span style={{ fontWeight: 600, fontSize: 17 }}>{detailUser.name || detailUser.username}</span>
                  {detailUser.name && detailUser.name !== detailUser.username && (
                    <span style={{ fontSize: 14, color: '#888' }}>@{detailUser.username}</span>
                  )}
                  <span
                    style={{
                      fontSize: 12,
                      padding: '2px 8px',
                      borderRadius: 4,
                      background: detailUser.status === 'active' ? '#e8f5e9' : '#f5f5f5',
                      color: detailUser.status === 'active' ? '#2ba471' : '#999',
                    }}
                  >
                    {detailUser.status === 'active' ? '活跃' : '未激活'}
                  </span>
                </div>

                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                  <span
                    style={{
                      fontSize: 12,
                      padding: '3px 10px',
                      borderRadius: 4,
                      background: `${getJobLevelColor(detailUser.job_level)}15`,
                      color: getJobLevelColor(detailUser.job_level),
                      border: `1px solid ${getJobLevelColor(detailUser.job_level)}30`,
                    }}
                  >
                    {getJobLevelLabel(detailUser.job_level)}
                  </span>
                  {detailUser.department && (
                    <span
                      style={{
                        fontSize: 12,
                        padding: '3px 10px',
                        borderRadius: 4,
                        background: '#e8f0fe',
                        color: '#0052d9',
                      }}
                    >
                      🏢 {detailUser.department}
                    </span>
                  )}
                </div>
              </div>

              {detailUser.responsibility_modules && Object.keys(detailUser.responsibility_modules).length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: '#333', marginBottom: 8 }}>
                    📌 责任模块
                  </div>
                  {Object.entries(detailUser.responsibility_modules).map(([mod, keywords]) => (
                    <div
                      key={mod}
                      style={{
                        border: '1px solid #eee',
                        borderRadius: 6,
                        padding: '8px 10px',
                        marginBottom: 6,
                        background: '#fafafa',
                      }}
                    >
                      <span style={{ fontSize: 12, fontWeight: 500, color: '#d46b08', marginRight: 6 }}>
                        {mod}
                      </span>
                      {keywords.map((kw) => (
                        <span
                          key={kw}
                          style={{
                            fontSize: 11,
                            padding: '1px 6px',
                            borderRadius: 3,
                            background: '#fff',
                            color: '#666',
                            marginRight: 4,
                          }}
                        >
                          {kw}
                        </span>
                      ))}
                    </div>
                  ))}
                </div>
              )}

              {detailUser.duty_text && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: '#333', marginBottom: 6 }}>
                    📋 职责画像
                  </div>
                  <div
                    style={{
                      fontSize: 13,
                      color: '#555',
                      lineHeight: 1.6,
                      background: '#f8f8f8',
                      padding: '10px 12px',
                      borderRadius: 6,
                    }}
                  >
                    {detailUser.duty_text}
                  </div>
                </div>
              )}

              {(() => {
                const globalRoleIds = detailUser.roles?.['global'] ?? [];
                if (globalRoleIds.length === 0 && !canManageSystemRoles) return null;
                return (
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                      <div style={{ fontSize: 13, fontWeight: 500, color: '#333' }}>🎯 全局角色</div>
                      {canManageSystemRoles && (
                        <Button
                          size="small"
                          variant="outline"
                          theme="primary"
                          onClick={() => openGlobalRoleEditor(detailUser)}
                        >
                          编辑
                        </Button>
                      )}
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {globalRoleIds.length > 0 ? (
                        globalRoleIds.map((roleId) => (
                          <span
                            key={roleId}
                            style={{
                              fontSize: 12,
                              padding: '3px 10px',
                              borderRadius: 4,
                              background: '#e6f7ff',
                              color: '#0050b3',
                            }}
                          >
                            {roleNameMap.get(roleId) || roleId}
                          </span>
                        ))
                      ) : (
                        <span style={{ fontSize: 12, color: '#ccc' }}>暂无全局角色</span>
                      )}
                    </div>
                  </div>
                );
              })()}

              {(() => {
                const projectRolesEntries = detailUser.roles
                  ? Object.entries(detailUser.roles).filter(([pid]) => pid !== 'global')
                  : [];
                const projectPermCount = projectRolesEntries.length;
                if (projectPermCount === 0 && !canManageSystemRoles) return null;
                return (
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                      <div style={{ fontSize: 13, fontWeight: 500, color: '#333' }}>📂 项目角色</div>
                      {canManageSystemRoles && (
                        <Button
                          size="small"
                          variant="outline"
                          theme="primary"
                          onClick={openProjectRoleEditorNew}
                        >
                          + 添加项目
                        </Button>
                      )}
                    </div>
                    {projectPermCount > 0 ? (
                      projectRolesEntries.map(([projectId, roleIds]) => (
                        <div
                          key={projectId}
                          style={{
                            border: '1px solid #e6f7ff',
                            borderRadius: 6,
                            padding: '8px 10px',
                            marginBottom: 6,
                            background: '#f0faff',
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <span style={{ fontSize: 12, fontWeight: 500, color: '#0050b3' }}>
                              项目 {projectNameMap.get(projectId) || projectId}
                            </span>
                            {canManageSystemRoles && (
                              <Button
                                size="small"
                                variant="outline"
                                theme="primary"
                                onClick={() => openProjectRoleEditor(detailUser, projectId)}
                              >
                                编辑
                              </Button>
                            )}
                          </div>
                          <div style={{ marginTop: 4, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                            {roleIds.map((roleId) => (
                              <span
                                key={roleId}
                                style={{
                                  fontSize: 11,
                                  padding: '2px 6px',
                                  borderRadius: 3,
                                  background: '#fff',
                                  color: '#0050b3',
                                  marginRight: 4,
                                }}
                              >
                                {roleNameMap.get(roleId) || roleId}
                              </span>
                            ))}
                          </div>
                        </div>
                      ))
                    ) : (
                      <span style={{ fontSize: 12, color: '#ccc' }}>暂无项目角色</span>
                    )}
                  </div>
                );
              })()}

              {detailUser.permissions && detailUser.permissions.length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: '#333', marginBottom: 8 }}>
                    🔐 权限列表
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {detailUser.permissions.map((perm) => (
                      <span
                        key={perm}
                        style={{
                          fontSize: 11,
                          padding: '2px 8px',
                          borderRadius: 3,
                          background: '#fff0f0',
                          color: '#cf1322',
                        }}
                      >
                        {permNameMap.get(perm) || perm}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {detailUser.external_credentials && Object.keys(detailUser.external_credentials).length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: '#333', marginBottom: 8 }}>
                    🔗 外部凭据
                  </div>
                  {Object.entries(detailUser.external_credentials).map(([key, cred]) => (
                    <div
                      key={key}
                      style={{
                        border: '1px solid #f0e6ff',
                        borderRadius: 6,
                        padding: '8px 10px',
                        marginBottom: 6,
                        background: '#f9f0ff',
                      }}
                    >
                      <div style={{ fontSize: 12, fontWeight: 500, color: '#531dab', marginBottom: 4 }}>
                        {key.toUpperCase()}
                      </div>
                      {Object.entries(cred).map(([credKey, credValue]) => (
                        <div key={credKey} style={{ fontSize: 12, color: '#555' }}>
                          <span style={{ color: '#888' }}>{credKey}：</span>
                          <span style={{ fontFamily: 'monospace' }}>
                            {credKey === 'password' ? '••••••' : credValue}
                          </span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : null}

          <div style={{ marginTop: 16 }}>
            <Button theme="default" block onClick={() => setDetailVisible(false)}>
              关闭
            </Button>
          </div>
        </div>
      </Popup>

      {/* 全局角色编辑弹窗：仅列出系统角色，勾选即赋予/取消全局角色 */}
      <Popup visible={globalRoleEditVisible} onClose={() => setGlobalRoleEditVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
          <h4 style={{ marginBottom: 4 }}>
            编辑全局角色：{detailUser?.name || detailUser?.username}
          </h4>
          <p style={{ fontSize: 12, color: '#999', marginBottom: 12 }}>
            勾选即赋予系统级全局角色，取消即移除
          </p>
          <div style={{ overflow: 'auto', flex: 1 }}>
            {systemRoles.length === 0 && (
              <div style={{ textAlign: 'center', padding: 30, color: '#999' }}>暂无可分配的系统角色</div>
            )}
            {systemRoles.map((r) => {
              const checked = globalRoleChecked.has(r.id);
              return (
                <div
                  key={r.id}
                  onClick={() =>
                    setGlobalRoleChecked((prev) => {
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
                  <CheckDot checked={checked} />
                  {r.name}
                </div>
              );
            })}
          </div>
          <Button
            theme="primary"
            block
            style={{ marginTop: 16 }}
            loading={globalRoleSaving}
            onClick={handleSaveGlobalRoles}
          >
            保存
          </Button>
        </div>
      </Popup>

      {/* 项目角色编辑弹窗：编辑已有项目角色（直接显示角色勾选），或添加新项目关联（需先选项目） */}
      <Popup visible={projectRoleEditVisible} onClose={() => setProjectRoleEditVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
          <h4 style={{ marginBottom: 4 }}>
            编辑项目角色：{detailUser?.name || detailUser?.username}
          </h4>
          {projectRoleSelProject ? (
            <p style={{ fontSize: 13, color: '#0050b3', marginBottom: 12 }}>
              项目：{projectNameMap.get(projectRoleSelProject) || projectRoleSelProject}
            </p>
          ) : (
            <>
              <p style={{ fontSize: 12, color: '#999', marginBottom: 12 }}>
                选择项目后勾选角色，取消勾选即移除
              </p>
              <select
                value={projectRoleSelProject}
                onChange={(e) => handleProjectRoleProjectChange(e.target.value)}
                style={{ marginBottom: 12, padding: '8px', borderRadius: 6, border: '1px solid #dcdcdc', fontSize: 14 }}
              >
                <option value="">请选择项目</option>
                {allProjects.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </>
          )}
          {/* 项目角色勾选列表 */}
          <div style={{ overflow: 'auto', flex: 1 }}>
            {projectRoles.length === 0 && (
              <div style={{ textAlign: 'center', padding: 30, color: '#999' }}>暂无可分配的项目角色</div>
            )}
            {projectRoles.map((r) => {
              const checked = projectRoleChecked.has(r.id);
              return (
                <div
                  key={r.id}
                  onClick={() =>
                    setProjectRoleChecked((prev) => {
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
                  <CheckDot checked={checked} />
                  {r.name}
                </div>
              );
            })}
          </div>
          <Button
            theme="primary"
            block
            style={{ marginTop: 16 }}
            loading={projectRoleSaving}
            disabled={!projectRoleSelProject}
            onClick={handleSaveProjectRoles}
          >
            保存
          </Button>
        </div>
      </Popup>
    </div>
  );
}

// 自绘勾选圆点：与 AssignRole 页一致，避免 Checkbox 默认白底在卡片上突兀
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