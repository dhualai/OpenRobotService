import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Button, Toast, Loading, Dialog, Input, Popup, Form, FormItem, Checkbox } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

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
  const [allPermissions, setAllPermissions] = useState<Permission[]>([]);
  const [permLoading, setPermLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const [contextMenu, setContextMenu] = useState<ContextMenuState>({
    visible: false,
    x: 0,
    y: 0,
    role: null,
  });

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

  const getPermissionColor = (resourceType: string): string => {
    const colorMap: Record<string, string> = {
      base: '#0052d9',
      user: '#2ba471',
      role: '#ff7d00',
      project: '#9b5cff',
      permission: '#e34d59',
      indicators: '#00a0e9',
    };
    return colorMap[resourceType] || '#0052d9';
  };

  if (loading) return <Loading text="加载角色列表..." />;

  const renderRoleCard = (role: Role) => {
    const permDetails = role._permDetails;

    return (
      <div
        key={role.id}
        ref={(el) => {
          if (el) cardRefs.current.set(role.id, el);
          else cardRefs.current.delete(role.id);
        }}
        onPointerDown={(e) => handleCardPointerDown(e, role)}
        onPointerMove={handleCardPointerMove}
        onPointerUp={handleCardPointerUp}
        onPointerLeave={handleCardPointerUp}
        onPointerCancel={handleCardPointerUp}
        onClick={() => handleCardClick(role)}
        style={{
          background: '#fff',
          borderRadius: 8,
          padding: 14,
          marginBottom: 10,
          boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
          cursor: 'pointer',
          transition: 'box-shadow 0.2s',
          userSelect: 'none',
          WebkitUserSelect: 'none',
          WebkitTouchCallout: 'none',
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLDivElement).style.boxShadow = '0 4px 12px rgba(0,0,0,0.12)';
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLDivElement).style.boxShadow = '0 1px 3px rgba(0,0,0,0.06)';
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 500, marginBottom: 4 }}>{role.name}</div>
            {role.description && (
              <div style={{ fontSize: 13, color: '#666', marginBottom: 8 }}>{role.description}</div>
            )}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {permDetails && permDetails.length > 0 ? (
                <>
                  {permDetails.slice(0, 6).map((p) => (
                    <span
                      key={p.id}
                      style={{
                        fontSize: 11,
                        padding: '2px 8px',
                        borderRadius: 4,
                        background: `${getPermissionColor(p.resource_type)}15`,
                        color: getPermissionColor(p.resource_type),
                        border: `1px solid ${getPermissionColor(p.resource_type)}30`,
                      }}
                    >
                      {p.name}
                    </span>
                  ))}
                  {permDetails.length > 6 && (
                    <span
                      style={{
                        fontSize: 11,
                        padding: '2px 8px',
                        color: '#999',
                      }}
                    >
                      +{permDetails.length - 6} 更多
                    </span>
                  )}
                </>
              ) : (
                <span style={{ fontSize: 11, color: '#bbb' }}>暂无权限</span>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  };

  const systemRoles = roles.filter((r) => r.role_type === 'system');
  const projectRoles = roles.filter((r) => r.role_type === 'project');

  return (
    <div style={{ padding: 16, position: 'relative' }}>
      <Button theme="primary" block style={{ marginBottom: 16 }} onClick={openCreate}>
        新建角色
      </Button>

      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, color: '#333' }}>系统角色</div>
      {systemRoles.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 20, color: '#999', fontSize: 13 }}>暂无系统角色</div>
      ) : (
        systemRoles.map(renderRoleCard)
      )}

      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, marginTop: 20, color: '#333' }}>项目角色</div>
      {projectRoles.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 20, color: '#999', fontSize: 13 }}>暂无项目角色</div>
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
            borderRadius: 8,
            boxShadow: '0 4px 16px rgba(0,0,0,0.15)',
            overflow: 'hidden',
            minWidth: 100,
          }}
        >
          <div
            style={{
              padding: '12px 16px',
              fontSize: 14,
              color: '#e34d59',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLDivElement).style.background = '#fef0f0';
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLDivElement).style.background = 'transparent';
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
        <div style={{ padding: 20, maxHeight: '80vh', overflow: 'auto' }}>
          <h4 style={{ marginBottom: 16 }}>
            {editingRoleId ? '编辑角色' : '新建角色'}
          </h4>
          <Form onSubmit={handleSaveRole}>
            <FormItem label="角色名称">
              <Input
                value={editForm.name}
                onChange={(v) => setEditForm((p) => ({ ...p, name: String(v) }))}
                placeholder="如 admin, editor"
                clearable
              />
            </FormItem>

            {!editingRoleId ? (
              <FormItem label="角色类型">
                <div style={{ display: 'flex', gap: 8 }}>
                  {(['project', 'system'] as const).map((t) => (
                    <div
                      key={t}
                      onClick={() => setEditForm((p) => ({ ...p, role_type: t }))}
                      style={{
                        flex: 1, textAlign: 'center', padding: '8px 0', borderRadius: 6, cursor: 'pointer',
                        fontSize: 13,
                        background: editForm.role_type === t ? '#0052d9' : '#f5f5f5',
                        color: editForm.role_type === t ? '#fff' : '#666',
                      }}
                    >
                      {t === 'project' ? '项目角色' : '系统角色'}
                    </div>
                  ))}
                </div>
              </FormItem>
            ) : (
              <FormItem label="角色类型">
                <span style={{ fontSize: 13, color: '#999' }}>
                  {editForm.role_type === 'system' ? '系统角色' : '项目角色'}（创建后不可更改）
                </span>
              </FormItem>
            )}

            {editingRoleId && (
              <FormItem label="权限配置">
                <div style={{ maxHeight: 300, overflow: 'auto', border: '1px solid #eee', borderRadius: 6, padding: '0 12px' }}>
                  {permLoading ? (
                    <Loading text="加载权限..." />
                  ) : allPermissions.length === 0 ? (
                    <div style={{ padding: 16, color: '#999', textAlign: 'center' }}>
                      暂无可配置的权限
                    </div>
                  ) : (
                    allPermissions.map((perm) => (
                      <div
                        key={perm.id}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 10,
                          padding: '10px 0',
                          borderBottom: '1px solid #f5f5f5',
                        }}
                        onClick={() => togglePerm(perm.id)}
                      >
                        <Checkbox
                          checked={editForm.permissions.includes(perm.id)}
                        />
                        <div>
                          <div style={{ fontSize: 14, fontWeight: 500 }}>
                            {perm.name}
                          </div>
                          <div style={{ fontSize: 12, color: '#999' }}>
                            {perm.resource_type} · {perm.action}
                          </div>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </FormItem>
            )}

            <FormItem>
              <div style={{ display: 'flex', gap: 8 }}>
                <Button
                  theme="default"
                  block
                  onClick={() => setEditVisible(false)}
                >
                  取消
                </Button>
                <Button
                  theme="primary"
                  block
                  type="submit"
                  loading={submitting}
                >
                  保存
                </Button>
              </div>
            </FormItem>
          </Form>
        </div>
      </Popup>
    </div>
  );
}
