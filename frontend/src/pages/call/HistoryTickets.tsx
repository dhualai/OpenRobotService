// 我要摇人：历史工单列表（右上角 ≡ 入口）
// 数据源：AI 模块 GET /api/ai/memory/tickets/all（按当前用户过滤）
// 搜索：前端模糊过滤（title/description）；状态筛选：qaListTickets status filter（后端）
// 分页：每页 PAGE_SIZE 条，下拉刷新、触底加载更多。
import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loading, Toast, Button, Popup, DialogPlugin } from 'tdesign-mobile-react';
import AppButton from '@/shared/components/AppButton';
import { Search, ArrowRight } from 'lucide-react';
import { qaListTickets, type AiTicketBrief } from '@/api/ai';
import { urgeTicket, reportTicket, cancelTicket, reDispatchTicket } from '@/api/ticket';
import { isTerminalTicketStatus, canUrgeTicket, canReportTicket, canShowCancelButton, canCancelTicketByUser } from '@/shared/constants/ticket';
import { useHorizontalScroll } from '@/shared/hooks/useHorizontalScroll';
import { useWorkbenchStore } from '@/stores/workbench';
import { useAuthStore } from '@/stores/auth';
import PullToRefresh from '@/shared/components/PullToRefresh';
import UserSelect from '@/shared/components/UserSelect';
import TitleEllipsis from '@/shared/components/TitleEllipsis';
import { formatDateTime } from '@/shared/utils/url';
import type { UserItem } from '@/api/users';

const PAGE_SIZE = 20;


const TYPE_LABEL: Record<string, string> = {
  problem: '报障', bug: '缺陷', feature: '需求', support: '支持', other: '其他',
};
// 类型 Tag 色调（设计稿 kindTone：需求 blue / 报障·缺陷 gray / 支持·其他 muted）
const TYPE_TONE: Record<string, string> = {
  feature: 'blue', problem: 'gray', bug: 'gray', support: 'muted', other: 'muted',
};
// 列表仅展示「除已关闭外」的工单；countKey 对应列表接口返回 by_status 的键（'__active__' 表示除已关闭外总数）
const STATUS_TABS = [
  { value: '', label: '全部', countKey: '__active__' },
  { value: 'new', label: '新建', countKey: 'new' },
  { value: 'in_progress', label: '处理中', countKey: 'in_progress' },
  { value: 'pending', label: '待处理', countKey: 'pending' },
  { value: 'resolved', label: '已解决', countKey: 'resolved' },
  { value: 'canceled', label: '已取消', countKey: 'canceled' },
  { value: 'closed', label: '已关闭', countKey: 'closed' },
];
// 状态徽标：浅灰底 + 蓝阶文字（设计稿 statusStyles 映射）
const STATUS_META: Record<string, { label: string; color: string; bg: string }> = {
  new:         { label: '新建',   color: 'var(--blue-3)', bg: 'var(--secondary)' },
  pending:     { label: '待处理', color: 'var(--blue-2)', bg: 'var(--secondary)' },
  dispatched:  { label: '已派单', color: 'var(--blue-3)', bg: 'var(--secondary)' },
  in_progress: { label: '处理中', color: 'var(--blue-2)', bg: 'var(--secondary)' },
  resolved:    { label: '已解决', color: 'var(--blue-1)', bg: 'var(--secondary)' },
  canceled:    { label: '已取消', color: 'var(--muted-foreground)', bg: 'var(--secondary)' },
  closed:      { label: '已关闭', color: 'var(--muted-foreground)', bg: 'var(--secondary)' },
};

