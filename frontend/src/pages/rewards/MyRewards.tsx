// 我的历史奖励 —— 微信商家转账（无卡包形态）的用户侧唯一入口
//
// 形态：奖励发放只在商户侧记账，用户在这里看见「历史奖励」，点「立即提现」才发起转账到零钱。
// 提现结果按后端 action 分三支处理（见 handleWithdraw）：
//   confirm    → 用 mch_id/app_id/package 调 WeixinJSBridge 拉起微信确认页
//   processing → 转账单非终态，提示稍后查看（不重复建单，防重复出资）
//   其它       → success/expired/rejected 直接提示并可刷新列表
//
// ⚠️ 确认页返回 ok 不等于到账：到账由微信回调扭转 reward 状态，前端只提示「已提交，等待到账」。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Input, Loading, Navbar, Popup, Toast } from 'tdesign-mobile-react';
import { POLL_INTERVAL_MS } from '@/config/poll';
import {
  fetchRewardStats,
  fetchRewards,
  fenToYuan,
  withdrawReward,
  type RewardItem,
  type RewardStats,
  type RewardStatus,
} from '@/api/wxpay';
import { requestMerchantTransfer } from '@/shared/utils/merchantTransfer';

const PAGE_SIZE = 20;

const STATUS_TABS: { key: RewardStatus | ''; label: string }[] = [
  { key: '', label: '全部' },
  { key: 'pending', label: '待提现' },
  { key: 'withdrawing', label: '提现中' },
  { key: 'success', label: '已到账' },
  { key: 'expired', label: '已失效' },
];

const STATUS_META: Record<RewardStatus, { label: string; color: string; bg: string }> = {
  pending: { label: '待提现', color: '#227197', bg: 'rgba(54,151,195,0.12)' },
  withdrawing: { label: '提现中', color: '#b76e00', bg: 'rgba(255,167,38,0.16)' },
  success: { label: '已到账', color: '#2ba471', bg: 'rgba(43,164,113,0.14)' },
  expired: { label: '已失效', color: '#8a8f92', bg: 'rgba(138,143,146,0.14)' },
};

/** 按钮文案：withdrawing 也允许点击 —— 后端会复用未过期的 package 再拉一次确认页 */
function actionLabel(status: RewardStatus): string {
  if (status === 'pending') return '立即提现';
  if (status === 'withdrawing') return '继续确认';
  if (status === 'success') return '已到账';
  return '已失效';
}

