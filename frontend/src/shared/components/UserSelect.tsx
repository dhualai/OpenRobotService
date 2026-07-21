// 用户选择器：下拉展开用户列表 + 模糊搜索（按姓名/账号过滤）
// 用于「升级」等需要指定目标用户的场景。浮层用原生 fixed 实现，
// 避免与页面已有的 tdesign Popup（如工单详情）嵌套冲突。
import { useEffect, useMemo, useState } from 'react';
import { Toast } from 'tdesign-mobile-react';
import { getUsers } from '@/api/users';
import type { UserItem } from '@/api/users';

interface Props {
  value?: string | null;
  onChange?: (user: UserItem) => void;
  placeholder?: string;
}

// 模块级缓存，5 分钟内复用，减少重复请求
let userCache: UserItem[] | null = null;
let userCacheTs = 0;

export default function UserSelect({ value, onChange, placeholder = '请选择升级对象' }: Props) {
  const [visible, setVisible] = useState(false);
  const [users, setUsers] = useState<UserItem[]>(userCache || []);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [error, setError] = useState('');

  const selected = useMemo(
    () => users.find((u) => u.id === value) || null,
    [users, value],
  );

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

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    if (!kw) return users;
    return users.filter(
      (u) =>
        (u.name || '').toLowerCase().includes(kw) ||
        (u.username || '').toLowerCase().includes(kw),
    );
  }, [users, keyword]);

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
              <span>选择升级对象</span>
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
                filtered.map((u) => (
                  <div
                    key={u.id}
                    className={`user-select__item ${u.id === value ? 'is-selected' : ''}`}
                    onClick={() => handlePick(u)}
                  >
                    <div className="user-select__item-name">{u.name || u.username}</div>
                    <div className="user-select__item-meta">
                      <span>{u.username}</span>
                      {u.status && (
                        <span className={`user-select__status user-select__status--${u.status}`}>
                          {u.status}
                        </span>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
