// 项目授权管理 - 基于接口文档 GET /api/admin/projects/licenses/{project_code}
import { useState, useEffect } from 'react';
import { Button, Toast, Loading, Dialog, Input, Popup, DateTimePicker } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import UserSelect from '@/shared/components/UserSelect';
import type { UserItem } from '@/api/users';

interface AuthItem {
  id: string;
  project_code: string;
  machine_code?: string;
  apply_time: string;
  expire_time: string;
  license_code: string;
  applicant: string;
  max_vehicles?: number | null;
}
interface Project { id?: string; code?: string; name: string; }

const maskCode = (code: string): string => {
  if (!code) return '';
  return code.length > 10 ? `${code.slice(0, 10)}...` : code;
};

const handleCopyCode = async (text: string) => {
  if (!text) return;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
    }
    Toast({ message: '已复制', theme: 'success' });
  } catch {
    Toast({ message: '复制失败，请手动复制', theme: 'error' });
  }
};

const todayStr = (): string => {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};

interface RoleItem { id: string; name: string; role_type?: string; }

interface AssociateItem {
  id: string;            // 本地临时 id（仅用于 React key 与本地移除）
  userId: string;        // 用户 id（对应后端 report_to_id）
  username: string;      // 登录名（对应后端 user_name）
  userName: string;      // 显示名
  roleId: string;        // 角色 id（对应后端 role_id）
  roleName: string;      // 角色名（仅用于显示）
  superiorId?: string | null; // 上级的 userId（对应后端 report_to_id）
}

interface ExistingProjectUser {
  id: string;            // = userId
  name: string;
  username: string;
  roleIds: string[];
  reportToId?: string | null; // 在该项目下的汇报人 userId
}

