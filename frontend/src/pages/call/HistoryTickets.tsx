// 我要摇人底部：我提交的历史工单列表（虚拟滚动，每条一行圆角 tab）
import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loading, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { useAuthStore } from '@/stores/auth';
import { useWorkbenchStore } from '@/stores/workbench';
import { normalizeStatus } from '@/shared/constants/ticket';
import { formatDateTime } from '@/shared/utils/url';

interface Ticket {
  id: string; title: string; status: string; project_name?: string; created_at: string;
  reporter_name?: string;
}

const ROW_HEIGHT = 56;
const STATUS_COLOR: Record<string, string> = {
  new: '#0052d9', in_progress: '#e37318', pending: '#e37318',
  resolved: '#2ba471', closed: '#999',
};

export default function HistoryTickets() {
  const navigate = useNavigate();
  const { username } = useAuthStore();
  const tasksRefreshKey = useWorkbenchStore((s) => s.tasksRefreshKey);
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');

  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchTickets = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: '1', size: '200' });
      if (username) params.set('reporter', username);
      const data = await request<{ items: Ticket[]; total: number }>(`/?${params.toString()}`);
      let list = data.items || [];
      // 后端可能忽略 reporter 参数，前端再兜底过滤
      if (username) list = list.filter((t) => !t.reporter_name || t.reporter_name === username);
      setTickets(list);
    } catch (err) {
      Toast({ message: `历史工单加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [username]);

  useEffect(() => { fetchTickets(); }, [fetchTickets]);
  useEffect(() => { if (tasksRefreshKey > 0) fetchTickets(); }, [tasksRefreshKey]);

  // ---- 虚拟滚动 ----
  const viewportRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportH, setViewportH] = useState(0);

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
                key={t.id}
                className="history-row"
                style={{ position: 'absolute', top: (start + i) * ROW_HEIGHT, left: 0, right: 0, height: ROW_HEIGHT }}
                onClick={() => navigate(`/app/tasks/${t.id}`)}
              >
                <span className="history-row__dot" style={{ background: STATUS_COLOR[t.status] || '#999' }} />
                <span className="history-row__title">{t.title}</span>
                <span className="history-row__status">{normalizeStatus(t.status)}</span>
                <span className="history-row__date">{formatDateTime(t.created_at).slice(0, 10)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
