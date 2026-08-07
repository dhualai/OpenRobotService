// 个人信息中心 —— GET /api/auth/me + PUT /api/admin/users/{username} + 头像上传（资源管理中心）
// 背景：微信登录用户默认 username 形如 wechat_xxxxxxxxxx（不可读），"我要摇人" 等处优先展示 name，
// 但用户此前从未设置过 name。本页提供自助修改昵称/头像的入口；首次进入（name 为空）时弹窗提示设置。
// 头像字段策略：进入页面时 /auth/me 返回的头像 id 不再覆盖 store（与登录/刷新时 fetchUserDetails 同源），
// 避免接口偶发缺字段时把已显示的头像清成上传图标（本次修复根因之一见 backend/app/core/auth_service.py）。
// 个人中心可编辑字段：姓名 / 公司 / 部门 / USP 账户(external_credentials.usp.username) / USP 密码。
// username 为系统内用户标识，只读展示；USP 密码在后端以 pbkdf2_sha256 哈希存储，前端不回显，留空表示不修改。
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Input, Button, Dialog, Upload, Toast, Popup } from 'tdesign-mobile-react';
import { UserCircleIcon, AddIcon } from 'tdesign-icons-react';
import { useAuthStore } from '@/stores/auth';
import { getMyProfile, getProfileOptions, updateMyProfile, uploadAvatar, avatarUrl, type MyProfile } from '@/api/profile';

const FIRST_VISIT_PROMPT_KEY = 'profile_prompt_shown';

// 原生 select 样式，与 TDesign Input 视觉对齐
const selectStyle: React.CSSProperties = {
  flex: 1,
  minWidth: 0,
  height: 40,
  padding: '0 12px',
  fontSize: 14,
  border: '1px solid #e7e7e7',
  borderRadius: 6,
  background: '#fff',
  color: '#333',
  appearance: 'none',
  WebkitAppearance: 'none',
};

