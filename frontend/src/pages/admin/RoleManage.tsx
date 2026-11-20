// 角色管理 —— 系统/项目角色列表、角色权限绑定查看与编辑、新建/删除角色。
// 样式参考 macaron roles 页：大号新建按钮 + 蓝条分组标题 + surface-card 角色卡
// （蓝软边权限芯片 + "+N 更多"展开）+ 弹层表单。
// 交互保留：长按卡片弹出删除菜单、点击卡片进入编辑。
import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Toast, Loading, Dialog, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { MacPlus, MacCheck } from '@/shared/components/macaronIcons';

interface Role {
  id: string;
  name: string;
  role_type: 'system' | 'project';
  description?: string;
  permissions?: string[];
  _permDetails?: Permission[];
}

interface Permission {
  id: string;
  name: string;
  code: string;
  resource_type: string;
  action: string;
  description?: string;
}

interface ContextMenuState {
  visible: boolean;
  x: number;
  y: number;
  role: Role | null;
}

/** 卡片上可见的权限芯片数，超出折叠为「+N 更多」（对照原型 VISIBLE=6） */
const VISIBLE_PERMS = 6;

const SYSTEM_ROLE_NAMES = new Set(['开发者', '超级管理员', '用户']);

export default function RoleManage() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(true);

  const [editVisible, setEditVisible] = useState(false);
  const [editingRoleId, setEditingRoleId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<{ name: string; role_type: 'system' | 'project'; permissions: string[] }>({
    name: '',
    role_type: 'project',
    permissions: [],
  });
  const [editOriginalPermissions, setEditOriginalPermissions] = useState<string[]>([]);
  const [editOriginalRole, setEditOriginalRole] = useState<{ name: string; role_type: 'system' | 'project' } | null>(null);
  const [allPermissions, setAllPermissions] = useState<Permission[]>([]);
  const [permLoading, setPermLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [contextMenu, setContextMenu] = useState<ContextMenuState>({
    visible: false,
    x: 0,
    y: 0,
    role: null,
  });
  // 展开更多权限的卡片集合（对照原型「+N 更多/收起」交互）
  const [expandedRoleIds, setExpandedRoleIds] = useState<Set<string>>(new Set());

  const request = useMemo(() => createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin'), []);
  const cardRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const longPressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const longPressTriggeredRef = useRef(false);
  const longPressRoleRef = useRef<Role | null>(null);

  const hideContextMenu = useCallback(() => {
    setContextMenu((prev) => ({ ...prev, visible: false }));
    longPressTriggeredRef.current = false;
  }, []);

  const fetchRoles = useCallback(async () => {
    setLoading(true);
    try {
      const rolesData = await request<Role[]>('/roles/');
      const normalizedRoles = normalizeList<Role>(rolesData);

      const rolesWithPerms = await Promise.all(
        normalizedRoles.map(async (role) => {
          try {
            const permData = await request<Permission[]>(`/roles/${role.id}/all-permissions`);
            const perms = normalizeList<Permission>(permData);
            return {
              ...role,
              permissions: perms.map((p) => p.id),
              _permDetails: perms,
            } as Role;
          } catch {
            return { ...role, permissions: [] };
          }
        })
      );

      setRoles(rolesWithPerms);
    } catch (e) {
      Toast({ message: String(e), theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [request]);

  const fetchAllPermissions = useCallback(async () => {
    try {
      const data = await request<Permission[]>('/permissions/');
      setAllPermissions(normalizeList<Permission>(data));
    } catch (e) {
      Toast({ message: `加载权限列表失败: ${String(e)}`, theme: 'error' });
    }
  }, [request]);

  useEffect(() => {
    fetchRoles();
    fetchAllPermissions();

    const handleGlobalClick = () => hideContextMenu();
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') hideContextMenu();
    };
    window.addEventListener('click', handleGlobalClick);
    window.addEventListener('scroll', handleGlobalClick, true);
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('click', handleGlobalClick);
      window.removeEventListener('scroll', handleGlobalClick, true);
      window.removeEventListener('keydown', handleKeyDown);
      if (longPressTimerRef.current) clearTimeout(longPressTimerRef.current);
    };
  }, [fetchRoles, fetchAllPermissions, hideContextMenu]);

  const openCreate = () => {
    setEditingRoleId(null);
    setEditForm({ name: '', role_type: 'project', permissions: [] });
    setEditOriginalPermissions([]);
    setEditOriginalRole(null);
    setEditVisible(true);
  };

  const openEdit = async (role: Role) => {
    if (longPressTriggeredRef.current) return;
    setEditingRoleId(role.id);
    setEditVisible(true);
    setPermLoading(true);

    try {
      let perms: string[] = role.permissions || [];

      if (allPermissions.length === 0) {
        await fetchAllPermissions();
      }

      try {
        const permData = await request<Permission[]>(`/roles/${role.id}/all-permissions`);
        const permDetails = normalizeList<Permission>(permData);
        perms = permDetails.map((p) => p.id);
      } catch {
        // use existing permissions from role
      }

      setEditForm({ name: role.name, role_type: role.role_type, permissions: [...perms] });
      setEditOriginalPermissions([...perms]);
      setEditOriginalRole({ name: role.name, role_type: role.role_type });
    } catch (e) {
      Toast({ message: `加载角色权限失败: ${String(e)}`, theme: 'error' });
    } finally {
      setPermLoading(false);
    }
  };

  const handleSaveRole = async () => {
    if (!editForm.name.trim()) {
      Toast({ message: '请输入角色名称', theme: 'warning' });
      return;
    }

    setSubmitting(true);
    try {
      if (editingRoleId) {
        const nameChanged = editOriginalRole && editForm.name !== editOriginalRole.name;
        const typeChanged = editOriginalRole && editForm.role_type !== editOriginalRole.role_type;

        if (nameChanged || typeChanged) {
          await request(`/roles/${editingRoleId}`, {
            method: 'PUT',
            body: JSON.stringify({
              ...(nameChanged ? { name: editForm.name } : {}),
              ...(typeChanged ? { role_type: editForm.role_type } : {}),
            }),
          });
        }

        const newPerms = editForm.permissions;
        const originalPerms = editOriginalPermissions;

        const addedPerms = newPerms.filter((p) => !originalPerms.includes(p));
        const removedPerms = originalPerms.filter((p) => !newPerms.includes(p));

        for (const permId of removedPerms) {
          try {
            await request(`/roles/${editingRoleId}/permissions/remove`, {
              method: 'POST',
              body: JSON.stringify({ permission_ids: [permId] }),
            });
          } catch {
            // ignore individual removal failures
          }
        }

        if (addedPerms.length > 0) {
          await request(`/roles/${editingRoleId}/permissions`, {
            method: 'POST',
            body: JSON.stringify({ permission_ids: addedPerms }),
          });
        }

        Toast({ message: '角色已更新', theme: 'success' });
      } else {
        await request('/roles/', {
          method: 'POST',
          body: JSON.stringify({ name: editForm.name, role_type: editForm.role_type }),
        });

        Toast({ message: '角色已创建', theme: 'success' });
      }

      setEditVisible(false);
      await fetchRoles();
    } catch (err) {
      Toast({
        message: `保存失败: ${err instanceof Error ? err.message : ''}`,
        theme: 'error',
      });
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = (role: Role) => {
    hideContextMenu();
    Dialog.confirm?.({
      title: '确认删除',
      content: `确定要删除角色「${role.name}」吗？`,
      onConfirm: async () => {
        try {
          await request(`/roles/${role.id}`, { method: 'DELETE' });
          Toast({ message: '已删除', theme: 'success' });
          await fetchRoles();
        } catch (err) {
          Toast({
            message: `删除失败: ${err instanceof Error ? err.message : ''}`,
            theme: 'error',
          });
        }
      },
    });
  };

  const startLongPress = useCallback((role: Role, clientX: number, clientY: number) => {
    longPressTriggeredRef.current = false;
    longPressRoleRef.current = role;
    if (longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current);
    }

    longPressTimerRef.current = setTimeout(() => {
      longPressTriggeredRef.current = true;
      const card = cardRefs.current.get(role.id);
      const cardRect = card?.getBoundingClientRect();

      let x = clientX;
      let y = clientY;

      if (cardRect) {
        const menuWidth = 120;
        const menuHeight = 48;
        if (x + menuWidth > cardRect.right) {
          x = cardRect.right - menuWidth - 8;
        }
        if (y + menuHeight > cardRect.bottom) {
          y = cardRect.bottom - menuHeight - 8;
        }
      }

      setContextMenu({ visible: true, x, y, role });
    }, 1000);
  }, []);

  const cancelLongPress = useCallback(() => {
    if (longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }
  }, []);

  const handleCardPointerDown = (e: React.PointerEvent, role: Role) => {
    startLongPress(role, e.clientX, e.clientY);
  };

  const handleCardPointerMove = () => {
    cancelLongPress();
  };

  const handleCardPointerUp = () => {
    cancelLongPress();
  };

  const handleCardClick = (role: Role) => {
    if (longPressTriggeredRef.current) {
      longPressTriggeredRef.current = false;
      return;
    }
    if (contextMenu.visible) return;
    openEdit(role);
  };

  const togglePerm = (permId: string) => {
    setEditForm((prev) => ({
      ...prev,
      permissions: prev.permissions.includes(permId)
        ? prev.permissions.filter((p) => p !== permId)
        : [...prev.permissions, permId],
    }));
  };

  const toggleExpanded = (roleId: string) => {
    setExpandedRoleIds((prev) => {
      const next = new Set(prev);
      if (next.has(roleId)) next.delete(roleId);
      else next.add(roleId);
      return next;
    });
  };

  if (loading) return <Loading text="加载角色列表..." />;

  const renderRoleCard = (role: Role) => {
    const permDetails = role._permDetails;
    const expanded = expandedRoleIds.has(role.id);
    const shown = permDetails && permDetails.length > 0
      ? (expanded ? permDetails : permDetails.slice(0, VISIBLE_PERMS))
      : [];
    const rest = (permDetails?.length ?? 0) - shown.length;

    return (
      <div
        key={role.id}
        ref={(el) => {
          if (el) cardRefs.current.set(role.id, el);
          else cardRefs.current.delete(role.id);
        }}
        className="mac-role-card"
        onPointerDown={(e) => handleCardPointerDown(e, role)}
        onPointerMove={handleCardPointerMove}
        onPointerUp={handleCardPointerUp}
        onPointerLeave={handleCardPointerUp}
        onPointerCancel={handleCardPointerUp}
        onClick={() => handleCardClick(role)}
      >
        <div className="mac-role-card__title">{role.name}</div>
        {role.description && (
          <div className="mac-role-card__desc">{role.description}</div>
        )}
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
          {permDetails && permDetails.length > 0 ? (
            <>
              {shown.map((p) => (
                <span key={p.id} className="mac-chip mac-chip--perm">
                  {p.name}
                </span>
              ))}
              {rest > 0 ? (
                <button
                  type="button"
                  className="mac-perm-more"
                  onClick={(e) => { e.stopPropagation(); toggleExpanded(role.id); }}
                >
                  +{rest} 更多
                </button>
              ) : permDetails.length > VISIBLE_PERMS ? (
                <button
                  type="button"
                  className="mac-perm-more"
                  onClick={(e) => { e.stopPropagation(); toggleExpanded(role.id); }}
                >
                  收起
                </button>
              ) : null}
            </>
          ) : (
            <span style={{ fontSize: 11, color: 'var(--mac-muted-fg)' }}>暂无权限</span>
          )}
        </div>
      </div>
    );
  };

  const systemRoles = roles.filter((r) => r.role_type === 'system' || SYSTEM_ROLE_NAMES.has(r.name));
  const projectRoles = roles.filter((r) => r.role_type === 'project' && !SYSTEM_ROLE_NAMES.has(r.name));

  return (
    <div className="mac-page">
      <button type="button" className="mac-btn mac-btn--primary mac-btn--lg mac-btn--block" onClick={openCreate}>
        <MacPlus size={16} />
        新建角色
      </button>

      <div className="mac-section-title" style={{ marginTop: 20 }}>
        <span className="mac-section-title__bar" />
        <h2 className="mac-section-title__text">系统角色</h2>
      </div>
      {systemRoles.length === 0 ? (
        <div className="mac-empty">暂无系统角色</div>
      ) : (
        systemRoles.map(renderRoleCard)
      )}

      <div className="mac-section-title">
        <span className="mac-section-title__bar" />
        <h2 className="mac-section-title__text">项目角色</h2>
      </div>
      {projectRoles.length === 0 ? (
        <div className="mac-empty">暂无项目角色</div>
      ) : (
        projectRoles.map(renderRoleCard)
      )}

      {contextMenu.visible && contextMenu.role && (
        <div
          onClick={(e) => {
            e.stopPropagation();
            handleDelete(contextMenu.role!);
          }}
          style={{
            position: 'fixed',
            left: contextMenu.x,
            top: contextMenu.y,
            zIndex: 9999,
            background: '#fff',
            borderRadius: 13,
            boxShadow: '0 4px 16px rgba(0,0,0,0.15)',
            overflow: 'hidden',
            minWidth: 100,
          }}
        >
          <div
            className="mac-context-menu__item"
            style={{
              padding: '12px 16px',
              fontSize: 14,
              color: 'var(--mac-fg)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            删除角色
          </div>
        </div>
      )}

      <Popup
        visible={editVisible}
        onClose={() => setEditVisible(false)}
        placement="bottom"
        showOverlay
      >
        <div className="mac-sheet" style={{ maxHeight: '80vh', overflow: 'auto' }}>
          <h4 className="mac-sheet__title">
            {editingRoleId ? '编辑角色' : '新建角色'}
          </h4>

          <div className="mac-field">
            <span className="mac-field__label">角色名称</span>
            <div className="mac-field__content">
              <input
                className="mac-input"
                value={editForm.name}
                onChange={(e) => setEditForm((p) => ({ ...p, name: e.target.value }))}
                placeholder="如 admin, editor"
              />
            </div>
          </div>

          <div className="mac-field">
            <span className="mac-field__label">角色类型</span>
            <div className="mac-field__content">
              <div className="mac-role-type">
                {(['project', 'system'] as const).map((t) => (
                  <button
                    key={t}
                    type="button"
                    className={editForm.role_type === t ? 'mac-btn mac-btn--primary' : 'mac-btn mac-btn--blue-outline'}
                    onClick={() => setEditForm((p) => ({ ...p, role_type: t }))}
                  >
                    {t === 'project' ? '项目角色' : '系统角色'}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {editingRoleId && (
            <div className="mac-field" style={{ borderBottom: 'none' }}>
              <span className="mac-field__label">权限配置</span>
              <div className="mac-field__content">
                <div className="mac-perm-list">
                  {permLoading ? (
                    <Loading text="加载权限..." />
                  ) : allPermissions.length === 0 ? (
                    <div style={{ padding: 16, color: 'var(--mac-muted-fg)', textAlign: 'center' }}>
                      暂无可配置的权限
                    </div>
                  ) : (
                    allPermissions.map((perm) => {
                      const checked = editForm.permissions.includes(perm.id);
                      return (
                        <button
                          key={perm.id}
                          type="button"
                          className={`mac-choice ${checked ? 'is-active' : ''}`}
                          onClick={() => togglePerm(perm.id)}
                        >
                          <span className="mac-choice__dot">
                            {checked && <MacCheck size={12} />}
                          </span>
                          <span style={{ flex: 1, minWidth: 0 }}>
                            <span style={{ display: 'block', fontSize: 13.5, color: 'var(--mac-fg)' }}>
                              {perm.name}
                            </span>
                            <span style={{ display: 'block', fontSize: 12, color: 'var(--mac-muted-fg)' }}>
                              {perm.resource_type} · {perm.action}
                            </span>
                          </span>
                        </button>
                      );
                    })
                  )}
                </div>
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
            <button
              type="button"
              className="mac-btn mac-btn--outline mac-btn--block"
              onClick={() => setEditVisible(false)}
            >
              取消
            </button>
            <button
              type="button"
              className="mac-btn mac-btn--primary mac-btn--block"
              disabled={submitting}
              onClick={handleSaveRole}
            >
              {submitting ? '保存中...' : '保存'}
            </button>
          </div>
        </div>
      </Popup>
    </div>
  );
}
