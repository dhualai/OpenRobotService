// 项目人员关联 - 按 username 构建用户树，支持长按移除
// 样式参考 macaron projects.auth 页：人员条目卡 + 幽灵按钮 + 单选行弹窗。
import { useState, useEffect, useRef } from 'react';
import { Toast, Loading, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import UserSelect from '@/shared/components/UserSelect';
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
}

export default function ProjectPeople({ selectedProject }: { selectedProject: Project | null }) {
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const [associateVisible, setAssociateVisible] = useState(false);
  const [associateUser, setAssociateUser] = useState<UserItem | null>(null);
  const [associateRole, setAssociateRole] = useState<string | null>(null);
  const [associateSuperiorUsername, setAssociateSuperiorUsername] = useState<string | null>(null);
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
        role_id: string; role_name: string; report_to_id?: string | null;
      }>>(`/projects/${project.id}/members`);
      const list = normalizeList<{
        user_id: string; username: string; name?: string | null;
        role_id: string; role_name: string; report_to_id?: string | null;
      }>(rows);
      const byUsername = new Map<string, ExistingProjectUser>();
      for (const r of list) {
        const existing = byUsername.get(r.username);
        if (existing) {
          if (!existing.roleIds.includes(r.role_id)) {
            existing.roleIds.push(r.role_id);
            existing.roleNames.push(r.role_name);
          }
          if (!existing.reportToId && r.report_to_id) {
            existing.reportToId = r.report_to_id;
          }
        } else {
          byUsername.set(r.username, {
            id: r.user_id,
            name: r.name || r.username,
            username: r.username,
            roleIds: [r.role_id],
            roleNames: [r.role_name],
            reportToId: r.report_to_id || null,
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
          style={{ marginLeft: 'auto' }}
          onClick={openAssociate}
        >
          + 添加关联人员
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
                  onMouseDown={(e) => beginLongPress(e.clientX, e.clientY, u.username)}
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
        <div className="mac-sheet" style={{ maxHeight: '70vh', overflow: 'auto' }}>
          <h4 className="mac-sheet__title" style={{ marginBottom: 4 }}>添加关联人员</h4>
          <div style={{ fontSize: 12, color: 'var(--mac-muted-fg)', marginBottom: 16 }}>
            项目：{selectedProject?.name}
          </div>

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

          <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8, color: 'var(--mac-fg)' }}>上级人员（可选，用于展示上下层关系）</div>
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
            ) : (
              superiorCandidates.map((c) => (
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

          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" className="mac-btn mac-btn--outline mac-btn--block" onClick={() => setAssociateVisible(false)}>取消</button>
            <button type="button" className="mac-btn mac-btn--primary mac-btn--block" disabled={submittingAssociates} onClick={handleSaveAssociate}>
              {submittingAssociates ? '保存中...' : '保存'}
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
