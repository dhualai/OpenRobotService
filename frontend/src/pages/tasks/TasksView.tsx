// 系统任务（供给视角）—— 上：AI 任务助手 / 下：工单卡片列表
// 卡片样式与输入卡片审美一致（白底 + 阴影 + 圆角）。
// 跨视图流转：消费 ticketDraft 自动建单；讨论按钮 → 带上下文跳回我要摇人。
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Navbar, Toast, Loading, Tag, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import Pagination from '@/shared/components/Pagination';
import { useWorkbenchStore } from '@/stores/workbench';
import { normalizeStatus, STATUS_DISPLAY_MAP, TICKET_TYPE_DISPLAY_MAP } from '@/shared/constants/ticket';
import { formatDateTime } from '@/shared/utils/url';

interface Ticket {
  id: string; title: string; description: string; status: string; priority: string;
  ticket_type: string; project_name?: string; assignee_name?: string; reporter_name?: string;
  contact?: string; created_at: string; updated_at: string;
}

const pageSize = 20;
const statusTheme = (s: string): 'success' | 'primary' | 'warning' =>
  s === 'closed' ? 'success' : s === 'new' ? 'primary' : 'warning';

export default function TasksView() {
  const navigate = useNavigate();
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');

  const {
    tasksRefreshKey, ticketDraft, consumeTicketDraft, refreshTasks,
  } = useWorkbenchStore();

  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [relevanceFilter, setRelevanceFilter] = useState('all');
  const [showFilterMenu, setShowFilterMenu] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);

  const fetchTickets = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(page), size: String(pageSize),
        ...(search && { keyword: search }),
        ...(statusFilter !== 'all' && { status: statusFilter }),
        ...(relevanceFilter !== 'all' && { relevance: relevanceFilter }),
      });
      const data = await request<{ items: Ticket[]; total: number }>(`/?${params.toString()}`);
      setTickets(data.items || []);
      setTotal(data.total || 0);
    } catch (err) {
      Toast({ message: `加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [page, search, statusFilter, relevanceFilter]);

  useEffect(() => { fetchTickets(); }, [fetchTickets]);
  useEffect(() => { if (tasksRefreshKey > 0) fetchTickets(); }, [tasksRefreshKey]);

  useEffect(() => {
    const draft = consumeTicketDraft();
    if (!draft) return;
    (async () => {
      try {
        await request<Ticket>('/', { method: 'POST', body: JSON.stringify(draft) });
        Toast({ message: '工单已创建', theme: 'success' });
        refreshTasks();
        setPage(1);
      } catch (err) {
        Toast({ message: `建单失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticketDraft]);

  const openDetail = (id: string) => { navigate(`/tasks/${id}`); };

  

  const relevanceOptions = [
    { value: 'all', label: '全部' },
    { value: 'mine', label: '待我处理' },
    { value: 'related', label: '与我相关' },
  ];

  const statusOptions = Object.entries(STATUS_DISPLAY_MAP).map(([value, label]) => ({ value, label }));

  const handleRelevanceChange = (value: string) => {
    setRelevanceFilter(value);
    setPage(1);
  };

  const handleStatusChange = (value: string) => {
    setStatusFilter(value);
    setPage(1);
  };

  const getFilterSummary = () => {
    const parts = [];
    if (relevanceFilter !== 'all') {
      parts.push(relevanceOptions.find((o) => o.value === relevanceFilter)?.label);
    }
    if (statusFilter !== 'all') {
      parts.push(statusOptions.find((o) => o.value === statusFilter)?.label);
    }
    return parts.length > 0 ? parts.join(' · ') : '筛选';
  };

  return (
    <div className="tasks-view">
      <Navbar title="系统任务" fixed />

      {/* 工单卡片列表 */}
      <div className="tasks-list-section">
        <div className="tasks-view__filters">
          <input
            className="tasks-search"
            placeholder="搜索工单…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          />
          <button className="tasks-filter-btn" onClick={() => setShowFilterMenu(true)}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1Z" />
            </svg>
            <span>{getFilterSummary()}</span>
          </button>
        </div>

        <div className="tasks-cards">
          {loading ? <Loading text="加载中…" /> : tickets.length === 0 ? (
            <div className="tasks-empty">暂无工单</div>
          ) : tickets.map((t) => (
            <div key={t.id} className="task-card2" onClick={() => openDetail(t.id)}>
              <div className="task-card2__head">
                <Tag theme={statusTheme(t.status)}>{normalizeStatus(t.status)}</Tag>
                <span className="task-card2__type">{TICKET_TYPE_DISPLAY_MAP[t.ticket_type] || t.ticket_type || '其他'}</span>
              </div>
              <div className="task-card2__title">{t.title}</div>
              <div className="task-card2__meta">
                <span>#{String(t.id).slice(0, 8)}</span>
                {t.project_name && <span>· {t.project_name}</span>}
                <span>· {formatDateTime(t.created_at).slice(0, 10)}</span>
              </div>
            </div>
          ))}
          <Pagination current={page} total={total} pageSize={pageSize} onChange={setPage} />
        </div>
      </div>

      <Popup visible={showFilterMenu} onClose={() => setShowFilterMenu(false)} placement="bottom" showOverlay>
        <div className="filter-menu">
          <div className="filter-menu__section">
            <h4 className="filter-menu__title">相关性</h4>
            <div className="filter-menu__items">
              {relevanceOptions.map((option) => (
                <button
                  key={option.value}
                  className={`filter-menu__item ${relevanceFilter === option.value ? 'is-active' : ''}`}
                  onClick={() => { handleRelevanceChange(option.value); }}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
          <div className="filter-menu__divider"></div>
          <div className="filter-menu__section">
            <h4 className="filter-menu__title">任务状态</h4>
            <div className="filter-menu__items">
              <button
                key="all"
                className={`filter-menu__item ${statusFilter === 'all' ? 'is-active' : ''}`}
                onClick={() => { handleStatusChange('all'); }}
              >
                全部
              </button>
              {statusOptions.map((option) => (
                <button
                  key={option.value}
                  className={`filter-menu__item ${statusFilter === option.value ? 'is-active' : ''}`}
                  onClick={() => { handleStatusChange(option.value); }}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </Popup>
    </div>
  );
}
