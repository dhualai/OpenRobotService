// 系统任务（供给视角）—— 上：AI 任务助手 / 下：工单卡片列表
// 马卡龙极简风格（参考 macaron-minimal-ui 设计）：胶囊筛选 + 灰阶卡片信息层级；
// 「待我处理」为按天时间轴。跨视图流转：消费 ticketDraft 自动建单。
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Navbar, Toast, Loading, Popup, Button, Textarea, Form, FormItem } from 'tdesign-mobile-react';
import ClearableInput from '@/shared/components/ClearableInput';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import Pagination from '@/shared/components/Pagination';
import UserAvatarMenu from '@/shared/components/UserAvatarMenu';
import { useWorkbenchStore } from '@/stores/workbench';
import { useAuthStore } from '@/stores/auth';
import { normalizeStatus, STATUS_DISPLAY_MAP, PRIORITY_DISPLAY_MAP, TICKET_TYPE_DISPLAY_MAP } from '@/shared/constants/ticket';
import { formatDateTime } from '@/shared/utils/url';
// 相关性分类过滤条件：列表查询与分类角标计数共用（底部导航「待我处理」角标复用同一口径）
import { buildRelevanceFilters, type TicketFilterCondition } from '@/shared/utils/ticketFilters';
import { Search, ArrowRight, Calendar, SlidersHorizontal } from 'lucide-react';

interface Ticket {
  id: string; title: string; description: string; status: string; priority: string;
  ticket_type: string; project_name?: string; assignee_name?: string; reporter_name?: string;
  contact?: string; created_at: string; updated_at: string;
  created_by?: string; created_by_name?: string;
  assigned_to?: string; assigned_to_name?: string;
  participants?: string[];
}

const pageSize = 20;

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

// 马卡龙极简工单卡片：状态为唯一带色文字（蓝阶），优先级蓝阶色块，
// 头像统一灰底白字，信息层级靠字号与字重区分（参考 macaron-minimal-ui 设计）。
function TicketCard({ t, onOpen }: { t: Ticket; onOpen: (id: string) => void }) {
  const creator = t.created_by_name || t.created_by || '-';
  const assignee = t.assigned_to_name || t.assigned_to || '-';
  const participants = (t.participants || []).filter(Boolean);
  return (
    <div className="task-card2" onClick={() => onOpen(t.id)}>
      <div className="task-card2__head">
        <div className="task-card2__head-tags">
          <span className="task-card2__status-tag" data-status={(t.status || '').toLowerCase()}>
            {normalizeStatus(t.status)}
          </span>
          <span className="task-card2__priority" data-priority={(t.priority || '').toLowerCase()}>
            {PRIORITY_DISPLAY_MAP[t.priority] || t.priority || '中'}
          </span>
        </div>
        <span className="task-card2__type">{TICKET_TYPE_DISPLAY_MAP[t.ticket_type] || t.ticket_type || '其他'}</span>
      </div>

      <div className="task-card2__title">{t.title}</div>

      {/* 人员流转：发起人 →（参与人）→ 处理人 */}
      <div className="task-card2__people">
        <div className="task-card2__person" title={`发起人：${creator}`}>
          <span className="task-card2__avatar">{creator.slice(0, 1).toUpperCase()}</span>
          <span className="task-card2__person-name">{creator}</span>
        </div>
        {participants.length > 0 && (
          <span className="task-card2__participants" title={`参与人：${participants.join('、')}`}>
            {participants.slice(0, 3).map((p, i) => (
              <span key={`${p}-${i}`} className="task-card2__participant">{p.slice(0, 1).toUpperCase()}</span>
            ))}
            {participants.length > 3 && (
              <span className="task-card2__participant task-card2__participant--overflow">+{participants.length - 3}</span>
            )}
          </span>
        )}
        <span className="task-card2__person-arrow">
          <ArrowRight size={14} strokeWidth={2} />
        </span>
        <div className="task-card2__person task-card2__person--assignee" title={`处理人：${assignee}`}>
          <span className="task-card2__person-name">{assignee}</span>
          <span className="task-card2__avatar">{assignee.slice(0, 1).toUpperCase()}</span>
        </div>
      </div>

      {/* 编号 · 项目 · 日期 */}
      <div className="task-card2__meta">
        <span className="task-card2__meta-id">#{String(t.id).slice(0, 8)}</span>
        {t.project_name && <span className="task-card2__meta-project">{t.project_name}</span>}
        <span className="task-card2__meta-date">
          <Calendar size={12} strokeWidth={2} />
          {formatDateTime(t.created_at).slice(0, 10)}
        </span>
      </div>
    </div>
  );
}

