// 设置用户 —— 迁移用户数据并合并账号
// 流程：选择源用户(A) → 选择目标用户(B) → 确认执行迁移
// 接口：
//   GET  /users/?limit=999999   用户列表
//   POST /users/migrate-user    迁移用户数据（task assigned_to + 派单字段 + 删除源用户）
// 样式参考 macaron user-transfer 页：说明卡 + 两个 surface-card 选择区 + 箭头 + 执行按钮 + 弹层选择。
import { useState, useEffect, useCallback, useMemo } from 'react';
import { Toast, Loading, Popup, Dialog } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import {
  MacChevronRight, MacArrowDown, MacCheck, MacSearch,
} from '@/shared/components/macaronIcons';

interface UserItem {
  id: string;
  username: string;
  name?: string | null;
  status?: string;
  department?: string | null;
}

export default function UserSetup() {
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const [users, setUsers] = useState<UserItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  // 选中的源用户(A)和目标用户(B)
  const [sourceUser, setSourceUser] = useState<UserItem | null>(null);
  const [targetUser, setTargetUser] = useState<UserItem | null>(null);

  // 弹窗控制
  const [sourcePickerVisible, setSourcePickerVisible] = useState(false);
  const [targetPickerVisible, setTargetPickerVisible] = useState(false);

  // 搜索关键词（弹窗内）
  const [searchKeyword, setSearchKeyword] = useState('');

  // 确认弹窗
  const [confirmVisible, setConfirmVisible] = useState(false);

  const loadUsers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request<UserItem[]>('/users/?limit=999', { skipCache: true });
      setUsers(normalizeList<UserItem>(data));
    } catch (err) {
      Toast({ message: `加载用户列表失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadUsers(); }, [loadUsers]);

  // 弹窗内过滤后的用户列表（排除已选的对方用户）
  const filteredUsersForPicker = useMemo(() => {
    const kw = searchKeyword.trim().toLowerCase();
    return users.filter((u) => {
      if (kw) {
        const text = `${u.name || ''} ${u.username}`.toLowerCase();
        if (!text.includes(kw)) return false;
      }
      return true;
    });
  }, [users, searchKeyword]);

  const handleSelectSource = (u: UserItem) => {
    if (targetUser && u.id === targetUser.id) {
      Toast({ message: '源用户不能与目标用户相同', theme: 'warning' });
      return;
    }
    setSourceUser(u);
    setSourcePickerVisible(false);
    setSearchKeyword('');
  };

  const handleSelectTarget = (u: UserItem) => {
    if (sourceUser && u.id === sourceUser.id) {
      Toast({ message: '目标用户不能与源用户相同', theme: 'warning' });
      return;
    }
    setTargetUser(u);
    setTargetPickerVisible(false);
    setSearchKeyword('');
  };

  const handleMigrate = async () => {
    if (!sourceUser || !targetUser) {
      Toast({ message: '请先选择源用户和目标用户', theme: 'warning' });
      return;
    }
    setSubmitting(true);
    try {
      await request('/users/migrate-user', {
        method: 'POST',
        body: JSON.stringify({
          source_user_id: sourceUser.id,
          target_user_id: targetUser.id,
        }),
      });
      Toast({ message: '迁移成功', theme: 'success' });
      setSourceUser(null);
      setTargetUser(null);
      setConfirmVisible(false);
      await loadUsers();
    } catch (err) {
      Toast({ message: `迁移失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <Loading text="加载中..." />;

  const sameUserPicked = !!sourceUser && !!targetUser && sourceUser.id === targetUser.id;

  return (
    <div className="admin-view">
      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* 说明卡片 */}
        <div className="mac-notice">
          <p className="mac-notice__title">操作说明</p>
          <p className="mac-notice__text">
            将源用户(A)的任务、派单字段迁移到目标用户(B)，并删除源用户。
            <span style={{ fontWeight: 600, color: 'var(--mac-fg)' }}>此操作不可逆，请谨慎执行。</span>
          </p>
        </div>

        {/* 源用户选择 */}
        <section className="mac-card mac-card--pad">
          <p style={{ margin: 0, fontSize: 11.5, color: 'var(--mac-muted-fg)' }}>源用户（将被删除）</p>
          <button
            type="button"
            className="mac-selector mac-selector--soft"
            style={{ marginTop: 8 }}
            onClick={() => { setSearchKeyword(''); setSourcePickerVisible(true); }}
          >
            <span className="mac-selector__body">
              {sourceUser ? (
                <span className="mac-selector__name">
                  {sourceUser.name || sourceUser.username}（{sourceUser.username}）
                </span>
              ) : (
                <span className="mac-selector__placeholder">点击选择源用户</span>
              )}
            </span>
            <span className="mac-selector__chevron"><MacChevronRight size={16} /></span>
          </button>
          {sourceUser && (
            <div className="mac-meta-line">
              <span className="mac-chip mac-chip--red">id: {sourceUser.id}</span>
              {sourceUser.department && <span className="mac-chip mac-chip--soft">{sourceUser.department}</span>}
            </div>
          )}
        </section>

        {/* 箭头指示 */}
        <div style={{ textAlign: 'center', color: 'var(--mac-muted-fg)', display: 'flex', justifyContent: 'center' }}>
          <MacArrowDown size={16} />
        </div>

        {/* 目标用户选择 */}
        <section className="mac-card mac-card--pad">
          <p style={{ margin: 0, fontSize: 11.5, color: 'var(--mac-muted-fg)' }}>目标用户（保留账号）</p>
          <button
            type="button"
            className="mac-selector mac-selector--soft"
            style={{ marginTop: 8 }}
            onClick={() => { setSearchKeyword(''); setTargetPickerVisible(true); }}
          >
            <span className="mac-selector__body">
              {targetUser ? (
                <span className="mac-selector__name">
                  {targetUser.name || targetUser.username}（{targetUser.username}）
                </span>
              ) : (
                <span className="mac-selector__placeholder">点击选择目标用户</span>
              )}
            </span>
            <span className="mac-selector__chevron"><MacChevronRight size={16} /></span>
          </button>
          {targetUser && (
            <div className="mac-meta-line">
              <span className="mac-chip mac-chip--blue">id: {targetUser.id}</span>
              {targetUser.department && <span className="mac-chip mac-chip--soft">{targetUser.department}</span>}
            </div>
          )}
        </section>

        {/* 执行按钮 */}
        <button
          type="button"
          className="mac-btn mac-btn--lg mac-btn--primary mac-btn--block"
          style={{ marginTop: 4 }}
          disabled={!sourceUser || !targetUser || sameUserPicked}
          onClick={() => setConfirmVisible(true)}
        >
          执行迁移
        </button>
        {sameUserPicked && (
          <p style={{ margin: 0, textAlign: 'center', fontSize: 11.5, color: 'var(--mac-muted-fg)' }}>
            源用户与目标用户不能相同
          </p>
        )}
      </div>

      {/* 源用户选择弹窗 */}
      <Popup visible={sourcePickerVisible} onClose={() => setSourcePickerVisible(false)} placement="bottom" showOverlay>
        <div className="mac-sheet" style={{ maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
          <h4 className="mac-sheet__title">选择源用户（将被删除）</h4>
          <div className="mac-search" style={{ marginBottom: 8 }}>
            <MacSearch size={16} />
            <input
              className="mac-search__input"
              value={searchKeyword}
              onChange={(e) => setSearchKeyword(e.target.value)}
              placeholder="搜索姓名 / 用户名"
            />
          </div>
          <div style={{ overflow: 'auto', flex: 1 }}>
            {filteredUsersForPicker
              .filter((u) => !targetUser || u.id !== targetUser.id)
              .map((u) => (
                <button
                  key={u.id}
                  type="button"
                  className="mac-list-item"
                  onClick={() => handleSelectSource(u)}
                >
                  <span style={{ minWidth: 0, flex: 1 }}>
                    <span className="mac-list-item__name">{u.name || u.username}</span>
                    <span className="mac-list-item__sub">{u.username} · {u.id}</span>
                  </span>
                  {sourceUser?.id === u.id && (
                    <span style={{ color: 'var(--mac-blue-2)', display: 'inline-flex' }}><MacCheck size={16} /></span>
                  )}
                </button>
              ))}
            {filteredUsersForPicker.length === 0 && (
              <div className="mac-empty">暂无匹配用户</div>
            )}
          </div>
        </div>
      </Popup>

      {/* 目标用户选择弹窗 */}
      <Popup visible={targetPickerVisible} onClose={() => setTargetPickerVisible(false)} placement="bottom" showOverlay>
        <div className="mac-sheet" style={{ maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
          <h4 className="mac-sheet__title">选择目标用户（保留账号）</h4>
          <div className="mac-search" style={{ marginBottom: 8 }}>
            <MacSearch size={16} />
            <input
              className="mac-search__input"
              value={searchKeyword}
              onChange={(e) => setSearchKeyword(e.target.value)}
              placeholder="搜索姓名 / 用户名"
            />
          </div>
          <div style={{ overflow: 'auto', flex: 1 }}>
            {filteredUsersForPicker
              .filter((u) => !sourceUser || u.id !== sourceUser.id)
              .map((u) => (
                <button
                  key={u.id}
                  type="button"
                  className="mac-list-item"
                  onClick={() => handleSelectTarget(u)}
                >
                  <span style={{ minWidth: 0, flex: 1 }}>
                    <span className="mac-list-item__name">{u.name || u.username}</span>
                    <span className="mac-list-item__sub">{u.username} · {u.id}</span>
                  </span>
                  {targetUser?.id === u.id && (
                    <span style={{ color: 'var(--mac-blue-2)', display: 'inline-flex' }}><MacCheck size={16} /></span>
                  )}
                </button>
              ))}
            {filteredUsersForPicker.length === 0 && (
              <div className="mac-empty">暂无匹配用户</div>
            )}
          </div>
        </div>
      </Popup>

      {/* 确认弹窗 */}
      <Dialog
        visible={confirmVisible}
        title="确认执行迁移"
        confirmBtn={{ content: '确认迁移', theme: 'danger' }}
        cancelBtn="取消"
        onConfirm={handleMigrate}
        onClose={() => setConfirmVisible(false)}
        closeOnOverlayClick={!submitting}
      >
        <div style={{ fontSize: 13, lineHeight: 1.8 }}>
          <p>即将执行以下操作：</p>
          <p>1. 将源用户 <strong style={{ color: '#ad4545' }}>{sourceUser?.name || sourceUser?.username}</strong> 的任务迁移到目标用户</p>
          <p>2. 将源用户的派单字段（部门/责任模块/职级/职责画像）拷贝给 <strong style={{ color: 'var(--mac-blue-1)' }}>{targetUser?.name || targetUser?.username}</strong></p>
          <p>3. <strong style={{ color: '#ad4545' }}>删除源用户</strong></p>
          <p style={{ color: 'var(--mac-muted-fg)', marginTop: 8 }}>此操作不可逆，请确认！</p>
        </div>
      </Dialog>

      {submitting && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.3)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200 }}>
          <Loading text="迁移中..." />
        </div>
      )}
    </div>
  );
}
