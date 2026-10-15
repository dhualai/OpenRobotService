// 后台管理 —— 奖励管理（后台管理 → 其他 → 奖励管理）
//
// 三块能力：
// 1. 转账测试：选中收款用户（下拉读 users 表）→ 发放奖励 → 对该奖励发起转账，
//    一步走完「记账 + 建单」
// 2. 转账单：列表 / 筛选 / 查单刷新 / 撤销
// 3. 奖励记录：列表 / 筛选 / 发起转账
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
} from 'tdesign-mobile-react';
import {
  PERM_WXPAY_ADMIN,
  cancelAdminBill,
  fetchAdminBillStats,
  fetchAdminBills,
  fetchAdminRewards,
  fetchAdminUserOptions,
  fenToYuan,
  grantAdminReward,
  refreshAdminBill,
  withdrawAdminReward,
} from '@/api/wxpay';
import type {
  AdminBillItem,
  AdminBillStats,
  AdminRewardItem,
  AdminUserOption,
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
  pending: '待提现',
  withdrawing: '提现中',
  success: '已到账',
  expired: '已失效',
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

export default function WxpayRewardManage() {
  const navigate = useNavigate();
  const allowed = useAuthStore((s) => s.hasPermission(PERM_WXPAY_ADMIN));

  const [tab, setTab] = useState('bills');
  const [actingId, setActingId] = useState<string | null>(null);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

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

  // ── 奖励记录 ──
  const [rewardStatus, setRewardStatus] = useState('');
  const [rewardKeyword, setRewardKeyword] = useState('');
  const [rewardPage, setRewardPage] = useState(1);
  const [rewards, setRewards] = useState<AdminRewardItem[]>([]);
  const [rewardTotal, setRewardTotal] = useState(0);
  const [rewardLoading, setRewardLoading] = useState(true);
  const [rewardError, setRewardError] = useState('');

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
  }, [rewardStatus, rewardKeyword, rewardPage]);

  const refreshAll = useCallback(() => {
    loadStats();
    loadBills();
    loadRewards();
  }, [loadStats, loadBills, loadRewards]);

  useEffect(() => {
    if (allowed) refreshAll();
  }, [allowed, refreshAll]);

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

  const billMaxPage = useMemo(() => Math.max(1, Math.ceil(billTotal / PAGE_SIZE)), [billTotal]);
  const rewardMaxPage = useMemo(
    () => Math.max(1, Math.ceil(rewardTotal / PAGE_SIZE)),
    [rewardTotal],
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

  return (
    <div className="admin-view">
      <Navbar title="奖励管理" leftArrow onLeftClick={() => navigate('/admin')} fixed />

      {/* ── 转账测试 ── */}
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

      {/* ── 状态概览 ── */}
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

      {/* ── 列表 ── */}
      <Tabs value={tab} onChange={(v) => setTab(String(v))}>
        <TabPanel value="bills" label="转账单">
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
              <div style={{ fontSize: 12, color: '#999', padding: '12px 0' }}>
                暂无转账单
              </div>
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
        </TabPanel>

        <TabPanel value="rewards" label="奖励记录">
          <section style={CARD}>
            <div style={FIELD_ROW}>
              <Input
                value={rewardKeyword}
                placeholder="搜索 openid / 活动名 / 来源 ID"
                onChange={(v) => setRewardKeyword(String(v ?? ''))}
              />
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
              {['', 'pending', 'withdrawing', 'success', 'expired'].map((s) => (
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
                  {s ? REWARD_STATUS_LABELS[s] : '全部'}
                </button>
              ))}
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
            </div>

            {rewardLoading ? (
              <div style={{ padding: '20px 0' }}>
                <Loading text="加载中..." />
              </div>
            ) : rewardError ? (
              <div style={{ fontSize: 12, color: '#e34d59' }}>{rewardError}</div>
            ) : rewards.length === 0 ? (
              <div style={{ fontSize: 12, color: '#999', padding: '12px 0' }}>
                暂无奖励记录
              </div>
            ) : (
              rewards.map((r) => {
                const done = r.status === 'success' || r.status === 'expired';
                return (
                  <div key={r.id} style={ROW}>
                    <div style={{ flex: 1, minWidth: 0, fontSize: 12, color: '#333' }}>
                      <div style={{ fontWeight: 600 }}>¥{fenToYuan(r.amount)}</div>
                      <div style={{ color: '#888', marginTop: 2 }}>
                        {REWARD_STATUS_LABELS[r.status] ?? r.status}
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
        </TabPanel>
      </Tabs>
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
