// 个人信息中心 —— GET /api/auth/me + PUT /api/admin/users/{username} + 头像上传（资源管理中心）
// 背景：微信登录用户默认 username 形如 wechat_xxxxxxxxxx（不可读），"我要摇人" 等处优先展示 name，
// 但用户此前从未设置过 name。本页提供自助修改昵称/头像的入口；首次进入（name 为空）时弹窗提示设置。
// 头像字段策略：进入页面时 /auth/me 返回的头像 id 不再覆盖 store（与登录/刷新时 fetchUserDetails 同源），
// 避免接口偶发缺字段时把已显示的头像清成上传图标（本次修复根因之一见 backend/app/core/auth_service.py）。
// 个人中心可编辑字段：姓名 / 公司 / 部门 / USP 密码。
// username 为系统内用户标识，只读展示；USP 账号根据姓名拼音自动生成（只读）；USP 密码以哈希存储，前端不回显，留空表示不修改。
import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Input, Button, Dialog, Toast, Popup } from 'tdesign-mobile-react';
import { UserCircleIcon, AddIcon } from 'tdesign-icons-react';
import { useAuthStore } from '@/stores/auth';
import { getMyProfile, getProfileOptions, generateUspUsername, updateMyProfile, uploadAvatar, avatarUrl, type MyProfile } from '@/api/profile';
import { setupWechatShare } from '@/shared/utils/wechatJsSdk';
import { WECHAT_CONFIG } from '@/config/wechat';
import { buildWechatAuthUrl, buildStateFromPath } from '@/shared/utils/url';

