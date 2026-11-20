// 用户选择器：下拉展开用户列表 + 模糊搜索（按姓名/账号过滤）
// 用于「升级」「项目负责人」等需要指定目标用户的场景。浮层用原生 fixed 实现，
// 避免与页面已有的 tdesign Popup（如工单详情）嵌套冲突。
import { useEffect, useMemo, useState } from 'react';
import { Toast } from 'tdesign-mobile-react';
import { getUsers } from '@/api/users';
import type { UserItem } from '@/api/users';

interface Props {
  value?: string | null;
  onChange?: (user: UserItem) => void;
  placeholder?: string;
  title?: string;
  /** 临时：置顶用户 id（如项目对接人）；无则不置顶 */
  pinUserId?: string | null;
  /** 置顶标注文案，默认「项目对接人」 */
  pinLabel?: string;
  /** 批量置顶的用户 id（如本项目的成员），排在其余人员之前并标注「项目成员」 */
  pinUserIds?: string[];
}

// 模块级缓存，5 分钟内复用，减少重复请求
let userCache: UserItem[] | null = null;
let userCacheTs = 0;

export default function UserSelect({
  value,
  onChange,
  placeholder = '请选择',
  title = '选择人员',
  pinUserId = null,
  pinLabel = '项目对接人',
  pinUserIds,
}: Props) {
  const [visible, setVisible] = useState(false);
  const [users, setUsers] = useState<UserItem[]>(userCache || []);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [keyword, setKeyword] = useState('');

  const selected = useMemo(() => users.find((u) => u.id === value) || null, [users, value]);

  const loadUsers = async () => {
    const now = Date.now();
    if (userCache && now - userCacheTs < 5 * 60 * 1000) {
      setUsers(userCache);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const list = await getUsers();
      userCache = list;
      userCacheTs = now;
      setUsers(list);
    } catch (e) {
      setError('获取用户列表失败');
      Toast({ message: `获取用户列表失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (visible) loadUsers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  /** 本项目成员：排在其余人员之前（顺序沿用列表原顺序） */
  const memberIds = useMemo(
    () => new Set((pinUserIds ?? []).map((id) => (id || '').trim()).filter(Boolean)),
    [pinUserIds],
  );

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    const matched = kw
      ? users.filter(
          (u) => (u.name || '').toLowerCase().includes(kw) || (u.username || '').toLowerCase().includes(kw),
        )
      : [...users];
    const list = memberIds.size
      ? [
          ...matched.filter((u) => memberIds.has(u.id)),
          ...matched.filter((u) => !memberIds.has(u.id)),
        ]
      : matched;
    const pin = (pinUserId || '').trim();
    if (!pin) return list;
    const idx = list.findIndex((u) => u.id === pin);
    if (idx > 0) {
      const pinned = list[idx];
      return [pinned, ...list.slice(0, idx), ...list.slice(idx + 1)];
    }
    if (idx === 0) return list;
    // 对接人不在当前列表（过滤掉或 assignable 未返回）时，补一条置顶占位，避免「有对接人却看不见」
    if (!kw) {
      const stub: UserItem = {
        id: pin,
        username: pin,
        name: pinLabel || pin,
        status: 'active',
      };
      return [stub, ...list];
    }
    return list;
  }, [users, keyword, pinUserId, pinLabel, memberIds]);

  const handlePick = (u: UserItem) => {
    onChange?.(u);
    setVisible(false);
    setKeyword('');
  };

  return (
    <div className="user-select">
      <button type="button" className="user-select__trigger" onClick={() => setVisible(true)}>
        {selected ? (
          <span className="user-select__trigger-text">{selected.name || selected.username}</span>
        ) : (
          <span className="user-select__trigger-placeholder">{placeholder}</span>
        )}
        <span className="user-select__arrow">▾</span>
      </button>

      {visible && (
        <>
          <div className="user-select__mask" onClick={() => setVisible(false)} />
          <div className="user-select__panel">
            <div className="user-select__panel-header">
              <span>{title}</span>
              <span className="user-select__close" onClick={() => setVisible(false)}>✕</span>
            </div>
            <input
              className="tasks-search user-select__search"
              placeholder="搜索姓名 / 账号…"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
            <div className="user-select__list">
              {loading ? (
                <div className="user-select__empty">加载中…</div>
              ) : error ? (
                <div className="user-select__empty">{error}</div>
              ) : filtered.length === 0 ? (
                <div className="user-select__empty">未找到匹配用户</div>
              ) : (
                filtered.map((u) => {
                  const isPinned = !!(pinUserId && u.id === pinUserId);
                  const isMember = memberIds.has(u.id);
                  // 对接人标注优先于「项目成员」；两者都没有则不显示角标
                  const badge = isPinned ? pinLabel : isMember ? '项目成员' : '';
                  return (
                    <div
                      key={u.id}
                      className={`user-select__item ${u.id === value ? 'is-selected' : ''}`}
                      onClick={() => handlePick(u)}
                    >
                      <div className="user-select__item-name" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <span>{u.name || u.username}</span>
                        {badge && (
                          <span
                            style={{
                              fontSize: 10,
                              lineHeight: '16px',
                              padding: '0 6px',
                              borderRadius: 3,
                              color: 'var(--blue-2)',
                              background: 'var(--blue-soft)',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {badge}
                          </span>
                        )}
                      </div>
                      <div className="user-select__item-meta">
                        <span>{badge || u.username}</span>
                        {u.status && (
                          <span className={`user-select__status user-select__status--${u.status}`}>
                            {u.status}
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
