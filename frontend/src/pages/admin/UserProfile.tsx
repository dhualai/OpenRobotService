// 个人信息中心 —— GET /api/auth/me + PUT /api/admin/users/{username} + 头像上传（资源管理中心）
// 背景：微信登录用户默认 username 形如 wechat_xxxxxxxxxx（不可读），"我要摇人" 等处优先展示 name，
// 但用户此前从未设置过 name。本页提供自助修改昵称/头像的入口；首次进入（name 为空）时弹窗提示设置。
// 头像字段策略：进入页面时 /auth/me 返回的头像 id 不再覆盖 store（与登录/刷新时 fetchUserDetails 同源），
// 避免接口偶发缺字段时把已显示的头像清成上传图标（本次修复根因之一见 backend/app/core/auth_service.py）。
// 个人中心可编辑字段：姓名（必填）/ 公司（选填）/ 部门（选填，按公司级联过滤）/ USP 密码 / 确认密码。
// 公司/部门来自主数据表（companies/departments），新增需提交审核工单，审核通过前仅提交者可见。
// username 为系统内用户标识，只读展示；USP 账号根据姓名拼音自动生成（只读）；USP 密码以哈希存储，前端不回显。
// 后端返回 external_credentials.usp.password 为 "-" 哨兵表示已设置密码，前端据此判断是否必填。
import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Input, Button, Dialog, Toast, Popup } from 'tdesign-mobile-react';
import ClearableInput from '@/shared/components/ClearableInput';
import FilterableSelect from '@/shared/components/FilterableSelect';
import { UserCircleIcon, AddIcon } from 'tdesign-icons-react';
import { useAuthStore } from '@/stores/auth';
import {
  getMyProfile, getProfileOptions, generateUspUsername, updateMyProfile,
  uploadAvatar, avatarUrl, submitNewCompany, submitNewDepartment,
  type MyProfile, type OrgOption, type ProfileFieldOptions,
} from '@/api/profile';
import { setupWechatShare } from '@/shared/utils/wechatJsSdk';
import { WECHAT_CONFIG } from '@/config/wechat';
import { buildWechatAuthUrl, buildStateFromPath } from '@/shared/utils/url';

// 防止「首次进入 → 微信 OAuth → 回跳」死循环：标记本次会话已尝试过一次
const WECHAT_PROFILE_OAUTH_KEY = 'profile_wechat_oauth_attempted';

