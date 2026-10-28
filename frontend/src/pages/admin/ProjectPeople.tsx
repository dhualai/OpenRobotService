// 项目人员关联 - 按 username 构建用户树，支持长按移除
// 样式参考 macaron projects.auth 页：人员条目卡 + 幽灵按钮 + 单选行弹窗。
import { useState, useEffect, useRef } from 'react';
import { Toast, Loading, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import UserSelect from '@/shared/components/UserSelect';
import { MacSearch } from '@/shared/components/macaronIcons';
import type { UserItem } from '@/api/users';

interface Project { id?: string; code?: string; name: string; }

interface RoleItem { id: string; name: string; role_type?: string; }

interface ExistingProjectUser {
  id: string;
  name: string;
  username: string;
  roleIds: string[];
  roleNames: string[];
  reportToId?: string | null;
  uspUsername?: string;
  uspPassword?: string;
}

export default function ProjectPeople({ selectedProject }: { selectedProject: Project | null }) {
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const [associateVisible, setAssociateVisible] = useState(false);
  const [associateMode, setAssociateMode] = useState<'user' | 'dept'>('user');

  // === 按人员模式 ===
  const [associateUser, setAssociateUser] = useState<UserItem | null>(null);
  const [associateRole, setAssociateRole] = useState<string | null>(null);
  const [associateSuperiorUsername, setAssociateSuperiorUsername] = useState<string | null>(null);
  const [superiorSearch, setSuperiorSearch] = useState('');
  const [submittingAssociates, setSubmittingAssociates] = useState(false);

  // === 按组织模式 ===
  const [orgCompanies, setOrgCompanies] = useState<{ id: string; name: string; status?: string }[]>([]);
  const [orgCompaniesLoading, setOrgCompaniesLoading] = useState(false);
  const [orgDepartments, setOrgDepartments] = useState<{ id: string; name: string; status?: string }[]>([]);
  const [orgDepartmentsLoading, setOrgDepartmentsLoading] = useState(false);
  const [orgSelectedCompany, setOrgSelectedCompany] = useState<string | null>(null);
  const [orgSelectedDept, setOrgSelectedDept] = useState<string | null>(null);
  const [orgDeptMembers, setOrgDeptMembers] = useState<{ id: string; username: string; name: string; usp_configured: boolean }[]>([]);
  const [orgDeptMembersLoading, setOrgDeptMembersLoading] = useState(false);
  const [orgRole, setOrgRole] = useState<string | null>(null);

  const [roles, setRoles] = useState<RoleItem[]>([]);
  const [rolesLoading, setRolesLoading] = useState(false);

  const [existingUsers, setExistingUsers] = useState<ExistingProjectUser[]>([]);
  const [existingUsersLoading, setExistingUsersLoading] = useState(false);
  const [collapsedUsernames, setCollapsedUsernames] = useState<Set<string>>(new Set());
  const [contextMenu, setContextMenu] = useState<{ username: string; x: number; y: number } | null>(null);
  const [removingUsername, setRemovingUsername] = useState<string | null>(null);
  const longPressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchRoles = async () => {
    setRolesLoading(true);
    try {
      const data = await request<RoleItem[]>('/roles/');
      setRoles(normalizeList<RoleItem>(data).filter((r) => r.role_type === 'project'));
    } catch (err) {
      Toast({ message: `加载角色列表失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setRolesLoading(false);
    }
  };

  // === 按组织模式：拉公司/部门/部门成员 ===
  const fetchOrgOptions = async () => {
    setOrgCompaniesLoading(true);
    setOrgDepartmentsLoading(true);
    try {
      const data = await request<{
        companies: { id: string; name: string; status?: string }[];
        departments_by_company: Record<string, { id: string; name: string; status?: string }[]>;
      }>('/options');
      const comps = normalizeList(data.companies).filter((c) => c.status !== 'rejected');
      setOrgCompanies(comps);
      // 扁平化 departments_by_company
      const allDepts: { id: string; name: string; status?: string; _companyName: string }[] = [];
      Object.entries(data.departments_by_company || {}).forEach(([compName, depts]) => {
        for (const d of normalizeList(depts)) {
          allDepts.push({ ...d, _companyName: compName });
        }
      });
      const approvedDepts = allDepts.filter((d) => d.status !== 'rejected');
      // 记录完整列表（含 _companyName），供公司切换时筛选
      (approvedDepts as { _companyName: string }[]).forEach((d) => d);
      setOrgDepartments(approvedDepts);
    } catch (err) {
      Toast({ message: `加载组织选项失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setOrgCompaniesLoading(false);
      setOrgDepartmentsLoading(false);
    }
  };

  // 部门下拉 —— 当前选中公司下的部门
  const visibleDepartments = orgSelectedCompany
    ? (orgDepartments as { id: string; name: string; status?: string; _companyName?: string }[])
        .filter((d) => d._companyName === orgCompanies.find((c) => c.id === orgSelectedCompany)?.name)
    : orgDepartments;

  const fetchDeptMembers = async (deptId: string) => {
    setOrgDeptMembersLoading(true);
    setOrgDeptMembers([]);
    try {
      const rows = await request<{ id: string; username: string; name: string; usp_configured: boolean }[]>(
        `/users/by-department?department_id=${encodeURIComponent(deptId)}`,
      );
      setOrgDeptMembers(normalizeList(rows));
    } catch (err) {
      Toast({ message: `加载部门成员失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setOrgDeptMembersLoading(false);
    }
  };

  // USP 已配置可添加；未配置跳过
  const uspReadyMembers = orgDeptMembers.filter((m) => m.usp_configured);
  const uspMissingCount = orgDeptMembers.length - uspReadyMembers.length;

  const fetchExistingUsers = async (project: Project) => {
    if (!project.id) { setExistingUsers([]); return; }
    setExistingUsersLoading(true);
    try {
      type MemberRow = {
        user_id: string; username: string; name?: string | null;
        role_id: string; role_name: string; report_to_id?: string | null;
        external_credentials?: { usp?: { username?: string; password?: string } } | unknown[] | null;
      };
      const rows = await request<MemberRow[]>(`/projects/${encodeURIComponent(project.id)}/members?include_usp=true`);
      const list = normalizeList<MemberRow>(rows);
      const byUsername = new Map<string, ExistingProjectUser>();
      for (const r of list) {
        // 提取 USP 账号/密码：后端 external_credentials 为空时可能返回 []（兼容旧实现）
        const ec = r.external_credentials;
        const usp = (ec && typeof ec === 'object' && !Array.isArray(ec))
          ? (ec as { usp?: { username?: string; password?: string } }).usp
          : undefined;
        const uspUsername = usp?.username || '';
        const uspPassword = usp?.password || '';

        const existing = byUsername.get(r.username);
        if (existing) {
          if (!existing.roleIds.includes(r.role_id)) {
            existing.roleIds.push(r.role_id);
            existing.roleNames.push(r.role_name);
          }
          if (!existing.reportToId && r.report_to_id) {
            existing.reportToId = r.report_to_id;
          }
          // USP 字段以非空值优先（同一用户多角色行，任一行带 USP 即视为已配置）
          if (!existing.uspUsername && uspUsername) existing.uspUsername = uspUsername;
          if (!existing.uspPassword && uspPassword) existing.uspPassword = uspPassword;
        } else {
          byUsername.set(r.username, {
            id: r.user_id,
            name: r.name || r.username,
            username: r.username,
            roleIds: [r.role_id],
            roleNames: [r.role_name],
            reportToId: r.report_to_id || null,
            uspUsername,
            uspPassword,
          });
        }
      }
      setExistingUsers(Array.from(byUsername.values()));
    } catch {
      setExistingUsers([]);
    } finally {
      setExistingUsersLoading(false);
    }
  };

  // 当外部 selectedProject 变化时，加载该项目的关联人员
  useEffect(() => {
    if (selectedProject) fetchExistingUsers(selectedProject);
  }, [selectedProject?.id]);

  const buildExistingUserTree = (list: ExistingProjectUser[]) => {
    const idSet = new Set(list.map((u) => u.id));
    const childrenMap = new Map<string, ExistingProjectUser[]>();
    const roots: ExistingProjectUser[] = [];
    list.forEach((u) => {
      const parent = u.reportToId;
      if (parent && idSet.has(parent)) {
        const siblings = childrenMap.get(parent) || [];
        siblings.push(u);
        childrenMap.set(parent, siblings);
      } else {
        roots.push(u);
      }
    });
    return { roots, childrenMap };
  };

  const handleRemoveExistingUser = async (username: string) => {
    if (!selectedProject?.id) { Toast({ message: '当前项目缺少 id', theme: 'warning' }); return; }
    const user = existingUsers.find((u) => u.username === username);
    if (!user) return;
    setRemovingUsername(username);
    try {
      for (const roleId of user.roleIds) {
        const qs = new URLSearchParams({
          user_id: user.id,
          project_id: selectedProject.id,
          role_id: roleId,
        }).toString();
        await request(`/users/project/role?${qs}`, { method: 'DELETE' });
      }
      Toast({ message: '已移除人员', theme: 'success' });
      await fetchExistingUsers(selectedProject);
    } catch (err) {
      Toast({ message: `移除失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setRemovingUsername(null);
      setContextMenu(null);
    }
  };

  const beginLongPress = (x: number, y: number, username: string) => {
    if (longPressTimer.current) clearTimeout(longPressTimer.current);
    longPressTimer.current = setTimeout(() => {
      setContextMenu({ username, x, y });
    }, 500);
  };
  const cancelLongPress = () => {
    if (longPressTimer.current) { clearTimeout(longPressTimer.current); longPressTimer.current = null; }
  };

  const openAssociate = (mode: 'user' | 'dept' = 'user') => {
    if (!selectedProject) {
      Toast({ message: '请先选择一个项目', theme: 'warning' });
      return;
    }
    setAssociateMode(mode);
    // 重置「按人员」
    setAssociateUser(null);
    setAssociateRole(null);
    setAssociateSuperiorUsername(null);
    setSuperiorSearch('');
    // 重置「按组织」
    setOrgSelectedCompany(null);
    setOrgSelectedDept(null);
    setOrgDeptMembers([]);
    setOrgRole(null);

    setAssociateVisible(true);
    if (roles.length === 0) fetchRoles();
    if (mode === 'dept' && orgCompanies.length === 0) fetchOrgOptions();
  };

  // === 保存：按组织批量添加 ===
  const handleSaveDeptAssociate = async () => {
    if (!selectedProject) { Toast({ message: '请先选择一个项目', theme: 'warning' }); return; }
    if (!selectedProject.id) { Toast({ message: '当前项目缺少 id，无法提交', theme: 'warning' }); return; }
    if (!orgSelectedCompany) { Toast({ message: '请选择公司', theme: 'warning' }); return; }
    if (!orgSelectedDept) { Toast({ message: '请选择部门', theme: 'warning' }); return; }
    if (!orgRole) { Toast({ message: '请选择角色', theme: 'warning' }); return; }
    if (uspReadyMembers.length === 0) { Toast({ message: '所选部门内没有已配置 USP 的成员可添加', theme: 'warning' }); return; }

    setSubmittingAssociates(true);
    try {
      const payload = uspReadyMembers.map((m) => ({
        user_name: m.username,
        role_id: orgRole,
      }));
      await request('/users/project/assign-roles', {
        method: 'POST',
        body: JSON.stringify({
          project_id: selectedProject.id,
          organization_ids: payload,
        }),
      });
      Toast({
        message: `已提交 ${uspReadyMembers.length} 人${uspMissingCount > 0 ? `（跳过 ${uspMissingCount} 人未配置 USP）` : ''}`,
        theme: 'success',
      });
      setAssociateVisible(false);
      await fetchExistingUsers(selectedProject);
    } catch (err) {
      Toast({ message: `批量添加失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmittingAssociates(false);
    }
  };

  const handleSaveAssociate = async () => {
    if (!selectedProject) { Toast({ message: '请先选择一个项目', theme: 'warning' }); return; }
    if (!selectedProject.id) { Toast({ message: '当前项目缺少 id，无法提交', theme: 'warning' }); return; }
    if (!associateUser) { Toast({ message: '请选择用户', theme: 'warning' }); return; }
    if (!associateRole) { Toast({ message: '请选择角色', theme: 'warning' }); return; }

    // 拦截：USP 账户或密码为空时不允许添加（导出人员授权包会缺失该用户）
    // 接口 _mask_usp_password 已将已设置密码掩码为 "-"，故 password === "" 即代表未设置
    try {
      const detail = await request<{
        external_credentials?: { usp?: { username?: string; password?: string } };
      }>(`/users/${encodeURIComponent(associateUser.username)}/detail`);
      const usp = detail?.external_credentials?.usp || {};
      const uspUsername = (usp.username || '').trim();
      const uspPassword = usp.password || '';
      if (!uspUsername) {
        Toast({ message: `用户「${associateUser.name || associateUser.username}」未配置 USP 账户，请先在用户管理中设置后再添加`, theme: 'warning' });
        return;
      }
      if (!uspPassword) {
        Toast({ message: `用户「${associateUser.name || associateUser.username}」未配置 USP 密码，请先在用户管理中设置后再添加`, theme: 'warning' });
        return;
      }
    } catch (err) {
      Toast({ message: `校验 USP 信息失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      return;
    }

    const payload: Record<string, string> = {
      user_name: associateUser.username,
      role_id: associateRole,
    };
    if (associateSuperiorUsername) payload.report_to_id = associateSuperiorUsername;

    setSubmittingAssociates(true);
    try {
      await request('/users/project/assign-roles', {
        method: 'POST',
        body: JSON.stringify({
          project_id: selectedProject.id,
          organization_ids: [payload],
        }),
      });
      Toast({ message: '已添加关联人员', theme: 'success' });
      setAssociateVisible(false);
      // await 刷新：保存后立即拉取最新已关联人员，连续添加时上级候选也能即时看到上一个添加的人员
      await fetchExistingUsers(selectedProject);
    } catch (err) {
      Toast({ message: `添加失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmittingAssociates(false);
    }
  };

  // 上级人员候选 = 已关联人员（按 username 去重，排除当前正要添加的用户自身）
  const superiorCandidates = (() => {
    const map = new Map<string, { username: string; label: string }>();
    existingUsers.forEach((u) => {
      map.set(u.username, { username: u.username, label: `${u.name}${u.roleNames.length ? ' - ' + u.roleNames.join('/') : ''}` });
    });
    return Array.from(map.values()).filter((c) => c.username !== associateUser?.username);
  })();
  // 按搜索关键词（姓名/用户名，不区分大小写）模糊过滤
  const filteredSuperiorCandidates = superiorCandidates.filter((c) => {
    const q = superiorSearch.trim().toLowerCase();
    if (!q) return true;
    return c.label.toLowerCase().includes(q) || c.username.toLowerCase().includes(q);
  });

  if (!selectedProject) {
    return <div className="mac-empty">请先选择项目</div>;
  }

  return (
    <div>
      {/* 已关联人员（含添加入口） */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--mac-fg)' }}>已关联人员</span>
        <span style={{ fontSize: 11, color: 'var(--mac-muted-fg)' }}>长按卡片可移除</span>
        <button
          type="button"
          className="mac-btn mac-btn--ghost"
          style={{ marginLeft: 'auto', marginRight: 6 }}
          onClick={() => openAssociate('user')}
        >
          + 添加关联人员
        </button>
        <button
          type="button"
          className="mac-btn mac-btn--ghost"
          onClick={() => openAssociate('dept')}
        >
          + 按组织添加
        </button>
      </div>
      {existingUsersLoading ? (
        <Loading text="加载中..." />
      ) : existingUsers.length === 0 ? (
        <div style={{ fontSize: 13, color: 'var(--mac-muted-fg)', padding: '8px 0' }}>该项目暂无已关联人员</div>
      ) : (
        (() => {
          const { roots, childrenMap } = buildExistingUserTree(existingUsers);
          const renderNode = (u: ExistingProjectUser, depth: number) => {
            const children = childrenMap.get(u.id) || [];
            const collapsed = collapsedUsernames.has(u.username);
            const roleNames = u.roleNames.join('、');
            return (
              <div key={u.username}>
                <div
                  className="mac-item"
                  style={{
                    marginBottom: 8, marginLeft: depth * 20,
                    background: removingUsername === u.username ? '#fbecec' : undefined,
                    userSelect: 'none', WebkitUserSelect: 'none',
                  }}
                  onTouchStart={(e) => beginLongPress(e.touches[0].clientX, e.touches[0].clientY, u.username)}
                  onTouchEnd={cancelLongPress}
                  onTouchMove={cancelLongPress}
                  onMouseDown={(e) => { if (e.button === 0) beginLongPress(e.clientX, e.clientY, u.username); }}
                  onMouseUp={cancelLongPress}
                  onMouseLeave={cancelLongPress}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                    <div style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--mac-fg)' }}>
                      {depth > 0 && <span style={{ color: '#bbb', marginRight: 4 }}>└</span>}
                      {u.name}
                      {children.length > 0 && (
                        <span
                          onClick={(e) => {
                            e.stopPropagation();
                            setCollapsedUsernames((prev) => {
                              const next = new Set(prev);
                              if (next.has(u.username)) next.delete(u.username); else next.add(u.username);
                              return next;
                            });
                          }}
                          style={{ marginLeft: 8, fontSize: 12, color: 'var(--mac-blue-2)', cursor: 'pointer' }}
                        >
                          {collapsed ? `展开(${children.length})` : '收起'}
                        </span>
                      )}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--mac-muted-fg)' }}>{u.username} · {roleNames}</div>
                  </div>
                  {(!u.uspUsername || !u.uspPassword) && (
                    <div style={{ marginTop: 4, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {!u.uspUsername && (
                        <span style={{ fontSize: 10.5, color: '#b3542a', background: '#fbe9df', borderRadius: 4, padding: '1px 6px' }}>
                          USP账户未设置
                        </span>
                      )}
                      {!u.uspPassword && (
                        <span style={{ fontSize: 10.5, color: '#b3542a', background: '#fbe9df', borderRadius: 4, padding: '1px 6px' }}>
                          USP账户密码未设置
                        </span>
                      )}
                    </div>
                  )}
                </div>
                {!collapsed && children.map((c) => renderNode(c, depth + 1))}
              </div>
            );
          };
          return roots.map((root) => renderNode(root, 0));
        })()
      )}

      {/* 添加关联人员弹窗 */}
      <Popup visible={associateVisible} onClose={() => setAssociateVisible(false)} placement="bottom" showOverlay>
        <div className="mac-sheet" style={{ maxHeight: '75vh', display: 'flex', flexDirection: 'column', paddingBottom: 12 }}>
          {/* 可滚动内容区 */}
          <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
            <h4 className="mac-sheet__title" style={{ marginBottom: 4 }}>添加关联人员</h4>
            <div style={{ fontSize: 12, color: 'var(--mac-muted-fg)', marginBottom: 12 }}>
              项目：{selectedProject?.name}
            </div>

            {/* 模式切换 Tab */}
            <div
              style={{
                display: 'flex', background: '#f2f3f5', borderRadius: 8, padding: 3, marginBottom: 16,
              }}
            >
              {(['user', 'dept'] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setAssociateMode(mode)}
                  style={{
                    flex: 1, padding: '6px 0', fontSize: 13, fontWeight: 600,
                    background: associateMode === mode ? '#fff' : 'transparent',
                    borderRadius: 6, border: 'none', cursor: 'pointer',
                    color: associateMode === mode ? 'var(--mac-fg)' : 'var(--mac-muted-fg)',
                    boxShadow: associateMode === mode ? '0 1px 2px rgba(0,0,0,0.08)' : 'none',
                  }}
                >
                  {mode === 'user' ? '按人员' : '按组织'}
                </button>
              ))}
            </div>

            {/* ===== 按人员模式 ===== */}
            {associateMode === 'user' && (
              <>
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8, color: 'var(--mac-fg)' }}>选择用户</div>
                <div style={{ marginBottom: 20 }}>
                  <UserSelect
                    value={associateUser?.id}
                    onChange={setAssociateUser}
                    placeholder="请选择用户"
                    title="选择用户"
                  />
                </div>

                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8, color: 'var(--mac-fg)' }}>选择角色</div>
                <div style={{ marginBottom: 20 }}>
                  {rolesLoading ? (
                    <Loading text="加载角色..." />
                  ) : roles.length === 0 ? (
                    <div style={{ padding: '10px 0', color: 'var(--mac-muted-fg)', fontSize: 13 }}>暂无可选角色，请先在角色管理中创建</div>
                  ) : (
                    roles.map((role) => (
                      <div
                        key={role.id}
                        className={`mac-radio ${associateRole === role.id ? 'is-active' : ''}`}
                        onClick={() => setAssociateRole(role.id)}
                      >
                        <span className="mac-radio__dot">
                          {associateRole === role.id && <span className="mac-radio__inner" />}
                        </span>
                        <span className="mac-radio__label">{role.name}</span>
                      </div>
                    ))
                  )}
                </div>

                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8, color: 'var(--mac-fg)' }}>上级人员（可选）</div>
                <div className="mac-search" style={{ marginBottom: 8 }}>
                  <MacSearch size={16} />
                  <input
                    className="mac-search__input"
                    value={superiorSearch}
                    onChange={(e) => setSuperiorSearch(e.target.value)}
                    placeholder="搜索人员名称 / 用户名"
                  />
                </div>
                <div style={{ marginBottom: 20 }}>
                  <div
                    className={`mac-radio ${!associateSuperiorUsername ? 'is-active' : ''}`}
                    onClick={() => setAssociateSuperiorUsername(null)}
                  >
                    <span className="mac-radio__dot">
                      {!associateSuperiorUsername && <span className="mac-radio__inner" />}
                    </span>
                    <span className="mac-radio__label">无（顶层）</span>
                  </div>
                  {superiorCandidates.length === 0 ? (
                    <div style={{ padding: '10px 0', color: 'var(--mac-muted-fg)', fontSize: 13 }}>暂无已添加人员可选为上级</div>
                  ) : filteredSuperiorCandidates.length === 0 ? (
                    <div style={{ padding: '10px 0', color: 'var(--mac-muted-fg)', fontSize: 13 }}>未找到匹配「{superiorSearch.trim()}」的人员</div>
                  ) : (
                    filteredSuperiorCandidates.map((c) => (
                      <div
                        key={c.username}
                        className={`mac-radio ${associateSuperiorUsername === c.username ? 'is-active' : ''}`}
                        onClick={() => setAssociateSuperiorUsername(c.username)}
                      >
                        <span className="mac-radio__dot">
                          {associateSuperiorUsername === c.username && <span className="mac-radio__inner" />}
                        </span>
                        <span className="mac-radio__label">{c.label}</span>
                      </div>
                    ))
                  )}
                </div>
              </>
            )}

            {/* ===== 按组织模式 ===== */}
            {associateMode === 'dept' && (
              <>
                {/* 公司选择 */}
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8, color: 'var(--mac-fg)' }}>选择公司</div>
                <div style={{ marginBottom: 16 }}>
                  {orgCompaniesLoading ? (
                    <Loading text="加载公司..." />
                  ) : orgCompanies.length === 0 ? (
                    <div style={{ padding: '10px 0', color: 'var(--mac-muted-fg)', fontSize: 13 }}>暂无已审核公司</div>
                  ) : (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {orgCompanies.map((c) => (
                        <button
                          key={c.id}
                          type="button"
                          onClick={() => {
                            setOrgSelectedCompany(c.id);
                            setOrgSelectedDept(null);
                            setOrgDeptMembers([]);
                          }}
                          style={{
                            padding: '5px 12px', fontSize: 12.5,
                            background: orgSelectedCompany === c.id ? 'var(--mac-blue, #3a7afe)' : '#f2f3f5',
                            color: orgSelectedCompany === c.id ? '#fff' : 'var(--mac-fg)',
                            borderRadius: 16, border: 'none', cursor: 'pointer', fontWeight: 500,
                          }}
                        >
                          {c.name}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {/* 部门选择 */}
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8, color: 'var(--mac-fg)' }}>选择部门</div>
                <div style={{ marginBottom: 16 }}>
                  {orgDepartmentsLoading ? (
                    <Loading text="加载部门..." />
                  ) : visibleDepartments.length === 0 ? (
                    <div style={{ padding: '10px 0', color: 'var(--mac-muted-fg)', fontSize: 13 }}>
                      {orgSelectedCompany ? '该公司暂无已审核部门' : '请先选择公司'}
                    </div>
                  ) : (
                    visibleDepartments.map((d) => (
                      <div
                        key={d.id}
                        className={`mac-radio ${orgSelectedDept === d.id ? 'is-active' : ''}`}
                        onClick={() => {
                          setOrgSelectedDept(d.id);
                          fetchDeptMembers(d.id);
                        }}
                      >
                        <span className="mac-radio__dot">
                          {orgSelectedDept === d.id && <span className="mac-radio__inner" />}
                        </span>
                        <span className="mac-radio__label">{d.name}</span>
                      </div>
                    ))
                  )}
                </div>

                {/* 部门成员预览 */}
                {orgSelectedDept && (
                  <>
                    <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8, color: 'var(--mac-fg)' }}>
                      部门成员（{orgDeptMembers.length}）
                    </div>
                    <div style={{ marginBottom: 16 }}>
                      {orgDeptMembersLoading ? (
                        <Loading text="加载成员..." />
                      ) : orgDeptMembers.length === 0 ? (
                        <div style={{ padding: '10px 0', color: 'var(--mac-muted-fg)', fontSize: 13 }}>该部门下暂无成员</div>
                      ) : (
                        <>
                          {uspMissingCount > 0 && (
                            <div style={{ fontSize: 11.5, color: '#b3542a', background: '#fbe9df', borderRadius: 6, padding: '6px 10px', marginBottom: 10 }}>
                              共 {orgDeptMembers.length} 人，其中 {uspReadyMembers.length} 人将被添加，{uspMissingCount} 人未配置 USP 自动跳过
                            </div>
                          )}
                          {orgDeptMembers.map((m) => (
                            <div
                              key={m.id}
                              style={{
                                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                                padding: '6px 0', borderBottom: '1px solid #f2f3f5', fontSize: 13,
                              }}
                            >
                              <span style={{ fontWeight: 500 }}>{m.name}</span>
                              <span style={{ fontSize: 11, color: m.usp_configured ? '#2a9d5c' : '#b3542a' }}>
                                {m.usp_configured ? '✓ USP 已配置' : '⚠ USP 未配置'}
                              </span>
                            </div>
                          ))}
                        </>
                      )}
                    </div>
                  </>
                )}

                {/* 角色选择 */}
                {orgSelectedDept && uspReadyMembers.length > 0 && (
                  <>
                    <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8, color: 'var(--mac-fg)' }}>选择角色（应用到全部 {uspReadyMembers.length} 人）</div>
                    <div style={{ marginBottom: 20 }}>
                      {rolesLoading ? (
                        <Loading text="加载角色..." />
                      ) : roles.length === 0 ? (
                        <div style={{ padding: '10px 0', color: 'var(--mac-muted-fg)', fontSize: 13 }}>暂无可选角色</div>
                      ) : (
                        roles.map((role) => (
                          <div
                            key={role.id}
                            className={`mac-radio ${orgRole === role.id ? 'is-active' : ''}`}
                            onClick={() => setOrgRole(role.id)}
                          >
                            <span className="mac-radio__dot">
                              {orgRole === role.id && <span className="mac-radio__inner" />}
                            </span>
                            <span className="mac-radio__label">{role.name}</span>
                          </div>
                        ))
                      )}
                    </div>
                  </>
                )}
              </>
            )}
          </div>

          {/* 底部固定操作区 */}
          <div
            style={{
              display: 'flex', gap: 8, paddingTop: 12, marginTop: 4,
              borderTop: '1px solid rgba(232, 234, 234, 0.6)', background: '#fff',
            }}
          >
            <button type="button" className="mac-btn mac-btn--outline mac-btn--block" onClick={() => setAssociateVisible(false)}>取消</button>
            <button
              type="button"
              className="mac-btn mac-btn--primary mac-btn--block"
              disabled={submittingAssociates}
              onClick={associateMode === 'user' ? handleSaveAssociate : handleSaveDeptAssociate}
            >
              {submittingAssociates
                ? '保存中...'
                : associateMode === 'user'
                  ? '保存'
                  : `批量添加${orgSelectedDept && uspReadyMembers.length > 0 ? ` (${uspReadyMembers.length})` : ''}`}
            </button>
          </div>
        </div>
      </Popup>

      {/* 长按移除菜单 */}
      {contextMenu && (
        <>
          <div
            onClick={() => setContextMenu(null)}
            onContextMenu={(e) => { e.preventDefault(); setContextMenu(null); }}
            style={{ position: 'fixed', inset: 0, zIndex: 1000 }}
          />
          <div
            style={{
              position: 'fixed',
              left: Math.min(contextMenu.x, window.innerWidth - 140),
              top: Math.min(contextMenu.y, window.innerHeight - 60),
              zIndex: 1001,
              background: '#fff', borderRadius: 13, padding: 4, minWidth: 120,
              boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
            }}
          >
            <div
              onClick={() => handleRemoveExistingUser(contextMenu.username)}
              style={{
                padding: '8px 12px', fontSize: 14, color: 'var(--mac-fg)',
                cursor: 'pointer', borderRadius: 9, userSelect: 'none',
              }}
            >
              {removingUsername === contextMenu.username ? '移除中...' : '移除人员'}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
