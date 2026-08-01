// 项目人员关联 - 按 username 构建用户树，支持长按移除
import { useState, useEffect, useRef } from 'react';
import { Button, Toast, Loading, Input, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import UserSelect from '@/shared/components/UserSelect';
import type { UserItem } from '@/api/users';

interface Project { id?: string; code?: string; name: string; }

interface RoleItem { id: string; name: string; role_type?: string; }

interface AssociateItem {
  id: string;
  username: string;
  userName: string;
  roleId: string;
  roleName: string;
  superiorUsername?: string | null;
}

interface ExistingProjectUser {
  id: string;
  name: string;
  username: string;
  roleIds: string[];
  roleNames: string[];
  reportToUsername?: string | null;
}

export default function ProjectPeople({ selectedProject }: { selectedProject: Project | null }) {
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const [associateVisible, setAssociateVisible] = useState(false);
  const [associateUser, setAssociateUser] = useState<UserItem | null>(null);
  const [associateRole, setAssociateRole] = useState<string | null>(null);
  const [associateSuperiorUsername, setAssociateSuperiorUsername] = useState<string | null>(null);
  const [associateList, setAssociateList] = useState<AssociateItem[]>([]);
  const [submittingAssociates, setSubmittingAssociates] = useState(false);

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

  const fetchExistingUsers = async (project: Project) => {
    if (!project.id) { setExistingUsers([]); return; }
    setExistingUsersLoading(true);
    try {
      const rows = await request<Array<{
        user_id: string; username: string; name?: string | null;
        role_id: string; role_name: string; report_to_name?: string | null;
      }>>(`/projects/${project.id}/members`);
      const list = normalizeList<{
        user_id: string; username: string; name?: string | null;
        role_id: string; role_name: string; report_to_name?: string | null;
      }>(rows);
      const byUsername = new Map<string, ExistingProjectUser>();
      for (const r of list) {
        const existing = byUsername.get(r.username);
        if (existing) {
          if (!existing.roleIds.includes(r.role_id)) {
            existing.roleIds.push(r.role_id);
            existing.roleNames.push(r.role_name);
          }
          if (!existing.reportToUsername && r.report_to_name) {
            existing.reportToUsername = r.report_to_name;
          }
        } else {
          byUsername.set(r.username, {
            id: r.user_id,
            name: r.name || r.username,
            username: r.username,
            roleIds: [r.role_id],
            roleNames: [r.role_name],
            reportToUsername: r.report_to_name || null,
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
    const usernameSet = new Set(list.map((u) => u.username));
    const childrenMap = new Map<string, ExistingProjectUser[]>();
    const roots: ExistingProjectUser[] = [];
    list.forEach((u) => {
      const parent = u.reportToUsername;
      if (parent && usernameSet.has(parent)) {
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
    }, 1000);
  };
  const cancelLongPress = () => {
    if (longPressTimer.current) { clearTimeout(longPressTimer.current); longPressTimer.current = null; }
  };

  const openAssociate = () => {
    if (!selectedProject) {
      Toast({ message: '请先选择一个项目', theme: 'warning' });
      return;
    }
    setAssociateUser(null);
    setAssociateRole(null);
    setAssociateSuperiorUsername(null);
    setAssociateVisible(true);
    if (roles.length === 0) fetchRoles();
  };

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
      setAssociateList((prev) => [
        ...prev,
        {
          id: `${associateUser.id}_${associateRole}_${Date.now()}`,
          username: associateUser.username,
          userName: associateUser.name || associateUser.username,
          roleId: associateRole,
          roleName: roleObj?.name || associateRole,
          superiorUsername: associateSuperiorUsername,
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

  const buildAssociateTree = (list: AssociateItem[]) => {
    const usernameSet = new Set(list.map((a) => a.username));
    const childrenMap = new Map<string, AssociateItem[]>();
    const roots: AssociateItem[] = [];
    list.forEach((a) => {
      if (a.superiorUsername && usernameSet.has(a.superiorUsername)) {
        const siblings = childrenMap.get(a.superiorUsername) || [];
        siblings.push(a);
        childrenMap.set(a.superiorUsername, siblings);
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
            onClick={() => setAssociateList((prev) => prev.filter((x) => x.id !== item.id && x.superiorUsername !== item.username))}
          >
            移除
          </Button>
        </div>
      </div>
      {(childrenMap.get(item.username) || []).map((child) => renderAssociateNode(child, depth + 1, childrenMap))}
    </div>
  );

  if (!selectedProject) {
    return <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>请先选择项目</div>;
  }

  return (
    <div style={{ padding: '0 16px 16px' }}>
      {/* 已关联人员卡片（含添加入口） */}
      <div style={{ background: '#fff', borderRadius: 8, padding: 14, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
          <div style={{ fontSize: 14, fontWeight: 600, flex: 1, minWidth: 0 }}>
            已关联人员（{existingUsers.length}）
            <span style={{ fontSize: 12, color: '#999', fontWeight: 400, marginLeft: 6 }}>长按卡片可移除</span>
          </div>
          <Button size="small" theme="primary" variant="outline" style={{ flexShrink: 0 }} onClick={openAssociate}>+ 添加关联人员</Button>
        </div>
        {existingUsersLoading ? (
          <Loading text="加载中..." />
        ) : existingUsers.length === 0 ? (
          <div style={{ fontSize: 13, color: '#999', padding: '8px 0' }}>该项目暂无已关联人员</div>
        ) : (
          (() => {
            const { roots, childrenMap } = buildExistingUserTree(existingUsers);
            const renderNode = (u: ExistingProjectUser, depth: number) => {
              const children = childrenMap.get(u.username) || [];
              const collapsed = collapsedUsernames.has(u.username);
              const roleNames = u.roleNames.join('、');
              return (
                <div key={u.username}>
                  <div
                    style={{
                      background: removingUsername === u.username ? '#fff1f0' : '#fafafa',
                      borderRadius: 8, padding: 14, marginBottom: 10, marginLeft: depth * 20,
                      boxShadow: '0 1px 3px rgba(0,0,0,0.06)', userSelect: 'none', WebkitUserSelect: 'none',
                    }}
                    onTouchStart={(e) => beginLongPress(e.touches[0].clientX, e.touches[0].clientY, u.username)}
                    onTouchEnd={cancelLongPress}
                    onTouchMove={cancelLongPress}
                    onMouseDown={(e) => beginLongPress(e.clientX, e.clientY, u.username)}
                    onMouseUp={cancelLongPress}
                    onMouseLeave={cancelLongPress}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                      <div style={{ fontSize: 14, fontWeight: 500 }}>
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

      {/* 本次添加预览 */}
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
              onClick={() => setAssociateSuperiorUsername(null)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '10px 0', borderBottom: '1px solid #f5f5f5', cursor: 'pointer',
              }}
            >
              <div
                style={{
                  width: 16, height: 16, borderRadius: '50%',
                  border: `1px solid ${!associateSuperiorUsername ? '#0052d9' : '#ccc'}`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}
              >
                {!associateSuperiorUsername && <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#0052d9' }} />}
              </div>
              <div style={{ fontSize: 14 }}>无（顶层）</div>
            </div>
            {associateList.length === 0 ? (
              <div style={{ padding: '10px 0', color: '#999', fontSize: 13 }}>暂无已添加人员可选为上级</div>
            ) : (
              associateList.map((a) => (
                <div
                  key={a.id}
                  onClick={() => setAssociateSuperiorUsername(a.username)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 0', borderBottom: '1px solid #f5f5f5', cursor: 'pointer',
                  }}
                >
                  <div
                    style={{
                      width: 16, height: 16, borderRadius: '50%',
                      border: `1px solid ${associateSuperiorUsername === a.username ? '#0052d9' : '#ccc'}`,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}
                  >
                    {associateSuperiorUsername === a.username && <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#0052d9' }} />}
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
              background: '#fff', borderRadius: 6, padding: 4, minWidth: 120,
              boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
            }}
          >
            <div
              onClick={() => handleRemoveExistingUser(contextMenu.username)}
              style={{
                padding: '8px 12px', fontSize: 14, color: '#e34d59',
                cursor: 'pointer', borderRadius: 4, userSelect: 'none',
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