export default function ProjectAuth() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [projectSearch, setProjectSearch] = useState('');
  const [items, setItems] = useState<AuthItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [projectLoading, setProjectLoading] = useState(true);
  const [projectPickerVisible, setProjectPickerVisible] = useState(false);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  // 添加关联人员弹窗：用户列表接入真实的可指派人员接口 GET /api/tasks/assignable-users
  const [associateVisible, setAssociateVisible] = useState(false);
  const [associateUser, setAssociateUser] = useState<UserItem | null>(null);
  const [associateRole, setAssociateRole] = useState<string | null>(null);
  const [associateSuperiorId, setAssociateSuperiorId] = useState<string | null>(null);
  const [associateList, setAssociateList] = useState<AssociateItem[]>([]);
  const [submittingAssociates, setSubmittingAssociates] = useState(false);

  // 角色列表接入角色管理接口 GET /api/admin/roles/
  const [roles, setRoles] = useState<RoleItem[]>([]);
  const [rolesLoading, setRolesLoading] = useState(false);

  // 项目已关联人员
  const [existingUsers, setExistingUsers] = useState<ExistingProjectUser[]>([]);
  const [existingUsersLoading, setExistingUsersLoading] = useState(false);
  // 已关联人员树：折叠状态 + 右键移除菜单
  const [collapsedUserIds, setCollapsedUserIds] = useState<Set<string>>(new Set());
  const [contextMenu, setContextMenu] = useState<{ userId: string; x: number; y: number } | null>(null);
  const [removingUserId, setRemovingUserId] = useState<string | null>(null);

  // 申请授权码：机器码 + 开始/结束日期 + 允许最大车数，调用 POST /export/apply_project_license（经 MQTT 审批）
  const [machineCode, setMachineCode] = useState('');
  const [licenseStartDate, setLicenseStartDate] = useState(todayStr());
  const [licenseEndDate, setLicenseEndDate] = useState(todayStr());
  const [maxVehicles, setMaxVehicles] = useState('');
  const [startDatePickerVisible, setStartDatePickerVisible] = useState(false);
  const [endDatePickerVisible, setEndDatePickerVisible] = useState(false);
  const [applyingLicense, setApplyingLicense] = useState(false);

  // 加载项目列表供选择
  useEffect(() => {
    request('/projects/')
      .then((data) => setProjects(normalizeList<Project>(data)))
      .catch((err) => Toast({ message: `加载项目失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }))
      .finally(() => setProjectLoading(false));
  }, []);

  // 根据选中的项目代码获取授权信息（传 type=all 获取全部授权记录）
  const fetchLicenses = async (projectCode: string) => {
    if (!projectCode) return;
    setLoading(true);
    try {
      const data = await request(`/projects/licenses/${encodeURIComponent(projectCode)}?type=all`);
      setItems(normalizeList<AuthItem>(data));
    } catch (err) {
      Toast({ message: `加载授权失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      setItems([]);
    } finally { setLoading(false); }
  };

  // 加载角色列表供关联人员选择角色使用（仅项目角色，系统权限角色不应出现在此选择器）
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

  // 加载项目已关联的人员：基于后端 project_role_relations（含 report_to_id）按 project.id 筛选
  const fetchExistingUsers = async (project: Project) => {
    setExistingUsersLoading(true);
    try {
      const [usersData, rolesData] = await Promise.all([
        request<{
          id: string;
          username: string;
          name?: string | null;
          project_role_relations?: Array<{ project_id: string; role_id: string; report_to_id?: string | null }>;
        }[]>('/users/?limit=1000'),
        request<RoleItem[]>('/roles/'),
      ]);
      const allUsers = normalizeList<{
        id: string;
        username: string;
        name?: string | null;
        project_role_relations?: Array<{ project_id: string; role_id: string; report_to_id?: string | null }>;
      }>(usersData);
      const allRoles = normalizeList<RoleItem>(rolesData);
      setRoles((prev) => (prev.length === 0 ? allRoles.filter((r) => r.role_type === 'project') : prev));

      const pid = project.id;
      const matched: ExistingProjectUser[] = [];
      for (const u of allUsers) {
        const rels = u.project_role_relations || [];
        const projRels = pid ? rels.filter((r) => r.project_id === pid) : [];
        if (projRels.length === 0) continue;
        matched.push({
          id: u.id,
          name: u.name || u.username,
          username: u.username,
          roleIds: projRels.map((r) => r.role_id),
          reportToId: projRels.find((r) => r.report_to_id)?.report_to_id || null,
        });
      }
      setExistingUsers(matched);
    } catch {
      setExistingUsers([]);
    } finally {
      setExistingUsersLoading(false);
    }
  };

  // 按汇报关系（reportToId）构建已关联人员树：上级不在本项目内则作为根节点
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

  // 移除人员：删除该用户在当前项目下的所有角色绑定（逐条调用 DELETE /users/project/role）
  const handleRemoveExistingUser = async (userId: string) => {
    if (!selectedProject?.id) { Toast({ message: '当前项目缺少 id', theme: 'warning' }); return; }
    const user = existingUsers.find((u) => u.id === userId);
    if (!user) return;
    setRemovingUserId(userId);
    try {
      for (const roleId of user.roleIds) {
        const qs = new URLSearchParams({
          user_id: userId,
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
      setRemovingUserId(null);
      setContextMenu(null);
    }
  };

  const handleProjectSelect = (project: Project) => {
    setSelectedProject(project);
    setProjectSearch('');
    setProjectPickerVisible(false);
    const code = project.code || project.name;
    fetchLicenses(code);
    fetchExistingUsers(project);
  };

  // 模糊匹配：按名称或代码任意关键词片段过滤（不要求项目代码，普通人记不住代码）
  const filteredProjects = projectSearch.trim()
    ? projects.filter((p) => {
        const kw = projectSearch.trim().toLowerCase();
        return p.name.toLowerCase().includes(kw) || (p.code || '').toLowerCase().includes(kw);
      })
    : projects;

  // 申请授权码：机器码 + 开始/结束日期 → 提交给后端，后端通过 MQTT 向设备端申请审批（最长约60秒）
  const handleApplyLicense = async () => {
    if (!selectedProject) { Toast({ message: '请先选择一个项目', theme: 'warning' }); return; }
    if (!machineCode.trim()) { Toast({ message: '请输入机器码', theme: 'warning' }); return; }
    if (!licenseStartDate || !licenseEndDate) { Toast({ message: '请选择开始和结束日期', theme: 'warning' }); return; }
    if (licenseStartDate > licenseEndDate) { Toast({ message: '开始日期不能晚于结束日期', theme: 'warning' }); return; }

    const projectCode = selectedProject.code || selectedProject.name;
    setApplyingLicense(true);
    try {
      const status = await request<{ status?: string; message?: string; license_content?: string }>(
        '/export/apply_project_license',
        {
          method: 'POST',
          body: JSON.stringify({
            project_code: projectCode,
            mac: machineCode.trim(),
            start_date: licenseStartDate,
            end_date: licenseEndDate,
            max_vehicles: maxVehicles.trim() ? Number(maxVehicles.trim()) : null,
          }),
          timeout: 65000, // 后端需等待 MQTT 审批结果，最长约 60 秒，长于默认的 30 秒超时
        },
      );

      if (status?.status === 'approved') {
        Toast({ message: `授权码申请成功：${status.license_content || ''}`, theme: 'success' });
        setMachineCode('');
        setLicenseStartDate(todayStr());
        setLicenseEndDate(todayStr());
        setMaxVehicles('');
        fetchLicenses(projectCode);
      } else if (status?.status === 'rejected') {
        Toast({ message: `申请被拒绝${status.message ? '：' + status.message : ''}`, theme: 'error' });
      } else {
        Toast({ message: status?.message || '申请未获批准，请稍后重试', theme: 'error' });
      }
    } catch (err) {
      Toast({ message: `申请授权码失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setApplyingLicense(false);
    }
  };

  const handleRevoke = (item: AuthItem) => {
    Dialog.confirm?.({
      title: '确认撤销',
      content: '确定要撤销此授权吗？',
      onConfirm: async () => {
        try {
          // 注意：接口文档暂无删除授权的独立端点，使用原有路径
          await request(`/project/auth/${item.id}`, { method: 'DELETE' });
          Toast({ message: '授权已撤销', theme: 'success' });
          if (selectedProject) {
            const code = selectedProject.code || selectedProject.name;
            fetchLicenses(code);
          }
        } catch (err) {
          Toast({ message: `撤销失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        }
      },
    });
  };

  // 打开添加关联人员弹窗
  const openAssociate = () => {
    if (!selectedProject) {
      Toast({ message: '请先选择一个项目', theme: 'warning' });
      return;
    }
    setAssociateUser(null);
    setAssociateRole(null);
    setAssociateSuperiorId(null);
    setAssociateVisible(true);
    if (roles.length === 0) fetchRoles();
  };

  // 保存关联人员：单条立即提交到后端 POST /users/project/assign-roles
  // 后端字段：user_name=username, role_id=roleId, report_to_id=superiorId(=上级 userId，为空则不传)
  const handleSaveAssociate = async () => {
    if (!selectedProject) { Toast({ message: '请先选择一个项目', theme: 'warning' }); return; }
    if (!selectedProject.id) { Toast({ message: '当前项目缺少 id，无法提交', theme: 'warning' }); return; }
    if (!associateUser) { Toast({ message: '请选择用户', theme: 'warning' }); return; }
    if (!associateRole) { Toast({ message: '请选择角色', theme: 'warning' }); return; }

    const roleObj = roles.find((r) => r.id === associateRole);
    const payload: Record<string, string> = {
      user_name: associateUser.username,
      role_id: associateRole,
    };
    if (associateSuperiorId) payload.report_to_id = associateSuperiorId;

    setSubmittingAssociates(true);
    try {
      await request('/users/project/assign-roles', {
        method: 'POST',
        body: JSON.stringify({
          project_id: selectedProject.id,
          organization_ids: [payload],
        }),
      });
      // 落库成功后同步加入本地列表（用于本次会话内的上下层关系展示与上级选择）
      setAssociateList((prev) => [
        ...prev,
        {
          id: `${associateUser.id}_${associateRole}_${Date.now()}`,
          userId: associateUser.id,
          username: associateUser.username,
          userName: associateUser.name || associateUser.username,
          roleId: associateRole,
          roleName: roleObj?.name || associateRole,
          superiorId: associateSuperiorId,
        },
      ]);
      Toast({ message: '已添加关联人员', theme: 'success' });
      setAssociateVisible(false);
      fetchExistingUsers(selectedProject);
    } catch (err) {
      Toast({ message: `添加失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmittingAssociates(false);
    }
  };

  // 按上下层关系构建树：无上级或上级已被移除的人员作为顶层节点
  const buildAssociateTree = (list: AssociateItem[]) => {
    const idSet = new Set(list.map((a) => a.id));
    const childrenMap = new Map<string, AssociateItem[]>();
    const roots: AssociateItem[] = [];
    list.forEach((a) => {
      if (a.superiorId && idSet.has(a.superiorId)) {
        const siblings = childrenMap.get(a.superiorId) || [];
        siblings.push(a);
        childrenMap.set(a.superiorId, siblings);
      } else {
        roots.push(a);
      }
    });
    return { roots, childrenMap };
  };

  const renderAssociateNode = (item: AssociateItem, depth: number, childrenMap: Map<string, AssociateItem[]>) => (
    <div key={item.id}>
      <div
        style={{
          background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10,
          boxShadow: '0 1px 3px rgba(0,0,0,0.06)', marginLeft: depth * 20,
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: 13, color: '#666' }}>
            {depth > 0 && <span style={{ color: '#bbb', marginRight: 4 }}>└</span>}
            {item.userName} - {item.roleName}
          </div>
          <Button
            size="small"
            theme="danger"
            variant="outline"
            onClick={() => setAssociateList((prev) => prev.filter((x) => x.id !== item.id && x.superiorId !== item.userId))}
          >
            移除
          </Button>
        </div>
      </div>
      {(childrenMap.get(item.id) || []).map((child) => renderAssociateNode(child, depth + 1, childrenMap))}
    </div>
  );

  return (
    <div style={{ padding: 16 }}>
      <h4 style={{ marginBottom: 12, fontSize: 15, fontWeight: 600 }}>选择项目查看授权</h4>

      {/* 项目选择下拉：点击展开底部列表，避免项目过多时摊开占屏 */}
      {projectLoading ? <Loading text="加载项目..." /> : (
        <div
          onClick={() => setProjectPickerVisible(true)}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            background: '#fff', borderRadius: 8, padding: '12px 14px', marginBottom: 16,
            boxShadow: '0 1px 3px rgba(0,0,0,0.06)', cursor: 'pointer',
          }}
        >
          <div>
            {selectedProject ? (
              <>
                <div style={{ fontWeight: 500 }}>{selectedProject.name}</div>
                {selectedProject.code && <div style={{ fontSize: 12, color: '#999' }}>项目代码：{selectedProject.code}</div>}
              </>
            ) : (
              <span style={{ color: '#bbb', fontSize: 14 }}>请选择项目</span>
            )}
          </div>
          <span style={{ color: '#999' }}>›</span>
        </div>
      )}

      <Popup visible={projectPickerVisible} onClose={() => setProjectPickerVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '70vh', overflow: 'auto' }}>
          <h4 style={{ marginBottom: 12 }}>选择项目</h4>
          <Input
            value={projectSearch}
            onChange={(v) => setProjectSearch(String(v))}
            placeholder="输入项目名称关键词模糊查找"
            clearable
            style={{ marginBottom: 12 }}
          />
          {filteredProjects.map((p) => (
            <div
              key={p.id || p.name}
              onClick={() => handleProjectSelect(p)}
              style={{
                background: selectedProject?.name === p.name ? '#e8f2ff' : '#fff',
                borderRadius: 8,
                padding: '12px 14px',
                marginBottom: 8,
                cursor: 'pointer',
                border: selectedProject?.name === p.name ? '1px solid #0052d9' : '1px solid transparent',
              }}
            >
              <div style={{ fontWeight: 500 }}>{p.name}</div>
              {p.code && <div style={{ fontSize: 12, color: '#999' }}>项目代码：{p.code}</div>}
            </div>
          ))}
          {filteredProjects.length === 0 && (
            <div style={{ textAlign: 'center', padding: 30, color: '#999' }}>未找到匹配的项目</div>
          )}
        </div>
      </Popup>

      {/* 授权列表 */}
      {!selectedProject ? (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>请选择一个项目查看授权信息</div>
      ) : loading ? <Loading text="加载授权..." /> : (
        <>
          <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 12, color: '#0052d9' }}>
            {selectedProject.name} - 授权记录 ({items.length})
          </div>
          {items.map((item) => (
            <div key={item.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                <div style={{ fontWeight: 500, wordBreak: 'break-all' }}>{maskCode(item.license_code)}</div>
                <span
                  onClick={() => handleCopyCode(item.license_code)}
                  style={{ flexShrink: 0, fontSize: 12, color: '#0052d9', cursor: 'pointer' }}
                >
                  复制
                </span>
              </div>
              {item.machine_code && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginTop: 4 }}>
                  <div style={{ fontSize: 12, color: '#999', wordBreak: 'break-all' }}>机器码：{maskCode(item.machine_code)}</div>
                  <span
                    onClick={() => handleCopyCode(item.machine_code!)}
                    style={{ flexShrink: 0, fontSize: 12, color: '#0052d9', cursor: 'pointer' }}
                  >
                    复制
                  </span>
                </div>
              )}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginTop: 4 }}>
                <div>
                  <div style={{ fontSize: 13, color: '#666' }}>有效期：{item.apply_time} ～ {item.expire_time}</div>
                  <div style={{ fontSize: 12, color: '#999' }}>申请人：{item.applicant}</div>
                  <div style={{ fontSize: 12, color: '#999' }}>允许最大车数：{item.max_vehicles != null ? item.max_vehicles : '不限制'}</div>
                </div>
                <Button size="small" theme="danger" variant="outline" onClick={() => handleRevoke(item)}>撤销</Button>
              </div>
            </div>
          ))}
          {items.length === 0 && (
            <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>该项目暂无授权记录</div>
          )}

          {/* 项目已关联人员：可折叠树形 + 右键移除 */}
          <div style={{ background: '#fff', borderRadius: 8, padding: 14, marginTop: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>
              已关联人员（{existingUsers.length}）<span style={{ fontSize: 12, color: '#999', fontWeight: 400, marginLeft: 6 }}>右键卡片可移除</span>
            </div>
            {existingUsersLoading ? (
              <Loading text="加载中..." />
            ) : existingUsers.length === 0 ? (
              <div style={{ fontSize: 13, color: '#999', padding: '8px 0' }}>该项目暂无已关联人员</div>
            ) : (
              (() => {
                const { roots, childrenMap } = buildExistingUserTree(existingUsers);
                const renderNode = (u: ExistingProjectUser, depth: number) => {
                  const children = childrenMap.get(u.id) || [];
                  const collapsed = collapsedUserIds.has(u.id);
                  const roleNames = u.roleIds.map((rid) => roles.find((r) => r.id === rid)?.name || rid).join('、');
                  return (
                    <div key={u.id}>
                      <div
                        style={{
                          background: removingUserId === u.id ? '#fff1f0' : '#fafafa',
                          borderRadius: 8, padding: 14, marginBottom: 10, marginLeft: depth * 20,
                          boxShadow: '0 1px 3px rgba(0,0,0,0.06)', cursor: 'context-menu',
                        }}
                        onContextMenu={(e) => {
                          e.preventDefault();
                          setContextMenu({ userId: u.id, x: e.clientX, y: e.clientY });
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                          <div style={{ fontSize: 14, fontWeight: 500 }}>
                            {depth > 0 && <span style={{ color: '#bbb', marginRight: 4 }}>└</span>}
                            {u.name}
                            {children.length > 0 && (
                              <span
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setCollapsedUserIds((prev) => {
                                    const next = new Set(prev);
                                    if (next.has(u.id)) next.delete(u.id); else next.add(u.id);
                                    return next;
                                  });
                                }}
                                style={{ marginLeft: 8, fontSize: 12, color: '#0052d9', cursor: 'pointer' }}
                              >
                                {collapsed ? `展开(${children.length})` : '收起'}
                              </span>
                            )}
                          </div>
                          <div style={{ fontSize: 12, color: '#999' }}>{u.username} · {roleNames}</div>
                        </div>
                      </div>
                      {!collapsed && children.map((c) => renderNode(c, depth + 1))}
                    </div>
                  );
                };
                return roots.map((root) => renderNode(root, 0));
              })()
            )}
          </div>

          {/* 申请授权码：输入机器码 + 开始/结束日期，经 MQTT 向设备端申请授权 */}
          <div style={{ background: '#fff', borderRadius: 8, padding: 14, marginTop: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>申请授权码</div>
            <Input
              value={machineCode}
              onChange={(v) => setMachineCode(String(v))}
              placeholder="请输入机器码"
              clearable
              style={{ marginBottom: 10 }}
            />
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              <div
                onClick={() => setStartDatePickerVisible(true)}
                style={{ flex: 1, border: '1px solid #dcdcdc', borderRadius: 6, padding: '10px 12px', color: licenseStartDate ? '#333' : '#bbb', fontSize: 14, cursor: 'pointer' }}
              >
                {licenseStartDate || '开始日期'}
              </div>
              <div
                onClick={() => setEndDatePickerVisible(true)}
                style={{ flex: 1, border: '1px solid #dcdcdc', borderRadius: 6, padding: '10px 12px', color: licenseEndDate ? '#333' : '#bbb', fontSize: 14, cursor: 'pointer' }}
              >
                {licenseEndDate || '结束日期'}
              </div>
            </div>
            <div style={{ fontSize: 13, color: '#666', marginBottom: 6 }}>允许最大车数</div>
            <Input
              value={maxVehicles}
              onChange={(v) => setMaxVehicles(String(v).replace(/[^\d]/g, ''))}
              type="number"
              placeholder="可选，留空表示不限制"
              clearable
              style={{ marginBottom: 12 }}
            />
            <Button theme="primary" block loading={applyingLicense} onClick={handleApplyLicense}>
              申请授权码
            </Button>
          </div>

          <Popup visible={startDatePickerVisible} onClose={() => setStartDatePickerVisible(false)} placement="bottom">
            <DateTimePicker
              mode="date"
              title="选择开始日期"
              format="YYYY-MM-DD"
              value={licenseStartDate || undefined}
              onConfirm={(v) => { setLicenseStartDate(String(v)); setStartDatePickerVisible(false); }}
              onCancel={() => setStartDatePickerVisible(false)}
            />
          </Popup>
          <Popup visible={endDatePickerVisible} onClose={() => setEndDatePickerVisible(false)} placement="bottom">
            <DateTimePicker
              mode="date"
              title="选择结束日期"
              format="YYYY-MM-DD"
              value={licenseEndDate || undefined}
              onConfirm={(v) => { setLicenseEndDate(String(v)); setEndDatePickerVisible(false); }}
              onCancel={() => setEndDatePickerVisible(false)}
            />
          </Popup>
        </>
      )}

      {/* 添加关联人员 */}
      <Button theme="primary" variant="outline" block style={{ marginTop: 16 }} onClick={openAssociate}>
        + 添加关联人员
      </Button>

      {associateList.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 13, color: '#999', marginBottom: 8 }}>本次已添加的关联人员（含上下层关系）</div>
          {(() => {
            const { roots, childrenMap } = buildAssociateTree(associateList);
            return roots.map((root) => renderAssociateNode(root, 0, childrenMap));
          })()}
        </div>
      )}

      {/* 添加关联人员弹窗 */}
      <Popup visible={associateVisible} onClose={() => setAssociateVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '70vh', overflow: 'auto' }}>
          <h4 style={{ marginBottom: 4 }}>添加关联人员</h4>
          <div style={{ fontSize: 12, color: '#999', marginBottom: 16 }}>
            项目：{selectedProject?.name}
          </div>

          <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8 }}>选择用户</div>
          <div style={{ marginBottom: 20 }}>
            <UserSelect
              value={associateUser?.id}
              onChange={setAssociateUser}
              placeholder="请选择用户"
              title="选择用户"
            />
          </div>

          <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8 }}>选择角色</div>
          <div style={{ marginBottom: 20 }}>
            {rolesLoading ? (
              <Loading text="加载角色..." />
            ) : roles.length === 0 ? (
              <div style={{ padding: '10px 0', color: '#999', fontSize: 13 }}>暂无可选角色，请先在角色管理中创建</div>
            ) : (
              roles.map((role) => (
              <div
                key={role.id}
                onClick={() => setAssociateRole(role.id)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '10px 0', borderBottom: '1px solid #f5f5f5', cursor: 'pointer',
                }}
              >
                <div
                  style={{
                    width: 16, height: 16, borderRadius: '50%',
                    border: `1px solid ${associateRole === role.id ? '#0052d9' : '#ccc'}`,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}
                >
                  {associateRole === role.id && (
                    <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#0052d9' }} />
                  )}
                </div>
                <div style={{ fontSize: 14 }}>{role.name}</div>
              </div>
              ))
            )}
          </div>

          <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8 }}>上级人员（可选，用于展示上下层关系）</div>
          <div style={{ marginBottom: 20 }}>
            <div
              onClick={() => setAssociateSuperiorId(null)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '10px 0', borderBottom: '1px solid #f5f5f5', cursor: 'pointer',
              }}
            >
              <div
                style={{
                  width: 16, height: 16, borderRadius: '50%',
                  border: `1px solid ${!associateSuperiorId ? '#0052d9' : '#ccc'}`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}
              >
                {!associateSuperiorId && <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#0052d9' }} />}
              </div>
              <div style={{ fontSize: 14 }}>无（顶层）</div>
            </div>
            {associateList.length === 0 ? (
              <div style={{ padding: '10px 0', color: '#999', fontSize: 13 }}>暂无已添加人员可选为上级</div>
            ) : (
              associateList.map((a) => (
                <div
                  key={a.id}
                  onClick={() => setAssociateSuperiorId(a.userId)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 0', borderBottom: '1px solid #f5f5f5', cursor: 'pointer',
                  }}
                >
                  <div
                    style={{
                      width: 16, height: 16, borderRadius: '50%',
                      border: `1px solid ${associateSuperiorId === a.userId ? '#0052d9' : '#ccc'}`,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}
                  >
                    {associateSuperiorId === a.userId && <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#0052d9' }} />}
                  </div>
                  <div style={{ fontSize: 14 }}>{a.userName} - {a.roleName}</div>
                </div>
              ))
            )}
          </div>

          <div style={{ display: 'flex', gap: 8 }}>
            <Button theme="default" block onClick={() => setAssociateVisible(false)}>取消</Button>
            <Button theme="primary" block loading={submittingAssociates} onClick={handleSaveAssociate}>保存</Button>
          </div>
        </div>
      </Popup>

      {/* 右键移除菜单：点击遮罩关闭 */}
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
              background: '#fff', borderRadius: 6, padding: 4, minWidth: 120,
              boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
            }}
          >
            <div
              onClick={() => handleRemoveExistingUser(contextMenu.userId)}
              style={{
                padding: '8px 12px', fontSize: 14, color: '#e34d59',
                cursor: 'pointer', borderRadius: 4, userSelect: 'none',
              }}
            >
              {removingUserId === contextMenu.userId ? '移除中...' : '移除人员'}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
