import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Toast, Loading, Popup } from 'tdesign-mobile-react';
import { UserRound, Crown, Users, Check } from 'lucide-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { usePointerDragToDrop } from '@/shared/hooks/usePointerDragToDrop';

interface User {
  id: string;
  username: string;
  name?: string | null;
  status?: string;
  company?: string | null;
  department?: string | null;
  company_id?: string | null;
  department_id?: string | null;
  job_level?: number;
  supervisor_id?: string | null;
}

interface TreeNode extends User {
  children: TreeNode[];
  depth: number;
}

export default function OrgChart() {
  const navigate = useNavigate();
  const request = useMemo(() => createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin'), []);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // 拖拽高亮目标 id（Pointer 拖拽，鼠标/触屏统一）
  const [dragOverUserId, setDragOverUserId] = useState<string | null>(null);
  // 选中用户弹窗（设置上级）
  const [selectUser, setSelectUser] = useState<User | null>(null);
  const [pickerVisible, setPickerVisible] = useState(false);
  // 折叠状态：默认全部折叠
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(new Set());

  // 分组视图：公司 → 部门 → 用户
  const [viewMode, setViewMode] = useState<'tree' | 'group'>('tree');

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

  // 用户 id → 用户 映射
  const userMap = useMemo(() => {
    const m = new Map<string, User>();
    users.forEach((u) => m.set(u.id, u));
    return m;
  }, [users]);

  // 构建组织树：supervisor_id 为 null 的为根节点
  const tree = useMemo((): TreeNode[] => {
    const childrenMap = new Map<string | null, User[]>();
    users.forEach((u) => {
      const parent = u.supervisor_id || null;
      if (!childrenMap.has(parent)) childrenMap.set(parent, []);
      childrenMap.get(parent)!.push(u);
    });

    const build = (parent: User | null, depth: number): TreeNode[] => {
      const children = childrenMap.get(parent?.id || null) || [];
      return children
        .sort((a, b) => (a.name || a.username).localeCompare(b.name || b.username))
        .map((u) => ({ ...u, depth, children: build(u, depth + 1) }));
    };

    return build(null, 0);
  }, [users]);

  // 默认折叠所有有子节点的节点（仅首次加载，后续操作不重置）
  const initialCollapseRef = useRef(false);
  useEffect(() => {
    if (initialCollapseRef.current) return;
    initialCollapseRef.current = true;
    const ids = new Set<string>();
    const collect = (nodes: TreeNode[]) => {
      nodes.forEach((n) => {
        if (n.children.length > 0) ids.add(n.id);
        collect(n.children);
      });
    };
    collect(tree);
    setCollapsedIds(ids);
  }, [tree]);

  // 未分配上级的用户（不在树中的 — 理论上应该都在 tree 里，因为 null supervisor 的就是 root）
  // 但如果存在循环引用，这些用户不会被遍历到
  const orphanUsers = useMemo(() => {
    const inTree = new Set<string>();
    const collect = (nodes: TreeNode[]) => {
      nodes.forEach((n) => {
        inTree.add(n.id);
        collect(n.children);
      });
    };
    collect(tree);
    return users.filter((u) => !inTree.has(u.id));
  }, [tree, users]);

  // 公司 → 部门 → 用户 分组
  const groupedByCompany = useMemo(() => {
    const m = new Map<string, Map<string, User[]>>();
    users.forEach((u) => {
      const company = u.company || '未分配公司';
      const dept = u.department || '未分配部门';
      if (!m.has(company)) m.set(company, new Map());
      const deptMap = m.get(company)!;
      if (!deptMap.has(dept)) deptMap.set(dept, []);
      deptMap.get(dept)!.push(u);
    });
    // 排序
    const sorted = Array.from(m.entries()).sort(([a], [b]) => a.localeCompare(b));
    sorted.forEach(([, deptMap]) => {
      const sortedDepts = Array.from(deptMap.entries()).sort(([a], [b]) => a.localeCompare(b));
      sortedDepts.forEach(([, userList]) => {
        userList.sort((a, b) => (a.name || a.username).localeCompare(b.name || b.username));
      });
    });
    return sorted;
  }, [users]);

  // 设置上级
  const handleSetSupervisor = async (userId: string, supervisorId: string | null) => {
    const user = userMap.get(userId);
    if (!user) return;
    if (supervisorId === userId) {
      Toast({ message: '不能将自己设为上级', theme: 'warning' });
      return;
    }
    // 检测循环引用
    if (supervisorId) {
      let cur: string | null = supervisorId;
      while (cur) {
        if (cur === userId) {
          Toast({ message: '不能形成循环汇报关系', theme: 'warning' });
          return;
        }
        const curUser = userMap.get(cur);
        cur = curUser?.supervisor_id || null;
      }
    }
    setSaving(true);
    try {
      await request(`/users/${user.username}`, {
        method: 'PUT',
        body: JSON.stringify({ supervisor_id: supervisorId || '' }),
      });
      Toast({ message: '上级已更新', theme: 'success' });
      setUsers((prev) =>
        prev.map((u) => (u.id === userId ? { ...u, supervisor_id: supervisorId } : u)),
      );
    } catch (e) {
      Toast({ message: `更新失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    } finally {
      setSaving(false);
    }
  };

  // Pointer 拖拽落点（鼠标/触屏统一，替代 HTML5 原生 draggable）
  const drag = usePointerDragToDrop({
    onDrop: (draggedId, targetId) => {
      handleSetSupervisor(draggedId, targetId);
    },
    onHoverChange: (targetId) => setDragOverUserId(targetId),
  });

  // 切换折叠
  const toggleCollapse = (id: string) => {
    setCollapsedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // 切换部门管理员身份（通过 job_level：1=普通员工，2=部门管理员）
  // 仅对有部门（department_id 非空）的用户可用
  const handleToggleDeptManager = useCallback(
    async (user: User, setAsManager: boolean) => {
      if (!user.department_id) {
        Toast({ message: '未分配部门的用户不能设为部门管理员', theme: 'warning' });
        return;
      }
      const targetLevel = setAsManager ? 2 : 1;
      setSaving(true);
      try {
        await request(`/users/${user.username}`, {
          method: 'PUT',
          body: JSON.stringify({ job_level: targetLevel }),
        });
        Toast({
          message: setAsManager ? '已设为部门管理员' : '已取消部门管理员',
          theme: 'success',
        });
        setUsers((prev) =>
          prev.map((u) => (u.id === user.id ? { ...u, job_level: targetLevel } : u)),
        );
      } catch (e) {
        Toast({ message: `操作失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
      } finally {
        setSaving(false);
      }
    },
    [request],
  );

  // 自动挂靠：将同公司同部门用户的 supervisor_id 设为该部门管理员
  const handleAutoHookSubordinates = useCallback(
    async (manager: User) => {
      if (!manager.department_id || (manager.job_level ?? 1) < 2) {
        Toast({ message: '仅部门管理员可执行自动挂靠', theme: 'warning' });
        return;
      }
      setSaving(true);
      try {
        const res = (await request(`/users/${manager.username}/auto-hook-subordinates`, {
          method: 'POST',
        })) as { message?: string };
        Toast({ message: res?.message || '自动挂靠完成', theme: 'success' });
        // 刷新用户列表以同步 supervisor_id 变化
        await fetchUsers();
      } catch (e) {
        Toast({ message: `自动挂靠失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
      } finally {
        setSaving(false);
      }
    },
    [request, fetchUsers],
  );

  // 渲染树节点
  const renderTreeNode = (node: TreeNode): React.ReactNode => {
    const isDragOver = dragOverUserId === node.id;
    const isCollapsed = collapsedIds.has(node.id);
    const hasChildren = node.children.length > 0;
    const supervisorName = node.supervisor_id
      ? (() => {
          const s = userMap.get(node.supervisor_id);
          return s ? (s.name || s.username) : null;
        })()
      : null;

    const isDeptManager = (node.job_level ?? 1) >= 2;
    const hasDept = !!node.department_id;

    return (
      <div key={node.id} style={{ marginLeft: node.depth * 20 }}>
        <div
          {...drag.bind(node.id)}
          onClick={() => { setSelectUser(node); setPickerVisible(true); }}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '10px 12px',
            margin: '4px 0',
            background: isDragOver ? '#e8f0fe' : isDeptManager ? '#fff8e1' : '#fff',
            border: isDragOver ? '2px dashed #0052d9' : isDeptManager ? '1px solid #d4b106' : '1px solid #eee',
            borderRadius: 8,
            cursor: 'pointer',
            touchAction: 'pan-y',
            transition: 'background 0.15s',
          }}
        >
          {hasChildren && (
            <span
              onClick={(e) => { e.stopPropagation(); toggleCollapse(node.id); }}
              style={{ fontSize: 14, cursor: 'pointer', flexShrink: 0, width: 20, textAlign: 'center', color: '#666' }}
            >
              {isCollapsed ? '▸' : '▾'}
            </span>
          )}
          {!hasChildren && <span style={{ width: 20, flexShrink: 0 }} />}
          <span style={{ display: 'flex', alignItems: 'center', color: isDeptManager ? '#b08400' : '#8a8f99' }}>
            {hasChildren ? <Users size={16} /> : isDeptManager ? <Crown size={16} /> : <UserRound size={16} />}
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 500, fontSize: 14 }}>
                {node.name || node.username}
              </span>
              {isDeptManager && (
                <span style={{ fontSize: 11, color: '#b08400', background: '#fff3c4', padding: '1px 6px', borderRadius: 3, fontWeight: 500 }}>
                  部门管理员
                </span>
              )}
              {/* 仅在树的根节点（无父节点、depth===0）显示公司/部门，避免子卡片重复显示 */}
              {node.depth === 0 && node.company && (
                <span style={{ fontSize: 11, color: '#0052d9', background: '#e8f0fe', padding: '1px 6px', borderRadius: 3 }}>
                  {node.company}
                </span>
              )}
              {node.depth === 0 && node.department && (
                <span style={{ fontSize: 11, color: '#d46b08', background: '#fff7e6', padding: '1px 6px', borderRadius: 3 }}>
                  {node.department}
                </span>
              )}
            </div>
            {supervisorName && (
              <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
                ↑ 汇报给：{supervisorName}
              </div>
            )}
            {/* 管理员操作按钮行 */}
            {hasDept && (
              <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
                {!isDeptManager && (
                  <button
                    onClick={(e) => { e.stopPropagation(); handleToggleDeptManager(node, true); }}
                    style={{
                      fontSize: 11, padding: '2px 8px', borderRadius: 3,
                      border: '1px solid #d4b106', background: '#fffbe6', color: '#b08400',
                      cursor: 'pointer',
                    }}
                  >
                    设为部门管理员
                  </button>
                )}
                {isDeptManager && (
                  <>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleAutoHookSubordinates(node); }}
                      style={{
                        fontSize: 11, padding: '2px 8px', borderRadius: 3,
                        border: '1px solid #0052d9', background: '#e8f0fe', color: '#0052d9',
                        cursor: 'pointer', fontWeight: 500,
                      }}
                    >
                      🔗 自动挂靠（同部门人员 → 其名下）
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleToggleDeptManager(node, false); }}
                      style={{
                        fontSize: 11, padding: '2px 8px', borderRadius: 3,
                        border: '1px solid #d9d9d9', background: '#fafafa', color: '#888',
                        cursor: 'pointer',
                      }}
                    >
                      取消管理员
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
          {hasChildren && (
            <span style={{ fontSize: 11, color: '#999', flexShrink: 0 }}>
              {node.children.length} 人
            </span>
          )}
        </div>
        {hasChildren && !isCollapsed && (
          <div style={{ marginTop: 2 }}>
            {node.children.map((child) => renderTreeNode(child))}
          </div>
        )}
      </div>
    );
  };

  // 上级选择弹窗中可选的用户列表（排除自己和自己的下级）
  const selectableSupervisors = useMemo(() => {
    if (!selectUser) return [];
    // 收集所有下级 id（不能选下级作为上级）
    const subordinateIds = new Set<string>();
    const collect = (parentId: string) => {
      users.forEach((u) => {
        if (u.supervisor_id === parentId) {
          subordinateIds.add(u.id);
          collect(u.id);
        }
      });
    };
    collect(selectUser.id);
    return users.filter((u) => u.id !== selectUser.id && !subordinateIds.has(u.id));
  }, [selectUser, users]);

  if (loading) return <Loading text="加载人员数据..." />;

  return (
    <div style={{ padding: 16, paddingBottom: 80 }}>
      {/* 顶部导航 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
        <Button size="small" variant="text" onClick={() => navigate('/admin/users')}>
          ← 返回
        </Button>
        <h3 style={{ margin: 0, flex: 1, fontSize: 16 }}>人员结构配置</h3>
        <Button
          size="small"
          variant="outline"
          theme={viewMode === 'tree' ? 'primary' : 'default'}
          onClick={() => setViewMode(viewMode === 'tree' ? 'group' : 'tree')}
        >
          {viewMode === 'tree' ? '切换分组视图' : '切换树形视图'}
        </Button>
        {viewMode === 'tree' && (
          <Button
            size="small"
            variant="text"
            onClick={() => {
              const allParentIds = new Set<string>();
              const collect = (nodes: TreeNode[]) => {
                nodes.forEach((n) => {
                  if (n.children.length > 0) allParentIds.add(n.id);
                  collect(n.children);
                });
              };
              collect(tree);
              // 如果当前全部折叠 → 全部展开；否则全部折叠
              setCollapsedIds(collapsedIds.size >= allParentIds.size ? new Set() : allParentIds);
            }}
          >
            {collapsedIds.size > 0 ? '全部展开' : '全部折叠'}
          </Button>
        )}
      </div>

      {/* 操作提示 */}
      <div style={{
        fontSize: 12, color: '#888', background: '#f6f8fa',
        padding: '8px 12px', borderRadius: 6, marginBottom: 12,
      }}>
        💡 拖拽用户到目标人上即可设置汇报关系；点击用户卡片可选择/修改上级
      </div>

      {saving && (
        <div style={{ textAlign: 'center', padding: 8, color: '#0052d9', fontSize: 12 }}>
          保存中...
        </div>
      )}

      {/* 树形视图 */}
      {viewMode === 'tree' && (
        <div>
          {tree.length > 0 ? (
            tree.map((node) => renderTreeNode(node))
          ) : (
            <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
              暂无人员数据
            </div>
          )}
          {orphanUsers.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 12, color: '#e34d59', marginBottom: 8 }}>
                ⚠️ 存在循环引用的用户（未出现在树中）：
              </div>
              {orphanUsers.map((u) => (
                <div key={u.id} style={{
                  padding: '8px 12px', background: '#fff0f0', borderRadius: 6,
                  marginBottom: 4, fontSize: 13, color: '#cf1322',
                }}>
                  {u.name || u.username} → 上级：{(s => s ? (s.name || s.username) : null)(userMap.get(u.supervisor_id || '')) || u.supervisor_id || '无'}
                </div>
              ))}
              <Button
                size="small"
                variant="outline"
                theme="danger"
                style={{ marginTop: 8 }}
                onClick={() => {
                  orphanUsers.forEach((u) => handleSetSupervisor(u.id, null));
                }}
              >
                重置这些用户的上级
              </Button>
            </div>
          )}
        </div>
      )}

      {/* 分组视图 */}
      {viewMode === 'group' && (
        <div>
          {groupedByCompany.map(([company, deptMap]) => (
            <div key={company} style={{ marginBottom: 16 }}>
              <div style={{
                fontSize: 14, fontWeight: 600, color: '#333',
                padding: '8px 12px', background: '#e8f0fe', borderRadius: 6,
                display: 'flex', alignItems: 'center', gap: 6,
              }}>
                🏢 {company}
                <span style={{ fontSize: 11, color: '#666', fontWeight: 400 }}>
                  ({Array.from(deptMap.values()).reduce((sum, l) => sum + l.length, 0)} 人)
                </span>
              </div>
              <div style={{ marginLeft: 12, marginTop: 4 }}>
                {Array.from(deptMap.entries()).map(([dept, userList]) => (
                  <div key={dept} style={{ marginBottom: 8 }}>
                    <div style={{
                      fontSize: 13, fontWeight: 500, color: '#d46b08',
                      padding: '6px 10px', background: '#fff7e6', borderRadius: 4,
                      marginTop: 4,
                    }}>
                      📂 {dept} ({userList.length})
                    </div>
                    <div style={{ marginLeft: 12, marginTop: 2 }}>
                      {userList.map((u) => {
                        const supervisor = u.supervisor_id ? userMap.get(u.supervisor_id) : null;
                        const isDeptManager = (u.job_level ?? 1) >= 2;
                        const hasDept = !!u.department_id;
                        return (
                          <div
                            key={u.id}
                            {...drag.bind(u.id)}
                            onClick={() => { setSelectUser(u); setPickerVisible(true); }}
                            style={{
                              display: 'flex', alignItems: 'flex-start', gap: 6,
                              padding: '8px 10px', margin: '4px 0',
                              background: dragOverUserId === u.id ? '#e8f0fe' : isDeptManager ? '#fff8e1' : '#fff',
                              border: dragOverUserId === u.id ? '2px dashed #0052d9' : isDeptManager ? '1px solid #d4b106' : '1px solid #f0f0f0',
                              borderRadius: 6, cursor: 'pointer',
                              flexDirection: 'column',
                              touchAction: 'pan-y',
                            }}
                          >
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                              <span style={{ display: 'flex', alignItems: 'center', color: isDeptManager ? '#b08400' : '#8a8f99' }}>
                                {isDeptManager ? <Crown size={14} /> : <UserRound size={14} />}
                              </span>
                              <span style={{ fontSize: 13, fontWeight: 500 }}>
                                {u.name || u.username}
                              </span>
                              {isDeptManager && (
                                <span style={{ fontSize: 10, color: '#b08400', background: '#fff3c4', padding: '1px 5px', borderRadius: 3, fontWeight: 500 }}>
                                  部门管理员
                                </span>
                              )}
                              {supervisor && (
                                <span style={{ fontSize: 11, color: '#999' }}>
                                  ↑ {supervisor.name || supervisor.username}
                                </span>
                              )}
                              {!supervisor && (
                                <span style={{ fontSize: 11, color: '#ccc' }}>无上级</span>
                              )}
                            </div>
                            {/* 管理员操作按钮行 */}
                            {hasDept && (
                              <div style={{ display: 'flex', gap: 6, marginTop: 4, flexWrap: 'wrap' }}>
                                {!isDeptManager && (
                                  <button
                                    onClick={(e) => { e.stopPropagation(); handleToggleDeptManager(u, true); }}
                                    style={{
                                      fontSize: 10, padding: '2px 7px', borderRadius: 3,
                                      border: '1px solid #d4b106', background: '#fffbe6', color: '#b08400',
                                      cursor: 'pointer',
                                    }}
                                  >
                                    设为部门管理员
                                  </button>
                                )}
                                {isDeptManager && (
                                  <>
                                    <button
                                      onClick={(e) => { e.stopPropagation(); handleAutoHookSubordinates(u); }}
                                      style={{
                                        fontSize: 10, padding: '2px 7px', borderRadius: 3,
                                        border: '1px solid #0052d9', background: '#e8f0fe', color: '#0052d9',
                                        cursor: 'pointer', fontWeight: 500,
                                      }}
                                    >
                                      🔗 自动挂靠
                                    </button>
                                    <button
                                      onClick={(e) => { e.stopPropagation(); handleToggleDeptManager(u, false); }}
                                      style={{
                                        fontSize: 10, padding: '2px 7px', borderRadius: 3,
                                        border: '1px solid #d9d9d9', background: '#fafafa', color: '#888',
                                        cursor: 'pointer',
                                      }}
                                    >
                                      取消管理员
                                    </button>
                                  </>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 上级选择弹窗 */}
      <Popup visible={pickerVisible} onClose={() => setPickerVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
          <h4 style={{ marginBottom: 4 }}>
            设置上级：{selectUser?.name || selectUser?.username}
          </h4>
          <p style={{ fontSize: 12, color: '#999', marginBottom: 12 }}>
            选择一人作为直属上级，或点击「清除上级」设为顶层
          </p>
          <div style={{ overflow: 'auto', flex: 1 }}>
            <div
              onClick={() => {
                if (selectUser) handleSetSupervisor(selectUser.id, null);
                setPickerVisible(false);
              }}
              style={{
                padding: '10px 4px', borderBottom: '1px solid #f5f5f5', cursor: 'pointer',
                fontSize: 14, color: '#e34d59', display: 'flex', alignItems: 'center', gap: 8,
              }}
            >
              🚫 清除上级（设为顶层）
            </div>
            {selectableSupervisors.map((u) => {
              const isCurrent = selectUser?.supervisor_id === u.id;
              return (
                <div
                  key={u.id}
                  onClick={() => {
                    if (selectUser) handleSetSupervisor(selectUser.id, u.id);
                    setPickerVisible(false);
                  }}
                  style={{
                    padding: '10px 4px', borderBottom: '1px solid #f5f5f5', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', gap: 8, fontSize: 14,
                    background: isCurrent ? '#f0faff' : 'transparent',
                  }}
                >
                  <span style={{ display: 'flex', alignItems: 'center', color: isCurrent ? '#0052d9' : '#8a8f99' }}>
                    {isCurrent ? <Check size={14} /> : <UserRound size={14} />}
                  </span>
                  <div>
                    <span style={{ fontWeight: 500 }}>{u.name || u.username}</span>
                    {u.company && (
                      <span style={{ fontSize: 11, color: '#0052d9', marginLeft: 6 }}>{u.company}</span>
                    )}
                    {u.department && (
                      <span style={{ fontSize: 11, color: '#d46b08', marginLeft: 4 }}>{u.department}</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          <Button theme="default" block style={{ marginTop: 16 }} onClick={() => setPickerVisible(false)}>
            关闭
          </Button>
        </div>
      </Popup>
    </div>
  );
}
