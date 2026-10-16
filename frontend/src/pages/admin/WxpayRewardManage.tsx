// 后台管理 —— 奖励管理（后台管理 → 其他 → 奖励管理）
//
// 五块能力（按权限条件渲染 Tab）：
// 1. 规则配置（PERM_REWARD_CONFIG）：闭环奖励 / 月度排名 / 点赞 / 审核策略
// 2. 待财务审核（PERM_REWARD_AUDIT）：批量通过 / 批量驳回 / 手动触发自动通过
// 3. 奖励记录（PERM_WXPAY_ADMIN）：扩筛选（role + source_type）
// 4. 转账单（PERM_WXPAY_ADMIN）：列表 / 查单刷新 / 撤销
// 5. 转账测试（PERM_WXPAY_ADMIN）：选中收款用户 → 发放奖励 → 发起转账
//
// ⚠️ 刻意不提供「删除」：转账单是资金流水，兜底查单与一致性巡检都依赖它留存，
//    删掉会让本地与微信侧对不上账。测试脏数据请走「撤销」。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Navbar,
  Loading,
  Toast,
  Button,
  Dialog,
  Input,
  Tabs,
  TabPanel,
  Checkbox,
  Switch,
} from 'tdesign-mobile-react';
import {
  PERM_WXPAY_ADMIN,
  PERM_REWARD_AUDIT,
  PERM_REWARD_CONFIG,
  cancelAdminBill,
  fetchAdminBillStats,
  fetchAdminBills,
  fetchAdminRewards,
  fetchAdminRewardStats,
  fetchAdminUserOptions,
  fenToYuan,
  grantAdminReward,
  refreshAdminBill,
  withdrawAdminReward,
  batchAuditRewards,
  batchRejectRewards,
  runAutoPassRewards,
  fetchRewardConfig,
  updateRewardConfig,
} from '@/api/wxpay';
import type {
  AdminBillItem,
  AdminBillStats,
  AdminRewardItem,
  AdminUserOption,
  AdminRewardStats,
  RewardConfigItem,
  RewardConfigUpdateIn,
} from '@/api/wxpay';
import { useAuthStore } from '@/stores/auth';

/** 微信侧转账单状态 → 中文 */
const BILL_STATE_LABELS: Record<string, string> = {
  ACCEPTED: '已受理',
  PROCESSING: '处理中',
  WAIT_USER_CONFIRM: '待用户确认',
  TRANSFERING: '转账中',
  SUCCESS: '已到账',
  FAIL: '失败',
  CANCELING: '撤销中',
  CANCELLED: '已撤销',
};

/** 终态：与后端 TransferBillState.TERMINAL 对齐 */
const TERMINAL_STATES = new Set(['SUCCESS', 'FAIL', 'CANCELLED']);

const REWARD_STATUS_LABELS: Record<string, string> = {
  pending_audit: '待财务审核',
  rejected: '已驳回',
  pending: '待提现',
  withdrawing: '提现中',
  success: '已到账',
  expired: '已失效',
};

const REWARD_ROLE_LABELS: Record<string, string> = {
  reporter: '提单人',
  assignee: '接单人',
  reviewer: '审核人',
  liker: '点赞人',
  ranker: '排名奖',
  manual: '手动',
};

const REWARD_SOURCE_TYPE_LABELS: Record<string, string> = {
  task: '工单',
  task_like: '工单点赞',
  monthly_rank: '月度排名',
  admin_test: '后台测试',
};

const PAGE_SIZE = 20;

const CARD: React.CSSProperties = {
  background: '#fff',
  borderRadius: 12,
  padding: 16,
  marginBottom: 16,
};

const CARD_TITLE: React.CSSProperties = {
  fontSize: 14,
  fontWeight: 600,
  color: '#333',
  marginBottom: 12,
};

const FIELD_ROW: React.CSSProperties = { marginBottom: 10 };

const FIELD_LABEL: React.CSSProperties = {
  fontSize: 12,
  color: '#888',
  marginBottom: 4,
};

/** 收款用户下拉：可滚动候选列表 */
const USER_LIST: React.CSSProperties = {
  marginTop: 6,
  maxHeight: 220,
  overflowY: 'auto',
  border: '1px solid #e7ebeb',
  borderRadius: 8,
};

const USER_HINT: React.CSSProperties = {
  marginTop: 6,
  fontSize: 11,
  color: '#999',
  lineHeight: 1.6,
};

function userOptionText(opt: AdminUserOption): string {
  if (!opt.openid) return '未绑定微信 openid';
  const tail = opt.openid.length > 6 ? `…${opt.openid.slice(-6)}` : opt.openid;
  return `${tail}（${opt.openid_source === 'self' ? '微信注册' : '已绑定'}）`;
}

/** 列表行：两栏（主信息 + 右侧操作区） */
const ROW: React.CSSProperties = {
  borderTop: '1px solid #f1f4f4',
  padding: '12px 0',
  display: 'flex',
  gap: 8,
  alignItems: 'flex-start',
};

function fmtTime(v?: string | null): string {
  if (!v) return '-';
  // 后端返回的是 UTC naive datetime，补 Z 让浏览器按时区解析
  const d = new Date(String(v).includes('T') ? v : `${v}Z`);
  return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString('zh-CN');
}

/** badge 小标签样式 */
const BADGE: React.CSSProperties = {
  display: 'inline-block',
  fontSize: 10,
  padding: '1px 6px',
  borderRadius: 8,
  background: '#eaf4fa',
  color: '#3697c3',
  marginRight: 4,
};

function pickConfig(configs: RewardConfigItem[] | undefined, key: string): RewardConfigItem | undefined {
  return (configs || []).find((c) => c.config_key === key);
}

