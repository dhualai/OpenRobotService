// 我要摇人：历史工单列表（右上角 ≡ 入口）
// 数据源：AI 模块 GET /api/ai/memory/tickets/all（admin 全部，其余仅本人创建）
// 搜索：前端模糊过滤（title/description）；状态筛选：qaListTickets status filter（后端）
// 分页：每页 PAGE_SIZE 条，下拉刷新、触底加载更多。
import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loading, Toast, Button } from 'tdesign-mobile-react';
import { qaListTickets, type AiTicketBrief } from '@/api/ai';
import { urgeTicket, reportTicket, cancelTicket } from '@/api/ticket';
import { useWorkbenchStore } from '@/stores/workbench';
import PullToRefresh from '@/shared/components/PullToRefresh';

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

export default function HistoryTickets({ showHeader = true }: { showHeader?: boolean }) {
  const navigate = useNavigate();
  const tasksRefreshKey = useWorkbenchStore((s) => s.tasksRefreshKey);

  const [tickets, setTickets] = useState<AiTicketBrief[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [skip, setSkip] = useState(0);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  // 构造筛选参数（status + keyword 后端 SQL LIKE 搜索，搜全部不只已加载）
  const buildFilters = useCallback(() => {
    const f: { status?: string; keyword?: string } = {};
    if (statusFilter) f.status = statusFilter;
    if (search.trim()) f.keyword = search.trim();
    return Object.keys(f).length ? f : undefined;
  }, [statusFilter, search]);

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

  // search/statusFilter 变化 → 防抖 400ms 后重新加载（后端搜索）
  useEffect(() => {
    const t = setTimeout(() => { loadInitial(); }, 400);
    return () => clearTimeout(t);
  }, [loadInitial]);

  // 后端已按 keyword 搜索，前端无需再过滤
  const displayedTickets = tickets;

  type ActionType = 'urge' | 'report' | 'cancel';
  const [acting, setActing] = useState<{ id: number; action: ActionType } | null>(null);
  const handleUrge = async (e: React.MouseEvent, t: AiTicketBrief) => {
    e.stopPropagation();
    if (!t.id) { Toast({ message: '工单号缺失', theme: 'warning' }); return; }
    setActing({ id: t.id, action: 'urge' });
    try { await urgeTicket(t.id); Toast({ message: '已催办，已通知处理人', theme: 'success' }); }
    catch (err) { Toast({ message: `催办失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }); }
    finally { setActing(null); }
  };
  const handleReport = async (e: React.MouseEvent, t: AiTicketBrief) => {
    e.stopPropagation();
    if (!t.id) { Toast({ message: '工单号缺失', theme: 'warning' }); return; }
    setActing({ id: t.id, action: 'report' });
    try { await reportTicket(t.id); Toast({ message: '已上报，已通知上级', theme: 'success' }); }
    catch (err) { Toast({ message: `上报失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }); }
    finally { setActing(null); }
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
        <input
          className="history-search"
          placeholder="搜索工单标题/描述…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
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
          displayedTickets.map((t) => (
            <div
              key={t.session_id}
              className="history-row"
              onClick={() => navigate(`/call/ticket/${t.session_id}`)}
            >
              <span className="history-row__dot" style={{ background: PRIORITY_COLOR[t.priority || ''] || '#999' }} />
              <span className="history-row__main">
                <span className="history-row__title">
                  {t.type && <span className="history-row__type">{TYPE_LABEL[t.type] || t.type}</span>}
                  {t.title}
                </span>
                {t.description && <span className="history-row__summary">{t.description.slice(0, 40)}</span>}
              </span>
              <span className="history-row__status">{t.priority || ''}</span>
              <span className="history-row__date">{(t.created_at || '').slice(0, 10)}</span>
              <div className="history-row__actions" onClick={(e) => e.stopPropagation()}>
                <Button size="extra-small" variant="outline" theme="default" disabled={acting?.id === t.id && acting?.action === 'urge'} onClick={(e) => handleUrge(e, t)}>催办</Button>
                <Button size="extra-small" variant="outline" theme="default" disabled={acting?.id === t.id && acting?.action === 'report'} onClick={(e) => handleReport(e, t)}>上报</Button>
                <Button size="extra-small" variant="outline" theme="default" disabled={acting?.id === t.id && acting?.action === 'cancel'} onClick={(e) => handleCancel(e, t)}>撤回</Button>
              </div>
            </div>
          ))
        )}
      </PullToRefresh>
    </div>
  );
}