export default function UserProfile() {
  const navigate = useNavigate();
  const { username, name, avatarResourceId, setProfile, logout, hasPermission } = useAuthStore();

  // 表单草稿
  const [nameDraft, setNameDraft] = useState(name);
  // 公司/部门：同时维护 ID（保存用）和 name（显示用）
  const [companyIdDraft, setCompanyIdDraft] = useState('');
  const [companyNameDraft, setCompanyNameDraft] = useState('');
  const [departmentIdDraft, setDepartmentIdDraft] = useState('');
  const [departmentNameDraft, setDepartmentNameDraft] = useState('');
  const [uspUsernameDraft, setUspUsernameDraft] = useState('');
  // USP 密码：已设置时后端返回 "-" 哨兵（选填，留空则保留原密码），未设置时需必填
  const [uspPasswordDraft, setUspPasswordDraft] = useState('');
  // USP 密码确认：需与密码一致
  const [uspPasswordConfirmDraft, setUspPasswordConfirmDraft] = useState('');
  // USP 密码是否已设置（后端返回 "-" 哨兵则为 true）
  const [hasUspPassword, setHasUspPassword] = useState(false);

  // 公司/部门下拉可选项（来自主数据表）
  const [companyOptions, setCompanyOptions] = useState<OrgOption[]>([]);
  const [departmentsByCompany, setDepartmentsByCompany] = useState<Record<string, OrgOption[]>>({});

  // 「添加」弹窗：addingField 标记当前在添加哪个字段
  const [addingField, setAddingField] = useState<'company' | 'department' | null>(null);
  const [addInputValue, setAddInputValue] = useState('');
  const [submittingNew, setSubmittingNew] = useState(false);

  // 进入页面时从 /me 拉取的原始值，用于判断哪些字段发生变更
  const [original, setOriginal] = useState<{
    name: string;
    companyId: string;
    departmentId: string;
    uspUsername: string;
  }>({ name: '', companyId: '', departmentId: '', uspUsername: '' });

  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [avatarError, setAvatarError] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [profile, options] = await Promise.all([
          getMyProfile(),
          getProfileOptions().catch(() => ({ companies: [], departments_by_company: {}, my_pending: { companies: [], departments: [] } }) as ProfileFieldOptions),
        ]);
        // 仅刷新昵称；头像 id 已由登录/刷新时 fetchUserDetails 写入 store，
        // 与 /auth/me 同源，无需重复覆盖（避免接口缺字段时把头像弄丢）。
        setProfile({ name: profile.name || '' });
        const snapshot = {
          name: profile.name || '',
          companyId: profile.company_id || '',
          departmentId: profile.department_id || '',
          uspUsername: profile.external_credentials?.usp?.username || '',
        };
        // 后端返回 "-" 哨兵表示已设置 USP 密码
        setHasUspPassword(profile.external_credentials?.usp?.password === '-');
        setOriginal(snapshot);
        setNameDraft(snapshot.name);
        setCompanyIdDraft(snapshot.companyId);
        setDepartmentIdDraft(snapshot.departmentId);
        // 通过 ID 查找 name 用于显示
        setCompanyOptions(options.companies || []);
        setDepartmentsByCompany(options.departments_by_company || {});
        const compName = (options.companies || []).find((c) => c.id === snapshot.companyId)?.name || profile.company || '';
        const deptName = Object.values(options.departments_by_company || {})
          .flat()
          .find((d) => d.id === snapshot.departmentId)?.name || profile.department || '';
        setCompanyNameDraft(compName);
        setDepartmentNameDraft(deptName);
        setUspUsernameDraft(snapshot.uspUsername);
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

  // 姓名变化时自动生成 USP 账户名（拼音去重），防抖 300ms
  const uspGenTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (uspGenTimer.current) {
      clearTimeout(uspGenTimer.current);
      uspGenTimer.current = null;
    }
    const trimmed = (nameDraft || '').trim();
    if (!trimmed) {
      setUspUsernameDraft('');
      return;
    }
    uspGenTimer.current = setTimeout(async () => {
      try {
        const uspName = await generateUspUsername(trimmed);
        setUspUsernameDraft(uspName);
      } catch {
        // 生成失败时静默，保留原值
      }
    }, 300);
    return () => {
      if (uspGenTimer.current) {
        clearTimeout(uspGenTimer.current);
        uspGenTimer.current = null;
      }
    };
  }, [nameDraft]);

  // 进入个人中心页即静默预置微信分享卡片：用户点右上角「…」可直接转发到群/好友/朋友圈
  useEffect(() => {
    if (loading) return;
    setupWechatShare({
      title: '设置你的个人信息',
      desc: '设置你的真实姓名，公司，部门等， 为你开放全部功能！',
      link: window.location.href,
      imgUrl: WECHAT_CONFIG.shareImgUrl,
    });
  }, [loading]);

  // 当前公司下的部门选项（级联过滤）
  const departmentOptions = useMemo(() => {
    return departmentsByCompany[companyNameDraft] || [];
  }, [departmentsByCompany, companyNameDraft]);

  // 公司选项转为名称数组供 FilterableSelect 使用，pending 项加标记
  const companyNames = useMemo(() => {
    return companyOptions.map((c) => c.status === 'pending' ? `${c.name}（审核中）` : c.name);
  }, [companyOptions]);

  // 部门选项转为名称数组
  const departmentNames = useMemo(() => {
    return departmentOptions.map((d) => d.status === 'pending' ? `${d.name}（审核中）` : d.name);
  }, [departmentOptions]);

  // 选择公司时，清空部门（级联）
  const handleCompanyChange = useCallback((displayName: string) => {
    // 去掉"（审核中）"后缀查找原始 name
    const realName = displayName.replace(/（审核中）$/, '');
    const comp = companyOptions.find((c) => c.name === realName);
    setCompanyNameDraft(displayName);
    setCompanyIdDraft(comp?.id || '');
    // 清空部门选择
    setDepartmentIdDraft('');
    setDepartmentNameDraft('');
  }, [companyOptions]);

  const handleDepartmentChange = useCallback((displayName: string) => {
    const realName = displayName.replace(/（审核中）$/, '');
    const dept = departmentOptions.find((d) => d.name === realName);
    setDepartmentNameDraft(displayName);
    setDepartmentIdDraft(dept?.id || '');
  }, [departmentOptions]);

  const isDirty = useCallback((): boolean => {
    if ((nameDraft || '').trim() !== original.name) return true;
    if ((companyIdDraft || '') !== original.companyId) return true;
    if ((departmentIdDraft || '') !== original.departmentId) return true;
    if ((uspUsernameDraft || '').trim() !== original.uspUsername) return true;
    if (uspPasswordDraft || uspPasswordConfirmDraft) return true;
    return false;
  }, [nameDraft, companyIdDraft, departmentIdDraft, uspUsernameDraft, uspPasswordDraft, uspPasswordConfirmDraft, original]);

  // 打开「添加」弹窗
  const openAddDialog = useCallback((field: 'company' | 'department') => {
    if (field === 'department' && !companyIdDraft) {
      Toast({ message: '请先选择公司', theme: 'warning' });
      return;
    }
    setAddingField(field);
    setAddInputValue('');
  }, [companyIdDraft]);

  // 确认添加：调用后端 API 创建 pending 记录 + 审核工单
  const confirmAdd = useCallback(async () => {
    const val = (addInputValue || '').trim();
    if (!val) {
      Toast({ message: '请输入内容', theme: 'warning' });
      return;
    }

    setSubmittingNew(true);
    try {
      if (addingField === 'company') {
        const res = await submitNewCompany(val);
        // 加入选项列表并选中
        const newOpt: OrgOption = { id: res.company.id, name: val, status: 'pending' };
        setCompanyOptions((prev) => [...prev, newOpt]);
        setCompanyNameDraft(`${val}（审核中）`);
        setCompanyIdDraft(res.company.id);
        Toast({ message: `已提交审核${res.ticket_id ? `，工单号 #${res.ticket_id}` : ''}`, theme: 'success' });
      } else if (addingField === 'department') {
        const res = await submitNewDepartment(val, companyIdDraft);
        const newOpt: OrgOption = { id: res.department.id, name: val, status: 'pending' };
        // 更新 departmentsByCompany
        const compName = companyNameDraft.replace(/（审核中）$/, '');
        setDepartmentsByCompany((prev) => ({
          ...prev,
          [compName]: [...(prev[compName] || []), newOpt],
        }));
        setDepartmentNameDraft(`${val}（审核中）`);
        setDepartmentIdDraft(res.department.id);
        Toast({ message: `已提交审核${res.ticket_id ? `，工单号 #${res.ticket_id}` : ''}`, theme: 'success' });
      }
      setAddingField(null);
      setAddInputValue('');
    } catch (err) {
      Toast({ message: `提交失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmittingNew(false);
    }
  }, [addInputValue, addingField, companyIdDraft, companyNameDraft]);

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

    // USP 密码校验：已设置密码时选填（留空则保留原密码），未设置时必填
    const hasNewPassword = !!uspPasswordDraft;
    if (hasNewPassword) {
      // 填了新密码 → 强度校验 + 一致性校验
      if (uspPasswordDraft.length < 8) {
        Toast({ message: '密码至少8位，至少包括字母、数字、特殊字符', theme: 'error' });
        return;
      }
      if (!/[a-zA-Z]/.test(uspPasswordDraft) || !/\d/.test(uspPasswordDraft) || !/[^a-zA-Z0-9]/.test(uspPasswordDraft)) {
        Toast({ message: '密码至少8位，至少包括字母、数字、特殊字符', theme: 'error' });
        return;
      }
      if (!uspPasswordConfirmDraft) {
        Toast({ message: '请再次输入新密码', theme: 'error' });
        return;
      }
      if (uspPasswordDraft !== uspPasswordConfirmDraft) {
        Toast({ message: '两次输入的密码不一致', theme: 'error' });
        return;
      }
    } else if (!hasUspPassword) {
      // 未设置过密码且未填 → 必填
      Toast({ message: '请输入 USP 密码', theme: 'error' });
      return;
    }

    // 仅发送有变更的字段
    const payload: {
      name?: string;
      company_id?: string;
      department_id?: string;
      external_credentials?: { usp: { username?: string; password?: string } };
    } = {};
    if (trimmedName !== original.name) payload.name = trimmedName;
    if ((companyIdDraft || '') !== original.companyId) payload.company_id = companyIdDraft || '';
    if ((departmentIdDraft || '') !== original.departmentId) payload.department_id = departmentIdDraft || '';

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
        companyId: companyIdDraft,
        departmentId: departmentIdDraft,
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
  }, [nameDraft, companyIdDraft, departmentIdDraft, uspUsernameDraft, uspPasswordDraft, uspPasswordConfirmDraft, original, username, name, setProfile, isDirty, navigate, hasUspPassword]);

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

        <Field label="姓名" required>
          <ClearableInput
            value={nameDraft}
            onChange={(v) => setNameDraft(String(v))}
            placeholder="请输入姓名，便于同事识别"
            maxlength={20}
          />
        </Field>

        <Field label="公司" hint="选填">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <FilterableSelect
              value={companyNameDraft}
              onChange={handleCompanyChange}
              options={companyNames}
              placeholder="请选择公司"
              title="选择公司"
              searchPlaceholder="搜索公司…"
            />
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

        <Field label="部门" hint="选填，按公司过滤">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <FilterableSelect
              value={departmentNameDraft}
              onChange={handleDepartmentChange}
              options={departmentNames}
              placeholder={companyNameDraft ? '请选择部门' : '请先选择公司'}
              title="选择部门"
              searchPlaceholder="搜索部门…"
            />
            <Button
              theme="primary"
              variant="outline"
              size="small"
              icon={<AddIcon size="16px" />}
              onClick={() => openAddDialog('department')}
              disabled={!companyIdDraft}
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
        </div>

        <Field label="USP 账号" hint="根据姓名拼音自动生成，不可手动修改">
          <Input
            value={uspUsernameDraft}
            readonly
            placeholder="输入姓名后自动生成"
          />
        </Field>

        <Field label="USP 密码" hint="至少8位，含字母、数字、特殊字符" required={!hasUspPassword}>
          <ClearableInput
            value={uspPasswordDraft}
            onChange={(v) => setUspPasswordDraft(String(v))}
            placeholder={hasUspPassword ? '留空则不修改' : '请输入 USP 密码'}
            type="password"
            passwordToggle
          />
        </Field>

        <Field label="确认密码" required={!hasUspPassword}>
          <ClearableInput
            value={uspPasswordConfirmDraft}
            onChange={(v) => setUspPasswordConfirmDraft(String(v))}
            placeholder={hasUspPassword ? '留空则不修改' : '请再次输入新密码'}
            type="password"
            passwordToggle
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
        onClose={() => !submittingNew && setAddingField(null)}
      >
        <div style={{ width: '80vw', maxWidth: 360, padding: 20, boxSizing: 'border-box' }}>
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>
            {addingField === 'company' ? '添加公司' : '添加部门'}
          </div>
          <div style={{ fontSize: 12, color: '#999', marginBottom: 12 }}>
            提交后将创建审核工单，管理员审核通过后其他用户可见
          </div>
          <ClearableInput
            value={addInputValue}
            onChange={(v) => setAddInputValue(String(v))}
            placeholder={addingField === 'company' ? '请输入公司名称' : '请输入部门/团队名称'}
            maxlength={64}
            autofocus
          />
          <div style={{ display: 'flex', gap: 12, marginTop: 20 }}>
            <Button block theme="default" variant="outline" onClick={() => setAddingField(null)} disabled={submittingNew}>
              取消
            </Button>
            <Button block theme="primary" onClick={confirmAdd} loading={submittingNew}>
              提交审核
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
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 6 }}>
        {required && <span style={{ fontSize: 12, color: '#e34d59', lineHeight: 1 }}>*</span>}
        <span style={{ fontSize: 12, color: '#999' }}>{label}</span>
        {hint && <span style={{ fontSize: 11, color: '#bbb' }}>({hint})</span>}
      </div>
      {children}
    </div>
  );
}

// 保留 MyProfile 类型引用，便于后续扩展（如展示 status 等）
export type { MyProfile };
