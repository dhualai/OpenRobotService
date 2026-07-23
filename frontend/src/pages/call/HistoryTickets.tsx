// 我要摇人底部：AI 诊断生成的历史工单
// 数据源：AI 模块 GET /api/ai/memory/tickets/all（admin 全部，其余仅本人创建）
// 列表用普通流渲染 + 容器 overflow:auto 滚动（不采用虚拟滚动，
// 避免 viewportH 单测为 0 时把可见条数锁死、导致多数据时滑不动的问题）
// 分页：每页 PAGE_SIZE 条，向下拖拽顶部整页刷新、滚动到底部加载更多。
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loading, Toast } from 'tdesign-mobile-react';
import { qaListTickets, type AiTicketBrief } from '@/api/ai';
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
            </div>
          ))
        )}
      </PullToRefresh>
    </div>
  );
}