// 防止「首次进入 → 微信 OAuth → 回跳」死循环：标记本次会话已尝试过一次
const WECHAT_PROFILE_OAUTH_KEY = 'profile_wechat_oauth_attempted';

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
  const { username, name, avatarResourceId, setProfile, logout, hasPermission } = useAuthStore();

  // 表单草稿
  const [nameDraft, setNameDraft] = useState(name);
  const [companyDraft, setCompanyDraft] = useState('');
  const [departmentDraft, setDepartmentDraft] = useState('');
  const [uspUsernameDraft, setUspUsernameDraft] = useState('');
  // USP 密码：仅用于「设置新密码」，留空表示不修改；后端存储的是哈希，前端不回显
  const [uspPasswordDraft, setUspPasswordDraft] = useState('');
  // USP 密码确认：需与密码一致
  const [uspPasswordConfirmDraft, setUspPasswordConfirmDraft] = useState('');

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
        // 首次进入且未设置姓名：自动走微信 OAuth 拉取昵称和头像（后端 snsapi_userinfo 回调写入 name/avatar_resource_id）
        // 用 sessionStorage 防死循环：同一会话只尝试一次
        if (!profile.name && WECHAT_CONFIG.loginEnabled && !sessionStorage.getItem(WECHAT_PROFILE_OAUTH_KEY)) {
          sessionStorage.setItem(WECHAT_PROFILE_OAUTH_KEY, '1');
          const state = buildStateFromPath('/admin/profile');
          window.location.href = buildWechatAuthUrl(state);
          return; // 页面即将跳转，不再执行后续逻辑
        }
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 姓名失焦时自动生成 USP 账户名（拼音去重）
  const handleNameBlur = useCallback(async () => {
    const trimmed = (nameDraft || '').trim();
    if (!trimmed) {
      setUspUsernameDraft('');
      return;
    }
    // 姓名未变化时不重新生成
    if (trimmed === original.name) return;
    try {
      const uspName = await generateUspUsername(trimmed);
      setUspUsernameDraft(uspName);
    } catch {
      // 生成失败时静默，保留原值
    }
  }, [nameDraft, original.name]);

  // 进入个人中心页即静默预置微信分享卡片：用户点右上角「…」可直接转发到群/好友/朋友圈
  useEffect(() => {
    if (loading) return;
    setupWechatShare({
      title: '设置你的个人信息',
      desc: '设置你的真实姓名，公司，部门等， 为你开发全部功能！',
      link: window.location.href,
      imgUrl: WECHAT_CONFIG.shareImgUrl,
    });
  }, [loading]);

  const isDirty = useCallback((): boolean => {
    if ((nameDraft || '').trim() !== original.name) return true;
    if ((companyDraft || '').trim() !== original.company) return true;
    if ((departmentDraft || '').trim() !== original.department) return true;
    if ((uspUsernameDraft || '').trim() !== original.uspUsername) return true;
    if (uspPasswordDraft || uspPasswordConfirmDraft) return true;
    return false;
  }, [nameDraft, companyDraft, departmentDraft, uspUsernameDraft, uspPasswordDraft, uspPasswordConfirmDraft, original]);

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
      setTimeout(() => navigate('/app/call', { replace: true }), 1000);
      return;
    }

    // USP 密码强度校验：留空表示不修改，填了则需满足强度要求
    const hasNewPassword = !!uspPasswordDraft;
    if (hasNewPassword) {
      if (uspPasswordDraft.length < 8) {
        Toast({ message: '密码至少8位，至少包括字母、数字、特殊字符', theme: 'error' });
        return;
      }
      if (!/[a-zA-Z]/.test(uspPasswordDraft) || !/\d/.test(uspPasswordDraft) || !/[^a-zA-Z0-9]/.test(uspPasswordDraft)) {
        Toast({ message: '密码至少8位，至少包括字母、数字、特殊字符', theme: 'error' });
        return;
      }
      if (uspPasswordDraft !== uspPasswordConfirmDraft) {
        Toast({ message: '两次输入的密码不一致', theme: 'error' });
        return;
      }
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
      setUspPasswordConfirmDraft('');
      if (trimmedName !== name) setProfile({ name: trimmedName });
      Toast({ message: '保存成功', theme: 'success' });
      navigate('/app/call', { replace: true });
    } catch (err) {
      Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSaving(false);
    }
  }, [nameDraft, companyDraft, departmentDraft, uspUsernameDraft, uspPasswordDraft, uspPasswordConfirmDraft, original, username, name, setProfile, isDirty, navigate]);

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

  // 隐藏的原生 file input，点击头像触发选择
  const fileInputRef = useRef<HTMLInputElement>(null);
  const handleFileChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      await handleUploadAvatar(file);
      // 重置 value 允许重复选择同一文件
      e.target.value = '';
    },
    [handleUploadAvatar],
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
          textAlign: 'center',
          marginBottom: 16,
        }}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".png,.jpg,.jpeg"
          onChange={handleFileChange}
          style={{ display: 'none' }}
        />
        <div
          onClick={() => !uploading && fileInputRef.current?.click()}
          style={{
            position: 'relative',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: uploading ? 'wait' : 'pointer',
            width: 80,
            height: 80,
            borderRadius: '50%',
            overflow: 'hidden',
            background: '#f5f5f5',
          }}
        >
          {avatarResourceId && !avatarError ? (
            <img
              src={avatarUrl(avatarResourceId)}
              alt="头像"
              style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
              onError={() => setAvatarError(true)}
            />
          ) : (
            <UserCircleIcon size="80px" />
          )}
          {uploading && (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                background: 'rgba(0,0,0,0.4)',
                color: '#fff',
                fontSize: 12,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              上传中
            </div>
          )}
        </div>
        <div style={{ fontSize: 12, color: '#999', marginTop: 8 }}>点击编辑</div>
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
            onBlur={handleNameBlur}
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
            {hasPermission('backend:company:add') && (
              <Button
                theme="primary"
                variant="outline"
                size="small"
                icon={<AddIcon size="16px" />}
                onClick={() => openAddDialog('company')}
              >
                添加公司
              </Button>
            )}
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
            {hasPermission('backend:part:add') && (
              <Button
                theme="primary"
                variant="outline"
                size="small"
                icon={<AddIcon size="16px" />}
                onClick={() => openAddDialog('department')}
              >
                添加部门
              </Button>
            )}
          </div>
        </Field>
      </div>

      <div style={{ background: '#fff', borderRadius: 12, padding: 16, marginTop: 16 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: '#333', marginBottom: 4 }}>
          USP 账户
        </div>
        <div style={{ fontSize: 12, color: '#999', marginBottom: 12 }}>
        </div>

        <Field label="USP 账号" hint="根据姓名拼音自动生成，不可手动修改">
          <Input
            value={uspUsernameDraft}
            readonly
            placeholder="输入姓名后自动生成"
          />
        </Field>

        <Field label="USP 密码" hint="至少8位，含字母、数字、特殊字符">
          <Input
            value={uspPasswordDraft}
            onChange={(v) => setUspPasswordDraft(String(v))}
            placeholder="留空则不修改"
            type="password"
            clearable
          />
        </Field>

        <Field label="确认密码">
          <Input
            value={uspPasswordConfirmDraft}
            onChange={(v) => setUspPasswordConfirmDraft(String(v))}
            placeholder="再次输入新密码"
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