export default function WxpayRewardManage() {
  const navigate = useNavigate();
  const allowed = useAuthStore((s) => s.hasPermission(PERM_WXPAY_ADMIN));
  const canAudit = useAuthStore((s) => s.hasPermission(PERM_REWARD_AUDIT));
  const canConfig = useAuthStore((s) => s.hasPermission(PERM_REWARD_CONFIG));

  // 可见 Tab（按权限）
  const visibleTabs = useMemo(() => {
    const base = ['rewards', 'bills', 'test'];
    if (canAudit) base.unshift('audit');
    if (canConfig) base.unshift('config');
    return base;
  }, [canAudit, canConfig]);

  const [tab, setTab] = useState<string>(visibleTabs[0] ?? 'rewards');
  const [actingId, setActingId] = useState<string | null>(null);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // ── 规则配置 ──
  const [configList, setConfigList] = useState<RewardConfigItem[]>([]);
  // 本地草稿（config_key → 展开后的草稿状态，便于实时编辑）
  const [configDraft, setConfigDraft] = useState<Record<string, {
    enabled: boolean;
    config: Record<string, unknown>;
  }>>({});
  const [configSaving, setConfigSaving] = useState<Record<string, boolean>>({});

  // ── 待财务审核 ──
  const [auditRewards, setAuditRewards] = useState<AdminRewardItem[]>([]);
  const [auditPage, setAuditPage] = useState(1);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditLoading, setAuditLoading] = useState(true);
  const [auditError, setAuditError] = useState('');
  const [auditIds, setAuditIds] = useState<Set<string>>(new Set());
  const [auditRemark, setAuditRemark] = useState('');
  const [auditActing, setAuditActing] = useState(false);
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [rejectRemarkInput, setRejectRemarkInput] = useState('');

  // ── 奖励统计概览（待审核笔数 / 金额等） ──
  const [rewardStats, setRewardStats] = useState<AdminRewardStats | null>(null);

  // ── 转账测试表单 ──
  const [form, setForm] = useState({
    user_id: '',
    openid: '',
    amount: '100',
    scene_name: '联调测试活动',
    reward_desc: '联调测试奖励',
    remark: '转账测试',
  });
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string>('');

  // ── 收款用户候选（下拉，读 users 表）──
  const [userKeyword, setUserKeyword] = useState('');
  const [userOptions, setUserOptions] = useState<AdminUserOption[]>([]);
  const [userTotal, setUserTotal] = useState(0);
  const [userLoading, setUserLoading] = useState(false);
  const [selectedUser, setSelectedUser] = useState<AdminUserOption | null>(null);

  const loadUserOptions = useCallback(
    (kw: string) => {
      if (!allowed) return;
      setUserLoading(true);
      fetchAdminUserOptions({ keyword: kw.trim() || undefined, limit: 200 })
        .then((r) => {
          if (!mountedRef.current) return;
          setUserOptions(r.items || []);
          setUserTotal(r.total || 0);
        })
        .catch(() => {
          if (!mountedRef.current) return;
          setUserOptions([]);
          setUserTotal(0);
        })
        .finally(() => {
          if (mountedRef.current) setUserLoading(false);
        });
    },
    [allowed],
  );

  // 首屏立即拉一次；之后输入关键字时防抖 300ms 再查（后端 limit 截断，靠搜索收敛）
  useEffect(() => {
    const timer = setTimeout(() => loadUserOptions(userKeyword), userKeyword ? 300 : 0);
    return () => clearTimeout(timer);
  }, [userKeyword, loadUserOptions]);

  const pickUser = useCallback((opt: AdminUserOption) => {
    setSelectedUser(opt);
    // openid 自动带入；解析不到时留空，由用户手填（测试环境支持任意值）
    setForm((f) => ({ ...f, user_id: opt.id, openid: opt.openid || '' }));
  }, []);

  // ── 转账单 ──
  const [stats, setStats] = useState<AdminBillStats | null>(null);
  const [billState, setBillState] = useState('');
  const [billKeyword, setBillKeyword] = useState('');
  const [billPage, setBillPage] = useState(1);
  const [bills, setBills] = useState<AdminBillItem[]>([]);
  const [billTotal, setBillTotal] = useState(0);
  const [billLoading, setBillLoading] = useState(true);
  const [billError, setBillError] = useState('');

  // ── 奖励记录（扩筛 role + source_type） ──
  const [rewardStatus, setRewardStatus] = useState('');
  const [rewardRole, setRewardRole] = useState('');
  const [rewardSourceType, setRewardSourceType] = useState('');
  const [rewardKeyword, setRewardKeyword] = useState('');
  const [rewardPage, setRewardPage] = useState(1);
  const [rewards, setRewards] = useState<AdminRewardItem[]>([]);
  const [rewardTotal, setRewardTotal] = useState(0);
  const [rewardLoading, setRewardLoading] = useState(true);
  const [rewardError, setRewardError] = useState('');

  // ── 加载奖励统计 ──
  const loadRewardStats = useCallback(() => {
    fetchAdminRewardStats()
      .then((r) => {
        if (mountedRef.current) setRewardStats(r);
      })
      .catch(() => {
        /* 概览失败不阻断列表 */
      });
  }, []);

  // ── 加载规则配置 ──
  const loadConfig = useCallback(() => {
    if (!canConfig) return;
    fetchRewardConfig()
      .then((r) => {
        if (!mountedRef.current) return;
        setConfigList(r);
        // 同步草稿
        setConfigDraft((prev) => {
          const next: Record<string, { enabled: boolean; config: Record<string, unknown> }> = {};
          for (const item of r) {
            next[item.config_key] = {
              enabled: item.enabled,
              config: { ...(item.config || {}) },
            };
          }
          // 保留已有未保存的草稿值（同一 config_key 覆盖）
          for (const k of Object.keys(prev)) {
            if (prev[k] && next[k]) next[k] = prev[k];
          }
          return next;
        });
      })
      .catch(() => {
        if (!mountedRef.current) setConfigList([]);
      });
  }, [canConfig]);

  // ── 加载待审核列表 ──
  const loadAuditRewards = useCallback(() => {
    if (!canAudit) return;
    setAuditLoading(true);
    setAuditError('');
    fetchAdminRewards({
      status: 'pending_audit',
      page: auditPage,
      size: PAGE_SIZE,
    })
      .then((r) => {
        if (!mountedRef.current) return;
        setAuditRewards(r.items || []);
        setAuditTotal(r.total || 0);
      })
      .catch((e: Error) => {
        if (!mountedRef.current) return;
        setAuditRewards([]);
        setAuditError(e?.message || '加载待审核列表失败');
      })
      .finally(() => {
        if (mountedRef.current) setAuditLoading(false);
      });
  }, [canAudit, auditPage]);

  const loadStats = useCallback(() => {
    fetchAdminBillStats()
      .then((r) => {
        if (mountedRef.current) setStats(r);
      })
      .catch(() => {
        /* 概览失败不阻断列表 */
      });
  }, []);

  const loadBills = useCallback(() => {
    setBillLoading(true);
    setBillError('');
    fetchAdminBills({
      state: billState || undefined,
      keyword: billKeyword.trim() || undefined,
      page: billPage,
      size: PAGE_SIZE,
    })
      .then((r) => {
        if (!mountedRef.current) return;
        setBills(r.items || []);
        setBillTotal(r.total || 0);
      })
      .catch((e: Error) => {
        if (!mountedRef.current) return;
        setBills([]);
        setBillError(e?.message || '加载转账单失败');
      })
      .finally(() => {
        if (mountedRef.current) setBillLoading(false);
      });
  }, [billState, billKeyword, billPage]);

  const loadRewards = useCallback(() => {
    setRewardLoading(true);
    setRewardError('');
    fetchAdminRewards({
      status: rewardStatus || undefined,
      role: rewardRole || undefined,
      source_type: rewardSourceType || undefined,
      keyword: rewardKeyword.trim() || undefined,
      page: rewardPage,
      size: PAGE_SIZE,
    })
      .then((r) => {
        if (!mountedRef.current) return;
        setRewards(r.items || []);
        setRewardTotal(r.total || 0);
      })
      .catch((e: Error) => {
        if (!mountedRef.current) return;
        setRewards([]);
        setRewardError(e?.message || '加载奖励记录失败');
      })
      .finally(() => {
        if (mountedRef.current) setRewardLoading(false);
      });
  }, [rewardStatus, rewardRole, rewardSourceType, rewardKeyword, rewardPage]);

  const refreshAll = useCallback(() => {
    loadStats();
    loadBills();
    loadRewards();
    loadRewardStats();
    if (canAudit) loadAuditRewards();
  }, [loadStats, loadBills, loadRewards, loadRewardStats, canAudit, loadAuditRewards]);

  useEffect(() => {
    if (allowed) refreshAll();
  }, [allowed, refreshAll]);

  // 初始化规则配置 & 奖励统计
  useEffect(() => {
    if (allowed) loadRewardStats();
    if (canConfig) loadConfig();
  }, [allowed, canConfig, loadRewardStats, loadConfig]);

  useEffect(() => {
    if (canAudit) loadAuditRewards();
  }, [canAudit, loadAuditRewards]);

  // ── 转账测试 ──
  const runTransferTest = useCallback(async () => {
    if (testing) return;
    const userId = form.user_id.trim();
    const amount = Number(form.amount);
    if (!userId) {
      Toast({ message: '请填写 user_id', theme: 'warning' });
      return;
    }
    if (!Number.isInteger(amount) || amount < 1) {
      Toast({ message: '金额须为 ≥1 的整数（单位：分）', theme: 'warning' });
      return;
    }
    if (!form.scene_name.trim() || !form.reward_desc.trim()) {
      Toast({ message: '活动名称与奖励说明为报备必填项', theme: 'warning' });
      return;
    }

    setTesting(true);
    setTestResult('');
    try {
      // source_type/source_id 带时间戳，保证每次测试都是新的一笔（防重复发放拦截）
      const stamp = Date.now();
      const granted = await grantAdminReward({
        user_id: userId,
        openid: form.openid.trim(),
        amount,
        scene_name: form.scene_name.trim(),
        reward_desc: form.reward_desc.trim(),
        remark: form.remark.trim(),
        source_type: 'admin_test',
        source_id: `test_${stamp}`,
      });
      const wd = await withdrawAdminReward(granted.id);
      setTestResult(
        `奖励 ${granted.id.slice(0, 8)}… → 单号 ${wd.out_bill_no}｜状态 ${wd.state || wd.action}｜${wd.message || ''}`,
      );
      Toast({ message: `已发起：${wd.state || wd.action}`, theme: 'success' });
      refreshAll();
    } catch (e) {
      const msg = e instanceof Error ? e.message : '转账测试失败';
      setTestResult(`失败：${msg}`);
      Toast({ message: msg, theme: 'error' });
    } finally {
      if (mountedRef.current) setTesting(false);
    }
  }, [form, testing, refreshAll]);

  // ── 转账单操作 ──
  const handleRefresh = useCallback(
    (bill: AdminBillItem) => {
      if (actingId) return;
      setActingId(bill.id);
      refreshAdminBill(bill.id)
        .then((r) => {
          Toast({ message: r.message || '已刷新', theme: 'success' });
          refreshAll();
        })
        .catch((e: Error) => Toast({ message: e?.message || '查单失败', theme: 'error' }))
        .finally(() => {
          if (mountedRef.current) setActingId(null);
        });
    },
    [actingId, refreshAll],
  );

  const handleCancel = useCallback(
    (bill: AdminBillItem) => {
      if (actingId) return;
      if (TERMINAL_STATES.has(bill.state || '')) {
        Toast({ message: '已到终态，不可撤销', theme: 'warning' });
        return;
      }
      Dialog.confirm?.({
        title: '确认撤销',
        content: `确定撤销转账单 ${bill.out_bill_no} 吗？\n撤销是受理语义，提交后会立即回查一次确认结果。`,
        onConfirm: () => {
          setActingId(bill.id);
          cancelAdminBill(bill.id)
            .then((r) => {
              Toast({ message: r.message || '撤销已受理', theme: 'success' });
              refreshAll();
            })
            .catch((e: Error) => Toast({ message: e?.message || '撤销失败', theme: 'error' }))
            .finally(() => {
              if (mountedRef.current) setActingId(null);
            });
        },
      });
    },
    [actingId, refreshAll],
  );

  const handleRewardWithdraw = useCallback(
    (item: AdminRewardItem) => {
      if (actingId) return;
      if (item.status === 'success' || item.status === 'expired') {
        Toast({ message: '该奖励已到终态，不可再提现', theme: 'warning' });
        return;
      }
      Dialog.confirm?.({
        title: '确认发起转账',
        content: `对奖励 ${item.id.slice(0, 8)}…（¥${fenToYuan(item.amount)}）发起转账？`,
        onConfirm: () => {
          setActingId(item.id);
          withdrawAdminReward(item.id)
            .then((r) => {
              Toast({
                message: `已发起：${r.state || r.action}`,
                theme: r.action === 'rejected' ? 'error' : 'success',
              });
              refreshAll();
            })
            .catch((e: Error) => Toast({ message: e?.message || '发起转账失败', theme: 'error' }))
            .finally(() => {
              if (mountedRef.current) setActingId(null);
            });
        },
      });
    },
    [actingId, refreshAll],
  );

  // ── 待审核：勾选切换 ──
  const toggleAuditId = useCallback((id: string) => {
    setAuditIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // ── 批量通过 ──
  const handleBatchAudit = useCallback(() => {
    if (auditActing) return;
    const ids = Array.from(auditIds);
    if (ids.length === 0) {
      Toast({ message: '请先勾选要通过的记录', theme: 'warning' });
      return;
    }
    Dialog.confirm?.({
      title: '确认批量通过',
      content: `将通过 ${ids.length} 笔待审核奖励，并自动发起转账。确定继续？`,
      onConfirm: async () => {
        setAuditActing(true);
        try {
          const r = await batchAuditRewards(ids);
          Toast({ message: r.message || `已通过 ${r.ok_count} 笔`, theme: 'success' });
          setAuditIds(new Set());
          loadAuditRewards();
          loadRewardStats();
        } catch (e) {
          const msg = e instanceof Error ? e.message : '批量通过失败';
          Toast({ message: msg, theme: 'error' });
        } finally {
          if (mountedRef.current) setAuditActing(false);
        }
      },
    });
  }, [auditIds, auditActing, loadAuditRewards, loadRewardStats]);

  // ── 批量驳回（弹 Dialog 填原因） ──
  const openRejectDialog = useCallback(() => {
    const ids = Array.from(auditIds);
    if (ids.length === 0) {
      Toast({ message: '请先勾选要驳回的记录', theme: 'warning' });
      return;
    }
    setRejectRemarkInput('');
    setRejectDialogOpen(true);
  }, [auditIds]);

  const confirmBatchReject = useCallback(() => {
    const ids = Array.from(auditIds);
    if (ids.length === 0 || auditActing) return;
    setAuditActing(true);
    setRejectDialogOpen(false);
    batchRejectRewards(ids, rejectRemarkInput.trim() || '批量驳回')
      .then((r) => {
        Toast({ message: r.message || `已驳回 ${r.ok_count} 笔`, theme: 'success' });
        setAuditIds(new Set());
        loadAuditRewards();
        loadRewardStats();
      })
      .catch((e: Error) => Toast({ message: e?.message || '批量驳回失败', theme: 'error' }))
      .finally(() => {
        if (mountedRef.current) setAuditActing(false);
      });
  }, [auditIds, auditActing, rejectRemarkInput, loadAuditRewards, loadRewardStats]);

  // ── 手动触发自动通过 ──
  const handleRunAutoPass = useCallback(() => {
    if (auditActing) return;
    Dialog.confirm?.({
      title: '手动触发自动通过',
      content: '对所有「待财务审核」记录重跑自动决策引擎（满足规则的自动通过）。确定继续？',
      onConfirm: async () => {
        setAuditActing(true);
        try {
          const r = await runAutoPassRewards();
          Toast({ message: r.message || '自动通过完成', theme: 'success' });
          loadAuditRewards();
          loadRewardStats();
        } catch (e) {
          const msg = e instanceof Error ? e.message : '自动通过失败';
          Toast({ message: msg, theme: 'error' });
        } finally {
          if (mountedRef.current) setAuditActing(false);
        }
      },
    });
  }, [auditActing, loadAuditRewards, loadRewardStats]);

  // ── 规则配置：保存单条 ──
  const saveConfigDraft = useCallback(
    async (configKey: string) => {
      if (configSaving[configKey]) return;
      const draft = configDraft[configKey];
      if (!draft) return;
      setConfigSaving((prev) => ({ ...prev, [configKey]: true }));
      try {
        const payload: RewardConfigUpdateIn = {
          enabled: draft.enabled,
          config: draft.config,
        };
        const r = await updateRewardConfig(configKey, payload);
        Toast({ message: `保存成功（${r.enabled ? '已启用' : '已停用'}）`, theme: 'success' });
        // 从服务端回拉一下 configList，使显示一致
        await loadConfig();
      } catch (e) {
        const msg = e instanceof Error ? e.message : '保存失败';
        Toast({ message: msg, theme: 'error' });
      } finally {
        if (mountedRef.current) setConfigSaving((prev) => ({ ...prev, [configKey]: false }));
      }
    },
    [configDraft, configSaving, loadConfig],
  );

  const billMaxPage = useMemo(() => Math.max(1, Math.ceil(billTotal / PAGE_SIZE)), [billTotal]);
  const rewardMaxPage = useMemo(
    () => Math.max(1, Math.ceil(rewardTotal / PAGE_SIZE)),
    [rewardTotal],
  );
  const auditMaxPage = useMemo(
    () => Math.max(1, Math.ceil(auditTotal / PAGE_SIZE)),
    [auditTotal],
  );

  if (!allowed) {
    return (
      <div className="admin-view">
        <Navbar title="奖励管理" leftArrow onLeftClick={() => navigate('/admin')} fixed />
        <div style={{ ...CARD, textAlign: 'center', color: '#999', fontSize: 13 }}>
          无权限访问（需要权限码 {PERM_WXPAY_ADMIN}）
        </div>
      </div>
    );
  }

  // 可见 Tab 顺序
  const TAB_PANELS: Array<{ value: string; label: string; render: () => React.ReactNode }> = [];
  if (canConfig) TAB_PANELS.push({ value: 'config', label: '规则配置', render: () => renderConfigTab() });
  if (canAudit) TAB_PANELS.push({ value: 'audit', label: '待财务审核', render: () => renderAuditTab() });
  TAB_PANELS.push({ value: 'rewards', label: '奖励记录', render: () => renderRewardsTab() });
  TAB_PANELS.push({ value: 'bills', label: '转账单', render: () => renderBillsTab() });
  TAB_PANELS.push({ value: 'test', label: '转账测试', render: () => renderTestTab() });

  // 保持 tab 在可见列表内
  useEffect(() => {
    if (!visibleTabs.includes(tab)) setTab(visibleTabs[0]);
  }, [visibleTabs, tab]);

  // ── 规则配置 Tab ──
  function renderConfigTab() {
    const updateDraft = (configKey: string, patch: Partial<{ enabled: boolean; config: Record<string, unknown> }>) => {
      setConfigDraft((prev) => {
        const cur = prev[configKey] || { enabled: false, config: {} };
        return {
          ...prev,
          [configKey]: {
            enabled: patch.enabled !== undefined ? patch.enabled : cur.enabled,
            config: patch.config !== undefined ? patch.config : cur.config,
          },
        };
      });
    };

    // 工具：更新 config 中某个 key 的数值（分）
    const updateConfigNum = (configKey: string, field: string, raw: string) => {
      const v = Number(raw);
      const newConfig = { ...(configDraft[configKey]?.config || {}) };
      newConfig[field] = Number.isFinite(v) ? v : 0;
      updateDraft(configKey, { config: newConfig });
    };

    // 工具：更新 config 中数组字段（多选，用逗号分隔）
    const updateConfigStringList = (configKey: string, field: string, raw: string) => {
      const newConfig = { ...(configDraft[configKey]?.config || {}) };
      const list = raw
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
      newConfig[field] = list;
      updateDraft(configKey, { config: newConfig });
    };

    const CLOSED_LOOP_KEY = 'task_archive';
    const MONTHLY_RANK_KEY = 'monthly_rank';
    const QUALITY_LIKE_KEY = 'quality_like';
    const AUDIT_STRATEGY_KEY = 'audit_policy';

    return (
      <>
        {/* 卡片① 基础闭环奖励 */}
        <ConfigCard
          title="基础闭环奖励"
          enabled={configDraft[CLOSED_LOOP_KEY]?.enabled ?? pickConfig(configList, CLOSED_LOOP_KEY)?.enabled ?? false}
          onEnabledChange={(v) => updateDraft(CLOSED_LOOP_KEY, { enabled: v })}
          saving={!!configSaving[CLOSED_LOOP_KEY]}
          onSave={() => saveConfigDraft(CLOSED_LOOP_KEY)}
        >
          <ConfigNumField
            label="提单人奖励"
            unit="分"
            value={String(configDraft[CLOSED_LOOP_KEY]?.config?.reporter_fen ?? '')}
            onChange={(v) => updateConfigNum(CLOSED_LOOP_KEY, 'reporter_fen', v)}
          />
          <ConfigNumField
            label="接单人奖励"
            unit="分"
            value={String(configDraft[CLOSED_LOOP_KEY]?.config?.assignee_fen ?? '')}
            onChange={(v) => updateConfigNum(CLOSED_LOOP_KEY, 'assignee_fen', v)}
          />
          <ConfigNumField
            label="审核人奖励"
            unit="分"
            value={String(configDraft[CLOSED_LOOP_KEY]?.config?.reviewer_fen ?? '')}
            onChange={(v) => updateConfigNum(CLOSED_LOOP_KEY, 'reviewer_fen', v)}
          />
        </ConfigCard>

        {/* 卡片② 月度排名 */}
        <ConfigCard
          title="月度排名"
          enabled={configDraft[MONTHLY_RANK_KEY]?.enabled ?? pickConfig(configList, MONTHLY_RANK_KEY)?.enabled ?? false}
          onEnabledChange={(v) => updateDraft(MONTHLY_RANK_KEY, { enabled: v })}
          saving={!!configSaving[MONTHLY_RANK_KEY]}
          onSave={() => saveConfigDraft(MONTHLY_RANK_KEY)}
        >
          <ConfigNumField
            label="Top 3 奖励"
            unit="分"
            value={String(configDraft[MONTHLY_RANK_KEY]?.config?.top3_fen ?? '')}
            onChange={(v) => updateConfigNum(MONTHLY_RANK_KEY, 'top3_fen', v)}
          />
          <ConfigNumField
            label="Top 4-10 奖励"
            unit="分"
            value={String(configDraft[MONTHLY_RANK_KEY]?.config?.top4_10_fen ?? '')}
            onChange={(v) => updateConfigNum(MONTHLY_RANK_KEY, 'top4_10_fen', v)}
          />
        </ConfigCard>

        {/* 卡片③ 优质点赞 */}
        <ConfigCard
          title="优质点赞"
          enabled={configDraft[QUALITY_LIKE_KEY]?.enabled ?? pickConfig(configList, QUALITY_LIKE_KEY)?.enabled ?? false}
          onEnabledChange={(v) => updateDraft(QUALITY_LIKE_KEY, { enabled: v })}
          saving={!!configSaving[QUALITY_LIKE_KEY]}
          onSave={() => saveConfigDraft(QUALITY_LIKE_KEY)}
        >
          <ConfigNumField
            label="每次点赞奖励"
            unit="分"
            value={String(configDraft[QUALITY_LIKE_KEY]?.config?.per_like_fen ?? '')}
            onChange={(v) => updateConfigNum(QUALITY_LIKE_KEY, 'per_like_fen', v)}
          />
          <ConfigNumField
            label="每日上限"
            unit="次"
            value={String(configDraft[QUALITY_LIKE_KEY]?.config?.daily_limit ?? '')}
            onChange={(v) => updateConfigNum(QUALITY_LIKE_KEY, 'daily_limit', v)}
          />
        </ConfigCard>

        {/* 卡片④ 审核策略 */}
        <ConfigCard
          title="审核策略"
          enabled={configDraft[AUDIT_STRATEGY_KEY]?.enabled ?? pickConfig(configList, AUDIT_STRATEGY_KEY)?.enabled ?? false}
          onEnabledChange={(v) => updateDraft(AUDIT_STRATEGY_KEY, { enabled: v })}
          saving={!!configSaving[AUDIT_STRATEGY_KEY]}
          onSave={() => saveConfigDraft(AUDIT_STRATEGY_KEY)}
        >
          <ConfigNumField
            label="自动通过单笔上限"
            unit="分"
            hint="超过此值强制进入审核"
            value={String(configDraft[AUDIT_STRATEGY_KEY]?.config?.auto_pass_max_fen ?? '')}
            onChange={(v) => updateConfigNum(AUDIT_STRATEGY_KEY, 'auto_pass_max_fen', v)}
          />
          <ConfigNumField
            label="强制审核-用户累计总额阈值"
            unit="分"
            hint="单个用户累计奖励超过此值，后续强制进入审核"
            value={String(configDraft[AUDIT_STRATEGY_KEY]?.config?.force_audit_user_total_count ?? '')}
            onChange={(v) => updateConfigNum(AUDIT_STRATEGY_KEY, 'force_audit_user_total_count', v)}
          />
          <ConfigNumField
            label="强制审核-用户当日总额阈值"
            unit="分"
            value={String(configDraft[AUDIT_STRATEGY_KEY]?.config?.force_audit_user_daily_fen ?? '')}
            onChange={(v) => updateConfigNum(AUDIT_STRATEGY_KEY, 'force_audit_user_daily_fen', v)}
          />
          <ConfigNumField
            label="强制审核-用户当日笔数阈值"
            unit="笔"
            value={String(configDraft[AUDIT_STRATEGY_KEY]?.config?.force_audit_user_daily_count ?? '')}
            onChange={(v) => updateConfigNum(AUDIT_STRATEGY_KEY, 'force_audit_user_daily_count', v)}
          />
          <div style={{ ...FIELD_ROW, marginTop: 6 }}>
            <div style={FIELD_LABEL}>启用自动通过场景（逗号分隔，留空=所有场景）</div>
            <Input
              placeholder="如: closed_loop, monthly_rank, quality_like"
              value={Array.isArray(configDraft[AUDIT_STRATEGY_KEY]?.config?.enabled_scenes) ? (configDraft[AUDIT_STRATEGY_KEY]?.config?.enabled_scenes as string[]).join(', ') : ''}
              onChange={(v) => updateConfigStringList(AUDIT_STRATEGY_KEY, 'enabled_scenes', String(v ?? ''))}
            />
          </div>
        </ConfigCard>
      </>
    );
  }

  // ── 待财务审核 Tab ──
  function renderAuditTab() {
    const auditIdsArr = Array.from(auditIds);
    const allChecked = auditRewards.length > 0 && auditRewards.every((r) => auditIds.has(r.id));
    const toggleAll = () => {
      if (allChecked) setAuditIds(new Set());
      else setAuditIds(new Set(auditRewards.map((r) => r.id)));
    };
    return (
      <>
        <section style={CARD}>
          <div style={{ fontSize: 12, color: '#666', marginBottom: 10 }}>
            待审核 {rewardStats?.pending_audit_count ?? 0} 笔 / ¥
            {fenToYuan(rewardStats?.pending_audit_amount ?? 0)}
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <div style={{ ...FIELD_ROW, flex: 1, marginBottom: 0, display: 'flex', gap: 6 }}>
              <Checkbox checked={allChecked} onChange={toggleAll}>全选</Checkbox>
            </div>
            <Button
              size="small"
              variant="outline"
              theme="primary"
              disabled={auditIdsArr.length === 0 || auditActing}
              loading={auditActing}
              onClick={handleBatchAudit}
            >
              批量通过（{auditIdsArr.length}）
            </Button>
            <Button
              size="small"
              variant="outline"
              theme="danger"
              disabled={auditIdsArr.length === 0 || auditActing}
              loading={auditActing}
              onClick={openRejectDialog}
            >
              批量驳回（{auditIdsArr.length}）
            </Button>
            <Button
              size="small"
              variant="outline"
              disabled={auditActing}
              loading={auditActing}
              onClick={handleRunAutoPass}
            >
              手动触发自动通过
            </Button>
          </div>
        </section>

        <section style={CARD}>
          {auditLoading ? (
            <div style={{ padding: '20px 0' }}>
              <Loading text="加载中..." />
            </div>
          ) : auditError ? (
            <div style={{ fontSize: 12, color: '#e34d59' }}>{auditError}</div>
          ) : auditRewards.length === 0 ? (
            <div style={{ fontSize: 12, color: '#999', padding: '12px 0' }}>暂无待审核记录</div>
          ) : (
            auditRewards.map((r) => (
              <div key={r.id} style={ROW}>
                <div style={{ marginRight: 8 }}>
                  <Checkbox
                    checked={auditIds.has(r.id)}
                    onChange={() => toggleAuditId(r.id)}
                  />
                </div>
                <div style={{ flex: 1, minWidth: 0, fontSize: 12, color: '#333' }}>
                  <div style={{ fontWeight: 600 }}>¥{fenToYuan(r.amount)}</div>
                  <div style={{ color: '#888', marginTop: 2 }}>
                    <span style={BADGE}>{REWARD_ROLE_LABELS[r.role || ''] ?? r.role ?? '-'}</span>
                    <span style={BADGE}>{REWARD_SOURCE_TYPE_LABELS[r.source_type] ?? r.source_type}</span>
                    {r.source_id ? `工单 ${r.source_id}` : ''}
                  </div>
                  <div style={{ color: '#aaa', marginTop: 2, wordBreak: 'break-all' }}>
                    用户 {r.user_id.slice(0, 12)}｜{r.openid || '无 openid'}｜{fmtTime(r.created_at)}
                  </div>
                  {r.audit_remark ? (
                    <div style={{ color: '#d54941', marginTop: 2 }}>风控标记：{r.audit_remark}</div>
                  ) : null}
                </div>
              </div>
            ))
          )}
          <Pager
            page={auditPage}
            maxPage={auditMaxPage}
            total={auditTotal}
            onChange={(p) => {
              setAuditPage(p);
              setAuditIds(new Set());
            }}
          />
        </section>

        {/* 驳回 Dialog */}
        {rejectDialogOpen ? (
          <Dialog
            visible={rejectDialogOpen}
            title="批量驳回"
            content={
              <div>
                <div style={{ fontSize: 12, color: '#666', marginBottom: 8 }}>
                  驳回 {auditIdsArr.length} 笔，必填驳回原因（作为 audit_remark）：
                </div>
                <Input
                  value={rejectRemarkInput}
                  placeholder="请输入驳回原因"
                  onChange={(v) => setRejectRemarkInput(String(v ?? ''))}
                />
              </div>
            }
            onClose={() => setRejectDialogOpen(false)}
            onConfirm={confirmBatchReject}
            confirmBtn={{ content: '确认驳回', theme: 'danger' }}
            cancelBtn={{ content: '取消' }}
          />
        ) : null}
      </>
    );
  }

  // ── 奖励记录 Tab ──
  function renderRewardsTab() {
    const statusOptions = ['', 'pending_audit', 'pending', 'withdrawing', 'success', 'expired', 'rejected'];
    return (
      <section style={CARD}>
        <div style={FIELD_ROW}>
          <Input
            value={rewardKeyword}
            placeholder="搜索 openid / 活动名 / 来源 ID"
            onChange={(v) => setRewardKeyword(String(v ?? ''))}
          />
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
          {statusOptions.map((s) => (
            <button
              key={s || 'all'}
              type="button"
              onClick={() => {
                setRewardStatus(s);
                setRewardPage(1);
              }}
              style={{
                fontSize: 11,
                padding: '3px 8px',
                borderRadius: 10,
                border: '1px solid #dfe3e3',
                background: rewardStatus === s ? '#3697c3' : '#fff',
                color: rewardStatus === s ? '#fff' : '#666',
                cursor: 'pointer',
              }}
            >
              {s ? REWARD_STATUS_LABELS[s] : '全部状态'}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
          <select
            value={rewardRole}
            onChange={(e) => {
              setRewardRole(e.target.value);
              setRewardPage(1);
            }}
            style={{
              flex: 1,
              padding: '6px 8px',
              borderRadius: 6,
              border: '1px solid #dfe3e3',
              fontSize: 12,
              background: '#fff',
            }}
          >
            <option value="">全部角色</option>
            {Object.entries(REWARD_ROLE_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
          <select
            value={rewardSourceType}
            onChange={(e) => {
              setRewardSourceType(e.target.value);
              setRewardPage(1);
            }}
            style={{
              flex: 1,
              padding: '6px 8px',
              borderRadius: 6,
              border: '1px solid #dfe3e3',
              fontSize: 12,
              background: '#fff',
            }}
          >
            <option value="">全部来源</option>
            {Object.entries(REWARD_SOURCE_TYPE_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <Button
            size="small"
            variant="outline"
            onClick={() => {
              setRewardPage(1);
              loadRewards();
            }}
          >
            搜索
          </Button>
          {(rewardRole || rewardSourceType || rewardStatus) ? (
            <Button
              size="small"
              variant="outline"
              onClick={() => {
                setRewardRole('');
                setRewardSourceType('');
                setRewardStatus('');
                setRewardPage(1);
              }}
            >
              清除筛选
            </Button>
          ) : null}
        </div>

        {rewardLoading ? (
          <div style={{ padding: '20px 0' }}>
            <Loading text="加载中..." />
          </div>
        ) : rewardError ? (
          <div style={{ fontSize: 12, color: '#e34d59' }}>{rewardError}</div>
        ) : rewards.length === 0 ? (
          <div style={{ fontSize: 12, color: '#999', padding: '12px 0' }}>暂无奖励记录</div>
        ) : (
          rewards.map((r) => {
            const done = r.status === 'success' || r.status === 'expired';
            const autoAudit = r.audit_by === 'AUTO' || r.audit_by === 'auto';
            return (
              <div key={r.id} style={ROW}>
                <div style={{ flex: 1, minWidth: 0, fontSize: 12, color: '#333' }}>
                  <div style={{ fontWeight: 600 }}>¥{fenToYuan(r.amount)}</div>
                  <div style={{ color: '#888', marginTop: 2 }}>
                    <span style={BADGE}>{REWARD_ROLE_LABELS[r.role || ''] ?? r.role ?? '-'}</span>
                    {REWARD_STATUS_LABELS[r.status] ?? r.status}
                    {autoAudit ? '（自动通过）' : ''}
                    {r.bill_state
                      ? `｜${BILL_STATE_LABELS[r.bill_state] ?? r.bill_state}`
                      : ''}
                  </div>
                  <div style={{ color: '#aaa', marginTop: 2, wordBreak: 'break-all' }}>
                    {r.scene_name}｜{r.openid || '无 openid'}｜{fmtTime(r.created_at)}
                  </div>
                  <div style={{ color: '#aaa', marginTop: 2 }}>
                    user {r.user_id.slice(0, 8)}
                    {r.out_bill_no ? `｜${r.out_bill_no}` : ''}
                  </div>
                </div>
                <Button
                  size="extra-small"
                  theme="primary"
                  variant="outline"
                  loading={actingId === r.id}
                  disabled={done || (!!actingId && actingId !== r.id)}
                  onClick={() => handleRewardWithdraw(r)}
                >
                  发起转账
                </Button>
              </div>
            );
          })
        )}

        <Pager
          page={rewardPage}
          maxPage={rewardMaxPage}
          total={rewardTotal}
          onChange={setRewardPage}
        />
      </section>
    );
  }

  // ── 转账单 Tab ──
  function renderBillsTab() {
    return (
      <section style={CARD}>
        <div style={FIELD_ROW}>
          <Input
            value={billKeyword}
            placeholder="搜索商户单号 / 微信单号 / openid"
            onChange={(v) => setBillKeyword(String(v ?? ''))}
          />
        </div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <Button
            size="small"
            variant="outline"
            onClick={() => {
              setBillKeyword((k) => k);
              setBillPage(1);
              loadBills();
            }}
          >
            搜索
          </Button>
          {billState ? (
            <Button
              size="small"
              variant="outline"
              onClick={() => {
                setBillState('');
                setBillPage(1);
              }}
            >
              清除状态筛选
            </Button>
          ) : null}
          <Button size="small" variant="outline" onClick={refreshAll}>
            刷新
          </Button>
        </div>

        {billLoading ? (
          <div style={{ padding: '20px 0' }}>
            <Loading text="加载中..." />
          </div>
        ) : billError ? (
          <div style={{ fontSize: 12, color: '#e34d59' }}>{billError}</div>
        ) : bills.length === 0 ? (
          <div style={{ fontSize: 12, color: '#999', padding: '12px 0' }}>暂无转账单</div>
        ) : (
          bills.map((b) => {
            const terminal = TERMINAL_STATES.has(b.state || '');
            return (
              <div key={b.id} style={ROW}>
                <div style={{ flex: 1, minWidth: 0, fontSize: 12, color: '#333' }}>
                  <div style={{ fontWeight: 600, wordBreak: 'break-all' }}>
                    {b.out_bill_no}
                  </div>
                  <div style={{ color: '#888', marginTop: 2 }}>
                    ¥{fenToYuan(b.amount || 0)}｜
                    {BILL_STATE_LABELS[b.state || ''] ?? b.state ?? '无状态'}
                    {b.transfer_bill_no ? `｜${b.transfer_bill_no}` : ''}
                  </div>
                  <div style={{ color: '#aaa', marginTop: 2 }}>
                    openid {b.openid || '-'}｜查单 {b.query_count} 次｜
                    {fmtTime(b.created_at)}
                  </div>
                  {b.fail_reason ? (
                    <div style={{ color: '#e34d59', marginTop: 2 }}>
                      失败：{b.fail_reason}
                    </div>
                  ) : null}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <Button
                    size="extra-small"
                    variant="outline"
                    loading={actingId === b.id}
                    disabled={!!actingId && actingId !== b.id}
                    onClick={() => handleRefresh(b)}
                  >
                    查单
                  </Button>
                  <Button
                    size="extra-small"
                    variant="outline"
                    theme="danger"
                    disabled={terminal || (!!actingId && actingId !== b.id)}
                    onClick={() => handleCancel(b)}
                  >
                    撤销
                  </Button>
                </div>
              </div>
            );
          })
        )}

        <Pager
          page={billPage}
          maxPage={billMaxPage}
          total={billTotal}
          onChange={setBillPage}
        />
      </section>
    );
  }

  // ── 转账测试 Tab ──
  function renderTestTab() {
    return (
      <section style={CARD}>
        <div style={CARD_TITLE}>转账测试</div>
        <div style={FIELD_ROW}>
          <div style={FIELD_LABEL}>收款用户（下拉取自 users 表，选中即填 users.id）</div>
          <Input
            value={userKeyword}
            placeholder="搜索姓名 / 账号 / users.id / openid"
            onChange={(v) => setUserKeyword(String(v ?? ''))}
          />
          <div style={USER_LIST}>
            {userLoading ? (
              <div style={USER_HINT}>加载中…</div>
            ) : userOptions.length === 0 ? (
              <div style={USER_HINT}>无匹配用户</div>
            ) : (
              userOptions.map((u) => {
                const active = u.id === form.user_id;
                return (
                  <div
                    key={u.id}
                    onClick={() => pickUser(u)}
                    style={{
                      padding: '8px 10px',
                      borderBottom: '1px solid #f1f4f4',
                      background: active ? '#eaf4fa' : '#fff',
                      cursor: 'pointer',
                    }}
                  >
                    <div style={{ fontSize: 13, color: '#333' }}>{u.name || u.username}</div>
                    <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
                      {u.username}｜{userOptionText(u)}
                    </div>
                  </div>
                );
              })
            )}
          </div>
          <div style={USER_HINT}>
            共 {userTotal} 人
            {userOptions.length < userTotal
              ? `，已显示前 ${userOptions.length} 条，请用上方搜索缩小范围`
              : ''}
          </div>
          <div style={USER_HINT}>
            已选中：
            {selectedUser
              ? `${selectedUser.name || selectedUser.username}（${selectedUser.id}）`
              : '未选择'}
          </div>
        </div>
        <div style={FIELD_ROW}>
          <div style={FIELD_LABEL}>收款 openid（选中用户后自动带入，可手改）</div>
          <Input
            value={form.openid}
            placeholder="测试环境可填任意值，如 oTEST0001"
            onChange={(v) => setForm((f) => ({ ...f, openid: String(v ?? '') }))}
          />
          {form.user_id && !form.openid.trim() ? (
            <div style={{ ...USER_HINT, color: '#d54941' }}>
              该用户解析不到 openid，请手填或从下拉重选；留空时后端回退 users.wechat_openid
            </div>
          ) : null}
        </div>
        <div style={FIELD_ROW}>
          <div style={FIELD_LABEL}>金额（分）</div>
          <Input
            value={form.amount}
            placeholder="100"
            onChange={(v) => setForm((f) => ({ ...f, amount: String(v ?? '') }))}
          />
        </div>
        <div style={FIELD_ROW}>
          <div style={FIELD_LABEL}>活动名称（报备信息①）</div>
          <Input
            value={form.scene_name}
            onChange={(v) => setForm((f) => ({ ...f, scene_name: String(v ?? '') }))}
          />
        </div>
        <div style={FIELD_ROW}>
          <div style={FIELD_LABEL}>奖励说明（报备信息②）</div>
          <Input
            value={form.reward_desc}
            onChange={(v) => setForm((f) => ({ ...f, reward_desc: String(v ?? '') }))}
          />
        </div>
        <div style={FIELD_ROW}>
          <div style={FIELD_LABEL}>转账备注（≤32 字符，用户可见）</div>
          <Input
            value={form.remark}
            onChange={(v) => setForm((f) => ({ ...f, remark: String(v ?? '') }))}
          />
        </div>
        <Button block theme="primary" loading={testing} onClick={runTransferTest}>
          {testing ? '发起中…' : '发放奖励并发起转账'}
        </Button>
        {testResult ? (
          <div
            style={{
              marginTop: 10,
              fontSize: 12,
              color: '#666',
              wordBreak: 'break-all',
              lineHeight: 1.6,
            }}
          >
            {testResult}
          </div>
        ) : null}
      </section>
    );
  }

  return (
    <div className="admin-view">
      <Navbar title="奖励管理" leftArrow onLeftClick={() => navigate('/admin')} fixed />

      {/* ── 状态概览 ── */}
      {canConfig || canAudit ? (
        <section style={CARD}>
          <div style={CARD_TITLE}>概览</div>
          {!rewardStats ? (
            <div style={{ fontSize: 12, color: '#999' }}>加载中…</div>
          ) : (
            <div style={{ fontSize: 12, color: '#666', lineHeight: 1.8 }}>
              待审核 {rewardStats.pending_audit_count} 笔 / ¥{fenToYuan(rewardStats.pending_audit_amount)}
              {rewardStats.rejected_count > 0 ? `｜已驳回 ${rewardStats.rejected_count}` : ''}
              ｜待提现 {rewardStats.pending_count}｜提现中 {rewardStats.withdrawing_count}｜已到账{' '}
              {rewardStats.success_count}｜已失效 {rewardStats.expired_count}
            </div>
          )}
        </section>
      ) : null}

      {/* ── 转账单概览（保留现有） ── */}
      {tab === 'bills' ? (
        <section style={CARD}>
          <div style={CARD_TITLE}>转账单概览</div>
          {!stats ? (
            <div style={{ fontSize: 12, color: '#999' }}>加载中…</div>
          ) : (
            <>
              <div style={{ fontSize: 12, color: '#666', marginBottom: 8 }}>
                共 {stats.total} 笔｜非终态 {stats.open_count} 笔｜终态{' '}
                {stats.terminal_count} 笔
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {Object.entries(stats.by_state || {}).map(([s, c]) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => {
                      setBillState((cur) => (cur === s ? '' : s));
                      setBillPage(1);
                    }}
                    style={{
                      fontSize: 11,
                      padding: '3px 8px',
                      borderRadius: 10,
                      border: '1px solid #dfe3e3',
                      background: billState === s ? '#3697c3' : '#fff',
                      color: billState === s ? '#fff' : '#666',
                      cursor: 'pointer',
                    }}
                  >
                    {BILL_STATE_LABELS[s] ?? s} {c}
                  </button>
                ))}
              </div>
            </>
          )}
        </section>
      ) : null}

      {/* ── Tabs ── */}
      <Tabs value={tab} onChange={(v) => setTab(String(v))}>
        {TAB_PANELS.map((p) => (
          <TabPanel key={p.value} value={p.value} label={p.label}>
            {p.render()}
          </TabPanel>
        ))}
      </Tabs>
    </div>
  );
}

// ── 规则配置子组件 ──
function ConfigCard({
  title,
  enabled,
  onEnabledChange,
  saving,
  onSave,
  children,
}: {
  title: string;
  enabled: boolean;
  onEnabledChange: (v: boolean) => void;
  saving: boolean;
  onSave: () => void;
  children: React.ReactNode;
}) {
  return (
    <section style={CARD}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 12,
        }}
      >
        <div style={CARD_TITLE}>{title}</div>
        <Switch
          value={enabled}
          onChange={(v) => onEnabledChange(!!v)}
        />
      </div>
      {children}
      <div style={{ marginTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
        <Button
          size="small"
          theme="primary"
          loading={saving}
          onClick={onSave}
        >
          保存
        </Button>
      </div>
    </section>
  );
}

function ConfigNumField({
  label,
  unit,
  hint,
  value,
  onChange,
}: {
  label: string;
  unit?: string;
  hint?: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div style={FIELD_ROW}>
        <div style={FIELD_LABEL}>{label}{unit ? `（${unit}）` : ''}</div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <Input
            value={value}
            placeholder="0"
            onChange={(v) => onChange(String(v))}
            type="number"
          />
        {unit ? <span style={{ fontSize: 12, color: '#999', minWidth: 32 }}>{unit}</span> : null}
      </div>
      {hint ? <div style={{ ...FIELD_LABEL, color: '#aaa', marginTop: 2 }}>{hint}</div> : null}
    </div>
  );
}

/** 分页器：上一页 / 下一页 + 页码，够用即可（管理端不追求复杂跳页） */
function Pager({
  page,
  maxPage,
  total,
  onChange,
}: {
  page: number;
  maxPage: number;
  total: number;
  onChange: (p: number) => void;
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        borderTop: '1px solid #f1f4f4',
        paddingTop: 10,
        fontSize: 12,
        color: '#888',
      }}
    >
      <span>共 {total} 条</span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Button
          size="extra-small"
          variant="outline"
          disabled={page <= 1}
          onClick={() => onChange(Math.max(1, page - 1))}
        >
          上一页
        </Button>
        <span>
          {page} / {maxPage}
        </span>
        <Button
          size="extra-small"
          variant="outline"
          disabled={page >= maxPage}
          onClick={() => onChange(Math.min(maxPage, page + 1))}
        >
          下一页
        </Button>
      </span>
    </div>
  );
}
