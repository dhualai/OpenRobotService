// 用户管理 —— 账号 CRUD、派单画像（责任模块/职责）、全局/项目角色、权限与外部凭据查看。
// 样式参考 macaron users 页：新建/人员结构按钮行 + 卡片搜索框 + 计数胶囊 +
// surface-card 用户卡（状态胶囊 + 职级/部门芯片 + 责任模块芯片 + 职责画像）+ 弹层表单。
import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Toast, Loading, Dialog, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { useAuthStore } from '@/stores/auth';
import {
  avatarUrl,
  getProfileOptions,
} from '@/api/profile';
import type { OrgOption, ProfileFieldOptions } from '@/api/profile';
import FilterableSelect from '@/shared/components/FilterableSelect';
import {
  MacSearch, MacCheck, MacBuilding2, MacClipboardList,
} from '@/shared/components/macaronIcons';

interface User {
  id: string;
  username: string;
  name?: string | null;
  status?: string;
  company?: string | null;
  department?: string | null;
  company_id?: string | null;
  department_id?: string | null;
  responsibility_modules?: Record<string, Record<string, string[]>> | null;
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
  company_id?: string;
  department_id?: string;
  responsibility_modules?: Record<string, Record<string, string[]>>;
  job_level?: number;
  duty_text?: string;
  status?: string;
  external_credentials?: Record<string, Record<string, string>>;
}

interface UserUpdateData {
  name?: string;
  department?: string;
  company_id?: string;
  department_id?: string;
  responsibility_modules?: Record<string, Record<string, string[]>>;
  job_level?: number;
  duty_text?: string;
  status?: string;
  password?: string;
}

const JOB_LEVEL_OPTIONS = [
  { label: '一线', value: 1 },
  { label: '管理/审核', value: 2 },
  { label: '仅兜底', value: 3 },
];

/** 取姓名首字符作为无头像时的回退；无姓名则取用户名首字符，全空时显示 ? */
const avatarInitial = (name?: string | null, username?: string) => {
  const src = (name && name.trim()) || (username && username.trim()) || '?';
  return src.slice(0, 1).toUpperCase();
};

/**
 * 归一化 responsibility_modules：兼容「旧两层 {产品: [模块]}」与「新三层 {产品: {界面: [功能]}}」。
 * 统一输出三层结构，供卡片/详情展示安全遍历。
 * - 两层（value 为数组）→ 归入虚拟界面「职责模块」
 * - 三层（value 为对象）→ 原样返回
 */
const normalizeRm = (rm?: Record<string, unknown> | null): Record<string, Record<string, string[]>> => {
  if (!rm || typeof rm !== 'object') return {};
  const out: Record<string, Record<string, string[]>> = {};
  for (const [product, v] of Object.entries(rm)) {
    if (!v) continue;
    if (Array.isArray(v)) {
      // 旧两层：整个数组作为「职责模块」界面的功能
      out[product] = { '职责模块': v.map(String) };
    } else if (typeof v === 'object') {
      const byIface: Record<string, string[]> = {};
      for (const [iface, funcs] of Object.entries(v as Record<string, unknown>)) {
        byIface[iface] = Array.isArray(funcs) ? funcs.map(String) : [String(funcs)];
      }
      out[product] = byIface;
    }
  }
  return out;
};

const STATUS_OPTIONS = [
  { label: '活跃', value: 'active' },
  { label: '未激活', value: 'inactive' },
];

/** 单选项行（原型 users 弹层：18px 圆 + 白色对勾） */
function ChoiceRow({ label, checked, onClick }: { label: string; checked: boolean; onClick: () => void }) {
  return (
    <button type="button" className={`mac-choice ${checked ? 'is-active' : ''}`} onClick={onClick}>
      <span className="mac-choice__dot">
        {checked && <MacCheck size={12} />}
      </span>
      <span className="mac-choice__label">{label}</span>
    </button>
  );
}

