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
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Input, Button, Dialog, Toast, Popup, Loading } from 'tdesign-mobile-react';
import ClearableInput from '@/shared/components/ClearableInput';
import FilterableSelect from '@/shared/components/FilterableSelect';
import { UserCircleIcon, AddIcon } from 'tdesign-icons-react';
import { useAuthStore } from '@/stores/auth';
import {
  getMyProfile, getProfileOptions, generateUspUsername, updateMyProfile,
  uploadAvatar, avatarUrl, submitNewCompany, submitNewDepartment,
  getMyTimezone, updateMyTimezone, detectTimezone,
  type MyProfile, type OrgOption, type ProfileFieldOptions,
} from '@/api/profile';
import { getProjects, getMyProjects } from '@/api/projects';
import { createTicket } from '@/api/ticket';
import { setupWechatShare } from '@/shared/utils/wechatJsSdk';
import { WECHAT_CONFIG } from '@/config/wechat';
import { buildWechatAuthUrl, buildStateFromPath } from '@/shared/utils/url';

// 防止「首次进入 → 微信 OAuth → 回跳」死循环：标记本次会话已尝试过一次
const WECHAT_PROFILE_OAUTH_KEY = 'profile_wechat_oauth_attempted';

// 通知时区候选：浏览器检测值置顶 + 常用 IANA 时区（工单提醒按此时区的工作时间推送）
const COMMON_TIMEZONES = [
  'Asia/Shanghai',
  'Asia/Hong_Kong',
  'Asia/Tokyo',
  'Asia/Singapore',
  'Europe/London',
  'Europe/Berlin',
  'Europe/Paris',
  'America/New_York',
  'America/Los_Angeles',
  'Australia/Sydney',
];

/** 时区来源展示文案；null/未设置表示仍走后端系统默认时区 */
const SOURCE_LABEL: Record<string, string> = {
  browser: '设备自动上报',
  manual: '手动设置',
  system: '系统默认',
};

/** 后端返回的是 UTC 时间字符串，按本地时区渲染 */
function formatTime(raw: string): string {
  const d = new Date(raw.includes('T') ? raw : `${raw.replace(' ', 'T')}Z`);
  return Number.isNaN(d.getTime()) ? raw : d.toLocaleString();
}