export default function HistoryTickets({ showHeader = true }: { showHeader?: boolean }) {
  const navigate = useNavigate();
  const tasksRefreshKey = useWorkbenchStore((s) => s.tasksRefreshKey);
  const username = useAuthStore((s) => s.username);
  const userId = useAuthStore((s) => s.userId);
  const isAdmin = useAuthStore((s) => s.isAdmin);

  // 状态 tab 栏横向滚动 ref（PC 桌面端滚轮/拖拽横滑，移动端原生触摸滑动）
  const tabsRef = useRef<HTMLDivElement>(null);
  useHorizontalScroll(tabsRef);

  const [tickets, setTickets] = useState<AiTicketBrief[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [skip, setSkip] = useState(0);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({});

  // 构造筛选参数：admin 不过滤，其余按当前用户过滤；「全部」= 除已关闭外全部
  const buildFilters = useCallback(() => {
    const f: { status?: string; keyword?: string; username?: string; exclude_status?: string } = {};
    if (statusFilter) f.status = statusFilter;
    else f.exclude_status = 'closed';
    if (search.trim()) f.keyword = search.trim();
    if (!isAdmin && username) f.username = username;
    return f;
  }, [statusFilter, search, username, isAdmin]);

  // 首屏 / 下拉刷新：重置分页
  const loadInitial = useCallback(async () => {
    setLoading(true);
    try {
      const res = await qaListTickets(0, PAGE_SIZE, buildFilters());
      const items = res?.data?.items || [];
      const total = res?.data?.total ?? items.length;
      setTickets(items);
      setSkip(items.length);
      setHasMore(total > items.length);
      // 复用列表接口返回的各状态分布 + 除已关闭外总数，填充 tab 计数（无需额外统计接口）
      const d = res?.data;
      if (d?.by_status) setStatusCounts({ ...d.by_status, __active__: d.active_total ?? 0 });
    } catch (err) {
      Toast({ message: `历史工单加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [buildFilters]);

  // 触底加载更多：追加下一页
  const loadMore = useCallback(async () => {
    const res = await qaListTickets(skip, PAGE_SIZE, buildFilters());
    const items = res?.data?.items || [];
    const total = res?.data?.total ?? 0;
    setTickets((prev) => [...prev, ...items]);
    setSkip((s) => s + items.length);
    setHasMore(total > skip + items.length);
  }, [skip, buildFilters]);

  // 联动加载：statusFilter 变 → 立即加载（含 keyword 联动）；search 变 → 防抖 400ms（非空）；search 空 → 立即
  const loadInitialRef = useRef(loadInitial);
  loadInitialRef.current = loadInitial;
  const prevStatus = useRef(statusFilter);
  useEffect(() => {
    const statusChanged = prevStatus.current !== statusFilter;
    prevStatus.current = statusFilter;
    // status 切换 / search 为空 → 立即加载；search 非空 → 防抖（避免每次按键请求）
    if (statusChanged || !search.trim()) {
      loadInitialRef.current();
      return;
    }
    const t = setTimeout(() => { loadInitialRef.current(); }, 400);
    return () => clearTimeout(t);
  }, [statusFilter, search]);

  // 后端已按 keyword 搜索，前端无需再过滤
  const displayedTickets = tickets;

  type ActionType = 'urge' | 'report' | 'cancel';
  const [acting, setActing] = useState<{ id: number; action: ActionType } | null>(null);

  // 催办/上报：先选用户
  const [actionTicket, setActionTicket] = useState<AiTicketBrief | null>(null);
  const [actionType, setActionType] = useState<'urge' | 'report'>('urge');
  const [actionUser, setActionUser] = useState<UserItem | null>(null);
  const [showActionPopup, setShowActionPopup] = useState(false);

  // 重新派单：必选用户倾向派单人 + 可选备注
  const [redispatchTicket, setRedispatchTicket] = useState<AiTicketBrief | null>(null);
  const [redispatchUser, setRedispatchUser] = useState<UserItem | null>(null);
  const [redispatchRemark, setRedispatchRemark] = useState('');
  const [showRedispatchPopup, setShowRedispatchPopup] = useState(false);
  const [redispatching, setRedispatching] = useState(false);

  const openActionPopup = (e: React.MouseEvent, t: AiTicketBrief, type: 'urge' | 'report') => {
    e.stopPropagation();
    if (!t.id) { Toast({ message: '工单号缺失', theme: 'warning' }); return; }
    setActionTicket(t);
    setActionType(type);
    setActionUser(null);
    setShowActionPopup(true);
  };

  const handleActionConfirm = async () => {
    if (!actionTicket?.id || !actionUser) { Toast({ message: '请选择通知用户', theme: 'warning' }); return; }
    setActing({ id: actionTicket.id, action: actionType });
    setActing(null); // 关闭按钮 loading 态由 popup loading 控制
    try {
      if (actionType === 'urge') {
        await urgeTicket(actionTicket.id, actionUser.id);
        Toast({ message: '已催办，已通知处理人', theme: 'success' });
      } else {
        await reportTicket(actionTicket.id, actionUser.id);
        Toast({ message: '已上报，已通知上级', theme: 'success' });
      }
      setShowActionPopup(false);
    } catch (err) {
      Toast({ message: `${actionType === 'urge' ? '催办' : '上报'}失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setActing(null);
    }
  };

  const doCancel = async (t: AiTicketBrief) => {
    if (!t.id) { Toast({ message: '工单号缺失', theme: 'warning' }); return; }
    setActing({ id: t.id, action: 'cancel' });
    try { await cancelTicket(t.id); Toast({ message: '已撤回，工单已取消', theme: 'success' }); loadInitial(); }
    catch (err) { Toast({ message: `撤回失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }); }
    finally { setActing(null); }
  };

  const handleCancel = (e: React.MouseEvent, t: AiTicketBrief) => {
    e.stopPropagation();
    if (!t.id) { Toast({ message: '工单号缺失', theme: 'warning' }); return; }
    const dlg = DialogPlugin.confirm!({
      title: '撤回工单',
      content: '撤回后工单将变为「已取消」，确认撤回吗？',
      confirmBtn: '撤回',
      cancelBtn: '再想想',
      onConfirm: () => { doCancel(t); dlg.destroy(); },
    });
  };

  const openRedispatchPopup = (e: React.MouseEvent, t: AiTicketBrief) => {
    e.stopPropagation();
    if (!t.id) { Toast({ message: '工单号缺失', theme: 'warning' }); return; }
    // 提单时已指定处理人的工单，派单 Step 0 强信号会覆盖重新派单的倾向人，重派无效——提前弹警告拦截
    const strongText = `${t.title || ''}\n${t.description || ''}`;
    const strongMatch = strongText.match(/指定(?:处理人|人|人员)[:：]\s*([^\]\s，,；;:：）)】]{2,6})/);
    if (strongMatch) {
      Toast({ message: `该工单已指定处理人「${strongMatch[1]}」，无法重新派单`, theme: 'warning' });
      return;
    }
    setRedispatchTicket(t);
    setRedispatchUser(null);
    setRedispatchRemark('');
    setShowRedispatchPopup(true);
  };

  const handleRedispatchConfirm = async () => {
    if (!redispatchTicket?.id) { Toast({ message: '工单号缺失', theme: 'warning' }); return; }
    if (!redispatchUser?.id && !redispatchUser?.username) { Toast({ message: '请选择倾向处理人', theme: 'warning' }); return; }
    setRedispatching(true);
    try {
      await reDispatchTicket(redispatchTicket.id, redispatchUser.id || redispatchUser.username, redispatchRemark.trim() || undefined);
      Toast({ message: '已重新派单，正在重新推荐处理人', theme: 'success' });
      setShowRedispatchPopup(false);
      // 重新派单后端会先清空 assigned_to（回 new 态）再异步重派：
      // ① 立即 loadInitial 一次，让列表马上从「旧处理人」变成「派单中」；
      // ② 再启动轮询（5s×12=60s），直到该工单派单完成（assigned_to 非空）自动显示新处理人。
      loadInitial();
      startRedispatchPoll(redispatchTicket.id);
    } catch (err) {
      Toast({ message: `重新派单失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setRedispatching(false);
    }
  };

  // ── 重新派单后轮询：直到目标工单派单完成，列表自动显示新处理人 ──
  const redispatchPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const stopRedispatchPoll = useCallback(() => {
    if (redispatchPollRef.current) {
      clearInterval(redispatchPollRef.current);
      redispatchPollRef.current = null;
    }
  }, []);

  const startRedispatchPoll = (ticketId: number) => {
    stopRedispatchPoll();
    let attempts = 0;
    redispatchPollRef.current = setInterval(async () => {
      attempts++;
      try {
        const res = await qaListTickets(0, PAGE_SIZE, buildFilters());
        const items = res?.data?.items || [];
        const total = res?.data?.total ?? items.length;
        const target = items.find((x) => x.id === ticketId);
        // 派单完成：目标工单不再处于"派单中"（status=new 且处理人为空），或已不在当前视图
        const done = target
          ? !(target.status === 'new' && !target.assigned_to && !target.assigned_to_name)
          : true; // 找不到目标也停止（可能已离开当前筛选视图）
        if (done || attempts >= 12) {
          stopRedispatchPoll();
          // 用最新数据替换列表，让"派单中"实时变成新处理人
          setTickets(items);
          setSkip(items.length);
          setHasMore(total > items.length);
          return;
        }
      } catch {
        /* 单次失败继续轮询 */
      }
    }, 5000);
  };

  // 组件卸载清理轮询，避免卸载后 setState
  useEffect(() => () => { stopRedispatchPoll(); }, [stopRedispatchPoll]);

  return (
    <div className="history-tickets">
      {showHeader && (
        <div className="history-tickets__head">
          <span>历史工单</span>
          <span className="history-tickets__count">{statusCounts.__active__ ?? tickets.length}</span>
        </div>
      )}
      {/* 搜索 + 状态快捷筛选 */}
      <div className="history-toolbar">
        <div className="history-search-wrap">
          <Search className="history-search__icon" size={16} strokeWidth={2} />
          <input
            className="history-search"
            placeholder="搜索工单标题/描述…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {search && (
            <button type="button" className="history-search__clear" onClick={() => setSearch('')} aria-label="清空">×</button>
          )}
        </div>
        {/* 横向滚动容器 */}
        <div
          ref={tabsRef}
          className="history-tabs"
        >
          {STATUS_TABS.map((tab) => {
            const count = statusCounts[tab.countKey];
            return (
              <div key={tab.value} className="history-tab-wrap">
                <button
                  type="button"
                  className={`history-tab${statusFilter === tab.value ? ' is-active' : ''}`}
                  onClick={() => setStatusFilter(tab.value)}
                >
                  {tab.label}
                </button>
                {count != null && <span className="history-tab__count">{count}</span>}
              </div>
            );
          })}
        </div>
      </div>

      <PullToRefresh
        className="history-tickets__viewport"
        onRefresh={loadInitial}
        onLoadMore={loadMore}
        hasMore={hasMore}
        showFooter={displayedTickets.length > 0}
        refreshKey={tasksRefreshKey}
      >
        {loading ? (
          <Loading text="加载中…" />
        ) : displayedTickets.length === 0 ? (
          <div className="history-tickets__empty">{search ? '无匹配工单' : '暂无历史工单'}</div>
        ) : (
          displayedTickets.map((t) => {
            const statusMeta = STATUS_META[t.status || ''] || { label: t.status || '', color: 'var(--muted-foreground)', bg: 'var(--secondary)' };
            return (
            /* 用 DB id（Task.id）导航：同一会话多次转单时 session_id 会重复
               （external_id 靠 ticket_seq 区分，DB 唯一约束在 (source, external_id) 而非 session_id），
               用 session_id 导航会让详情页 qaGetTicket 命中歧义结果——点的是当前工单，
               显示却是同 session 的另一条。DB id 唯一定位，彻底消除歧义。 */
            <div
              key={t.id}
              className="history-row"
              onClick={() => navigate(`/call/ticket/db_${t.id}`)}
            >
              {/* 顶行（设计稿：类型 Tag + 标题 flex-1 截断 + 编号胶囊 + 日期） */}
              <div className="history-row__top">
                {t.type && <span className={`history-row__kind history-row__kind--${TYPE_TONE[t.type] || 'muted'}`}>{TYPE_LABEL[t.type] || t.type}</span>}
                <TitleEllipsis text={t.title || '(无标题)'} lines={2} titleClassName="history-row__title" as="span" fontSize={14.5} lineHeight={1.4} />
                <span className="history-row__id">#{t.id}</span>
                <span className="history-row__date">{formatDateTime(t.created_at ?? '').slice(0, 10)}</span>
              </div>
              {t.description && <span className="history-row__summary">{t.description}</span>}
              {t.project && <span className="history-row__project">所属项目：{t.project}</span>}
              {/* 人员流转（设计稿：头像 blue-3 + 姓名 | ArrowRight blue-3 居中 | 姓名 + 头像 blue-2）。
                  派单中（status=new 且处理人未写入，AI 派单 Worker 60s 轮询中）：显示「派单中」呼吸动效 */}
              <div className="task-card2__people">
                <div className="task-card2__person task-card2__person--creator" title={`发起人：${t.created_by_name || t.created_by || '-'}`} aria-label={`发起人：${t.created_by_name || t.created_by || '-'}`}>
                  <span className="task-card2__avatar">{(t.created_by_name || t.created_by || '?').slice(0, 1).toUpperCase()}</span>
                  <span className="task-card2__person-name">{t.created_by_name || t.created_by || '-'}</span>
                </div>
                <span className="task-card2__person-arrow"><ArrowRight size={16} strokeWidth={2} /></span>
                {(t.status === 'new' && !t.assigned_to && !t.assigned_to_name) ? (
                  <div className="task-card2__person task-card2__person--assignee" title="U老师 正在派单" aria-label="U老师 正在派单">
                    <span className="task-card2__avatar task-card2__avatar--assignee task-card2__avatar--dispatching"><i className="dispatch-pulse" /></span>
                    <span className="task-card2__person-name task-card2__person-name--dispatching">派单中</span>
                  </div>
                ) : (
                  <div className="task-card2__person task-card2__person--assignee" title={`处理人：${t.assigned_to_name || t.assigned_to || '-'}`}>
                    <span className="task-card2__avatar task-card2__avatar--assignee">{(t.assigned_to_name || t.assigned_to || '?').slice(0, 1).toUpperCase()}</span>
                    <span className="task-card2__person-name">{t.assigned_to_name || t.assigned_to || '-'}</span>
                  </div>
                )}
              </div>
              {/* 底部行（设计稿：状态/优先级 Tag bg-secondary text-blue-2 + 操作按钮组） */}
              <div className="history-row__bottom">
                <div className="history-row__bottom-tags">
                  {statusMeta.label && (
                    <span className="history-row__status" style={{ color: 'var(--blue-2)', background: 'var(--secondary)' }}>{statusMeta.label}</span>
                  )}
                  {t.priority && <span className="history-row__priority-tag">{t.priority}</span>}
                </div>
                {/* 操作按钮：已解决/已取消/已关闭（终态）整组不显示；
                    新建/待处理可催办、撤回；处理中仅可上报；不可用按钮禁用 */}
                {!isTerminalTicketStatus(t.status) && (
                  <div className="history-row__actions" onClick={(e) => e.stopPropagation()}>
                    <AppButton tone="blue" size="extra-small" disabled={!canUrgeTicket(t.status) || (acting?.id === t.id && acting?.action === 'urge')} title={canUrgeTicket(t.status) ? undefined : '仅新建/待处理工单可催办'} aria-label={canUrgeTicket(t.status) ? undefined : '催办（仅新建/待处理工单可催办）'} onClick={(e) => openActionPopup(e, t, 'urge')}>催办</AppButton>
                    <AppButton tone="blue" size="extra-small" disabled={!canReportTicket(t.status) || (acting?.id === t.id && acting?.action === 'report')} title={canReportTicket(t.status) ? undefined : '仅处理中工单可上报'} aria-label={canReportTicket(t.status) ? undefined : '上报（仅处理中工单可上报）'} onClick={(e) => openActionPopup(e, t, 'report')}>上报</AppButton>
                    {(t.source === 'ai' || !t.source) && !!t.assigned_to && (
                    <AppButton tone="blue-deep" size="extra-small" loading={redispatching && redispatchTicket?.id === t.id} onClick={(e) => openRedispatchPopup(e, t)}>重新派单</AppButton>
                    )}
                    {canShowCancelButton(t.status) && canCancelTicketByUser(t.created_by, username, isAdmin, userId) && (
                    <AppButton tone="blue" size="extra-small" loading={acting?.id === t.id && acting?.action === 'cancel'} disabled={acting?.id === t.id && acting?.action === 'cancel'} onClick={(e) => handleCancel(e, t)}>撤回</AppButton>
                    )}
                  </div>
                )}
              </div>
            </div>
            );
          })
        )}
      </PullToRefresh>

      {/* 催办/上报 用户选择弹窗 */}
      <Popup visible={showActionPopup} onClose={() => setShowActionPopup(false)} placement="bottom" showOverlay>
        <div className="conv-dialog">
          <h4 className="conv-dialog__title">{actionType === 'urge' ? '催办 — 选择通知用户' : '上报 — 选择通知用户'}</h4>
          <div style={{ marginBottom: 16 }}>
            <UserSelect
              value={actionUser?.id ?? null}
              onChange={(u) => setActionUser(u)}
              placeholder="选择通知对象"
              title="选择通知用户"
            />
          </div>
          <div className="conv-dialog__btns">
            <Button block theme="default" onClick={() => setShowActionPopup(false)}>取消</Button>
            <Button block theme="primary" disabled={!actionUser} loading={!!acting} onClick={handleActionConfirm}>确定</Button>
          </div>
        </div>
      </Popup>

      {/* 重新派单 弹窗（必选倾向处理人 + 可选备注） */}
      <Popup visible={showRedispatchPopup} onClose={() => setShowRedispatchPopup(false)} placement="bottom" showOverlay>
        <div className="conv-dialog">
          <h4 className="conv-dialog__title">重新派单</h4>
          <p className="conv-dialog__msg">将强制重新智能派单，请选择倾向处理人</p>
          <div style={{ marginBottom: 16 }}>
            <UserSelect
              value={redispatchUser?.id ?? null}
              onChange={(u) => setRedispatchUser(u)}
              placeholder="选择倾向处理人（必选）"
              title="选择倾向处理人"
            />
          </div>
          <input
            className="conv-dialog__input"
            placeholder="备注（可选）：换人原因或给新处理人的说明"
            value={redispatchRemark}
            onChange={(e) => setRedispatchRemark(e.target.value)}
            maxLength={200}
          />
          <div className="conv-dialog__btns">
            <Button block theme="default" onClick={() => setShowRedispatchPopup(false)}>取消</Button>
            <Button block theme="primary" disabled={!redispatchUser} loading={redispatching} onClick={handleRedispatchConfirm}>确定重新派单</Button>
          </div>
        </div>
      </Popup>
    </div>
  );
}