export default function UserProfile() {
  const navigate = useNavigate();
  const { username, name, avatarResourceId, setProfile, logout } = useAuthStore();

  // 表单草稿
  const [nameDraft, setNameDraft] = useState(name);
  const [companyDraft, setCompanyDraft] = useState('');
  const [departmentDraft, setDepartmentDraft] = useState('');
  const [uspUsernameDraft, setUspUsernameDraft] = useState('');
  // USP 密码：仅用于「设置新密码」，留空表示不修改；后端存储的是哈希，前端不回显
  const [uspPasswordDraft, setUspPasswordDraft] = useState('');

  // 公司/部门下拉可选项（来自 users 表去重值，点「添加」可自定义新值）
  const [companyOptions, setCompanyOptions] = useState<string[]>([]);
  const [departmentOptions, setDepartmentOptions] = useState<string[]>([]);
  // 「添加」弹窗：addingField 标记当前在添加哪个字段
  const [addingField, setAddingField] = useState<'company' | 'department' | null>(null);
  const [addInputValue, setAddInputValue] = useState('');

  // 进入页面时从 /me 拉取的原始值，用于判断哪些字段发生变更
  const [original, setOriginal] = useState<{
    name: string;
    company: string;
    department: string;
    uspUsername: string;
  }>({ name: '', company: '', department: '', uspUsername: '' });

  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);
  const [loading, setLoading] = useState(true);
  const [avatarError, setAvatarError] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [profile, options] = await Promise.all([getMyProfile(), getProfileOptions().catch(() => ({ companies: [], departments: [] }))]);
        // 仅刷新昵称；头像 id 已由登录/刷新时 fetchUserDetails 写入 store，
        // 与 /auth/me 同源，无需重复覆盖（避免接口缺字段时把头像弄丢）。
        setProfile({ name: profile.name || '' });
        const snapshot = {
          name: profile.name || '',
          company: profile.company || '',
          department: profile.department || '',
          uspUsername: profile.external_credentials?.usp?.username || '',
        };
        setOriginal(snapshot);
        setNameDraft(snapshot.name);
        setCompanyDraft(snapshot.company);
        setDepartmentDraft(snapshot.department);
        setUspUsernameDraft(snapshot.uspUsername);
        // 初始化下拉选项；若当前值不在去重列表中（如刚自定义），补进去保证可选中
        const cos = Array.from(new Set([...(options.companies || []), snapshot.company].filter(Boolean))) as string[];
        const depts = Array.from(new Set([...(options.departments || []), snapshot.department].filter(Boolean))) as string[];
        setCompanyOptions(cos);
        setDepartmentOptions(depts);
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

  const isDirty = useCallback((): boolean => {
    if ((nameDraft || '').trim() !== original.name) return true;
    if ((companyDraft || '').trim() !== original.company) return true;
    if ((departmentDraft || '').trim() !== original.department) return true;
    if ((uspUsernameDraft || '').trim() !== original.uspUsername) return true;
    if (uspPasswordDraft) return true;
    return false;
  }, [nameDraft, companyDraft, departmentDraft, uspUsernameDraft, uspPasswordDraft, original]);

  // 打开「添加」弹窗
  const openAddDialog = useCallback((field: 'company' | 'department') => {
    setAddingField(field);
    setAddInputValue('');
  }, []);

  // 确认添加：去重后写入选项列表并选中
  const confirmAdd = useCallback(() => {
    const val = (addInputValue || '').trim();
    if (!val) {
      Toast({ message: '请输入内容', theme: 'warning' });
      return;
    }
    if (addingField === 'company') {
      setCompanyOptions((prev) => (prev.includes(val) ? prev : [...prev, val]));
      setCompanyDraft(val);
    } else if (addingField === 'department') {
      setDepartmentOptions((prev) => (prev.includes(val) ? prev : [...prev, val]));
      setDepartmentDraft(val);
    }
    setAddingField(null);
    setAddInputValue('');
  }, [addInputValue, addingField]);

  const saveProfile = useCallback(async () => {
    const trimmedName = (nameDraft || '').trim();
    if (!trimmedName) {
      Toast({ message: '姓名不能为空', theme: 'error' });
      return;
    }
    if (!isDirty()) {
      Toast({ message: '资料无变更', theme: 'success' });
      return;
    }

    // 仅发送有变更的字段
    const payload: {
      name?: string;
      company?: string;
      department?: string;
      external_credentials?: { usp: { username?: string; password?: string } };
    } = {};
    if (trimmedName !== original.name) payload.name = trimmedName;
    const trimmedCompany = (companyDraft || '').trim();
    if (trimmedCompany !== original.company) payload.company = trimmedCompany;
    const trimmedDept = (departmentDraft || '').trim();
    if (trimmedDept !== original.department) payload.department = trimmedDept;

    const trimmedUspUsername = (uspUsernameDraft || '').trim();
    const uspUsernameChanged = trimmedUspUsername !== original.uspUsername;
    const hasNewPassword = !!uspPasswordDraft;
    if (uspUsernameChanged || hasNewPassword) {
      const usp: { username?: string; password?: string } = {};
      if (uspUsernameChanged) usp.username = trimmedUspUsername;
      if (hasNewPassword) usp.password = uspPasswordDraft;
      payload.external_credentials = { usp };
    }

    setSaving(true);
    try {
      await updateMyProfile(username, payload);
      // 刷新本地快照与 store
      const nextSnapshot = {
        name: trimmedName,
        company: trimmedCompany,
        department: trimmedDept,
        uspUsername: uspUsernameChanged ? trimmedUspUsername : original.uspUsername,
      };
      setOriginal(nextSnapshot);
      setUspUsernameDraft(nextSnapshot.uspUsername);
      setUspPasswordDraft('');
      if (trimmedName !== name) setProfile({ name: trimmedName });
      Toast({ message: '保存成功', theme: 'success' });
    } catch (err) {
      Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSaving(false);
    }
  }, [nameDraft, companyDraft, departmentDraft, uspUsernameDraft, uspPasswordDraft, original, username, name, setProfile, isDirty]);

  const handleUploadAvatar = useCallback(
    async (file: File): Promise<{ status: 'success' | 'fail'; response: { url?: string } }> => {
      setUploading(true);
      try {
        const resource = await uploadAvatar(file, username);
        await updateMyProfile(username, { avatar_resource_id: resource.id });
        setProfile({ avatarResourceId: resource.id });
        setAvatarError(false);
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
        navigate('/login?reason=logout', { replace: true });
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
        {avatarResourceId && !avatarError ? (
          <img
            src={avatarUrl(avatarResourceId)}
            alt="头像"
            style={{ width: 80, height: 80, borderRadius: '50%', objectFit: 'cover' }}
            onError={() => setAvatarError(true)}
          />
        ) : (
          <UserCircleIcon size="80px" />
        )}
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
        <div style={{ fontSize: 14, fontWeight: 600, color: '#333', marginBottom: 12 }}>
          基本资料
        </div>

        <Field label="用户名" hint="系统内用户标识，不可修改">
          <div style={{ fontSize: 14, color: '#333' }}>{loading ? '加载中...' : username}</div>
        </Field>

        <Field label="姓名">
          <Input
            value={nameDraft}
            onChange={(v) => setNameDraft(String(v))}
            placeholder="请输入姓名，便于同事识别"
            maxlength={20}
            clearable
          />
        </Field>

        <Field label="公司">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <select
              value={companyDraft}
              onChange={(e) => setCompanyDraft(e.target.value)}
              style={selectStyle}
            >
              <option value="">请选择公司</option>
              {companyOptions.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <Button
              theme="primary"
              variant="outline"
              size="small"
              icon={<AddIcon size="16px" />}
              onClick={() => openAddDialog('company')}
            >
              添加公司
            </Button>
          </div>
        </Field>

        <Field label="部门">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <select
              value={departmentDraft}
              onChange={(e) => setDepartmentDraft(e.target.value)}
              style={selectStyle}
            >
              <option value="">请选择部门</option>
              {departmentOptions.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
            <Button
              theme="primary"
              variant="outline"
              size="small"
              icon={<AddIcon size="16px" />}
              onClick={() => openAddDialog('department')}
            >
              添加部门
            </Button>
          </div>
        </Field>
      </div>

      <div style={{ background: '#fff', borderRadius: 12, padding: 16, marginTop: 16 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: '#333', marginBottom: 4 }}>
          USP 账户
        </div>
        <div style={{ fontSize: 12, color: '#999', marginBottom: 12 }}>
          用于派单系统登录；密码以哈希存储，留空表示不修改。
        </div>

        <Field label="USP 账号">
          <Input
            value={uspUsernameDraft}
            onChange={(v) => setUspUsernameDraft(String(v))}
            placeholder="请输入 USP 账号"
            maxlength={64}
            clearable
          />
        </Field>

        <Field label="USP 密码">
          <Input
            value={uspPasswordDraft}
            onChange={(v) => setUspPasswordDraft(String(v))}
            placeholder="留空则不修改"
            type="password"
            clearable
          />
        </Field>
      </div>

      <Button
        theme="primary"
        block
        loading={saving}
        onClick={saveProfile}
        disabled={loading}
        style={{ marginTop: 16 }}
      >
        保存
      </Button>

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

      {/* 添加公司/部门 自定义输入弹层 */}
      <Popup
        visible={addingField !== null}
        placement="center"
        onClose={() => setAddingField(null)}
      >
        <div style={{ width: '80vw', maxWidth: 360, padding: 20, boxSizing: 'border-box' }}>
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 12 }}>
            {addingField === 'company' ? '添加公司' : '添加部门'}
          </div>
          <Input
            value={addInputValue}
            onChange={(v) => setAddInputValue(String(v))}
            placeholder={addingField === 'company' ? '请输入公司名称' : '请输入部门/团队名称'}
            maxlength={64}
            clearable
            autofocus
          />
          <div style={{ display: 'flex', gap: 12, marginTop: 20 }}>
            <Button block theme="default" variant="outline" onClick={() => setAddingField(null)}>
              取消
            </Button>
            <Button block theme="primary" onClick={confirmAdd}>
              确定
            </Button>
          </div>
        </div>
      </Popup>
    </div>
  );
}

/** 表单字段行：左侧标签 + 右侧控件/内容 */
function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 6 }}>
        <span style={{ fontSize: 12, color: '#999' }}>{label}</span>
        {hint && <span style={{ fontSize: 11, color: '#bbb' }}>({hint})</span>}
      </div>
      {children}
    </div>
  );
}

// 保留 MyProfile 类型引用，便于后续扩展（如展示 status 等）
export type { MyProfile };
