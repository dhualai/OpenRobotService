// 我要摇人：历史工单列表（右上角 ≡ 入口）
// 数据源：AI 模块 GET /api/ai/memory/tickets/all（按当前用户过滤）
// 搜索：前端模糊过滤（title/description）；状态筛选：qaListTickets status filter（后端）
// 分页：每页 PAGE_SIZE 条，下拉刷新、触底加载更多。
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loading, Toast, Button, Popup } from 'tdesign-mobile-react';
import { qaListTickets, type AiTicketBrief } from '@/api/ai';
import { urgeTicket, reportTicket, cancelTicket } from '@/api/ticket';
import { isTerminalTicketStatus, canUrgeTicket, canReportTicket, canCancelTicket } from '@/shared/constants/ticket';
import { useWorkbenchStore } from '@/stores/workbench';
import { useAuthStore } from '@/stores/auth';
import PullToRefresh from '@/shared/components/PullToRefresh';
import UserSelect from '@/shared/components/UserSelect';
import type { UserItem } from '@/api/users';

const PAGE_SIZE = 20;

const PRIORITY_COLOR: Record<string, string> = {
  紧急: '#d54941', 高: '#e37318', 中: '#0052d9', 低: '#999',
};
const TYPE_LABEL: Record<string, string> = {
  problem: '报障', bug: '缺陷', feature: '需求', support: '支持', other: '其他',
};
const STATUS_TABS = [
  { value: '', label: '全部' },
  { value: 'new', label: '新建' },
  { value: 'in_progress', label: '处理中' },
  { value: 'pending', label: '待处理' },
  { value: 'resolved', label: '已解决' },
  { value: 'canceled', label: '已取消' },
  { value: 'closed', label: '已关闭' },
];
// 状态徽标（圆点已表达优先级，这里展示真实工单状态）
const STATUS_META: Record<string, { label: string; color: string; bg: string }> = {
  new:         { label: '新建',   color: '#0052d9', bg: '#ecf2fe' },
  pending:     { label: '待处理', color: '#e37318', bg: '#fdf3e7' },
  dispatched:  { label: '已派单', color: '#0052d9', bg: '#ecf2fe' },
  in_progress: { label: '处理中', color: '#2ba471', bg: '#e8f8f2' },
  resolved:    { label: '已解决', color: '#00a870', bg: '#e6f9f2' },
  canceled:    { label: '已取消', color: '#999',    bg: '#f2f3f5' },
  closed:      { label: '已关闭', color: '#999',    bg: '#f2f3f5' },
};