// 「待我处理」时间轴：按创建日期分组（新→旧），组内按紧急度排序，左侧日期列 + 竖向虚线。
function TicketTimeline({ items, onOpen }: { items: Ticket[]; onOpen: (id: string) => void }) {
  const groups = useMemo(() => {
    const map = new Map<string, Ticket[]>();
    for (const t of items) {
      const date = (t.created_at || '').slice(0, 10) || '未知';
      const list = map.get(date) ?? [];
      list.push(t);
      map.set(date, list);
    }
    return [...map.entries()]
      .sort((a, b) => (a[0] < b[0] ? 1 : -1))
      .map(([date, list]) => ({
        date,
        list: [...list].sort(
          (a, b) => (PRIORITY_WEIGHT_MAP[b.priority] || 0) - (PRIORITY_WEIGHT_MAP[a.priority] || 0),
        ),
      }));
  }, [items]);

  return (
    <div className="task-timeline">
      {groups.map((g) => (
        <div key={g.date} className="task-timeline__group">
          <div className="task-timeline__date">
            <span className="task-timeline__date-md">{g.date.slice(5)}</span>
            <span className="task-timeline__date-year">{g.date.slice(0, 4)}</span>
            <span className="task-timeline__dot" />
          </div>
          <div className="task-timeline__line">
            {g.list.map((t) => (
              <TicketCard key={t.id} t={t} onOpen={onOpen} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

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

  // 新建工单悬浮按钮：液态玻璃质感 + 可拖动（拖动超过阈值视为拖拽，不触发点击）
  const [fabPos, setFabPos] = useState<{ x: number; y: number } | null>(null);
  const [fabDragging, setFabDragging] = useState(false);
  const fabDragRef = useRef({ x: 0, y: 0, moved: false });
  const FAB_SIZE = 52;
  const FAB_MARGIN = 12;

  useEffect(() => {
    const clamp = (x: number, y: number) => ({
      x: Math.min(Math.max(x, FAB_MARGIN), Math.max(FAB_MARGIN, window.innerWidth - FAB_SIZE - FAB_MARGIN)),
      y: Math.min(Math.max(y, FAB_MARGIN), Math.max(FAB_MARGIN, window.innerHeight - FAB_SIZE - FAB_MARGIN)),
    });
    setFabPos(clamp(window.innerWidth - FAB_SIZE - 16, window.innerHeight - FAB_SIZE - 150));
    const onResize = () => setFabPos((p) => (p ? clamp(p.x, p.y) : p));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const handleFabPointerDown = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (!fabPos) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    fabDragRef.current = { x: e.clientX - fabPos.x, y: e.clientY - fabPos.y, moved: false };
    setFabDragging(true);
  };

  const handleFabPointerMove = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (!fabPos || !fabDragRef.current) return;
    const d = fabDragRef.current;
    const next = {
      x: Math.min(Math.max(e.clientX - d.x, FAB_MARGIN), Math.max(FAB_MARGIN, window.innerWidth - FAB_SIZE - FAB_MARGIN)),
      y: Math.min(Math.max(e.clientY - d.y, FAB_MARGIN), Math.max(FAB_MARGIN, window.innerHeight - FAB_SIZE - FAB_MARGIN)),
    };
    if (Math.abs(next.x - fabPos.x) > 2 || Math.abs(next.y - fabPos.y) > 2) d.moved = true;
    setFabPos(next);
  };

  const handleFabPointerUp = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    setFabDragging(false);
  };

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

  

  // 「全部」始终展示：有 frontend:task:all 权限时看全量工单，
  // 无权限时退化为项目维度（与 fetchTickets 的回退口径一致）。
  const relevanceOptions = useMemo(() => [
    { value: 'global', label: '全部' },
    { value: 'all', label: '项目相关' },
    { value: 'mine', label: '待我处理' },
    { value: 'related', label: '与我相关' },
  ], []);

  // 拉取各分类角标条数：与列表共用同一套相关性过滤口径（不受搜索/状态/优先级影响），
  // 每次只取 total（size=1）；单个分类失败静默跳过，保留旧值。
  const fetchRelevanceCounts = useCallback(async () => {
    if (countsFetchingRef.current) return;
    countsFetchingRef.current = true;
    try {
      const entries = await Promise.all(
        relevanceOptions.map(async (option) => {
          try {
            // 「全部」无权限时按项目维度计数，与列表回退口径一致
            const key = option.value === 'global' && !canViewAllTasks ? 'all' : option.value;
            const data = await request<{ total: number }>('/filter', {
              method: 'POST',
              body: JSON.stringify({
                filters: buildRelevanceFilters(key, username, projectIds),
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
  }, [relevanceOptions, username, projectIds, canViewAllTasks]);
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
            <div className="tasks-view__search-card">
              <Search size={16} strokeWidth={2} />
              <input
                className="tasks-search"
                placeholder="搜索工单…"
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1); }}
              />
            </div>
          </div>

          <div className="tasks-view__sort-row">
            <span className="tasks-view__sort-label">排序</span>
            <button
              className={`tasks-view__sort-option ${sortBy === 'priority' && sortOrder === 'desc' ? 'is-active' : ''}`}
              onClick={() => {
                setSortBy('priority');
                setSortOrder('desc');
                setPage(1);
              }}
            >
              紧急优先
            </button>
            {sortOptions.map((option) => (
              <button
                key={option.value}
                className={`tasks-view__sort-option ${sortBy === option.value ? 'is-active' : ''}`}
                onClick={() => {
                  if (sortBy === option.value) {
                    if (sortOrder === 'desc') {
                      setSortOrder('asc');
                    } else {
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
              {statusOptions.map((option) => (
                <button
                  key={option.value}
                  className={`tasks-view__filter-chip ${statusFilter === option.value ? 'is-active' : ''}`}
                  onClick={() => { handleStatusChange(option.value); }}
                >
                  {option.label}
                </button>
              ))}
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
              <SlidersHorizontal size={14} strokeWidth={2} />
              <span>筛选</span>
            </button>
          </div>
        </div>

        <div className="tasks-cards">
          {loading ? <Loading text="加载中…" /> : tickets.length === 0 ? (
            <div className="tasks-empty">暂无工单</div>
          ) : relevanceFilter === 'mine' ? (
            <TicketTimeline items={tickets} onOpen={openDetail} />
          ) : (
            tickets.map((t) => (
              <TicketCard key={t.id} t={t} onOpen={openDetail} />
            ))
          )}
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

      {/* 新建工单悬浮按钮：液态玻璃质感，可拖动 */}
      {canManageTasks && fabPos && (
        <div className="tasks-view__fab" style={{ left: fabPos.x, top: fabPos.y, width: FAB_SIZE }}>
          <button
            className={`tasks-view__fab-btn${creatingTask ? ' is-submitting' : ''}${fabDragging ? ' is-dragging' : ''}`}
            onPointerDown={handleFabPointerDown}
            onPointerMove={handleFabPointerMove}
            onPointerUp={handleFabPointerUp}
            onPointerCancel={handleFabPointerUp}
            onClick={() => { if (!fabDragRef.current.moved) setShowCreateModal(true); }}
            disabled={creatingTask}
            aria-label="新建工单"
          >
            <span className="tasks-view__fab-highlight" />
            {creatingTask ? (
              <span className="chat-ticket-spinner" />
            ) : (
              <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" xmlns="http://www.w3.org/2000/svg" className="tasks-view__fab-icon">
                <path d="M16 1H8V5H16V1Z" />
                <path d="M6 3H3V23H13.8762C13.0139 21.897 12.5 20.5085 12.5 19C12.5 15.4101 15.4101 12.5 19 12.5C19.6978 12.5 20.3699 12.61 21 12.8135V3H18V7H6V3Z" />
                <path d="M24 20H20V24H18V20H14V18H18V14H20V18H24V20Z" />
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