export default function UserProfile() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { username, name, avatarResourceId, setProfile, logout, hasPermission } = useAuthStore();

  // 扫码进入链路：URL 带 scene 参数时，保存后弹出"应用状态检查"弹窗
  const sceneParam = searchParams.get('scene') || searchParams.get('scene_str') || null;

  // ── 扫码进入引导：蒙层 + 白色曲线框高亮对应区域 ──
  const [highlightTarget, setHighlightTarget] = useState<null | 'name' | 'password'>(null);
  const [highlightRect, setHighlightRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null);
  const guideShownRef = useRef(false);
  const nameSectionRef = useRef<HTMLDivElement>(null);
  const uspPasswordSectionRef = useRef<HTMLDivElement>(null);

  // ── 「应用状态检查」弹窗状态 ──
  const [appCheckVisible, setAppCheckVisible] = useState(false);
  const [projectList, setProjectList] = useState<{ id: string; project_code: string; name: string; contact_person?: string | null; contact_person_id?: string | null }[]>([]);
  const [projectNames, setProjectNames] = useState<string[]>([]);
  const [myProjectCodes, setMyProjectCodes] = useState<string[]>([]); // 当前用户已有关联项目的 project_code 集
  const [appCheckLoading, setAppCheckLoading] = useState(false);
  const [selectedProjectName, setSelectedProjectName] = useState('');
  const [appCheckStep, setAppCheckStep] = useState<'select' | 'checking' | 'in-project' | 'applying' | 'apply-done' | 'apply-failed'>('select');
  const [applyResult, setApplyResult] = useState<{ ticketId?: number; contactPerson?: string; contactPersonId?: string; projectName?: string } | null>(null);
  // 项目搜索关键词：只有用户输入了才显示下拉选项
  const [projectSearchKeyword, setProjectSearchKeyword] = useState('');

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

  // 通知时区（决定工单提醒按哪个时区的工作时间推送）
  const [tzDraft, setTzDraft] = useState('');
  const [tzLocked, setTzLocked] = useState(false);
  const [tzSaving, setTzSaving] = useState(false);
  const [tzOptions, setTzOptions] = useState<string[]>(COMMON_TIMEZONES);
  const [tzSource, setTzSource] = useState<string | null>(null);
  const [tzUpdatedAt, setTzUpdatedAt] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [profile, options, tzInfo] = await Promise.all([
          getMyProfile(),
          getProfileOptions().catch(() => ({ companies: [], departments_by_company: {}, my_pending: { companies: [], departments: [] } }) as ProfileFieldOptions),
          getMyTimezone().catch(() => null),
        ]);
        // 通知时区：未设置时展示后端系统兜底值；浏览器检测值置顶方便一键切回
        setTzDraft(tzInfo?.timezone || tzInfo?.system_default || 'Asia/Shanghai');
        setTzLocked(!!tzInfo?.locked);
        setTzSource(tzInfo?.source ?? null);
        setTzUpdatedAt(tzInfo?.updated_at ?? null);
        const detected = detectTimezone();
        setTzOptions(
          detected
            ? [detected, ...COMMON_TIMEZONES.filter((t) => t !== detected)]
            : COMMON_TIMEZONES,
        );
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

  // ── 引导蒙层核心：根据 target 读取对应 DOM 节点的 getBoundingClientRect，设置蒙层位置 ──
  const focusGuide = useCallback((target: 'name' | 'password') => {
    const el = target === 'name' ? nameSectionRef.current : uspPasswordSectionRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    // 四周多留 6px padding，让曲线框不紧贴内容
    setHighlightRect({
      left: r.left - 6 + window.scrollX,
      top: r.top - 6 + window.scrollY,
      width: r.width + 12,
      height: r.height + 12,
    });
    setHighlightTarget(target);
    // 滚到目标区域中间，保证可见
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, []);

  // 扫码进入（URL 带 scene 参数）时：页面加载完自动开启「蒙层引导」
  useEffect(() => {
    if (loading) return;
    if (!sceneParam) return;
    if (guideShownRef.current) return;
    // 等布局 + 头像上传等异步渲染稳定后再定位
    const t = setTimeout(() => {
      guideShownRef.current = true;
      focusGuide('name');
    }, 300);
    return () => clearTimeout(t);
  }, [loading, sceneParam, focusGuide]);

  // 引导显示期间，监听 scroll / resize 实时更新曲线框位置
  useEffect(() => {
    if (!highlightTarget) return;
    const updateRect = () => {
      const el = highlightTarget === 'name' ? nameSectionRef.current : uspPasswordSectionRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      setHighlightRect({
        left: r.left - 6 + window.scrollX,
        top: r.top - 6 + window.scrollY,
        width: r.width + 12,
        height: r.height + 12,
      });
    };
    window.addEventListener('scroll', updateRect, true);
    window.addEventListener('resize', updateRect);
    return () => {
      window.removeEventListener('scroll', updateRect, true);
      window.removeEventListener('resize', updateRect);
    };
  }, [highlightTarget]);

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

  // 保存通知时区（保留当前锁定状态）
  const handleSaveTimezone = useCallback(async () => {
    if (!tzDraft) return;
    setTzSaving(true);
    try {
      const res = await updateMyTimezone(tzDraft, tzLocked);
      setTzLocked(!!res?.locked);
      setTzSource('manual');
      setTzUpdatedAt(new Date().toISOString());
      Toast({ message: '时区已保存', theme: 'success' });
    } catch (err) {
      Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setTzSaving(false);
    }
  }, [tzDraft, tzLocked]);

  // 切换锁定：锁定后登录时不再按设备时区自动覆盖
  const handleToggleTimezoneLock = useCallback(async () => {
    const next = !tzLocked;
    setTzSaving(true);
    try {
      const res = await updateMyTimezone(tzDraft, next);
      setTzLocked(!!res?.locked);
      Toast({ message: next ? '已锁定时区' : '已解除锁定', theme: 'success' });
    } catch (err) {
      Toast({ message: `操作失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setTzSaving(false);
    }
  }, [tzDraft, tzLocked]);

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

  // 应用状态检查弹窗：搜索关键词变化时模糊匹配（只在有输入时才展示结果）
  const filteredProjectNames = useMemo(() => {
    const kw = projectSearchKeyword.trim().toLowerCase();
    if (!kw) return []; // 没输入就不展示任何选项
    return projectNames.filter((name) => name.toLowerCase().includes(kw));
  }, [projectNames, projectSearchKeyword]);

  // 应用状态检查：选中项目后处理
  const handleAppCheckProjectConfirm = useCallback(async () => {
    if (!selectedProjectName) {
      Toast({ message: '请先选择项目', theme: 'warning' });
      return;
    }
    // 从 "项目名（project_code）" 反查 project_code
    const matched = projectList.find((p) => `${p.name}（${p.project_code}）` === selectedProjectName);
    if (!matched) {
      Toast({ message: '未找到对应项目', theme: 'error' });
      return;
    }
    setAppCheckStep('checking');

    // 检查当前用户是否在该项目中
    const inProject = myProjectCodes.includes(matched.project_code);

    if (inProject) {
      // 在项目中 → 直接引导同步
      setAppCheckStep('in-project');
      setApplyResult({ projectName: matched.name });
      return;
    }

    // 不在项目中 → 找对接人，创建工单
    setAppCheckStep('applying');
    const contactPersonId = matched.contact_person_id;
    const contactPerson = matched.contact_person || contactPersonId || '项目对接人';

    if (!contactPersonId) {
      // 项目没配对接人 → 降级提示，不创建工单
      setAppCheckStep('apply-failed');
      setApplyResult({
        contactPerson,
        projectName: matched.name,
      });
      return;
    }

    try {
      const ticket = await createTicket({
        title: `【申请加入项目】${matched.name}`,
        description: `用户 ${username}（${name || username}）申请加入项目「${matched.name}」（${matched.project_code}），请审核是否将其添加为项目成员。`,
        ticket_type: 'support',
        priority: 'medium',
        project_name: matched.name,
        project_id: matched.project_code,
        assigned_to: contactPersonId,
      });
      setAppCheckStep('apply-done');
      setApplyResult({
        ticketId: ticket.id,
        contactPerson,
        contactPersonId,
        projectName: matched.name,
      });
    } catch (err) {
      console.error('[AppCheck] 创建申请工单失败', err);
      setAppCheckStep('apply-failed');
      setApplyResult({
        contactPerson,
        contactPersonId,
        projectName: matched.name,
      });
    }
  }, [selectedProjectName, projectList, myProjectCodes, username, name]);

  // 弹窗关闭：在 project_code 基础上加上 selectedProjectName 避免 lint 警告，同时 nav 回 call
  const handleAppCheckClose = useCallback(() => {
    setAppCheckVisible(false);
    setAppCheckStep('select');
    setTimeout(() => navigate('/app/call', { replace: true }), 300);
  }, [navigate]);

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

    // USP 密码校验：任何时候都可选（留空则保留原密码或不变）
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

      // 扫码进入链路：URL 带 scene 参数时，保存后弹出"应用状态检查"弹窗
      if (sceneParam) {
        setAppCheckVisible(true);
        setAppCheckStep('select');
        setSelectedProjectName('');
        setApplyResult(null);
        // 预加载项目列表 + 我的项目（供弹窗内检查用户是否在项目中）
        setAppCheckLoading(true);
        Promise.all([
          getProjects('', 0, 500).catch(() => []),
          getMyProjects(false).catch(() => []),
        ]).then(([allProjects, myProjects]) => {
          setProjectList(allProjects || []);
          setProjectNames((allProjects || []).map((p) => `${p.name}（${p.project_code}）`));
          setMyProjectCodes((myProjects || []).map((p) => p.project_code));
        }).finally(() => setAppCheckLoading(false));
      } else {
        setTimeout(() => navigate('/app/call', { replace: true }), 1000);
      }
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

      {/* 「我的奖励」入口已迁移至后台管理首页欢迎区（头像与用户名右侧），见 Dashboard.tsx */}

      <div style={{ background: '#fff', borderRadius: 12, padding: 16 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: '#333', marginBottom: 12 }}>
          基本资料
        </div>

        <Field label="用户名" hint="系统内用户标识，不可修改">
          <div style={{ fontSize: 14, color: '#333' }}>{loading ? '加载中...' : username}</div>
        </Field>

        <div ref={nameSectionRef}>
          <Field label="姓名" required>
            <ClearableInput
              value={nameDraft}
              onChange={(v) => setNameDraft(String(v))}
              placeholder="请输入姓名，便于同事识别"
              maxlength={20}
            />
          </Field>
        </div>

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
              style={{ flexShrink: 0 }}
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
            {companyIdDraft && (
              <Button
                theme="primary"
                variant="outline"
                size="small"
                icon={<AddIcon size="16px" />}
                onClick={() => openAddDialog('department')}
                style={{ flexShrink: 0 }}
              >
                添加部门
              </Button>
            )}
          </div>
        </Field>
      </div>

      <div style={{ background: '#fff', borderRadius: 12, padding: 16, marginTop: 16 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: '#333', marginBottom: 4 }}>
          通知时区
        </div>
        <div style={{ fontSize: 12, color: '#999', marginBottom: 12 }}>
          决定工单逾期/临期提醒按哪个时区的工作时间推送
        </div>

        <Field label="时区" hint="默认取当前设备时区，可手动选择">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <FilterableSelect
              value={tzDraft}
              onChange={(v) => setTzDraft(String(v))}
              options={tzOptions}
              placeholder="选择时区"
              title="选择时区"
              searchPlaceholder="搜索时区…"
            />
            <Button
              theme="primary"
              variant="outline"
              loading={tzSaving}
              onClick={handleSaveTimezone}
              style={{ flexShrink: 0 }}
            >
              应用
            </Button>
          </div>
          <div style={{ marginTop: 8, fontSize: 12, color: '#888', display: 'flex', alignItems: 'center', gap: 4 }}>
            <span>{tzLocked ? '已锁定：登录时不再自动更新' : '未锁定：登录时按当前设备时区自动更新'}</span>
            <Button theme="primary" variant="text" size="small" onClick={handleToggleTimezoneLock}>
              {tzLocked ? '解除锁定' : '锁定'}
            </Button>
          </div>
          <div style={{ marginTop: 4, fontSize: 12, color: '#aaa' }}>
            {`来源：${SOURCE_LABEL[tzSource ?? ''] || '未设置（按系统默认 Asia/Shanghai）'}`}
            {tzUpdatedAt ? ` · 更新于 ${formatTime(tzUpdatedAt)}` : ''}
          </div>
        </Field>
      </div>

      <div ref={uspPasswordSectionRef} style={{ background: '#fff', borderRadius: 12, padding: 16, marginTop: 16 }}>
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

        <Field label="USP 密码" hint="至少8位，含字母、数字、特殊字符；留空则不修改">
          <ClearableInput
            value={uspPasswordDraft}
            onChange={(v) => setUspPasswordDraft(String(v))}
            placeholder="留空则不修改"
            type="password"
            passwordToggle
          />
        </Field>

        <Field label="确认密码">
          <ClearableInput
            value={uspPasswordConfirmDraft}
            onChange={(v) => setUspPasswordConfirmDraft(String(v))}
            placeholder="留空则不修改"
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

      {/* 「应用状态检查」弹窗：扫码进入后引导用户完成项目注册 */}
      <Popup
        visible={appCheckVisible}
        placement="bottom"
        destroyOnClose
        onClose={handleAppCheckClose}
      >
        <div style={{ width: '100%', padding: 20, boxSizing: 'border-box', maxHeight: '75vh', overflowY: 'auto', background: '#fff', borderTopLeftRadius: 20, borderTopRightRadius: 20 }}>
          {/* 顶部拖拽指示条 */}
          <div style={{ width: 36, height: 4, background: '#ddd', borderRadius: 2, margin: '0 auto 12px' }} />
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 4, textAlign: 'center' }}>
            应用状态检查
          </div>
          <div style={{ fontSize: 12, color: '#999', marginBottom: 16, textAlign: 'center' }}>
            请选择你要注册登录的项目
          </div>

          {/* 步骤 1：加载 / 搜索 + 选择项目 */}
          {appCheckStep === 'select' && (
            <>
              {appCheckLoading ? (
                <Loading text="正在加载项目列表…" />
              ) : projectNames.length === 0 ? (
                <div style={{ color: '#999', textAlign: 'center', padding: 20 }}>
                  暂无可选项目
                </div>
              ) : (
                <>
                  {/* 搜索输入框（聚焦在上半区，不触发二级弹窗） */}
                  <div style={{ position: 'relative' }}>
                    <ClearableInput
                      value={projectSearchKeyword}
                      onChange={(v) => {
                        setProjectSearchKeyword(String(v));
                        // 用户改了搜索词就清掉之前的选中
                        if (selectedProjectName) setSelectedProjectName('');
                      }}
                      placeholder="输入项目名或编号搜索…"
                      autofocus
                    />

                    {/* 已选中项目的 chips 提示 */}
                    {selectedProjectName && (
                      <div
                        style={{
                          marginTop: 10,
                          padding: '8px 12px',
                          background: '#e8f3ff',
                          color: '#1a73e8',
                          borderRadius: 8,
                          fontSize: 13,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                        }}
                      >
                        <span>✅ 已选：{selectedProjectName}</span>
                        <span
                          style={{ cursor: 'pointer', fontSize: 16 }}
                          onClick={() => setSelectedProjectName('')}
                        >
                          ✕
                        </span>
                      </div>
                    )}

                    {/* 搜索结果下拉（只有输入关键词才展示） */}
                    {projectSearchKeyword.trim() && (
                      <div
                        style={{
                          marginTop: 8,
                          maxHeight: 200,
                          overflowY: 'auto',
                          border: '1px solid #eee',
                          borderRadius: 10,
                          background: '#fafafa',
                        }}
                      >
                        {filteredProjectNames.length === 0 ? (
                          <div style={{ padding: 16, textAlign: 'center', color: '#999', fontSize: 13 }}>
                            未找到匹配项目
                          </div>
                        ) : (
                          filteredProjectNames.map((name) => (
                            <div
                              key={name}
                              onClick={() => {
                                setSelectedProjectName(name);
                                // 选完保留搜索词让用户看到上下文，也可清空
                              }}
                              style={{
                                padding: '12px 14px',
                                borderBottom: '1px solid #eee',
                                fontSize: 14,
                                color: selectedProjectName === name ? '#1a73e8' : '#333',
                                fontWeight: selectedProjectName === name ? 600 : 400,
                                background: selectedProjectName === name ? '#e8f3ff' : '#fff',
                                cursor: 'pointer',
                              }}
                            >
                              {name}
                            </div>
                          ))
                        )}
                      </div>
                    )}

                    {/* 未输入时的引导提示 */}
                    {!projectSearchKeyword.trim() && !selectedProjectName && (
                      <div style={{ marginTop: 10, fontSize: 12, color: '#aaa', textAlign: 'center' }}>
                        请输入项目名或编号开始搜索
                      </div>
                    )}
                  </div>
                </>
              )}
              <div style={{ display: 'flex', gap: 12, marginTop: 20 }}>
                <Button block theme="default" variant="outline" onClick={handleAppCheckClose}>
                  稍后再说
                </Button>
                <Button
                  block
                  theme="primary"
                  onClick={handleAppCheckProjectConfirm}
                  loading={appCheckLoading}
                  disabled={!selectedProjectName}
                >
                  确认项目
                </Button>
              </div>
            </>
          )}

          {/* 步骤 2：检查中 */}
          {appCheckStep === 'checking' && (
            <div style={{ padding: 40, textAlign: 'center' }}>
              <Loading text="检查你的项目权限…" />
            </div>
          )}

          {/* 步骤 3a：用户已在项目中 → 引导同步 */}
          {appCheckStep === 'in-project' && applyResult && (
            <>
              <div style={{ fontSize: 14, color: '#333', lineHeight: 1.8, marginBottom: 12 }}>
                <div>✅ 你已在项目「<strong>{applyResult.projectName}</strong>」中。</div>
                <div style={{ marginTop: 8 }}>请按以下图示操作：</div>
                <div style={{ marginTop: 8 }}>
                  点击 USP 界面的【<strong>更新</strong>】→【<strong>同步</strong>】后，即可登录。
                </div>
              </div>
              <img
                src={`${import.meta.env.BASE_URL}update_users.png`}
                alt="更新-同步操作指引"
                style={{ width: '100%', borderRadius: 8, border: '1px solid #eee' }}
              />
              <div style={{ display: 'flex', gap: 12, marginTop: 20 }}>
                <Button block theme="primary" onClick={handleAppCheckClose}>
                  我已知晓
                </Button>
              </div>
            </>
          )}

          {/* 步骤 3b：申请工单已创建 → 等待审核 */}
          {appCheckStep === 'apply-done' && applyResult && (
            <>
              <div style={{ fontSize: 14, color: '#333', lineHeight: 1.8, marginBottom: 12 }}>
                <div>✅ 项目加入申请已为你发起。</div>
                {applyResult.ticketId && (
                  <div style={{ fontSize: 12, color: '#666', marginTop: 4 }}>
                    工单号 #{applyResult.ticketId}
                  </div>
                )}
                <div style={{ marginTop: 8 }}>
                  等待 <strong>{applyResult.contactPerson}</strong> 审核通过后，请按图示操作：
                </div>
                <div style={{ marginTop: 8 }}>
                  点击 USP 界面的【<strong>更新</strong>】→【<strong>同步</strong>】后，即可登录。
                </div>
              </div>
              <img
                src={`${import.meta.env.BASE_URL}update_users.png`}
                alt="更新-同步操作指引"
                style={{ width: '100%', borderRadius: 8, border: '1px solid #eee' }}
              />
              <div style={{ display: 'flex', gap: 12, marginTop: 20 }}>
                <Button block theme="primary" onClick={handleAppCheckClose}>
                  我已知晓
                </Button>
              </div>
            </>
          )}

          {/* 步骤 3c：申请失败（无对接人 / 工单创建失败） */}
          {appCheckStep === 'apply-failed' && applyResult && (
            <>
              <div style={{ fontSize: 14, color: '#333', lineHeight: 1.8, marginBottom: 12 }}>
                {!applyResult.contactPersonId ? (
                  <>
                    <div>⚠️ 项目「<strong>{applyResult.projectName}</strong>」暂未配置对接人。</div>
                    <div style={{ marginTop: 8 }}>
                      请联系 <strong>{applyResult.contactPerson}</strong> 或项目管理员，手动将你添加为项目成员。
                    </div>
                  </>
                ) : (
                  <>
                    <div>❌ 申请工单创建失败，请稍后重试。</div>
                    <div style={{ marginTop: 8 }}>
                      你也可以直接联系 <strong>{applyResult.contactPerson}</strong>（{applyResult.contactPersonId}）手动添加。
                    </div>
                  </>
                )}
              </div>
              <div style={{ display: 'flex', gap: 12, marginTop: 20 }}>
                <Button block theme="default" variant="outline" onClick={handleAppCheckClose}>
                  稍后再说
                </Button>
                <Button
                  block
                  theme="primary"
                  onClick={() => {
                    setAppCheckStep('select');
                    setApplyResult(null);
                  }}
                >
                  重新选择
                </Button>
              </div>
            </>
          )}
        </div>
      </Popup>

      {/* ── 扫码进入引导蒙层：box-shadow 挖洞 + 白色曲线框 ── */}
      {highlightTarget && highlightRect && (
        <>
          {/* 白色曲线框 + box-shadow 向外扩散形成半透明蒙层（pointer-events: none 让点击穿透到高亮区） */}
          <div
            style={{
              position: 'fixed',
              left: highlightRect.left,
              top: highlightRect.top,
              width: highlightRect.width,
              height: highlightRect.height,
              border: '3px solid #fff',
              borderRadius: 16,
              // box-shadow 向外扩散 9999px，形成全屏半透明遮罩，同时高亮区自然"挖洞"
              boxShadow: '0 0 0 9999px rgba(0, 0, 0, 0.6)',
              pointerEvents: 'none',
              zIndex: 9998,
              // 白色曲线呼吸动画
              animation: 'guide-pulse 2s ease-in-out infinite',
            }}
          />

          {/* 底部固定提示条（pointer-events: auto，按钮可点） */}
          <div
            style={{
              position: 'fixed',
              bottom: 24,
              left: 16,
              right: 16,
              maxWidth: 420,
              margin: '0 auto',
              padding: '16px 20px',
              background: '#fff',
              borderRadius: 14,
              boxShadow: '0 6px 24px rgba(0,0,0,0.25)',
              zIndex: 9999,
              pointerEvents: 'auto',
            }}
          >
            {highlightTarget === 'name' ? (
              <>
                <div style={{ fontSize: 15, fontWeight: 600, color: '#333', marginBottom: 6 }}>
                  👋 第 1 步 / 共 2 步
                </div>
                <div style={{ fontSize: 13, color: '#666', lineHeight: 1.7 }}>
                  请在上方<strong>白色框</strong>内输入你的<strong>真实姓名</strong>。
                  <div style={{ color: '#999', fontSize: 12, marginTop: 4 }}>
                    姓名会自动用于生成你的 USP 账号（拼音）。
                  </div>
                </div>
              </>
            ) : (
              <>
                <div style={{ fontSize: 15, fontWeight: 600, color: '#333', marginBottom: 6 }}>
                  🔐 第 2 步 / 共 2 步
                </div>
                <div style={{ fontSize: 13, color: '#666', lineHeight: 1.7 }}>
                  请在上方<strong>白色框</strong>内设置你的<strong>USP 密码</strong>。
                  <div style={{ color: '#999', fontSize: 12, marginTop: 4 }}>
                    密码要求：至少 8 位，同时包含字母、数字和特殊字符。
                  </div>
                </div>
              </>
            )}
            <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
              <Button
                theme="default"
                variant="outline"
                size="small"
                block
                onClick={() => {
                  setHighlightTarget(null);
                  setHighlightRect(null);
                }}
              >
                跳过引导
              </Button>
              {highlightTarget === 'name' ? (
                <Button
                  theme="primary"
                  size="small"
                  block
                  onClick={() => focusGuide('password')}
                >
                  下一步
                </Button>
              ) : (
                <Button
                  theme="primary"
                  size="small"
                  block
                  onClick={() => {
                    setHighlightTarget(null);
                    setHighlightRect(null);
                  }}
                >
                  我已知晓
                </Button>
              )}
            </div>
          </div>

          {/* 曲线框呼吸动画 keyframes（全局注入一次即可） */}
          <style>{`
            @keyframes guide-pulse {
              0%, 100% { box-shadow: 0 0 0 9999px rgba(0,0,0,0.6), 0 0 0 0 rgba(255,255,255,0.7); }
              50%      { box-shadow: 0 0 0 9999px rgba(0,0,0,0.6), 0 0 0 12px rgba(255,255,255,0); }
            }
          `}</style>
        </>
      )}
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
