// 人员结构配置 —— 汇报关系配置页（样式对齐 macaron-minimal-ui org-structure 页）：
// 顶部返回栏 + 全部展开/折叠 + 视图切换、搜索、扁平人员卡片（部门管理员蓝卡 +
// 公司/部门芯片 + 展开下属区）、分组视图（按部门）；保留拖拽/弹窗设置上级、
// 设为部门管理员、自动挂靠与循环引用修复。
import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Toast, Loading, Popup } from 'tdesign-mobile-react';
import { ChevronLeft, ChevronRight, Crown, Link2, Search, UserRound, Users, Check } from 'lucide-react';
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

/** 单选项行（对齐 macaron users 弹层样式） */
function ChoiceRow({ label, checked, onClick }: { label: string; checked: boolean; onClick: () => void }) {
  return (
    <button type="button" className={`mac-choice ${checked ? 'is-active' : ''}`} onClick={onClick}>
      <span className="mac-choice__dot">
        {checked && <Check size={12} />}
      </span>
      <span className="mac-choice__label">{label}</span>
    </button>
  );
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
  // 折叠状态：默认全部展开（层级缩进一目了然），可点节点右侧「N 人」或「全部折叠」收起
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(new Set());

  // 分组视图：按部门（对齐原型 org-structure 的 dept 分组）
  const [viewMode, setViewMode] = useState<'tree' | 'group'>('tree');
  const [keyword, setKeyword] = useState('');

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

  // 构建组织树：supervisor_id 为 null 的为根节点，按层级记录 depth（用于缩进）
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

  // 树形视图数据：无搜索词时展示完整层级树；搜索时保留命中节点及其祖先路径，
  // 层级缩进结构不因搜索而丢失
  const filteredTree = useMemo((): TreeNode[] => {
    const kw = keyword.trim().toLowerCase();
    if (!kw) return tree;
    const match = (u: TreeNode) =>
      (u.name || '').toLowerCase().includes(kw) ||
      (u.username || '').toLowerCase().includes(kw) ||
      (u.company || '').toLowerCase().includes(kw) ||
      (u.department || '').toLowerCase().includes(kw);
    const keep = (nodes: TreeNode[]): TreeNode[] =>
      nodes
        .map((n) => ({ ...n, children: keep(n.children) }))
        .filter((n) => match(n) || n.children.length > 0);
    return keep(tree);
  }, [tree, keyword]);

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

  // 扁平用户列表（列表/分组视图共用），支持按姓名/用户名/公司/部门搜索
  const flatList = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    const list = kw
      ? users.filter(
          (u) =>
            (u.name || '').toLowerCase().includes(kw) ||
            (u.username || '').toLowerCase().includes(kw) ||
            (u.company || '').toLowerCase().includes(kw) ||
            (u.department || '').toLowerCase().includes(kw),
        )
      : users;
    return [...list].sort((a, b) => (a.name || a.username).localeCompare(b.name || b.username));
  }, [users, keyword]);

  // 分组视图：按部门分组（对齐原型）
  const groupedByDept = useMemo(() => {
    const m = new Map<string, User[]>();
    flatList.forEach((u) => {
      const dept = u.department || '未分配部门';
      if (!m.has(dept)) m.set(dept, []);
      m.get(dept)!.push(u);
    });
    return Array.from(m.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [flatList]);

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

  // 全部展开 / 全部折叠
  const allParentIds = useMemo(() => {
    const ids = new Set<string>();
    const collect = (nodes: TreeNode[]) => {
      nodes.forEach((n) => {
        if (n.children.length > 0) ids.add(n.id);
        collect(n.children);
      });
    };
    collect(tree);
    return ids;
  }, [tree]);
  const allCollapsed = collapsedIds.size >= allParentIds.size;
  const toggleAll = () => {
    setCollapsedIds(allCollapsed ? new Set() : new Set(allParentIds));
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

  const openPicker = (user: User) => {
    setSelectUser(user);
    setPickerVisible(true);
  };

  // 渲染人员卡片（对齐原型 PersonRow：图标圆 + 姓名/芯片 + 操作 + 人数展开）
  const renderPersonCard = (user: User, children: TreeNode[]): React.ReactNode => {
    const hasChildren = children.length > 0;
    const isDeptManager = (user.job_level ?? 1) >= 2;
    const isDragOver = dragOverUserId === user.id;
    const isCollapsed = collapsedIds.has(user.id);

    return (
      <div
        key={user.id}
        className={`mac-org-card${isDeptManager ? ' mac-org-card--manager' : ''}${isDragOver ? ' mac-org-card--drag-over' : ''}`}
      >
        <div
          {...drag.bind(user.id)}
          onClick={() => openPicker(user)}
          className="mac-org-card__body"
          style={{ cursor: 'pointer', touchAction: 'pan-y' }}
        >
          <span className={`mac-org-card__icon${isDeptManager ? ' mac-org-card__icon--crown' : ''}`}>
            {isDeptManager ? <Crown size={15} /> : hasChildren ? <Users size={15} /> : <UserRound size={15} />}
          </span>

          <div className="mac-org-card__main">
            <div className="mac-org-card__chips">
              <span className="mac-org-card__name">{user.name || user.username}</span>
              {isDeptManager && <span className="mac-chip mac-chip--blue">部门管理员</span>}
              {user.company && <span className="mac-chip mac-chip--outline">{user.company}</span>}
              {user.department && <span className="mac-chip mac-chip--outline">{user.department}</span>}
            </div>

            {isDeptManager ? (
              <div className="mac-org-card__actions">
                <button
                  type="button"
                  className="mac-org-btn mac-org-btn--emphasis"
                  onClick={(e) => { e.stopPropagation(); handleAutoHookSubordinates(user); }}
                >
                  <Link2 size={13} />
                  自动挂靠（同部门人员）
                </button>
                <button
                  type="button"
                  className="mac-org-btn"
                  onClick={(e) => { e.stopPropagation(); handleToggleDeptManager(user, false); }}
                >
                  取消管理员
                </button>
              </div>
            ) : user.department_id ? (
              <div className="mac-org-card__actions">
                <button
                  type="button"
                  className="mac-org-btn"
                  onClick={(e) => { e.stopPropagation(); handleToggleDeptManager(user, true); }}
                >
                  设为部门管理员
                </button>
              </div>
            ) : null}
          </div>

          {hasChildren && (
            <button
              type="button"
              className={`mac-org-card__count${isCollapsed ? '' : ' is-open'}`}
              onClick={(e) => { e.stopPropagation(); toggleCollapse(user.id); }}
            >
              {children.length} 人
              <ChevronRight size={14} className="mac-org-chev" />
            </button>
          )}
        </div>
      </div>
    );
  };

  // 渲染树节点：按层级递归渲染，下级相对上级缩进（mac-org-children 每层 24px）
  const renderTreeNode = (node: TreeNode, forceOpen: boolean): React.ReactNode => {
    const isCollapsed = collapsedIds.has(node.id) && !forceOpen;
    const hasChildren = node.children.length > 0;
    return (
      <div key={node.id}>
        {renderPersonCard(node, node.children)}
        {hasChildren && !isCollapsed && (
          <div className="mac-org-children">
            {node.children.map((child) => renderTreeNode(child, forceOpen))}
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
    <div className="mac-page">
      {/* 顶部返回栏（对齐原型 glass-bar）：返回 + 标题 + 全部展开/折叠 + 视图切换 */}
      <div className="mac-org-head">
        <button type="button" className="mac-org-head__back" onClick={() => navigate('/admin/users')}>
          <ChevronLeft size={20} />
          <span>返回</span>
        </button>
        <h3 className="mac-org-head__title">人员结构配置</h3>
        <div className="mac-org-head__actions">
          <button type="button" className="mac-org-btn" onClick={toggleAll}>
            {allCollapsed ? '全部展开' : '全部折叠'}
          </button>
          <button
            type="button"
            className={`mac-org-btn${viewMode === 'group' ? ' mac-org-btn--active' : ''}`}
            onClick={() => setViewMode(viewMode === 'tree' ? 'group' : 'tree')}
          >
            {viewMode === 'tree' ? '切换分组视图' : '列表视图'}
          </button>
        </div>
      </div>

      {/* 操作提示 */}
      <p className="mac-org-tip">
        拖拽用户到目标人上即可设置汇报关系；点击用户卡片可选择 / 修改上级。
      </p>

      {/* 搜索 */}
      <div className="mac-search mac-search--card" style={{ marginBottom: 12 }}>
        <Search size={16} style={{ color: 'var(--mac-muted-fg)', flexShrink: 0 }} />
        <input
          className="mac-search__input"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="搜索姓名 / 部门…"
        />
      </div>

      {saving && (
        <div style={{ textAlign: 'center', padding: 8, color: 'var(--mac-blue-2)', fontSize: 12 }}>
          保存中...
        </div>
      )}

      {viewMode === 'group' ? (
        /* 分组视图：按部门（对齐原型） */
        flatList.length === 0 ? (
          <div className="mac-empty" style={{ padding: '40px 0' }}>
            {keyword ? '未找到匹配的人员' : '暂无人员数据'}
          </div>
        ) : (
          groupedByDept.map(([dept, deptUsers]) => (
            <section key={dept} style={{ marginBottom: 16 }}>
              <h4
                style={{
                  margin: '0 0 8px 4px', fontSize: 11.5, fontWeight: 600,
                  letterSpacing: '0.02em', color: 'var(--mac-muted-fg)',
                }}
              >
                {dept}
                <span style={{ fontWeight: 400 }}> {deptUsers.length} 人</span>
              </h4>
              {deptUsers.map((u) => renderPersonCard(u, []))}
            </section>
          ))
        )
      ) : (
        /* 树形视图：按层级递归缩进（自动挂靠后同部门人员移入其名下，缩进自然增加） */
        filteredTree.length === 0 ? (
          <div className="mac-empty" style={{ padding: '40px 0' }}>
            {keyword ? '未找到匹配的人员' : '暂无人员数据'}
          </div>
        ) : (
          <div>{filteredTree.map((node) => renderTreeNode(node, !!keyword.trim()))}</div>
        )
      )}

      {/* 循环引用用户修复 */}
      {orphanUsers.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 12, color: '#ad4545', marginBottom: 8 }}>
            ⚠️ 存在循环引用的用户（未出现在树中）：
          </div>
          {orphanUsers.map((u) => {
            const supervisor = u.supervisor_id ? userMap.get(u.supervisor_id) : null;
            return (
              <div
                key={u.id}
                style={{
                  padding: '10px 12px', background: '#fbecec', borderRadius: 12,
                  marginBottom: 6, fontSize: 12.5, color: '#ad4545',
                }}
              >
                {u.name || u.username} → 上级：{(supervisor && (supervisor.name || supervisor.username)) || u.supervisor_id || '无'}
              </div>
            );
          })}
          <button
            type="button"
            className="mac-org-btn"
            style={{ marginTop: 8, color: '#ad4545', borderColor: '#f0d4d4' }}
            onClick={() => {
              orphanUsers.forEach((u) => handleSetSupervisor(u.id, null));
            }}
          >
            重置这些用户的上级
          </button>
        </div>
      )}

      {/* 上级选择弹窗 */}
      <Popup visible={pickerVisible} onClose={() => setPickerVisible(false)} placement="bottom" showOverlay>
        <div className="mac-sheet" style={{ maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
          <h4 className="mac-sheet__title" style={{ marginBottom: 4 }}>
            设置上级：{selectUser?.name || selectUser?.username}
          </h4>
          <p className="mac-note" style={{ textAlign: 'left', marginBottom: 12 }}>
            选择一人作为直属上级，或点击「清除上级」设为顶层
          </p>
          <div style={{ overflow: 'auto', flex: 1 }}>
            <button
              type="button"
              className="mac-choice"
              style={{ color: '#ad4545' }}
              onClick={() => {
                if (selectUser) handleSetSupervisor(selectUser.id, null);
                setPickerVisible(false);
              }}
            >
              <span className="mac-choice__dot" style={{ borderColor: '#f0d4d4', background: '#fbecec' }}>
                <Check size={12} style={{ color: '#ad4545' }} />
              </span>
              <span className="mac-choice__label" style={{ color: '#ad4545' }}>
                清除上级（设为顶层）
              </span>
            </button>
            {selectableSupervisors.map((u) => (
              <ChoiceRow
                key={u.id}
                label={
                  [u.name || u.username, u.company, u.department].filter(Boolean).join(' · ') || u.username
                }
                checked={selectUser?.supervisor_id === u.id}
                onClick={() => {
                  if (selectUser) handleSetSupervisor(selectUser.id, u.id);
                  setPickerVisible(false);
                }}
              />
            ))}
          </div>
          <button
            type="button"
            className="mac-btn mac-btn--outline mac-btn--block"
            style={{ marginTop: 16 }}
            onClick={() => setPickerVisible(false)}
          >
            关闭
          </button>
        </div>
      </Popup>
    </div>
  );
}
