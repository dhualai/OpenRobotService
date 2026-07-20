// 我要摇人底部：AI 诊断生成的历史工单（全量，按角色）
// 数据源：AI 模块 GET /api/ai/qa/tickets（admin 全部，其余仅本人创建）
import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loading, Toast } from 'tdesign-mobile-react';
import { qaListTickets, type AiTicketBrief } from '@/api/ai';
import { useWorkbenchStore } from '@/stores/workbench';

const PRIORITY_COLOR: Record<string, string> = {
  紧急: '#d54941', 高: '#e37318', 中: '#0052d9', 低: '#999',
};
const TYPE_LABEL: Record<string, string> = {
  problem: '报障', bug: '缺陷', feature: '需求', support: '支持', other: '其他',
};

export default function HistoryTickets() {
  const navigate = useNavigate();
  const tasksRefreshKey = useWorkbenchStore((s) => s.tasksRefreshKey);

  const [tickets, setTickets] = useState<AiTicketBrief[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchTickets = useCallback(async () => {
    setLoading(true);
    try {
      const res = await qaListTickets(0, 200); // {code, data:{items, total}}
      setTickets(res?.data?.items || []);
    } catch (err) {
      Toast({ message: `历史工单加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchTickets(); }, [fetchTickets]);
  useEffect(() => { if (tasksRefreshKey > 0) fetchTickets(); }, [tasksRefreshKey, fetchTickets]);

  // ---- 虚拟滚动 ----
  const viewportRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportH, setViewportH] = useState(0);
  const ROW_HEIGHT = 56;

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    setViewportH(el.clientHeight);
    const onScroll = () => setScrollTop(el.scrollTop);
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  const totalH = tickets.length * ROW_HEIGHT;
  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - 2);
  const visibleCount = Math.ceil(viewportH / ROW_HEIGHT) + 4;
  const end = Math.min(tickets.length, start + visibleCount);
  const visible = tickets.slice(start, end);

  return (
    <div className="history-tickets">
      <div className="history-tickets__head">
        <span>历史工单</span>
        <span className="history-tickets__count">{tickets.length}</span>
      </div>
      <div className="history-tickets__viewport" ref={viewportRef}>
        {loading ? (
          <Loading text="加载中…" />
        ) : tickets.length === 0 ? (
          <div className="history-tickets__empty">暂无历史工单</div>
        ) : (
          <div style={{ height: totalH, position: 'relative' }}>
            {visible.map((t, i) => (
              <div
                key={t.session_id}
                className="history-row"
                style={{ position: 'absolute', top: (start + i) * ROW_HEIGHT, left: 0, right: 0, height: ROW_HEIGHT }}
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
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
