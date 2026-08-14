// 系统任务（供给视角）—— 上：AI 任务助手 / 下：工单卡片列表
// 卡片样式与输入卡片审美一致（白底 + 阴影 + 圆角）。
// 跨视图流转：消费 ticketDraft 自动建单；讨论按钮 → 带上下文跳回我要摇人。
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Navbar, Toast, Loading, Tag, Popup, Button, Textarea, Form, FormItem } from 'tdesign-mobile-react';
import ClearableInput from '@/shared/components/ClearableInput';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import Pagination from '@/shared/components/Pagination';
import UserAvatarMenu from '@/shared/components/UserAvatarMenu';
import { useWorkbenchStore } from '@/stores/workbench';
import { useAuthStore } from '@/stores/auth';
import { normalizeStatus, STATUS_DISPLAY_MAP, PRIORITY_DISPLAY_MAP, TICKET_TYPE_DISPLAY_MAP } from '@/shared/constants/ticket';
import { formatDateTime } from '@/shared/utils/url';

interface Ticket {
  id: string; title: string; description: string; status: string; priority: string;
  ticket_type: string; project_name?: string; assignee_name?: string; reporter_name?: string;
  contact?: string; created_at: string; updated_at: string;
  created_by?: string; created_by_name?: string;
  assigned_to?: string; assigned_to_name?: string;
}

const pageSize = 20;

const STATUS_COLOR_MAP: Record<string, string> = {
  new: '#0052d9',
  in_progress: '#2ba471',
  pending: '#e37318',
  paused: '#e37318',
  resolved: '#00a870',
  closed: '#999999',
  canceled: '#d54941',
  cancelled: '#d54941',
};

const getStatusColor = (status: string): string => {
  const key = (status || '').toLowerCase();
  return STATUS_COLOR_MAP[key] || '#666666';
};

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

// 从 URL 查询参数解析筛选状态的工具函数
const parseFilterFromUrl = (params: URLSearchParams) => {
  return {
    search: params.get('q') || '',
    statusFilter: params.get('status') || 'all',
    priorityFilter: params.get('priority') || 'all',
    relevanceFilter: params.get('relevance') || 'mine',
    page: parseInt(params.get('page') || '1', 10),
    sortBy: params.get('sort') || 'priority',
    sortOrder: params.get('order') || 'desc',
  };
};

// 与后端 TicketFilter 对应的复合过滤条件（支持 or/and 嵌套）
interface TicketFilterCondition {
  field?: string;
  op?: string;
  value?: string;
  or?: TicketFilterCondition[];
  and?: TicketFilterCondition[];
}

// 相关性分类（全部/项目相关/待我处理/与我相关）的基础过滤条件，不含搜索/状态/优先级。
// 列表查询与分类角标计数共用，保证两侧口径一致；
// 待我处理/与我相关在缺少用户名时回退为项目维度，与列表行为一致。
const buildRelevanceFilters = (
  relevance: string,
  username: string,
  projectIds: string[],
): TicketFilterCondition[] => {
  if (relevance === 'global') {
    // 「全部」：不过滤项目、人员相关性，直接拉全量
    return [];
  }
  if (relevance === 'mine' && username) {
    const workingStatusFilters = [
      { field: 'status', op: 'eq', value: 'new' },
      { field: 'status', op: 'eq', value: 'in_progress' },
      { field: 'status', op: 'eq', value: 'pending' },
    ];
    return [{
      or: [
        {
          and: [
            { or: workingStatusFilters },
            { field: 'assignedTo', op: 'eq', value: username },
          ],
        },
        {
          and: [
            { field: 'status', op: 'eq', value: 'resolved' },
            { field: 'createdBy', op: 'eq', value: username },
          ],
        },
      ],
    }];
  }
  if (relevance === 'related' && username) {
    const userRelatedFilters = [
      { field: 'createdBy', op: 'eq', value: username },
      { field: 'createdByName', op: 'contains', value: username },
      { field: 'assignedTo', op: 'eq', value: username },
      { field: 'assignedToName', op: 'contains', value: username },
      { field: 'customer', op: 'eq', value: username },
      { field: 'customerName', op: 'contains', value: username },
    ];
    return [{ or: userRelatedFilters }];
  }
  // 「项目相关」：仅展示与当前用户关联的项目（projectIds）下的工单，
  // 项目列表为空时（未加载/无项目）回退为不限制。
  return projectIds.length > 0
    ? [{ or: projectIds.map((pid) => ({ field: 'projectId', op: 'eq', value: pid })) }]
    : [];
};