export default function HistoryTickets({ showHeader = true }: { showHeader?: boolean }) {
  const navigate = useNavigate();
  const tasksRefreshKey = useWorkbenchStore((s) => s.tasksRefreshKey);
  const username = useAuthStore((s) => s.username);
  const isAdmin = useAuthStore((s) => s.isAdmin);

  const [tickets, setTickets] = useState<AiTicketBrief[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [skip, setSkip] = useState(0);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  // 构造筛选参数：admin 不过滤，其余按当前用户过滤
  const buildFilters = useCallback(() => {
    const f: { status?: string; keyword?: string; username?: string } = {};
    if (statusFilter) f.status = statusFilter;
    if (search.trim()) f.keyword = search.trim();
    if (!isAdmin && username) f.username = username;
    return Object.keys(f).length ? f : undefined;
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

  const handleCancel = async (e: React.MouseEvent, t: AiTicketBrief) => {
    e.stopPropagation();
    if (!t.id) { Toast({ message: '工单号缺失', theme: 'warning' }); return; }
    setActing({ id: t.id, action: 'cancel' });
    try { await cancelTicket(t.id); Toast({ message: '已撤回，工单已取消', theme: 'success' }); loadInitial(); }
    catch (err) { Toast({ message: `撤回失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }); }
    finally { setActing(null); }
  };

  return (
    <div className="history-tickets">
      {showHeader && (
        <div className="history-tickets__head">
          <span>历史工单</span>
          <span className="history-tickets__count">{tickets.length}</span>
        </div>
      )}
      {/* 搜索 + 状态快捷筛选 */}
      <div className="history-toolbar">
        <div className="history-search-wrap">
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
        <div className="history-tabs">
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.value}
              type="button"
              className={`history-tab${statusFilter === tab.value ? ' is-active' : ''}`}
              onClick={() => setStatusFilter(tab.value)}
            >
              {tab.label}
            </button>
          ))}
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
            const statusMeta = STATUS_META[t.status || ''] || { label: t.status || '', color: '#666', bg: '#f2f3f5' };
            return (
            <div
              key={t.id}
              className="history-row"
              onClick={() => navigate(`/call/ticket/${t.session_id}`)}
            >
              <div className="history-row__top">
                <span className="history-row__dot" style={{ background: PRIORITY_COLOR[t.priority || ''] || '#999' }} />
                {t.type && <span className="history-row__type">{TYPE_LABEL[t.type] || t.type}</span>}
                <span className="history-row__title">{t.title}</span>
                {t.priority && (
                  <span className="history-row__priority" style={{ color: PRIORITY_COLOR[t.priority] || '#999' }}>{t.priority}</span>
                )}
                <span className="history-row__date">{(t.created_at || '').slice(0, 10)}</span>
              </div>
              {t.description && <span className="history-row__summary">{t.description}</span>}
              {t.project && <span className="history-row__project">所属项目：{t.project}</span>}
              {/* 人员流转：发起人 → 处理人（照搬系统任务卡片 task-card2__people 样式）。
                  派单中（status=new 且处理人未写入，AI 派单 Worker 60s 轮询中）：显示「派单中」呼吸动效 */}
              <div className="task-card2__people">
                <div className="task-card2__person task-card2__person--creator" title={`发起人：${t.created_by_name || t.created_by || '-'}`}>
                  <span className="task-card2__avatar">{(t.created_by_name || t.created_by || '?').slice(0, 1).toUpperCase()}</span>
                  <span className="task-card2__person-text">
                    <span className="task-card2__person-label">发起人</span>
                    <span className="task-card2__person-name">{t.created_by_name || t.created_by || '-'}</span>
                  </span>
                </div>
                <span className="task-card2__person-arrow">➡️</span>
                {(t.status === 'new' && !t.assigned_to && !t.assigned_to_name) ? (
                  <div className="task-card2__person task-card2__person--assignee" title="U老师 正在派单">
                    <span className="task-card2__avatar task-card2__avatar--assignee task-card2__avatar--dispatching"><i className="dispatch-pulse" /></span>
                    <span className="task-card2__person-text">
                      <span className="task-card2__person-label">处理人</span>
                      <span className="task-card2__person-name task-card2__person-name--dispatching">派单中</span>
                    </span>
                  </div>
                ) : (
                  <div className="task-card2__person task-card2__person--assignee" title={`处理人：${t.assigned_to_name || t.assigned_to || '-'}`}>
                    <span className="task-card2__avatar task-card2__avatar--assignee">{(t.assigned_to_name || t.assigned_to || '?').slice(0, 1).toUpperCase()}</span>
                    <span className="task-card2__person-text">
                      <span className="task-card2__person-label">处理人</span>
                      <span className="task-card2__person-name">{t.assigned_to_name || t.assigned_to || '-'}</span>
                    </span>
                  </div>
                )}
              </div>
              <div className="history-row__bottom">
                {statusMeta.label && (
                  <span className="history-row__status" style={{ color: statusMeta.color, background: statusMeta.bg }}>{statusMeta.label}</span>
                )}
                {/* 操作按钮：已解决/已取消/已关闭（终态）整组不显示；
                    新建/待处理可催办、撤回；处理中仅可上报；不可用按钮禁用 */}
                {!isTerminalTicketStatus(t.status) && (
                  <div className="history-row__actions" onClick={(e) => e.stopPropagation()}>
                    <Button size="extra-small" variant="outline" theme="default" disabled={!canUrgeTicket(t.status) || (acting?.id === t.id && acting?.action === 'urge')} title={canUrgeTicket(t.status) ? undefined : '仅新建/待处理工单可催办'} onClick={(e) => openActionPopup(e, t, 'urge')}>催办</Button>
                    <Button size="extra-small" variant="outline" theme="default" disabled={!canReportTicket(t.status) || (acting?.id === t.id && acting?.action === 'report')} title={canReportTicket(t.status) ? undefined : '仅处理中工单可上报'} onClick={(e) => openActionPopup(e, t, 'report')}>上报</Button>
                    <Button size="extra-small" variant="outline" theme="default" disabled={!canCancelTicket(t.status) || (acting?.id === t.id && acting?.action === 'cancel')} title={canCancelTicket(t.status) ? undefined : '仅新建/待处理工单可撤回'} onClick={(e) => handleCancel(e, t)}>撤回</Button>
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
    </div>
  );
}
