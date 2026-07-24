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
import { useAuthStore } from '@/stores/auth';
import { normalizeStatus, STATUS_DISPLAY_MAP, PRIORITY_DISPLAY_MAP, TICKET_TYPE_DISPLAY_MAP } from '@/shared/constants/ticket';
import { formatDateTime } from '@/shared/utils/url';

interface Ticket {
  id: string; title: string; description: string; status: string; priority: string;
  ticket_type: string; project_name?: string; assignee_name?: string; reporter_name?: string;
  contact?: string; created_at: string; updated_at: string;
}

const pageSize = 20;
const statusTheme = (s: string): 'success' | 'primary' | 'warning' =>
  s === 'closed' ? 'success' : s === 'new' ? 'primary' : 'warning';

const priorityTheme = (p: string): 'success' | 'default' | 'warning' | 'danger' => {
  switch (p) {
    case 'low': return 'success';
    case 'medium': return 'default';
    case 'high': return 'warning';
    case 'urgent': return 'danger';
    default: return 'default';
  }
};

const PRIORITY_WEIGHT_MAP: Record<string, number> = {
  urgent: 4,
  high: 3,
  medium: 2,
  low: 1,
};

export default function TasksView() {
  const navigate = useNavigate();
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');

  const {
    tasksRefreshKey, ticketDraft, consumeTicketDraft, refreshTasks,
  } = useWorkbenchStore();

  const { username } = useAuthStore();

  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [priorityFilter, setPriorityFilter] = useState('all');
  const [relevanceFilter, setRelevanceFilter] = useState('mine');
  const [showFilterMenu, setShowFilterMenu] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [sortBy, setSortBy] = useState('priority');
  const [sortOrder, setSortOrder] = useState('desc');

  const fetchTickets = useCallback(async () => {
    setLoading(true);
    try {
      let data;
      
      if (relevanceFilter === 'related' && username) {
        const filters: any[] = [];
        
        const userRelatedFilters = [
          { field: 'createdBy', op: 'eq', value: username },
          { field: 'createdByName', op: 'contains', value: username },
          { field: 'assignedTo', op: 'eq', value: username },
          { field: 'assignedToName', op: 'contains', value: username },
          { field: 'customer', op: 'eq', value: username },
          { field: 'customerName', op: 'contains', value: username },
        ];
        
        filters.push({ or: userRelatedFilters });
        
        if (search) {
          filters.push({ field: 'title', op: 'contains', value: search });
        }
        
        if (statusFilter !== 'all') {
          filters.push({ field: 'status', op: 'eq', value: statusFilter });
        }
        
        if (priorityFilter !== 'all') {
          filters.push({ field: 'priority', op: 'eq', value: priorityFilter });
        }
        
        const sorts = sortBy === 'priority' 
          ? [] 
          : [{ field: sortBy === 'created_at' ? 'createdAt' : 'updatedAt', direction: sortOrder }];
        
        data = await request<{ items: Ticket[]; total: number }>('/filter', {
          method: 'POST',
          body: JSON.stringify({
            filters,
            sorts,
            page,
            size: pageSize,
          }),
        });
      } else {
        const params = new URLSearchParams({
          page: String(page), size: String(pageSize),
          ...(search && { keyword: search }),
          ...(statusFilter !== 'all' && { status: statusFilter }),
          ...(priorityFilter !== 'all' && { priority: priorityFilter }),
          sort_by: sortBy,
          sort_order: sortOrder,
        });
        
        if (relevanceFilter === 'mine' && username) {
          params.set('assigned_to', username);
        }
        
        data = await request<{ items: Ticket[]; total: number }>(`/?${params.toString()}`);
      }
      
      let sortedItems = data.items || [];
      if (sortBy === 'priority') {
        sortedItems = [...sortedItems].sort((a, b) => {
          const weightA = PRIORITY_WEIGHT_MAP[a.priority] || 0;
          const weightB = PRIORITY_WEIGHT_MAP[b.priority] || 0;
          return sortOrder === 'desc' ? weightB - weightA : weightA - weightB;
        });
      }
      setTickets(sortedItems);
      setTotal(data.total || 0);
    } catch (err) {
      Toast({ message: `加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [page, search, statusFilter, priorityFilter, relevanceFilter, username, sortBy, sortOrder]);

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

  const priorityOptions = Object.entries(PRIORITY_DISPLAY_MAP).map(([value, label]) => ({ value, label }));

  const sortOptions = [
    { value: 'created_at', label: '创建时间' },
    { value: 'updated_at', label: '更新时间' },
  ];

  const handleRelevanceChange = (value: string) => {
    setRelevanceFilter(value);
    setPage(1);
  };

  const handleStatusChange = (value: string) => {
    setStatusFilter(value);
    setPage(1);
  };

  const handlePriorityChange = (value: string) => {
    setPriorityFilter(value);
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
    if (priorityFilter !== 'all') {
      parts.push(priorityOptions.find((o) => o.value === priorityFilter)?.label);
    }
    return parts.length > 0 ? parts.join(' · ') : '筛选';
  };

  return (
    <div className="tasks-view">
      <Navbar title="系统任务" fixed />

      {/* 工单卡片列表 */}
      <div className="tasks-list-section">
        <div className="tasks-view__filters">
          <div className="tasks-view__search-row">
            <input
              className="tasks-search"
              placeholder="搜索工单…"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            />
          </div>

          <div className="tasks-view__sort-row">
            <span className="tasks-view__sort-label">排序：</span>
            <button
              className={`tasks-view__priority-quick-sort ${sortBy === 'priority' && sortOrder === 'desc' ? 'is-active' : ''}`}
              onClick={() => {
                setSortBy('priority');
                setSortOrder('desc');
                setPage(1);
              }}
            >
              <span className="tasks-view__priority-icon">!</span>
              <span>紧急优先</span>
            </button>
            <div className="tasks-view__sort-options">
              {sortOptions.map((option) => (
                <button
                  key={option.value}
                  className={`tasks-view__sort-option ${sortBy === option.value ? (sortOrder === 'desc' ? 'is-active is-desc' : 'is-active is-asc') : ''}`}
                  onClick={() => {
                    if (sortBy === option.value) {
                      if (sortOrder === 'desc') {
                        setSortOrder('asc');
                      } else if (sortOrder === 'asc') {
                        setSortBy('priority');
                        setSortOrder('desc');
                      }
                    } else {
                      setSortBy(option.value);
                      setSortOrder('desc');
                    }
                    setPage(1);
                  }}
                >
                  {option.label}
                  {sortBy === option.value && (
                    <span className="tasks-view__sort-arrow">
                      {sortOrder === 'desc' ? '↓' : '↑'}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>

          <div className="tasks-view__filter-row">
            <div className="tasks-view__filter-chips">
              {relevanceOptions.map((option) => (
                <button
                  key={option.value}
                  className={`tasks-view__filter-chip ${relevanceFilter === option.value ? 'is-active' : ''}`}
                  onClick={() => { handleRelevanceChange(option.value); }}
                >
                  {option.label}
                </button>
              ))}
              <div className="tasks-view__filter-divider"></div>
              {statusOptions.map((option) => (
                <button
                  key={option.value}
                  className={`tasks-view__filter-chip ${statusFilter === option.value ? 'is-active' : ''}`}
                  onClick={() => { handleStatusChange(option.value); }}
                >
                  {option.label}
                </button>
              ))}
              <div className="tasks-view__filter-divider"></div>
              {priorityOptions.map((option) => (
                <button
                  key={option.value}
                  className={`tasks-view__filter-chip ${priorityFilter === option.value ? 'is-active' : ''}`}
                  onClick={() => { handlePriorityChange(option.value); }}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <button className="tasks-filter-btn" onClick={() => setShowFilterMenu(true)}>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1Z" />
              </svg>
              <span>筛选</span>
            </button>
          </div>
        </div>

        <div className="tasks-cards">
          {loading ? <Loading text="加载中…" /> : tickets.length === 0 ? (
            <div className="tasks-empty">暂无工单</div>
          ) : tickets.map((t) => (
            <div key={t.id} className="task-card2" onClick={() => openDetail(t.id)}>
              <div className="task-card2__head">
                <Tag theme={statusTheme(t.status)}>{normalizeStatus(t.status)}</Tag>
                <Tag theme={priorityTheme(t.priority)} className="task-card2__priority">
                  {PRIORITY_DISPLAY_MAP[t.priority] || t.priority}
                </Tag>
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
          <div className="filter-menu__divider"></div>
          <div className="filter-menu__section">
            <h4 className="filter-menu__title">优先级</h4>
            <div className="filter-menu__items">
              <button
                key="all"
                className={`filter-menu__item ${priorityFilter === 'all' ? 'is-active' : ''}`}
                onClick={() => { handlePriorityChange('all'); }}
              >
                全部
              </button>
              {priorityOptions.map((option) => (
                <button
                  key={option.value}
                  className={`filter-menu__item ${priorityFilter === option.value ? 'is-active' : ''}`}
                  onClick={() => { handlePriorityChange(option.value); }}
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