/** yyyy-MM-dd（本地时区）；空值返回占位 */
function fmtDate(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export default function MyRewards() {
  const navigate = useNavigate();

  const [status, setStatus] = useState<RewardStatus | ''>('');
  const [pages, setPages] = useState(1); // 已加载页数；每次按 PAGE_SIZE*pages 拉取，翻页不丢已看内容
  const [items, setItems] = useState<RewardItem[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<RewardStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  /** 真实姓名弹窗：微信实名校验未通过时要求用户填写 */
  const [nameDialog, setNameDialog] = useState<{ open: boolean; reward: RewardItem | null }>({
    open: false,
    reward: null,
  });
  const [realName, setRealName] = useState('');
  /** 正在提现的奖励 id：串行化点击，避免连点打出多笔建单请求 */
  const [pendingId, setPendingId] = useState<string | null>(null);

  const load = useCallback(
    async (opts?: { silent?: boolean; more?: boolean }) => {
      try {
        if (opts?.more) setLoadingMore(true);
        else setLoading(true);
        const [list, st] = await Promise.all([
          fetchRewards({ status, page: 1, size: PAGE_SIZE * pages }),
          fetchRewardStats().catch(() => null),
        ]);
        setItems(list.items || []);
        setTotal(list.total || 0);
        if (st) setStats(st);
        setError('');
      } catch (e) {
        if (!opts?.more) setItems([]);
        setError(e instanceof Error ? e.message : '加载失败，请稍后重试');
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [status, pages],
  );

  useEffect(() => {
    void load();
  }, [load]);

  // 有「提现中」的奖励时轮询：微信确认后由回调改状态，这里只做前端兜底刷新
  const hasWithdrawing = useMemo(() => items.some((i) => i.status === 'withdrawing'), [items]);
  useEffect(() => {
    if (!hasWithdrawing) return;
    const timer = window.setInterval(() => void load({ silent: true }), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [hasWithdrawing, load]);

  const closeNameDialog = useCallback(() => {
    setNameDialog({ open: false, reward: null });
  }, []);

  const runWithdraw = useCallback(
    async (item: RewardItem, userName?: string) => {
      if (pendingId) return; // 已有提现在途：忽略连点
      if (item.status === 'success' || item.status === 'expired') return;

      setPendingId(item.id);
      try {
        const res = await withdrawReward(item.id, userName);

        if (res.action === 'need_real_name') {
          // 收款人姓名未通过微信实名校验：弹窗收集真实姓名后重新提交
          setRealName('');
          setNameDialog({ open: true, reward: item });
          return;
        }

        if (res.action === 'confirm') {
          const r = await requestMerchantTransfer({
            mchId: res.mch_id,
            appId: res.app_id,
            package: res.package,
          });
          if (r.result === 'ok') {
            Toast({ message: '已提交，到账后自动更新', theme: 'success' });
          } else if (r.result === 'cancel') {
            Toast({ message: '已取消确认，可重新提现', theme: 'warning' });
          } else if (r.result === 'unsupported') {
            Toast({ message: '请在微信中打开以完成收款确认', theme: 'warning' });
          } else {
            Toast({ message: r.message || '调起失败，请重新提现', theme: 'error' });
          }
        } else if (res.action === 'processing') {
          // 转账单已建且非终态：不重复建单，只提示 + 等回调/轮询
          Toast({ message: res.message || '转账处理中，请稍后查看', theme: 'warning' });
        } else if (res.action === 'success') {
          Toast({ message: '已到账', theme: 'success' });
        } else if (res.action === 'expired') {
          Toast({ message: res.message || '奖励已失效', theme: 'warning' });
        } else {
          Toast({ message: res.message || '暂不可提现', theme: 'error' });
        }

        await load({ silent: true });
      } catch (e) {
        Toast({ message: e instanceof Error ? e.message : '提现失败，请稍后重试', theme: 'error' });
      } finally {
        setPendingId(null);
      }
    },
    [pendingId, load],
  );

  /** 提交真实姓名并重新发起提现（仅在收到 need_real_name 后使用） */
  const submitRealName = useCallback(async () => {
    const name = realName.trim();
    if (!name) {
      Toast({ message: '请填写真实姓名', theme: 'warning' });
      return;
    }
    const reward = nameDialog.reward;
    if (!reward) return;
    closeNameDialog();
    await runWithdraw(reward, name);
  }, [realName, nameDialog.reward, closeNameDialog, runWithdraw]);

  const hasMore = items.length < total;

  return (
    <div
      className="rewards-page"
      style={{ background: 'var(--gradient-surface)', flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}
    >
      <Navbar title="我的历史奖励" leftArrow onLeftClick={() => navigate(-1)} />

      <div className="page-container" style={{ paddingTop: 16, paddingBottom: 24 }}>
        {/* 汇总：待提现 / 累计已到账 */}
        <div
          style={{
            background: 'var(--card)',
            borderRadius: 16,
            padding: 16,
            border: '1px solid var(--border)',
            marginBottom: 12,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8 }}>
            <span style={{ fontSize: 12, color: '#8a8f92' }}>待提现（元）</span>
            <span style={{ fontSize: 12, color: '#8a8f92', marginLeft: 'auto' }}>
              {stats?.pending_count ?? 0} 笔可提
            </span>
          </div>
          <div style={{ fontSize: 28, fontWeight: 700, color: '#227197', lineHeight: 1.3 }}>
            ¥{fenToYuan(stats?.pending_amount ?? 0)}
          </div>
          <div
            style={{
              marginTop: 8,
              paddingTop: 8,
              borderTop: '1px dashed var(--border)',
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: 12,
              color: '#8a8f92',
            }}
          >
            <span>累计已到账</span>
            <span style={{ color: '#2ba471' }}>¥{fenToYuan(stats?.withdrawn_amount ?? 0)}</span>
          </div>
        </div>

        {/* 状态筛选 */}
        <div style={{ display: 'flex', gap: 8, overflowX: 'auto', paddingBottom: 8, marginBottom: 4 }}>
          {STATUS_TABS.map((t) => {
            const active = status === t.key;
            return (
              <button
                key={t.key || 'all'}
                type="button"
                data-testid={`reward-tab-${t.key || 'all'}`}
                onClick={() => {
                  if (status === t.key) return;
                  setPages(1);
                  setStatus(t.key);
                }}
                style={{
                  flexShrink: 0,
                  padding: '6px 14px',
                  borderRadius: 999,
                  border: active ? '1px solid #227197' : '1px solid var(--border)',
                  background: active ? '#227197' : 'var(--card)',
                  color: active ? '#fff' : '#666',
                  fontSize: 13,
                  cursor: 'pointer',
                }}
              >
                {t.label}
              </button>
            );
          })}
        </div>

        {loading ? (
          <div style={{ padding: '48px 0', display: 'flex', justifyContent: 'center' }}>
            <Loading text="加载中..." />
          </div>
        ) : error ? (
          <div style={{ textAlign: 'center', padding: '48px 0', color: '#8a8f92', fontSize: 13 }}>
            <div style={{ marginBottom: 12 }}>{error}</div>
            <Button theme="primary" variant="outline" size="small" onClick={() => void load()}>
              重试
            </Button>
          </div>
        ) : items.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '48px 0', color: '#8a8f92', fontSize: 13 }}>
            暂无奖励记录
          </div>
        ) : (
          <>
            {items.map((item) => {
              const meta = STATUS_META[item.status] || STATUS_META.pending;
              const disabled = item.status === 'success' || item.status === 'expired';
              const busy = pendingId === item.id;
              return (
                <div
                  key={item.id}
                  data-testid="reward-card"
                  style={{
                    background: 'var(--card)',
                    borderRadius: 16,
                    padding: 14,
                    border: '1px solid var(--border)',
                    marginBottom: 10,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{ fontSize: 15, fontWeight: 600, color: '#333', flex: 1, minWidth: 0 }}>
                      {item.scene_name || '奖励'}
                    </div>
                    <span
                      style={{
                        fontSize: 11,
                        color: meta.color,
                        background: meta.bg,
                        borderRadius: 6,
                        padding: '2px 8px',
                        flexShrink: 0,
                      }}
                    >
                      {meta.label}
                    </span>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'flex-end', marginTop: 6 }}>
                    <span style={{ fontSize: 22, fontWeight: 700, color: '#e37318' }}>
                      ¥{fenToYuan(item.amount)}
                    </span>
                    <span style={{ fontSize: 11, color: '#8a8f92', marginLeft: 'auto' }}>
                      {item.status === 'success' && item.withdrawn_at
                        ? `到账于 ${fmtDate(item.withdrawn_at)}`
                        : item.status === 'expired'
                          ? `已于 ${fmtDate(item.expire_at)} 失效`
                          : item.expire_at
                            ? `有效期至 ${fmtDate(item.expire_at)}`
                            : ''}
                    </span>
                  </div>

                  {item.reward_desc && (
                    <div style={{ fontSize: 12, color: '#8a8f92', marginTop: 4 }}>{item.reward_desc}</div>
                  )}

                  <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
                    <Button
                      theme={disabled ? 'default' : 'primary'}
                      size="small"
                      disabled={disabled}
                      loading={busy}
                      data-testid={`reward-action-${item.id}`}
                      onClick={() => void runWithdraw(item)}
                    >
                      {actionLabel(item.status)}
                    </Button>
                  </div>
                </div>
              );
            })}

            {hasMore && (
              <Button
                theme="default"
                variant="outline"
                block
                loading={loadingMore}
                onClick={() => setPages((p) => p + 1)}
              >
                加载更多
              </Button>
            )}
          </>
        )}

        <div style={{ fontSize: 11, color: '#bbb', textAlign: 'center', marginTop: 16, lineHeight: 1.6 }}>
          提现将转入你的微信零钱，需在微信内完成收款确认；
          <br />
          确认后一般即时到账，最终状态以本页显示为准。
        </div>
      </div>

      {/* 真实姓名收集：微信对单笔 ≥2000 元的转账要求收款人姓名与实名一致 */}
      <Popup visible={nameDialog.open} onClose={closeNameDialog} placement="center" showOverlay>
        <div style={{ padding: 16, width: '80vw', maxWidth: 320 }}>
          <h4 style={{ marginBottom: 8, fontSize: 16, fontWeight: 600 }}>填写真实姓名</h4>
          <p style={{ fontSize: 13, color: '#8a8f92', marginBottom: 16, lineHeight: 1.6 }}>
            微信要求单笔 2000 元及以上的转账校验收款人实名，请填写与你的微信实名信息一致的姓名。
          </p>
          <Input
            placeholder="请输入真实姓名"
            value={realName}
            onChange={(v) => setRealName(String(v))}
          />
          <div style={{ display: 'flex', gap: 12, marginTop: 16 }}>
            <Button block variant="outline" onClick={closeNameDialog}>
              取消
            </Button>
            <Button block theme="primary" onClick={() => void submitRealName()}>
              提交并提现
            </Button>
          </div>
        </div>
      </Popup>
    </div>
  );
}
