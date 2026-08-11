// 设置用户 —— 迁移用户数据并合并账号
// 流程：选择源用户(A) → 选择目标用户(B) → 确认执行迁移
// 接口：
//   GET  /users/?limit=999999   用户列表
//   POST /users/migrate-user    迁移用户数据（task assigned_to + 派单字段 + 删除源用户）
import { useState, useEffect, useCallback, useMemo } from 'react';
import { Button, Toast, Loading, Popup, Tag, Dialog } from 'tdesign-mobile-react';
import ClearableInput from '@/shared/components/ClearableInput';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

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

  return (
    <div className="admin-view">
      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* 说明卡片 */}
        <div style={{ background: '#fffbe6', borderRadius: 12, padding: 14, fontSize: 12, color: '#93763a', lineHeight: 1.8 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>操作说明</div>
          将源用户(A)的任务、派单字段迁移到目标用户(B)，并删除源用户。此操作不可逆，请谨慎执行。
        </div>

        {/* 源用户选择 */}
        <div style={{ background: '#fff', borderRadius: 12, padding: 16 }}>
          <PickerField
            label="源用户（将被删除）"
            value={sourceUser ? `${sourceUser.name || ''} (${sourceUser.username})` : ''}
            placeholder="点击选择源用户"
            onClick={() => { setSearchKeyword(''); setSourcePickerVisible(true); }}
            highlightColor={sourceUser ? '#e34d59' : undefined}
          />
          {sourceUser && (
            <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              <Tag theme="danger" variant="light" size="small">id: {sourceUser.id}</Tag>
              {sourceUser.department && <Tag theme="default" variant="light" size="small">{sourceUser.department}</Tag>}
            </div>
          )}
        </div>

        {/* 箭头指示 */}
        <div style={{ textAlign: 'center', color: '#999', fontSize: 24 }}>↓</div>

        {/* 目标用户选择 */}
        <div style={{ background: '#fff', borderRadius: 12, padding: 16 }}>
          <PickerField
            label="目标用户（保留账号）"
            value={targetUser ? `${targetUser.name || ''} (${targetUser.username})` : ''}
            placeholder="点击选择目标用户"
            onClick={() => { setSearchKeyword(''); setTargetPickerVisible(true); }}
            highlightColor={targetUser ? '#00a870' : undefined}
          />
          {targetUser && (
            <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              <Tag theme="success" variant="light" size="small">id: {targetUser.id}</Tag>
              {targetUser.department && <Tag theme="default" variant="light" size="small">{targetUser.department}</Tag>}
            </div>
          )}
        </div>

        {/* 执行按钮 */}
        <Button
          theme="danger"
          block
          size="large"
          disabled={!sourceUser || !targetUser}
          onClick={() => setConfirmVisible(true)}
        >
          执行迁移
        </Button>
      </div>

      {/* 源用户选择弹窗 */}
      <Popup visible={sourcePickerVisible} onClose={() => setSourcePickerVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
          <h4 style={{ marginBottom: 12 }}>选择源用户（将被删除）</h4>
          <ClearableInput
            value={searchKeyword}
            onChange={(v) => setSearchKeyword(String(v))}
            placeholder="搜索姓名 / 用户名"
            style={{ marginBottom: 12 }}
          />
          <div style={{ overflow: 'auto', flex: 1 }}>
            {filteredUsersForPicker
              .filter((u) => !targetUser || u.id !== targetUser.id)
              .map((u) => (
                <div
                  key={u.id}
                  onClick={() => handleSelectSource(u)}
                  style={{
                    padding: '12px 4px', borderBottom: '1px solid #f5f5f5', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  }}
                >
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <span style={{ fontSize: 14, fontWeight: 500 }}>{u.name || u.username}</span>
                    <span style={{ fontSize: 12, color: '#999' }}>{u.username} · {u.id}</span>
                  </div>
                  {sourceUser?.id === u.id && <span style={{ color: '#e34d59' }}>✓</span>}
                </div>
              ))}
            {filteredUsersForPicker.length === 0 && (
              <div style={{ textAlign: 'center', padding: 30, color: '#999' }}>暂无匹配用户</div>
            )}
          </div>
        </div>
      </Popup>

      {/* 目标用户选择弹窗 */}
      <Popup visible={targetPickerVisible} onClose={() => setTargetPickerVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
          <h4 style={{ marginBottom: 12 }}>选择目标用户（保留账号）</h4>
          <ClearableInput
            value={searchKeyword}
            onChange={(v) => setSearchKeyword(String(v))}
            placeholder="搜索姓名 / 用户名"
            style={{ marginBottom: 12 }}
          />
          <div style={{ overflow: 'auto', flex: 1 }}>
            {filteredUsersForPicker
              .filter((u) => !sourceUser || u.id !== sourceUser.id)
              .map((u) => (
                <div
                  key={u.id}
                  onClick={() => handleSelectTarget(u)}
                  style={{
                    padding: '12px 4px', borderBottom: '1px solid #f5f5f5', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  }}
                >
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <span style={{ fontSize: 14, fontWeight: 500 }}>{u.name || u.username}</span>
                    <span style={{ fontSize: 12, color: '#999' }}>{u.username} · {u.id}</span>
                  </div>
                  {targetUser?.id === u.id && <span style={{ color: '#00a870' }}>✓</span>}
                </div>
              ))}
            {filteredUsersForPicker.length === 0 && (
              <div style={{ textAlign: 'center', padding: 30, color: '#999' }}>暂无匹配用户</div>
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
          <p>1. 将源用户 <strong style={{ color: '#e34d59' }}>{sourceUser?.name || sourceUser?.username}</strong> 的任务迁移到目标用户</p>
          <p>2. 将源用户的派单字段（部门/责任模块/职级/职责画像）拷贝给 <strong style={{ color: '#00a870' }}>{targetUser?.name || targetUser?.username}</strong></p>
          <p>3. <strong style={{ color: '#e34d59' }}>删除源用户</strong></p>
          <p style={{ color: '#999', marginTop: 8 }}>此操作不可逆，请确认！</p>
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

function PickerField({ label, value, placeholder, onClick, highlightColor }: { label: string; value: string; placeholder: string; onClick: () => void; highlightColor?: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <label style={{ fontSize: 12, color: '#999' }}>{label}</label>
      <div
        onClick={onClick}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          fontSize: 14, background: '#f8fafc', borderRadius: 8, padding: '12px 14px', cursor: 'pointer',
        }}
      >
        <span style={{ color: value ? (highlightColor || '#333') : '#bbb' }}>{value || placeholder}</span>
        <span style={{ color: '#999' }}>›</span>
      </div>
    </div>
  );
}
