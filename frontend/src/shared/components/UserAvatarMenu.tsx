// 个人信息中心 —— 固定在 Navbar 右上角的用户入口
// 触发器：有头像显示头像缩略图，否则显示 UserCircleIcon（tdesign 图标）。
// 点击从顶部弹出面板：头像（点击可更换）/ 用户名称 / 用户 ID / 退出登录。
// 复用 api/profile.ts 的 getMyProfile / uploadAvatar / avatarUrl，无需后端改动。
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Popup, Button, Toast } from 'tdesign-mobile-react';
import { UserCircleIcon, CameraIcon, LogoutIcon } from 'tdesign-icons-react';
import { useAuthStore } from '@/stores/auth';
import { getMyProfile, updateMyProfile, uploadAvatar, avatarUrl } from '@/api/profile';

const ALLOWED_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
const MAX_SIZE = 5 * 1024 * 1024; // 5MB

export default function UserAvatarMenu() {
  const navigate = useNavigate();
  const { username, name, avatarResourceId, setProfile, logout } = useAuthStore();
  const [visible, setVisible] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 打开面板时拉取最新资料（补齐 name / avatarResourceId 到 store）
  useEffect(() => {
    if (!visible) return;
    (async () => {
      try {
        const profile = await getMyProfile();
        setProfile({
          name: profile.name || undefined,
          avatarResourceId: profile.avatar_resource_id ?? null,
        });
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

  const handleLogout = () => {
    setVisible(false);
    logout();
    navigate('/login', { replace: true });
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
                <img src={avatarSrc} alt="头像" />
              ) : (
                <UserCircleIcon size="56px" />
              )}
              <span className="user-panel__avatar-badge">
                <CameraIcon size="12px" />
              </span>
            </button>
            <div className="user-panel__info">
              <div className="user-panel__name">{displayName}</div>
              <div className="user-panel__id">ID: {username || '-'}</div>
            </div>
          </div>

          <div className="user-panel__hint">
            {uploading ? '头像上传中…' : '点击头像可更换'}
          </div>

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
    </>
  );
}
