// 个人信息中心 —— 固定在 Navbar 右上角的用户入口
// 触发器：有头像显示头像缩略图，否则显示 UserCircleIcon（tdesign 图标）。
// 点击从顶部弹出面板：头像（点击可更换）/ 姓名（点击铅笔可编辑）/ 用户 ID / 退出登录。
// 复用 api/profile.ts 的 getMyProfile / updateMyProfile / uploadAvatar / avatarUrl，无需后端改动。
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Popup, Button, Toast, Input } from 'tdesign-mobile-react';
import { UserCircleIcon, CameraIcon, LogoutIcon, EditIcon, CheckIcon, CloseIcon } from 'tdesign-icons-react';
import { useAuthStore } from '@/stores/auth';
import { getMyProfile, updateMyProfile, uploadAvatar, avatarUrl } from '@/api/profile';

const ALLOWED_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
const MAX_SIZE = 5 * 1024 * 1024; // 5MB
const MAX_NAME_LENGTH = 20;

export default function UserAvatarMenu() {
  const navigate = useNavigate();
  const { username, name, avatarResourceId, setProfile, logout } = useAuthStore();
  const [visible, setVisible] = useState(false);
  const [previewVisible, setPreviewVisible] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [newName, setNewName] = useState('');
  const [savingName, setSavingName] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 打开面板时拉取最新昵称（头像 id 不在此覆盖——见下注释）
  useEffect(() => {
    if (!visible) return;
    (async () => {
      try {
        const profile = await getMyProfile();
        // 仅刷新昵称；头像 id 已由登录/刷新时 fetchUserDetails（/users/{username}/detail）写入 store，
        // 与 /auth/me 同源，无需重复覆盖——避免 /auth/me 返回 null 时把 store 里正确的头像 id 清空，
        // 导致「打开面板头像显示一下又变回图标」。
        setProfile({ name: profile.name || undefined });
      } catch { /* 静默失败，展示 store 已有信息 */ }
    })();
  }, [visible, setProfile]);

  const handlePickAvatar = () => {
    if (uploading) return;
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ''; // 允许重复选择同一文件
    if (!file) return;
    if (!ALLOWED_TYPES.includes(file.type)) {
      Toast({ message: '仅支持 JPG/PNG/WebP/GIF 图片', theme: 'warning' });
      return;
    }
    if (file.size > MAX_SIZE) {
      Toast({ message: '图片不能超过 5MB', theme: 'warning' });
      return;
    }
    setUploading(true);
    try {
      const resource = await uploadAvatar(file, username);
      await updateMyProfile(username, { avatar_resource_id: resource.id });
      setProfile({ avatarResourceId: resource.id });
      Toast({ message: '头像已更新', theme: 'success' });
    } catch (err) {
      Toast({ message: `上传失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setUploading(false);
    }
  };

  // 姓名编辑：进入编辑态时以当前姓名填充输入框
  const handleStartEditName = () => {
    setNewName(name || '');
    setEditingName(true);
  };

  const handleCancelEditName = () => {
    setEditingName(false);
    setNewName('');
  };

  const handleSaveName = async () => {
    const trimmed = newName.trim();
    if (!trimmed) {
      Toast({ message: '姓名不能为空', theme: 'warning' });
      return;
    }
    if (trimmed === name) {
      setEditingName(false);
      return;
    }
    setSavingName(true);
    try {
      await updateMyProfile(username, { name: trimmed });
      setProfile({ name: trimmed });
      setEditingName(false);
      Toast({ message: '姓名已更新', theme: 'success' });
    } catch (err) {
      Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSavingName(false);
    }
  };

  const handleLogout = () => {
    setVisible(false);
    logout();
    navigate('/login?reason=logout', { replace: true });
  };

  const displayName = name || username || '用户';
  const avatarSrc = avatarResourceId ? avatarUrl(avatarResourceId) : '';

  return (
    <>
      {/* 隐藏的文件选择器 */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/gif"
        style={{ display: 'none' }}
        onChange={handleFileChange}
      />

      {/* Navbar 右上角触发器 */}
      <button
        className="user-avatar-menu__trigger"
        onClick={() => setVisible(true)}
        aria-label="个人信息中心"
      >
        {avatarSrc ? (
          <img className="user-avatar-menu__thumb" src={avatarSrc} alt="头像" />
        ) : (
          <UserCircleIcon size="24px" />
        )}
      </button>

      {/* 个人信息面板（顶部弹出） */}
      <Popup visible={visible} placement="top" onVisibleChange={setVisible}>
        <div className="user-panel">
          <div className="user-panel__profile">
            <button className="user-panel__avatar" onClick={handlePickAvatar} aria-label="更换头像">
              {avatarSrc ? (
                <img
                  src={avatarSrc}
                  alt="头像"
                  onClick={(e) => {
                    e.stopPropagation();
                    setPreviewVisible(true);
                  }}
                />
              ) : (
                <UserCircleIcon size="56px" />
              )}
              <span
                className="user-panel__avatar-badge"
                onClick={(e) => {
                  e.stopPropagation();
                  handlePickAvatar();
                }}
              >
                <CameraIcon size="12px" />
              </span>
            </button>
            <div className="user-panel__info">
              {editingName ? (
                <div className="user-panel__name-edit">
                  <Input
                    value={newName}
                    onChange={(v) => setNewName(String(v))}
                    placeholder="请输入姓名"
                    maxlength={MAX_NAME_LENGTH}
                    disabled={savingName}
                    clearable
                  />
                  <button
                    className="user-panel__name-action user-panel__name-action--confirm"
                    onClick={handleSaveName}
                    disabled={savingName}
                    aria-label="确认修改"
                  >
                    <CheckIcon size="18px" />
                  </button>
                  <button
                    className="user-panel__name-action"
                    onClick={handleCancelEditName}
                    disabled={savingName}
                    aria-label="取消修改"
                  >
                    <CloseIcon size="18px" />
                  </button>
                </div>
              ) : (
                <div className="user-panel__name-row">
                  <span className="user-panel__name">{displayName}</span>
                  <button
                    className="user-panel__name-edit-btn"
                    onClick={handleStartEditName}
                    aria-label="编辑姓名"
                  >
                    <EditIcon size="15px" />
                  </button>
                </div>
              )}
              <div className="user-panel__id">ID: {username || '-'}</div>
            </div>
          </div>

          <div className="user-panel__hint">
            {uploading ? '头像上传中…' : '点击头像可更换'}
          </div>

          <Button
            block
            theme="primary"
            variant="outline"
            style={{ marginBottom: 8 }}
            onClick={() => {
              setVisible(false);
              navigate('/admin/profile');
            }}
          >
            完善个人资料
          </Button>

          <Button
            block
            theme="danger"
            variant="outline"
            icon={<LogoutIcon size="18px" />}
            onClick={handleLogout}
          >
            退出登录
          </Button>
        </div>
      </Popup>

      {/* 头像全屏预览：点击上传后的头像图片进入，点击遮罩关闭 */}
      {previewVisible && avatarSrc && (
        <div className="avatar-fullscreen" onClick={() => setPreviewVisible(false)}>
          <img className="avatar-fullscreen__img" src={avatarSrc} alt="头像大图" />
        </div>
      )}
    </>
  );
}