export default function UserManage() {
  const navigate = useNavigate();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  const [editVisible, setEditVisible] = useState(false);
  const [editingUsername, setEditingUsername] = useState<string | null>(null);
  const [editingUserId, setEditingUserId] = useState<string | null>(null);
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

  // 公司/部门：同时维护 ID（保存用）和 name（显示用）
  const [companyIdDraft, setCompanyIdDraft] = useState('');
  const [companyNameDraft, setCompanyNameDraft] = useState('');
  const [departmentIdDraft, setDepartmentIdDraft] = useState('');
  const [departmentNameDraft, setDepartmentNameDraft] = useState('');

  // 公司/部门下拉可选项（来自主数据表）
  const [companyOptions, setCompanyOptions] = useState<OrgOption[]>([]);
  const [departmentsByCompany, setDepartmentsByCompany] = useState<Record<string, OrgOption[]>>({});

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

  const filteredUsers = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    if (!kw) return users;
    return users.filter(
      (u) =>
        (u.username && u.username.toLowerCase().includes(kw)) ||
        (u.name && u.name.toLowerCase().includes(kw)) ||
        (u.department && u.department.toLowerCase().includes(kw))
    );
  }, [users, keyword]);

  const openCreate = () => {
    setEditingUsername(null);
    setEditingUserId(null);
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
    setCompanyIdDraft('');
    setCompanyNameDraft('');
    setDepartmentIdDraft('');
    setDepartmentNameDraft('');
    // 懒加载公司/部门选项（若尚未加载）
    if (companyOptions.length === 0) {
      getProfileOptions()
        .then((opt) => {
          setCompanyOptions(opt.companies || []);
          setDepartmentsByCompany(opt.departments_by_company || {});
        })
        .catch(() => {});
    }
    setEditVisible(true);
  };

  const openEdit = async (user: User) => {
    setEditingUsername(user.username);
    setEditingUserId(user.id);
    setEditLoading(true);

    let detail: User;
    try {
      detail = await request<User>(`/users/${user.username}/detail`);
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
    } catch {
      detail = user;
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
    }

    // 懒加载公司/部门选项，然后通过 ID 回填名称
    const usedOpt: ProfileFieldOptions =
      companyOptions.length > 0
        ? { companies: companyOptions, departments_by_company: departmentsByCompany, my_pending: { companies: [], departments: [] } }
        : await getProfileOptions().catch(() => ({
            companies: [],
            departments_by_company: {},
            my_pending: { companies: [], departments: [] },
          }));
    if (companyOptions.length === 0) {
      setCompanyOptions(usedOpt.companies || []);
      setDepartmentsByCompany(usedOpt.departments_by_company || {});
    }

    const compId = detail.company_id || '';
    const deptId = detail.department_id || '';
    const compName = (usedOpt.companies || []).find((c) => c.id === compId)?.name || detail.company || '';
    const deptName = Object.values(usedOpt.departments_by_company || {})
      .flat()
      .find((d) => d.id === deptId)?.name || detail.department || '';
    setCompanyIdDraft(compId);
    setCompanyNameDraft(compName);
    setDepartmentIdDraft(deptId);
    setDepartmentNameDraft(deptName);

    setEditLoading(false);
    setEditVisible(true);
  };

  // 公司/部门：选项与级联选择
  const departmentOptions = useMemo(() => {
    return departmentsByCompany[companyNameDraft.replace(/（审核中）$/, '')] || [];
  }, [departmentsByCompany, companyNameDraft]);

  const companyNames = useMemo(() => {
    return companyOptions.map((c) => (c.status === 'pending' ? `${c.name}（审核中）` : c.name));
  }, [companyOptions]);

  const departmentNames = useMemo(() => {
    return departmentOptions.map((d) => (d.status === 'pending' ? `${d.name}（审核中）` : d.name));
  }, [departmentOptions]);

  const handleCompanyChange = useCallback((displayName: string) => {
    const realName = displayName.replace(/（审核中）$/, '');
    const comp = companyOptions.find((c) => c.name === realName);
    setCompanyNameDraft(displayName);
    setCompanyIdDraft(comp?.id || '');
    setDepartmentIdDraft('');
    setDepartmentNameDraft('');
  }, [companyOptions]);

  const handleDepartmentChange = useCallback((displayName: string) => {
    const realName = displayName.replace(/（审核中）$/, '');
    const dept = departmentOptions.find((d) => d.name === realName);
    setDepartmentNameDraft(displayName);
    setDepartmentIdDraft(dept?.id || '');
  }, [departmentOptions]);

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

    setIsSaving(true);
    try {
      const realCompName = companyNameDraft.replace(/（审核中）$/, '');
      const realDeptName = departmentNameDraft.replace(/（审核中）$/, '');
      if (editingUsername) {
        const updateData: UserUpdateData = {
          name: form.name || undefined,
          department: realDeptName || undefined,
          company_id: companyIdDraft || '',
          department_id: departmentIdDraft || '',
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
          department: realDeptName || undefined,
          company_id: companyIdDraft || '',
          department_id: departmentIdDraft || '',
          job_level: form.job_level,
          duty_text: form.duty_text || undefined,
          status: form.status,
          // 初始化 USP 账户：usp.username 与登录账号一致，usp.password 使用明文，
          // 由后端 create_user 走 get_password_hash(pbkdf2_sha256) 加密存储，
          // 与个人中心更新接口保持一致。
          external_credentials: {
            usp: {
              username: form.username,
              password: form.password,
            },
          },
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

  const getJobLevelLabel = (level?: number) => {
    const opt = JOB_LEVEL_OPTIONS.find((o) => o.value === level);
    return opt ? opt.label : '未知';
  };

  if (loading) return <Loading text="加载用户列表..." />;

  return (
    <div className="mac-page">
      {/* 顶部操作：新建用户 / 人员结构 / 责任模块树 */}
      <div style={{ display: 'flex', gap: 8 }}>
        <button type="button" className="mac-btn mac-btn--primary" style={{ flex: 1 }} onClick={openCreate}>
          新建用户
        </button>
        <button
          type="button"
          className="mac-btn mac-btn--outline"
          style={{ fontWeight: 400 }}
          onClick={() => navigate('/admin/org-chart')}
        >
          人员结构
        </button>
        <button
          type="button"
          className="mac-btn mac-btn--outline"
          style={{ fontWeight: 400 }}
          onClick={() => navigate('/admin/module-tree')}
        >
          责任模块树
        </button>
      </div>

      {/* 搜索框 + 计数胶囊 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12 }}>
        <div className="mac-search mac-search--card" style={{ flex: 1 }}>
          <MacSearch size={16} />
          <input
            className="mac-search__input"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索用户名/姓名/部门…"
          />
        </div>
        <span className="mac-count-pill">{filteredUsers.length} 人</span>
      </div>

      {/* 用户卡片列表 */}
      {filteredUsers.length === 0 ? (
        <div className="mac-empty" style={{ padding: '40px 0' }}>
          {keyword ? '未找到匹配的用户' : '暂无用户，请点击"新建用户"添加'}
        </div>
      ) : (
        filteredUsers.map((user) => (
          <div
            key={user.id}
            className="mac-user-card"
            style={{ marginTop: 10 }}
            onClick={() => openDetail(user)}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div style={{ display: 'flex', gap: 12, flex: 1, minWidth: 0 }}>
                {user.avatar_resource_id ? (
                  <img
                    className="mac-user-card__avatar mac-user-card__avatar--img"
                    src={avatarUrl(user.avatar_resource_id)}
                    alt={user.name || user.username}
                  />
                ) : (
                  <span className="mac-user-card__avatar mac-user-card__avatar--initial" aria-hidden>
                    {avatarInitial(user.name, user.username)}
                  </span>
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                    <span className="mac-user-card__title">{user.name || user.username}</span>
                    {user.name && user.name !== user.username && (
                      <span className="mac-user-card__account">@{user.username}</span>
                    )}
                    <span className={`mac-chip mac-chip--tag ${user.status === 'active' ? 'mac-chip--tag-blue' : 'mac-chip--tag-muted'}`}>
                      {user.status === 'active' ? '活跃' : '未激活'}
                    </span>
                  </div>

                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                    <span className="mac-chip mac-chip--outline">{getJobLevelLabel(user.job_level)}</span>
                    {user.department && (
                      <span className="mac-chip mac-chip--dept">
                        <MacBuilding2 size={12} />
                        {user.department}
                      </span>
                    )}
                  </div>

                  {user.responsibility_modules && Object.keys(user.responsibility_modules).length > 0 && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 4 }}>
                      {Object.entries(normalizeRm(user.responsibility_modules)).map(([product, byIface]) => (
                        <span key={product} className="mac-chip mac-chip--outline">
                          {product}
                          {Object.entries(byIface).map(([iface, funcs]) =>
                            (Array.isArray(funcs) ? funcs : []).map((fn) => (
                              <span key={`${iface}-${fn}`} className="mac-chip mac-chip--soft">{fn}</span>
                            ))
                          )}
                        </span>
                      ))}
                    </div>
                  )}

                  {user.duty_text && (
                    <div className="mac-user-card__duty">
                      <span className="mac-user-card__duty-icon"><MacClipboardList size={14} /></span>
                      <span
                        style={{
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          display: '-webkit-box',
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: 'vertical',
                        }}
                      >
                        {user.duty_text}
                      </span>
                    </div>
                  )}
                </div>
              </div>

              <div className="mac-user-card__actions">
                <button
                  type="button"
                  className="mac-btn mac-btn--ghost"
                  onClick={(e) => { e.stopPropagation(); openEdit(user); }}
                >
                  编辑
                </button>
                <button
                  type="button"
                  className="mac-btn mac-btn--ghost"
                  onClick={(e) => { e.stopPropagation(); handleDelete(user); }}
                >
                  删除
                </button>
              </div>
            </div>
          </div>
        ))
      )}

      {/* 新建/编辑弹层 */}
      <Popup visible={editVisible} onClose={() => setEditVisible(false)} placement="bottom" showOverlay>
        <div className="mac-sheet" style={{ maxHeight: '85vh', overflow: 'auto' }}>
          <h4 className="mac-sheet__title" style={{ marginBottom: 4 }}>{editingUsername ? '编辑用户' : '新建用户'}</h4>

          {editLoading ? (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Loading text="加载用户信息..." />
            </div>
          ) : (
            <>
              {!editingUsername && (
                <>
                  <div className="mac-field">
                    <span className="mac-field__label">用户名</span>
                    <div className="mac-field__content">
                      <input
                        className="mac-input"
                        value={form.username}
                        onChange={(e) => setForm((p) => ({ ...p, username: e.target.value }))}
                        placeholder="登录账号"
                      />
                    </div>
                  </div>

                  <div className="mac-field">
                    <span className="mac-field__label">密码</span>
                    <div className="mac-field__content">
                      <input
                        className="mac-input"
                        type="password"
                        value={form.password}
                        onChange={(e) => setForm((p) => ({ ...p, password: e.target.value }))}
                        placeholder="初始密码"
                      />
                    </div>
                  </div>
                </>
              )}

              <div className="mac-field">
                <span className="mac-field__label">姓名</span>
                <div className="mac-field__content">
                  <input
                    className="mac-input"
                    value={form.name || ''}
                    onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                    placeholder="真实姓名"
                  />
                </div>
              </div>

              <div className="mac-field">
                <span className="mac-field__label">公司</span>
                <div className="mac-field__content">
                  <FilterableSelect
                    value={companyNameDraft}
                    onChange={handleCompanyChange}
                    options={companyNames}
                    placeholder="请选择公司"
                    title="选择公司"
                    searchPlaceholder="搜索公司…"
                  />
                </div>
              </div>

              <div className="mac-field">
                <span className="mac-field__label">部门</span>
                <div className="mac-field__content">
                  <FilterableSelect
                    value={departmentNameDraft}
                    onChange={handleDepartmentChange}
                    options={departmentNames}
                    placeholder={companyNameDraft ? '请选择部门' : '请先选择公司'}
                    title="选择部门"
                    searchPlaceholder="搜索部门…"
                  />
                </div>
              </div>

              <div className="mac-field">
                <span className="mac-field__label">职级</span>
                <div className="mac-field__content">
                  {JOB_LEVEL_OPTIONS.map((opt) => (
                    <ChoiceRow
                      key={opt.value}
                      label={opt.label}
                      checked={form.job_level === opt.value}
                      onClick={() => setForm((p) => ({ ...p, job_level: opt.value }))}
                    />
                  ))}
                </div>
              </div>

              {!editingUsername && (
                <div className="mac-field">
                  <span className="mac-field__label">状态</span>
                  <div className="mac-field__content">
                    {STATUS_OPTIONS.map((opt) => (
                      <ChoiceRow
                        key={opt.value}
                        label={opt.label}
                        checked={form.status === opt.value}
                        onClick={() => setForm((p) => ({ ...p, status: opt.value }))}
                      />
                    ))}
                  </div>
                </div>
              )}

              <div className="mac-field">
                <span className="mac-field__label">责任模块</span>
                <div className="mac-field__content">
                  {(() => {
                    const mods = form.responsibility_modules;
                    if (!mods || Object.keys(mods).length === 0) {
                      return (
                        <div className="mac-note" style={{ textAlign: 'left', marginBottom: 8 }}>
                          暂无责任模块，请在下方责任模块树中认领
                        </div>
                      );
                    }
                    return Object.entries(normalizeRm(mods)).map(([product, byIface]) => (
                      <div key={product} style={{ marginBottom: 8 }}>
                        <span className="mac-chip mac-chip--outline" style={{ marginRight: 6 }}>{product}</span>
                        {Object.entries(byIface).map(([iface, funcs]) => (
                          <span key={iface} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginRight: 6, flexWrap: 'wrap' }}>
                            <span style={{ fontSize: 12, color: 'var(--mac-muted-fg)' }}>{iface}：</span>
                            {(Array.isArray(funcs) ? funcs : []).map((fn) => (
                              <span key={fn} className="mac-chip mac-chip--soft">{fn}</span>
                            ))}
                          </span>
                        ))}
                      </div>
                    ));
                  })()}
                  <div style={{ marginTop: 4 }}>
                    <button
                      type="button"
                      className="mac-btn mac-btn--ghost"
                      onClick={() => {
                        if (editingUserId) {
                          navigate(`/admin/module-tree?user=${encodeURIComponent(editingUserId)}`);
                        } else {
                          navigate('/admin/module-tree');
                        }
                      }}
                    >
                      前往责任模块树编辑
                    </button>
                  </div>
                </div>
              </div>

              <div className="mac-field" style={{ borderBottom: 'none' }}>
                <span className="mac-field__label">职责画像</span>
                <div className="mac-field__content">
                  <textarea
                    className="mac-textarea"
                    style={{ height: 96 }}
                    value={form.duty_text || ''}
                    onChange={(e) => setForm((p) => ({ ...p, duty_text: e.target.value }))}
                    placeholder="供 AI 派单匹配参考的职责描述…"
                    maxLength={500}
                  />
                </div>
              </div>

              <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
                <button type="button" className="mac-btn mac-btn--outline mac-btn--block" onClick={() => setEditVisible(false)}>
                  取消
                </button>
                <button type="button" className="mac-btn mac-btn--primary mac-btn--block" disabled={isSaving} onClick={handleSave}>
                  {isSaving ? '保存中...' : '保存'}
                </button>
              </div>
            </>
          )}
        </div>
      </Popup>

      {/* 用户详情弹层 */}
      <Popup visible={detailVisible} onClose={() => setDetailVisible(false)} placement="bottom" showOverlay>
        <div className="mac-sheet" style={{ maxHeight: '85vh', overflow: 'auto' }}>
          <h4 className="mac-sheet__title">用户详情</h4>

          {detailLoading ? (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Loading text="加载详情..." />
            </div>
          ) : detailUser ? (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
                {detailUser.avatar_resource_id ? (
                  <img
                    className="mac-detail-avatar mac-detail-avatar--img"
                    src={avatarUrl(detailUser.avatar_resource_id)}
                    alt={detailUser.name || detailUser.username}
                  />
                ) : (
                  <span className="mac-detail-avatar mac-detail-avatar--initial" aria-hidden>
                    {avatarInitial(detailUser.name, detailUser.username)}
                  </span>
                )}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', flex: 1, minWidth: 0 }}>
                  <span className="mac-detail-name">{detailUser.name || detailUser.username}</span>
                  {detailUser.name && detailUser.name !== detailUser.username && (
                    <span className="mac-detail-account">@{detailUser.username}</span>
                  )}
                  <span className={`mac-chip mac-chip--tag ${detailUser.status === 'active' ? 'mac-chip--tag-blue' : 'mac-chip--tag-muted'}`}>
                    {detailUser.status === 'active' ? '活跃' : '未激活'}
                  </span>
                </div>
              </div>

              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 16 }}>
                <span className="mac-chip mac-chip--outline">{getJobLevelLabel(detailUser.job_level)}</span>
                {detailUser.department && (
                  <span className="mac-chip mac-chip--dept">
                    <MacBuilding2 size={12} />
                    {detailUser.department}
                  </span>
                )}
              </div>

              {detailUser.responsibility_modules && Object.keys(detailUser.responsibility_modules).length > 0 && (
                <div className="mac-detail-section">
                  <div
                    className="mac-detail-section__title"
                    style={{ alignItems: 'center', gap: 6, cursor: 'pointer', color: 'var(--mac-blue-2)' }}
                    onClick={() => detailUser.id && navigate(`/admin/module-tree?user=${encodeURIComponent(detailUser.id)}`)}
                  >
                    责任模块
                    <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--mac-muted-fg)' }}>点击编辑负责模块 ▶</span>
                  </div>
                  {Object.entries(normalizeRm(detailUser.responsibility_modules)).map(([product, byIface]) => (
                    <div key={product} className="mac-detail-panel" style={{ marginBottom: 8 }}>
                      <span className="mac-chip mac-chip--outline" style={{ marginRight: 6 }}>{product}</span>
                      {Object.entries(byIface).map(([iface, funcs]) => (
                        <div key={iface} style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 4, marginTop: 4 }}>
                          <span style={{ fontSize: 12, color: 'var(--mac-muted-fg)', whiteSpace: 'nowrap', marginRight: 2 }}>{iface}：</span>
                          {(Array.isArray(funcs) ? funcs : []).map((fn) => (
                            <span key={fn} className="mac-chip mac-chip--soft" style={{ marginRight: 4 }}>{fn}</span>
                          ))}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}

              {detailUser.duty_text && (
                <div className="mac-detail-section">
                  <div className="mac-detail-section__title">职责画像</div>
                  <div className="mac-detail-panel" style={{ fontSize: 13, color: 'var(--mac-fg)', lineHeight: 1.6 }}>
                    {detailUser.duty_text}
                  </div>
                </div>
              )}

              {(() => {
                const globalRoleIds = detailUser.roles?.['global'] ?? [];
                if (globalRoleIds.length === 0 && !canManageSystemRoles) return null;
                return (
                  <div className="mac-detail-section">
                    <div className="mac-detail-section__title">
                      全局角色
                      {canManageSystemRoles && (
                        <button type="button" className="mac-btn mac-btn--ghost" onClick={() => openGlobalRoleEditor(detailUser)}>
                          编辑
                        </button>
                      )}
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {globalRoleIds.length > 0 ? (
                        globalRoleIds.map((roleId) => (
                          <span key={roleId} className="mac-chip mac-chip--blue">
                            {roleNameMap.get(roleId) || roleId}
                          </span>
                        ))
                      ) : (
                        <span style={{ fontSize: 12, color: 'var(--mac-muted-fg)' }}>暂无全局角色</span>
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
                  <div className="mac-detail-section">
                    <div className="mac-detail-section__title">
                      项目角色
                      {canManageSystemRoles && (
                        <button type="button" className="mac-btn mac-btn--ghost" onClick={openProjectRoleEditorNew}>
                          + 添加项目
                        </button>
                      )}
                    </div>
                    {projectPermCount > 0 ? (
                      projectRolesEntries.map(([projectId, roleIds]) => (
                        <div key={projectId} className="mac-detail-panel">
                          <div className="mac-detail-panel__head">
                            <span>项目 {projectNameMap.get(projectId) || projectId}</span>
                            {canManageSystemRoles && (
                              <button type="button" className="mac-btn mac-btn--ghost" onClick={() => openProjectRoleEditor(detailUser, projectId)}>
                                编辑
                              </button>
                            )}
                          </div>
                          <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                            {roleIds.map((roleId) => (
                              <span key={roleId} className="mac-chip mac-chip--blue">
                                {roleNameMap.get(roleId) || roleId}
                              </span>
                            ))}
                          </div>
                        </div>
                      ))
                    ) : (
                      <span style={{ fontSize: 12, color: 'var(--mac-muted-fg)' }}>暂无项目角色</span>
                    )}
                  </div>
                );
              })()}

              {detailUser.permissions && detailUser.permissions.length > 0 && (
                <div className="mac-detail-section">
                  <div className="mac-detail-section__title">权限列表</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {detailUser.permissions.map((perm) => (
                      <span key={perm} className="mac-chip mac-chip--red">
                        {permNameMap.get(perm) || perm}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {detailUser.external_credentials && Object.keys(detailUser.external_credentials).length > 0 && (
                <div className="mac-detail-section">
                  <div className="mac-detail-section__title">外部凭据</div>
                  {Object.entries(detailUser.external_credentials).map(([key, cred]) => (
                    <div key={key} className="mac-detail-panel">
                      <div className="mac-detail-panel__head">{key.toUpperCase()}</div>
                      {Object.entries(cred).map(([credKey, credValue]) => (
                        <div key={credKey} className="mac-labelvalue" style={{ marginTop: 4 }}>
                          <span className="mac-labelvalue__label">{credKey}</span>
                          <span className="mac-labelvalue__value" style={{ fontFamily: 'monospace' }}>
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
            <button type="button" className="mac-btn mac-btn--outline mac-btn--block" onClick={() => setDetailVisible(false)}>
              关闭
            </button>
          </div>
        </div>
      </Popup>

      {/* 全局角色编辑弹窗：仅列出系统角色，勾选即赋予/取消全局角色 */}
      <Popup visible={globalRoleEditVisible} onClose={() => setGlobalRoleEditVisible(false)} placement="bottom" showOverlay>
        <div className="mac-sheet" style={{ maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
          <h4 className="mac-sheet__title" style={{ marginBottom: 4 }}>
            编辑全局角色：{detailUser?.name || detailUser?.username}
          </h4>
          <p className="mac-note" style={{ textAlign: 'left', marginBottom: 12 }}>
            勾选即赋予系统级全局角色，取消即移除
          </p>
          <div style={{ overflow: 'auto', flex: 1 }}>
            {systemRoles.length === 0 && (
              <div className="mac-empty">暂无可分配的系统角色</div>
            )}
            {systemRoles.map((r) => {
              const checked = globalRoleChecked.has(r.id);
              return (
                <ChoiceRow
                  key={r.id}
                  label={r.name}
                  checked={checked}
                  onClick={() =>
                    setGlobalRoleChecked((prev) => {
                      const next = new Set(prev);
                      if (next.has(r.id)) next.delete(r.id);
                      else next.add(r.id);
                      return next;
                    })
                  }
                />
              );
            })}
          </div>
          <button
            type="button"
            className="mac-btn mac-btn--primary mac-btn--block"
            style={{ marginTop: 16 }}
            disabled={globalRoleSaving}
            onClick={handleSaveGlobalRoles}
          >
            {globalRoleSaving ? '保存中...' : '保存'}
          </button>
        </div>
      </Popup>

      {/* 项目角色编辑弹窗：编辑已有项目角色（直接显示角色勾选），或添加新项目关联（需先选项目） */}
      <Popup visible={projectRoleEditVisible} onClose={() => setProjectRoleEditVisible(false)} placement="bottom" showOverlay>
        <div className="mac-sheet" style={{ maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
          <h4 className="mac-sheet__title" style={{ marginBottom: 4 }}>
            编辑项目角色：{detailUser?.name || detailUser?.username}
          </h4>
          {projectRoleSelProject ? (
            <p style={{ fontSize: 13, color: 'var(--mac-blue-2)', marginBottom: 12 }}>
              项目：{projectNameMap.get(projectRoleSelProject) || projectRoleSelProject}
            </p>
          ) : (
            <>
              <p className="mac-note" style={{ textAlign: 'left', marginBottom: 12 }}>
                选择项目后勾选角色，取消勾选即移除
              </p>
              <select
                className="mac-select"
                style={{ marginBottom: 12 }}
                value={projectRoleSelProject}
                onChange={(e) => handleProjectRoleProjectChange(e.target.value)}
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
              <div className="mac-empty">暂无可分配的项目角色</div>
            )}
            {projectRoles.map((r) => {
              const checked = projectRoleChecked.has(r.id);
              return (
                <ChoiceRow
                  key={r.id}
                  label={r.name}
                  checked={checked}
                  onClick={() =>
                    setProjectRoleChecked((prev) => {
                      const next = new Set(prev);
                      if (next.has(r.id)) next.delete(r.id);
                      else next.add(r.id);
                      return next;
                    })
                  }
                />
              );
            })}
          </div>
          <button
            type="button"
            className="mac-btn mac-btn--primary mac-btn--block"
            style={{ marginTop: 16 }}
            disabled={projectRoleSaving || !projectRoleSelProject}
            onClick={handleSaveProjectRoles}
          >
            {projectRoleSaving ? '保存中...' : '保存'}
          </button>
        </div>
      </Popup>
    </div>
  );
}
