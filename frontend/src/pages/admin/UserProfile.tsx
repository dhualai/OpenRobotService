// 个人信息管理 —— GET /api/auth/me + PUT /api/admin/users/{username} + 头像上传（资源管理中心）
// 背景：微信登录用户默认 username 形如 wechat_xxxxxxxxxx（不可读），"我要摇人" 等处优先展示 name，
// 但用户此前从未设置过 name。本页提供自助修改昵称/头像的入口；首次进入（name 为空）时弹窗提示设置。
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Avatar, Input, Button, Dialog, Upload, Toast } from 'tdesign-mobile-react';
import { useAuthStore } from '@/stores/auth';
import { getMyProfile, updateMyProfile, uploadAvatar, avatarUrl } from '@/api/profile';

const FIRST_VISIT_PROMPT_KEY = 'profile_prompt_shown';

export default function UserProfile() {
  const navigate = useNavigate();
  const { username, name, avatarResourceId, setProfile, logout } = useAuthStore();
  const [nameDraft, setNameDraft] = useState(name);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const profile = await getMyProfile();
        setProfile({ name: profile.name || '', avatarResourceId: profile.avatar_resource_id ?? null });
        setNameDraft(profile.name || '');
        if (!profile.name && !sessionStorage.getItem(FIRST_VISIT_PROMPT_KEY)) {
          sessionStorage.setItem(FIRST_VISIT_PROMPT_KEY, '1');
          setShowPrompt(true);
        }
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const saveName = useCallback(async () => {
    const trimmed = nameDraft.trim();
    if (!trimmed) {
      Toast({ message: '姓名不能为空', theme: 'error' });
      return;
    }
    setSaving(true);
    try {
      await updateMyProfile(username, { name: trimmed });
      setProfile({ name: trimmed });
      Toast({ message: '保存成功', theme: 'success' });
    } catch (err) {
      Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSaving(false);
    }
  }, [nameDraft, username, setProfile]);

  const handleUploadAvatar = useCallback(
    async (file: File): Promise<{ status: 'success' | 'fail'; response: { url?: string } }> => {
      setUploading(true);
      try {
        const resource = await uploadAvatar(file, username);
        await updateMyProfile(username, { avatar_resource_id: resource.id });
        setProfile({ avatarResourceId: resource.id });
        Toast({ message: '头像已更新', theme: 'success' });
        return { status: 'success', response: { url: avatarUrl(resource.id) } };
      } catch (err) {
        Toast({ message: `上传失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        return { status: 'fail', response: {} };
      } finally {
        setUploading(false);
      }
    },
    [username, setProfile],
  );

  const handleLogout = useCallback(() => {
    Dialog.confirm?.({
      title: '确认登出',
      content: '确定要退出登录吗？',
      onConfirm: () => {
        logout();
        navigate('/login', { replace: true });
      },
    });
  }, [logout, navigate]);

  return (
    <div style={{ padding: 16 }}>
      <div
        style={{
          background: '#fff',
          borderRadius: 12,
          padding: 24,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 12,
          marginBottom: 16,
        }}
      >
        <Avatar
          size="80px"
          image={avatarResourceId ? avatarUrl(avatarResourceId) : undefined}
          icon={<span style={{ fontSize: 36 }}>👤</span>}
        />
        <Upload
          accept=".png,.jpg,.jpeg"
          max={1}
          disabled={uploading}
          requestMethod={async (files) => {
            const file = Array.isArray(files) ? files[0] : files;
            const raw = file?.raw;
            if (!raw) return { status: 'fail', response: {} };
            return handleUploadAvatar(raw);
          }}
        >
          <Button theme="light" size="small" loading={uploading}>
            {uploading ? '上传中...' : '更换头像'}
          </Button>
        </Upload>
      </div>

      <div style={{ background: '#fff', borderRadius: 12, padding: 16 }}>
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12, color: '#999', marginBottom: 6 }}>微信ID</div>
          <div style={{ fontSize: 14, color: '#333' }}>{loading ? '加载中...' : username}</div>
        </div>

        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12, color: '#999', marginBottom: 6 }}>姓名</div>
          <Input
            value={nameDraft}
            onChange={(v) => setNameDraft(String(v))}
            placeholder="请输入姓名，便于同事识别"
            maxlength={20}
          />
        </div>

        <Button theme="primary" block loading={saving} onClick={saveName}>
          保存
        </Button>
      </div>

      <Button theme="danger" variant="outline" block style={{ marginTop: 16 }} onClick={handleLogout}>
        登出
      </Button>

      <Dialog
        visible={showPrompt}
        title="完善个人信息"
        content="检测到您还未设置姓名和头像，设置后同事在「我要摇人」等场景能更容易认出您，是否现在设置？"
        confirmBtn="去设置"
        cancelBtn="稍后再说"
        onConfirm={() => setShowPrompt(false)}
        onCancel={() => setShowPrompt(false)}
        onClose={() => setShowPrompt(false)}
      />
    </div>
  );
}
