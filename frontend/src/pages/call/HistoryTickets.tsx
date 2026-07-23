// 我要摇人底部：AI 诊断生成的历史工单
// 数据源：AI 模块 GET /api/ai/memory/tickets/all（admin 全部，其余仅本人创建）
// 列表用普通流渲染 + 容器 overflow:auto 滚动（不采用虚拟滚动，
// 避免 viewportH 单测为 0 时把可见条数锁死、导致多数据时滑不动的问题）
// 分页：每页 PAGE_SIZE 条，向下拖拽顶部整页刷新、滚动到底部加载更多。
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loading, Toast } from 'tdesign-mobile-react';
import { NotificationIcon, UploadIcon, RollbackIcon } from 'tdesign-icons-react';
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

export default function HistoryTickets({ showHeader = true }: { showHeader?: boolean }) {
  const navigate = useNavigate();
  const tasksRefreshKey = useWorkbenchStore((s) => s.tasksRefreshKey);

  const [tickets, setTickets] = useState<AiTicketBrief[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [skip, setSkip] = useState(0);

  // 首屏 / 下拉刷新：重置分页
  const loadInitial = useCallback(async () => {
    setLoading(true);
    try {
      const res = await qaListTickets(0, PAGE_SIZE);
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
  }, []);

  // 触底加载更多：追加下一页
  const loadMore = useCallback(async () => {
    const res = await qaListTickets(skip, PAGE_SIZE);
    const items = res?.data?.items || [];
    const total = res?.data?.total ?? 0;
    setTickets((prev) => [...prev, ...items]);
    setSkip((s) => s + items.length);
    setHasMore(total > skip + items.length);
  }, [skip]);

  useEffect(() => { loadInitial(); }, [loadInitial]);

  const [acting, setActing] = useState<number | null>(null);
  const handleUrge = async (e: React.MouseEvent, t: AiTicketBrief) => {
    e.stopPropagation();
    if (!t.id) { Toast({ message: '工单号缺失', theme: 'warning' }); return; }
    setActing(t.id);
    try { await urgeTicket(t.id); Toast({ message: '已催办，已通知处理人', theme: 'success' }); }
    catch (err) { Toast({ message: `催办失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }); }
    finally { setActing(null); }
  };
  const handleReport = async (e: React.MouseEvent, t: AiTicketBrief) => {
    e.stopPropagation();
    if (!t.id) { Toast({ message: '工单号缺失', theme: 'warning' }); return; }
    setActing(t.id);
    try { await reportTicket(t.id); Toast({ message: '已上报，已通知上级', theme: 'success' }); }
    catch (err) { Toast({ message: `上报失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }); }
    finally { setActing(null); }
  };
  const handleCancel = async (e: React.MouseEvent, t: AiTicketBrief) => {
    e.stopPropagation();
    if (!t.id) { Toast({ message: '工单号缺失', theme: 'warning' }); return; }
    setActing(t.id);
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
      <PullToRefresh
        className="history-tickets__viewport"
        onRefresh={loadInitial}
        onLoadMore={loadMore}
        hasMore={hasMore}
        showFooter={tickets.length > 0}
        refreshKey={tasksRefreshKey}
      >
        {loading ? (
          <Loading text="加载中…" />
        ) : tickets.length === 0 ? (
          <div className="history-tickets__empty">暂无历史工单</div>
        ) : (
          tickets.map((t) => (
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
                <button type="button" className="history-action-btn" disabled={acting === t.id} onClick={(e) => handleUrge(e, t)} title="催办" aria-label="催办"><NotificationIcon size="16px" /></button>
                <button type="button" className="history-action-btn" disabled={acting === t.id} onClick={(e) => handleReport(e, t)} title="上报" aria-label="上报"><UploadIcon size="16px" /></button>
                <button type="button" className="history-action-btn" disabled={acting === t.id} onClick={(e) => handleCancel(e, t)} title="撤回" aria-label="撤回"><RollbackIcon size="16px" /></button>
              </div>
            </div>
          ))
        )}
      </PullToRefresh>
    </div>
  );
}