// 将筛选状态同步到 URL 查询参数的工具函数
const buildFilterParams = (filter: {
  search: string; statusFilter: string; priorityFilter: string;
  relevanceFilter: string; page: number; sortBy: string; sortOrder: string;
}) => {
  const params = new URLSearchParams();
  if (filter.search) params.set('q', filter.search);
  if (filter.statusFilter !== 'all') params.set('status', filter.statusFilter);
  if (filter.priorityFilter !== 'all') params.set('priority', filter.priorityFilter);
  if (filter.relevanceFilter !== 'mine') params.set('relevance', filter.relevanceFilter);
  if (filter.page > 1) params.set('page', String(filter.page));
  if (filter.sortBy !== 'priority') {
    params.set('sort', filter.sortBy);
    params.set('order', filter.sortOrder);
  }
  return params.toString();
};

export default function TasksView() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');

  const {
    tasksRefreshKey, ticketDraft, consumeTicketDraft, refreshTasks,
  } = useWorkbenchStore();

  const { username, hasPermission, projectIds } = useAuthStore();
  const canManageTasks = hasPermission('frontend:develop');
  const canViewAllTasks = hasPermission('frontend:task:all');

  // 从 URL 初始化筛选状态
  const initialFilter = useRef(parseFilterFromUrl(searchParams));

  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState(() => initialFilter.current.search);
  const [statusFilter, setStatusFilter] = useState(() => initialFilter.current.statusFilter);
  const [priorityFilter, setPriorityFilter] = useState(() => initialFilter.current.priorityFilter);
  const [relevanceFilter, setRelevanceFilter] = useState(() => initialFilter.current.relevanceFilter);
  const [showFilterMenu, setShowFilterMenu] = useState(false);
  const [page, setPage] = useState(() => initialFilter.current.page);
  const [total, setTotal] = useState(0);
  const [sortBy, setSortBy] = useState(() => initialFilter.current.sortBy);
  const [sortOrder, setSortOrder] = useState(() => initialFilter.current.sortOrder);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [creatingTask, setCreatingTask] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [createForm, setCreateForm] = useState({
    title: '',
    description: '',
    priority: 'medium',
    ticket_type: 'problem',
  });

  const isFetchingRef = useRef(false);
  const fetchTicketsRef = useRef<typeof fetchTickets>(async () => {});

  // 各分类（全部/项目相关/待我处理/与我相关）的工单条数，用于筛选条目的右上角角标
  const [relevanceCounts, setRelevanceCounts] = useState<Record<string, number>>({});
  const countsFetchingRef = useRef(false);
  const fetchCountsRef = useRef<() => Promise<void>>(async () => {});

  const fetchTickets = useCallback(async (silent = false) => {
    if (isFetchingRef.current) return;
    isFetchingRef.current = true;
    if (!silent) setLoading(true);
    try {
      // 相关性基础过滤（全部/项目相关/待我处理/与我相关）；
      // 「全部」无权限时按项目相关口径处理，与可见的分类选项一致。
      const relevanceKey = relevanceFilter === 'global' && !canViewAllTasks ? 'all' : relevanceFilter;
      const filters: TicketFilterCondition[] = buildRelevanceFilters(relevanceKey, username, projectIds);

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

      const data = await request<{ items: Ticket[]; total: number }>('/filter', {
        method: 'POST',
        body: JSON.stringify({
          filters,
          sorts,
          page,
          size: pageSize,
        }),
        skipCache: true,
      });

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
      if (!silent) {
        Toast({ message: `加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      }
    } finally {
      isFetchingRef.current = false;
      if (!silent) setLoading(false);
    }
  }, [page, search, statusFilter, priorityFilter, relevanceFilter, username, projectIds, sortBy, sortOrder, canViewAllTasks]);

  fetchTicketsRef.current = fetchTickets;

  // 筛选状态变化时同步到 URL
  useEffect(() => {
    const newParams = buildFilterParams({
      search, statusFilter, priorityFilter,
      relevanceFilter, page, sortBy, sortOrder,
    });
    if (newParams !== searchParams.toString()) {
      setSearchParams(newParams, { replace: true });
    }
  }, [search, statusFilter, priorityFilter, relevanceFilter, page, sortBy, sortOrder]);

  useEffect(() => { fetchTickets(); }, [fetchTickets]);
  useEffect(() => { if (tasksRefreshKey > 0) fetchTickets(); }, [tasksRefreshKey]);

  useEffect(() => {
    const interval = setInterval(() => {
      fetchTicketsRef.current(true);
      fetchCountsRef.current();
    }, 2000);
    return () => clearInterval(interval);
  }, []);

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

  

  const relevanceOptions = useMemo(() => [
    ...(canViewAllTasks ? [{ value: 'global', label: '全部' }] : []),
    { value: 'all', label: '项目相关' },
    { value: 'mine', label: '待我处理' },
    { value: 'related', label: '与我相关' },
  ], [canViewAllTasks]);

  // 拉取各分类角标条数：与列表共用同一套相关性过滤口径（不受搜索/状态/优先级影响），
  // 每次只取 total（size=1）；单个分类失败静默跳过，保留旧值。
  const fetchRelevanceCounts = useCallback(async () => {
    if (countsFetchingRef.current) return;
    countsFetchingRef.current = true;
    try {
      const entries = await Promise.all(
        relevanceOptions.map(async (option) => {
          try {
            const data = await request<{ total: number }>('/filter', {
              method: 'POST',
              body: JSON.stringify({
                filters: buildRelevanceFilters(option.value, username, projectIds),
                sorts: [],
                page: 1,
                size: 1,
              }),
              skipCache: true,
            });
            return [option.value, data.total] as const;
          } catch {
            return null;
          }
        }),
      );
      const next: Record<string, number> = {};
      entries.forEach((entry) => {
        if (entry) next[entry[0]] = entry[1];
      });
      setRelevanceCounts(next);
    } catch {
      // 计数失败保持旧角标，不打扰页面
    } finally {
      countsFetchingRef.current = false;
    }
  }, [relevanceOptions, username, projectIds]);
  fetchCountsRef.current = fetchRelevanceCounts;

  useEffect(() => { fetchRelevanceCounts(); }, [fetchRelevanceCounts]);
  useEffect(() => { if (tasksRefreshKey > 0) fetchRelevanceCounts(); }, [tasksRefreshKey]);

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

  const handleSyncExternalTasks = async () => {
    setSyncing(true);
    try {
      await request('/sources/wecom/projects/sync', {
        method: 'POST',
      });
      Toast({ message: '外部任务同步成功', theme: 'success' });
      refreshTasks();
      setPage(1);
    } catch (err) {
      Toast({ message: `同步失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSyncing(false);
    }
  };

  const handleCreateTask = async () => {
    if (!createForm.title.trim()) {
      Toast({ message: '请输入工单标题', theme: 'warning' });
      return;
    }
    if (!createForm.description.trim()) {
      Toast({ message: '请输入工单描述', theme: 'warning' });
      return;
    }
    setCreatingTask(true);
    try {
      await request<Ticket>('/', {
        method: 'POST',
        body: JSON.stringify(createForm),
      });
      Toast({ message: '工单创建成功', theme: 'success' });
      setShowCreateModal(false);
      setCreateForm({ title: '', description: '', priority: 'medium', ticket_type: 'problem' });
      refreshTasks();
      setPage(1);
    } catch (err) {
      Toast({ message: `创建失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setCreatingTask(false);
    }
  };

  return (
    <div className="tasks-view">
      <Navbar
        title="系统任务"
        fixed
        right={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {canManageTasks && (
              <button
                className="tasks-view__sync-btn"
                onClick={handleSyncExternalTasks}
                disabled={syncing}
                aria-label="同步外部任务"
              >
                {syncing ? (
                  <span className="tasks-view__sync-spinner" />
                ) : (
                  <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2"/>
                  </svg>
                )}
                <span>{syncing ? '同步中…' : '同步外部任务'}</span>
              </button>
            )}
            <UserAvatarMenu />
          </div>
        }
      />

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
                  {typeof relevanceCounts[option.value] === 'number' && (
                    <span className="tasks-count-badge">
                      {relevanceCounts[option.value] > 99 ? '99+' : relevanceCounts[option.value]}
                    </span>
                  )}
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
                <div className="task-card2__head-tags">
                  <span
                    className="task-card2__status-tag"
                    style={{ background: getStatusColor(t.status), color: '#fff' }}
                  >
                    {normalizeStatus(t.status)}
                  </span>
                  <Tag theme={priorityTheme(t.priority)} className="task-card2__priority">
                    {PRIORITY_DISPLAY_MAP[t.priority] || t.priority}
                  </Tag>
                </div>
                <span className="task-card2__type">{TICKET_TYPE_DISPLAY_MAP[t.ticket_type] || t.ticket_type || '其他'}</span>
              </div>
              <div className="task-card2__title">{t.title}</div>

              <div className="task-card2__divider" />

              {/* 人员流转：发起人 → 处理人 */}
              <div className="task-card2__people">
                <div className="task-card2__person task-card2__person--creator" title={`发起人：${t.created_by_name || t.created_by || '-'}`}>
                  <span className="task-card2__avatar">{(t.created_by_name || t.created_by || '?').slice(0, 1).toUpperCase()}</span>
                  <span className="task-card2__person-text">
                    <span className="task-card2__person-label">发起人</span>
                    <span className="task-card2__person-name">{t.created_by_name || t.created_by || '-'}</span>
                  </span>
                </div>
                <span className="task-card2__person-arrow">➡️</span>
                <div className="task-card2__person task-card2__person--assignee" title={`处理人：${t.assigned_to_name || t.assigned_to || '-'}`}>
                  <span className="task-card2__avatar task-card2__avatar--assignee">{(t.assigned_to_name || t.assigned_to || '?').slice(0, 1).toUpperCase()}</span>
                  <span className="task-card2__person-text">
                    <span className="task-card2__person-label">处理人</span>
                    <span className="task-card2__person-name">{t.assigned_to_name || t.assigned_to || '-'}</span>
                  </span>
                </div>
              </div>

              {/* 编号 · 项目 · 日期 */}
              <div className="task-card2__meta">
                <span className="task-card2__meta-id">#{String(t.id).slice(0, 8)}</span>
                {t.project_name && <span className="task-card2__meta-project">{t.project_name}</span>}
                <span className="task-card2__meta-date">
                  <span className="task-card2__meta-date-label">创建时间</span>
                  <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="3" y="4" width="18" height="18" rx="2" />
                    <path d="M16 2v4M8 2v4M3 10h18" />
                  </svg>
                  {formatDateTime(t.created_at).slice(0, 10)}
                </span>
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
                  {typeof relevanceCounts[option.value] === 'number' && (
                    <span className="tasks-count-badge">
                      {relevanceCounts[option.value] > 99 ? '99+' : relevanceCounts[option.value]}
                    </span>
                  )}
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

      {/* 新建工单悬浮按钮 */}
      {canManageTasks && (
        <div className="tasks-view__fab">
          <button
            className={`tasks-view__fab-btn${creatingTask ? ' is-submitting' : ''}`}
            onClick={() => setShowCreateModal(true)}
            disabled={creatingTask}
            aria-label="新建工单"
          >
            {creatingTask ? (
              <span className="chat-ticket-spinner" />
            ) : (
              <svg viewBox="0 0 24 24" width="18" height="24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path fill="currentColor" d="M16 1H8V5H16V1Z" />
                <path fill="currentColor" d="M6 3H3V23H13.8762C13.0139 21.897 12.5 20.5085 12.5 19C12.5 15.4101 15.4101 12.5 19 12.5C19.6978 12.5 20.3699 12.61 21 12.8135V3H18V7H6V3Z" />
                <path fill="currentColor" d="M24 20H20V24H18V20H14V18H18V14H20V18H24V20Z" />
              </svg>
            )}
          </button>
          <span className="tasks-view__fab-label">{creatingTask ? '提交中…' : '新建工单'}</span>
        </div>
      )}

      {/* 新建工单表单弹窗 */}
      <Popup visible={showCreateModal} onClose={() => setShowCreateModal(false)} placement="bottom" showOverlay>
        <div className="tasks-create-modal">
          <h4 className="tasks-create-modal__title">新建工单</h4>
          <Form onSubmit={handleCreateTask}>
            <FormItem label="标题">
              <ClearableInput
                value={createForm.title}
                onChange={(v) => setCreateForm((p) => ({ ...p, title: String(v) }))}
                placeholder="请输入工单标题"
              />
            </FormItem>
            <FormItem label="类型">
              <div className="tasks-create-modal__radio-group">
                {Object.entries(TICKET_TYPE_DISPLAY_MAP).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    className={`tasks-create-modal__radio-btn ${createForm.ticket_type === value ? 'is-active' : ''}`}
                    onClick={() => setCreateForm((p) => ({ ...p, ticket_type: value }))}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </FormItem>
            <FormItem label="优先级">
              <div className="tasks-create-modal__radio-group">
                {Object.entries(PRIORITY_DISPLAY_MAP).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    className={`tasks-create-modal__radio-btn ${createForm.priority === value ? 'is-active' : ''}`}
                    onClick={() => setCreateForm((p) => ({ ...p, priority: value }))}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </FormItem>
            <FormItem label="描述">
              <Textarea
                value={createForm.description}
                onChange={(v) => setCreateForm((p) => ({ ...p, description: String(v) }))}
                placeholder="请描述问题详情…"
                rows={4}
              />
            </FormItem>
            <FormItem>
              <div className="tasks-create-modal__actions">
                <Button theme="default" block onClick={() => setShowCreateModal(false)}>取消</Button>
                <Button theme="primary" block type="submit" loading={creatingTask}>创建工单</Button>
              </div>
            </FormItem>
          </Form>
        </div>
      </Popup>
    </div>
  );
}
