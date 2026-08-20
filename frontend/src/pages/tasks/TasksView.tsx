// 系统任务（供给视角）—— 上：AI 任务助手 / 下：工单卡片列表
// 马卡龙极简风格（参考 macaron-minimal-ui 设计）：胶囊筛选 + 灰阶卡片信息层级；
// 「待我处理」为按天时间轴。跨视图流转：消费 ticketDraft 自动建单。
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { createPortal } from 'react-dom';
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
import { Search, ArrowRight, Calendar, SlidersHorizontal, ChevronDown } from 'lucide-react';
import { avatarUrl } from '@/api/profile';
import { useHorizontalScroll } from '@/shared/hooks/useHorizontalScroll';
import SubscriptionReminder from '@/shared/components/SubscriptionReminder';
import { getMyProjects, type ProjectItem } from '@/api/projects';

interface Ticket {
  id: string; title: string; description: string; status: string; priority: string;
  ticket_type: string; project_name?: string; assignee_name?: string; reporter_name?: string;
  contact?: string; created_at: string; updated_at: string;
  created_by?: string; created_by_name?: string;
  assigned_to?: string; assigned_to_name?: string;
  participants?: string[];
}

/** username / user_id → avatar_resource_id 的查找表；缺失时回退为首字母头像 */
type AvatarMap = Map<string, number>;

const pageSize = 20;

const PRIORITY_WEIGHT_MAP: Record<string, number> = {
  urgent: 4,
  high: 3,
  medium: 2,
  low: 1,
};

// 默认选中的任务状态：新建 / 进行中 / 已挂起 / 已解决（排除 已取消 / 已关闭）
const DEFAULT_STATUS_VALUES: string[] = ['new', 'in_progress', 'pending', 'resolved'];
const ALL_STATUS_VALUES: string[] = Object.keys(STATUS_DISPLAY_MAP);
// 优先级默认全选（low / medium / high / urgent）
const ALL_PRIORITY_VALUES: string[] = Object.keys(PRIORITY_DISPLAY_MAP);

// 从 URL 查询参数解析筛选状态的工具函数
const parseFilterFromUrl = (params: URLSearchParams) => {
  const rawStatus = params.get('status');
  // 约定：URL 中缺失 status 时使用默认值；status=all 表示全部选中（无状态过滤）；否则按逗号分隔解析
  let statusFilter: string[];
  if (rawStatus === null) {
    statusFilter = [...DEFAULT_STATUS_VALUES];
  } else if (rawStatus === 'all') {
    statusFilter = [...ALL_STATUS_VALUES];
  } else {
    const parsed = rawStatus.split(',').map((s) => s.trim()).filter(Boolean);
    statusFilter = parsed.length > 0 ? parsed : [...DEFAULT_STATUS_VALUES];
  }
  // 优先级多选：缺失或 'all' 视为全选，否则按逗号分隔解析
  const rawPriority = params.get('priority');
  let priorityFilter: string[];
  if (rawPriority === null || rawPriority === 'all') {
    priorityFilter = [...ALL_PRIORITY_VALUES];
  } else {
    const parsed = rawPriority.split(',').map((s) => s.trim()).filter(Boolean);
    priorityFilter = parsed.length > 0 ? parsed : [...ALL_PRIORITY_VALUES];
  }
  return {
    search: params.get('q') || '',
    statusFilter,
    priorityFilter,
    relevanceFilter: params.get('relevance') || 'mine',
    // 项目过滤：空字符串表示「全部」（不过滤）
    projectFilter: params.get('project') || '',
    // 处理人过滤：空字符串表示「全部」（不过滤）；值为处理人 username
    assigneeFilter: params.get('assignee') || '',
    // 创建人过滤：空字符串表示「全部」（不过滤）；值为创建人 username
    creatorFilter: params.get('creator') || '',
    // 时间过滤：空字符串表示不限制；值为 YYYY-MM-DDTHH:mm（精确到分钟）
    createdStart: params.get('createdStart') || '',
    createdEnd: params.get('createdEnd') || '',
    resolvedStart: params.get('resolvedStart') || '',
    resolvedEnd: params.get('resolvedEnd') || '',
    closedStart: params.get('closedStart') || '',
    closedEnd: params.get('closedEnd') || '',
    page: parseInt(params.get('page') || '1', 10),
    sortBy: params.get('sort') || 'priority',
    sortOrder: params.get('order') || 'desc',
  };
};

// 数组对比：判断两个无序数组是否包含相同元素
const sameSet = (a: string[], b: string[]) =>
  a.length === b.length && a.every((v) => b.includes(v));

// 创建时间起止统一使用原生 datetime-local：PC 为浏览器日期面板，移动端自动唤起系统原生滚轮选择器
//（tdesign DateTimePicker 拖动时存在跳变问题，已弃用）

// 创建时间边界值归一化为后端可比的秒级 datetime 串：
//   - 仅日期（YYYY-MM-DD）→ 起始 00:00:00 / 结束 23:59:59
//   - 含时分（YYYY-MM-DDTHH:mm）→ 起始补 :00、结束补 :59（含所选分钟）
const toBoundaryISO = (v: string, end: boolean): string => {
  if (!v) return '';
  if (v.length === 10) return end ? `${v}T23:59:59` : `${v}T00:00:00`;
  return end ? `${v}:59` : `${v}:00`;
};
// 安全调用原生 datetime-local 的 showPicker（点击整框即弹起选择器，浏览器不支持时静默回退到点击默认行为）
const openNativePicker = (el: HTMLInputElement | null) => {
  if (!el) return;
  const input = el as HTMLInputElement & { showPicker?: () => void };
  // 优先 showPicker（PC 浏览器面板 / Android 滚轮），不支持或被拒时退回 focus（iOS Safari 唤起原生滚轮）
  try {
    if (typeof input.showPicker === 'function') input.showPicker();
    else input.focus();
  } catch {
    try { input.focus(); } catch { /* 忽略 */ }
  }
};

// 日期区间字段（创建/解决/关单时间共用）：原生 datetime-local + 「起始/终止时间」占位覆盖层。
// startRef/endRef 由调用方持有，用于点击整框即唤起原生选择器（openNativePicker）。
type DateRangeFieldProps = {
  startValue: string;
  endValue: string;
  onStartChange: (v: string) => void;
  onEndChange: (v: string) => void;
  startRef: { current: HTMLInputElement | null };
  endRef: { current: HTMLInputElement | null };
};
function DateRangeField({ startValue, endValue, onStartChange, onEndChange, startRef, endRef }: DateRangeFieldProps) {
  return (
    <div className="filter-menu__date-range">
      <div className="filter-menu__date-field">
        <input
          ref={startRef}
          type="datetime-local"
          className={`filter-menu__date-input${startValue ? '' : ' filter-menu__date-input--empty'}`}
          step={60}
          value={startValue}
          max={endValue || undefined}
          onChange={(e) => onStartChange(e.target.value)}
          onClick={() => openNativePicker(startRef.current)}
        />
        {!startValue && <span className="filter-menu__date-placeholder">起始时间</span>}
      </div>
      <span className="filter-menu__date-sep">至</span>
      <div className="filter-menu__date-field">
        <input
          ref={endRef}
          type="datetime-local"
          className={`filter-menu__date-input${endValue ? '' : ' filter-menu__date-input--empty'}`}
          step={60}
          value={endValue}
          min={startValue || undefined}
          onChange={(e) => onEndChange(e.target.value)}
          onClick={() => openNativePicker(endRef.current)}
        />
        {!endValue && <span className="filter-menu__date-placeholder">终止时间</span>}
      </div>
    </div>
  );
}

// 将筛选状态同步到 URL 查询参数的工具函数
const buildFilterParams = (filter: {
  search: string; statusFilter: string[]; priorityFilter: string[];
  relevanceFilter: string; projectFilter: string; assigneeFilter: string; creatorFilter: string;
  createdStart: string; createdEnd: string; resolvedStart: string; resolvedEnd: string; closedStart: string; closedEnd: string; page: number; sortBy: string; sortOrder: string;
}) => {
  const params = new URLSearchParams();
  if (filter.search) params.set('q', filter.search);
  // 与默认值一致时省略 status 参数，保持 URL 简洁
  if (!sameSet(filter.statusFilter, DEFAULT_STATUS_VALUES)) {
    if (sameSet(filter.statusFilter, ALL_STATUS_VALUES)) {
      params.set('status', 'all');
    } else if (filter.statusFilter.length > 0) {
      params.set('status', filter.statusFilter.join(','));
    }
    // statusFilter 为空时不设置参数（等同于默认值，避免空 status=）
  }
  // 优先级：全选时省略；否则按逗号分隔输出（空数组不设置参数，等同于默认全选）
  if (filter.priorityFilter.length > 0 && !sameSet(filter.priorityFilter, ALL_PRIORITY_VALUES)) {
    params.set('priority', filter.priorityFilter.join(','));
  }
  if (filter.relevanceFilter !== 'mine') params.set('relevance', filter.relevanceFilter);
  // 项目过滤：非空时才输出（空 = 全部）
  if (filter.projectFilter) params.set('project', filter.projectFilter);
  // 处理人过滤：非空时才输出（空 = 全部）
  if (filter.assigneeFilter) params.set('assignee', filter.assigneeFilter);
  if (filter.creatorFilter) params.set('creator', filter.creatorFilter);
  // 创建时间过滤：非空时才输出（空 = 不限制）
  if (filter.createdStart) params.set('createdStart', filter.createdStart);
  if (filter.createdEnd) params.set('createdEnd', filter.createdEnd);
  if (filter.resolvedStart) params.set('resolvedStart', filter.resolvedStart);
  if (filter.resolvedEnd) params.set('resolvedEnd', filter.resolvedEnd);
  if (filter.closedStart) params.set('closedStart', filter.closedStart);
  if (filter.closedEnd) params.set('closedEnd', filter.closedEnd);
  if (filter.page > 1) params.set('page', String(filter.page));
  if (filter.sortBy !== 'priority') {
    params.set('sort', filter.sortBy);
    params.set('order', filter.sortOrder);
  }
  return params.toString();
};

// 马卡龙极简工单卡片：状态为唯一带色文字（蓝阶），优先级蓝阶色块，
// 头像统一灰底白字（无头像时）/ 圆形头像图片（有 avatar_resource_id 时），
// 信息层级靠字号与字重区分（参考 macaron-minimal-ui 设计）。
function TicketCard({ t, onOpen, avatarMap }: { t: Ticket; onOpen: (id: string) => void; avatarMap?: AvatarMap }) {
  const creator = t.created_by_name || t.created_by || '-';
  const assignee = t.assigned_to_name || t.assigned_to || '-';
  const participants = (t.participants || []).filter(Boolean);
  const creatorAvatarId = t.created_by ? avatarMap?.get(t.created_by) : undefined;
  const assigneeAvatarId = t.assigned_to ? avatarMap?.get(t.assigned_to) : undefined;
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
        <div className="task-card2__person" title={`发起人：${creator}`} aria-label={`发起人：${creator}`}>
          {creatorAvatarId ? (
            <img
              className="task-card2__avatar task-card2__avatar--img"
              src={avatarUrl(creatorAvatarId)}
              alt={creator}
            />
          ) : (
            <span className="task-card2__avatar">{creator.slice(0, 1).toUpperCase()}</span>
          )}
          <span className="task-card2__person-name">{creator}</span>
        </div>
        {participants.length > 0 && (
          <span className="task-card2__participants" title={`参与人：${participants.join('、')}`} aria-label={`参与人：${participants.join('、')}`}>
            {participants.slice(0, 3).map((p, i) => {
              const pid = avatarMap?.get(p);
              return pid ? (
                <img
                  key={`${p}-${i}`}
                  className="task-card2__participant task-card2__participant--img"
                  src={avatarUrl(pid)}
                  alt={p}
                />
              ) : (
                <span key={`${p}-${i}`} className="task-card2__participant">{p.slice(0, 1).toUpperCase()}</span>
              );
            })}
            {participants.length > 3 && (
              <span className="task-card2__participant task-card2__participant--overflow">+{participants.length - 3}</span>
            )}
          </span>
        )}
        <span className="task-card2__person-arrow">
          <ArrowRight size={14} strokeWidth={2} />
        </span>
        <div className="task-card2__person task-card2__person--assignee" title={`处理人：${assignee}`} aria-label={`处理人：${assignee}`}>
          <span className="task-card2__person-name">{assignee}</span>
          {assigneeAvatarId ? (
            <img
              className="task-card2__avatar task-card2__avatar--img task-card2__avatar--assignee"
              src={avatarUrl(assigneeAvatarId)}
              alt={assignee}
            />
          ) : (
            <span className="task-card2__avatar task-card2__avatar--assignee">{assignee.slice(0, 1).toUpperCase()}</span>
          )}
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

// 「待我处理」原先按日期分组的时间轴已移除：所有分类统一走扁平卡片列表，
// 排序完全由 fetchTickets 中的 sortBy/sortOrder 决定，快捷排序对所有分类生效。

// 内联下拉选择器：点击触发 chip 后在 chip 下方展开固定定位的下拉面板，
// 支持搜索过滤；选项首项约定为「全部」（value=''）。用于「项目」「处理人」单选过滤，
// 替代原底部弹层（无需多一层弹窗）。面板通过 portal 渲染到 body 以绕开 chip 容器的 overflow 裁剪。
function ChipDropdown({
  label, active, options, selectedValue, searchPlaceholder, emptyText, onSelect,
}: {
  label: string;
  active: boolean;
  options: Array<{ value: string; label: string }>;
  selectedValue: string;
  searchPlaceholder: string;
  emptyText: string;
  onSelect: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [coords, setCoords] = useState<{ top: number; left: number; minWidth: number; maxHeight: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    return kw ? options.filter((o) => o.label.toLowerCase().includes(kw)) : options;
  }, [options, keyword]);

  const openPanel = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const gap = 4;
    const maxH = 320;
    const minWidth = Math.max(r.width, 200);
    const left = Math.min(r.left, window.innerWidth - minWidth - 8);
    const spaceBelow = window.innerHeight - r.bottom - gap;
    const spaceAbove = r.top - gap;
    // 触发 chip 下方空间不足且上方更宽裕时向上展开，避免低部选项超出视口不可达
    let top: number;
    let maxHeight: number;
    if (spaceBelow >= maxH || spaceBelow >= spaceAbove) {
      top = r.bottom + gap;
      maxHeight = Math.min(maxH, spaceBelow);
    } else {
      top = Math.max(gap, r.top - gap - maxH);
      maxHeight = Math.min(maxH, spaceAbove);
    }
    setCoords({ top, left, minWidth, maxHeight });
    setKeyword('');
    setOpen(true);
  }, []);

  // 面板展开时聚焦搜索框（延迟一帧，避免与打开面板的 click 冲突）
  useEffect(() => {
    if (!open) return;
    const id = setTimeout(() => searchInputRef.current?.focus(), 0);
    return () => clearTimeout(id);
  }, [open]);

  // 点击外部 / Esc / 滚动 / 窗口尺寸变化 关闭面板
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t)) return;
      if (panelRef.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    const close = () => setOpen(false);
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [open]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={`tasks-view__filter-chip tasks-view__filter-chip--dropdown ${(active || open) ? 'is-active' : ''}`}
        onClick={() => { if (open) setOpen(false); else openPanel(); }}
      >
        <span className="tasks-view__filter-chip-value">{label}</span>
        <ChevronDown size={12} strokeWidth={2} />
      </button>
      {open && coords && createPortal(
        <div ref={panelRef} className="chip-dropdown" style={{ top: coords.top, left: coords.left, minWidth: coords.minWidth, maxHeight: coords.maxHeight }}>
          <div className="chip-dropdown__search">
            <Search size={14} strokeWidth={2} />
            <input
              ref={searchInputRef}
              className="chip-dropdown__search-input"
              placeholder={searchPlaceholder}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
          </div>
          <div className="chip-dropdown__list">
            {filtered.length === 0 ? (
              <div className="chip-dropdown__empty">{emptyText}</div>
            ) : (
              filtered.map((o) => (
                <div
                  key={o.value || '__all__'}
                  className={`chip-dropdown__item ${selectedValue === o.value ? 'is-selected' : ''}`}
                  onClick={() => { onSelect(o.value); setOpen(false); }}
                >
                  {o.label}
                </div>
              ))
            )}
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}

// 筛选弹窗内的内联下拉：点击触发行就地展开「搜索框 + 选项列表」（首项「全部」）。
// 不使用 portal / fixed，跟随筛选弹窗正常流，避免与底部弹层 z-index 冲突。
// 选项可能很多（项目 / 用户），用纵向列表而非胶囊，配合搜索收敛。
function FilterMenuDropdown({
  label, options, selectedValue, searchPlaceholder, emptyText, onSelect,
}: {
  label: string;
  options: Array<{ value: string; label: string }>;
  selectedValue: string;
  searchPlaceholder: string;
  emptyText: string;
  onSelect: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [keyword, setKeyword] = useState('');

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    return kw ? options.filter((o) => o.label.toLowerCase().includes(kw)) : options;
  }, [options, keyword]);

  return (
    <div className="filter-menu__inline-dropdown">
      <button
        type="button"
        className="filter-menu__dropdown-trigger"
        onClick={() => { setKeyword(''); setOpen((v) => !v); }}
      >
        <span className={selectedValue ? 'filter-menu__dropdown-value' : 'filter-menu__dropdown-placeholder'}>
          {label}
        </span>
        <ChevronDown size={14} strokeWidth={2} />
      </button>
      {open && (
        <div className="filter-menu__inline-panel">
          <div className="filter-menu__inline-search">
            <Search size={14} strokeWidth={2} />
            <input
              className="filter-menu__search-input"
              placeholder={searchPlaceholder}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
          </div>
          <div className="filter-menu__inline-list">
            {filtered.length === 0 ? (
              <div className="filter-menu__inline-empty">{emptyText}</div>
            ) : (
              filtered.map((o) => (
                <button
                  key={o.value || '__all__'}
                  type="button"
                  className={`filter-menu__inline-item ${selectedValue === o.value ? 'is-selected' : ''}`}
                  onClick={() => { onSelect(o.value); setOpen(false); }}
                >
                  {o.label}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function TasksView() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
  const adminRequest = useMemo(() => createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin'), []);

  const {
    tasksRefreshKey, ticketDraft, consumeTicketDraft, refreshTasks,
  } = useWorkbenchStore();

  const { username, userId, hasPermission, projectIds } = useAuthStore();
  const canManageTasks = hasPermission('frontend:develop');

  // 状态/优先级筛选 chip 栏横向滚动（PC 桌面端滚轮/拖拽横滑，移动端原生触摸滑动）
  const filterChipsRef = useRef<HTMLDivElement>(null);
  useHorizontalScroll(filterChipsRef);
  const canViewAllTasks = hasPermission('frontend:task:all');

  // 从 URL 初始化筛选状态
  const initialFilter = useRef(parseFilterFromUrl(searchParams));

  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState(() => initialFilter.current.search);
  const [statusFilter, setStatusFilter] = useState(() => initialFilter.current.statusFilter);
  const [priorityFilter, setPriorityFilter] = useState<string[]>(() => initialFilter.current.priorityFilter);
  const [relevanceFilter, setRelevanceFilter] = useState(() => initialFilter.current.relevanceFilter);
  // 项目过滤：空字符串 = 「全部」；否则为选中项目的 id
  const [projectFilter, setProjectFilter] = useState(() => initialFilter.current.projectFilter);
  // 当前用户关联的项目列表（用于项目过滤下拉）
  const [myProjects, setMyProjects] = useState<ProjectItem[]>([]);
  // 处理人过滤：空字符串 = 「全部」；否则为选中处理人的 username
  const [assigneeFilter, setAssigneeFilter] = useState(() => initialFilter.current.assigneeFilter);
  // 创建人过滤：空字符串 = 「全部」；否则为选中创建人的 username
  const [creatorFilter, setCreatorFilter] = useState(() => initialFilter.current.creatorFilter);
  // 处理人候选列表（含 username 与 name，用于处理人过滤下拉）
  const [assignees, setAssignees] = useState<Array<{ username: string; name?: string }>>([]);
  // 时间过滤：空字符串 = 不限制；值为 YYYY-MM-DDTHH:mm（精确到分钟）
  const [createdStart, setCreatedStart] = useState(() => initialFilter.current.createdStart);
  const [createdEnd, setCreatedEnd] = useState(() => initialFilter.current.createdEnd);
  const [resolvedStart, setResolvedStart] = useState(() => initialFilter.current.resolvedStart);
  const [resolvedEnd, setResolvedEnd] = useState(() => initialFilter.current.resolvedEnd);
  const [closedStart, setClosedStart] = useState(() => initialFilter.current.closedStart);
  const [closedEnd, setClosedEnd] = useState(() => initialFilter.current.closedEnd);
  // datetime-local 输入框引用（用于点击整框即唤起原生选择器）
  const startDtInputRef = useRef<HTMLInputElement | null>(null);
  const endDtInputRef = useRef<HTMLInputElement | null>(null);
  const resolvedStartDtInputRef = useRef<HTMLInputElement | null>(null);
  const resolvedEndDtInputRef = useRef<HTMLInputElement | null>(null);
  const closedStartDtInputRef = useRef<HTMLInputElement | null>(null);
  const closedEndDtInputRef = useRef<HTMLInputElement | null>(null);
  const [showFilterMenu, setShowFilterMenu] = useState(false);
  // 筛选弹窗草稿：弹窗内选择先写入草稿，点「确定」才提交生效；关闭（遮罩/返回）则丢弃。
  const [draft, setDraft] = useState<{
    relevance: string; status: string[]; project: string; assignee: string; creator: string; priority: string[];
    createdStart: string; createdEnd: string;
    resolvedStart: string; resolvedEnd: string;
    closedStart: string; closedEnd: string;
  } | null>(null);
  const [page, setPage] = useState(() => initialFilter.current.page);
  const [total, setTotal] = useState(0);
  const [sortBy, setSortBy] = useState(() => initialFilter.current.sortBy);
  const [sortOrder, setSortOrder] = useState(() => initialFilter.current.sortOrder);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [creatingTask, setCreatingTask] = useState(false);
  const [syncing, setSyncing] = useState(false);

  // username / user_id → avatar_resource_id 查找表：用于工单卡片创建人/处理人头像渲染。
  // 缺失权限（backend:user:base:read）或网络失败时静默回退为首字母头像。
  // 同一次请求复用于「处理人过滤」下拉候选（仅需 username + name 字段）。
  const [avatarMap, setAvatarMap] = useState<AvatarMap>(new Map());
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await adminRequest<Array<{
          username?: string; id?: string; name?: string | null; avatar_resource_id?: number | null;
        }>>('/users/?skip=0&limit=1000');
        if (cancelled) return;
        const m: AvatarMap = new Map();
        const list: Array<{ username: string; name?: string }> = [];
        for (const u of data || []) {
          if (u.username) {
            list.push({ username: u.username, name: u.name || undefined });
          }
          if (!u.avatar_resource_id) continue;
          if (u.username) m.set(u.username, u.avatar_resource_id);
          if (u.id) m.set(u.id, u.avatar_resource_id);
        }
        setAvatarMap(m);
        setAssignees(list);
      } catch {
        // 无权限或失败：保持首字母回退、处理人下拉仅展示「全部」
      }
    })();
    return () => { cancelled = true; };
  }, [adminRequest]);

  // 拉取当前用户关联的项目列表（GET /api/admin/projects/me），用于项目过滤下拉。
  // 失败时静默回退为空列表（项目筛选仅展示「全部」）。
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await getMyProjects();
        if (cancelled) return;
        setMyProjects(list);
      } catch {
        // 无权限或失败：项目下拉仅保留「全部」
      }
    })();
    return () => { cancelled = true; };
  }, []);
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
      const filters: TicketFilterCondition[] = buildRelevanceFilters(relevanceKey, userId || username, projectIds);

      if (search) {
        const keyword = search.trim();
        const searchConditions: TicketFilterCondition[] = [
          { field: 'title', op: 'contains', value: keyword },
        ];
        // 纯数字关键词按工单编号精确查找（卡片展示的 #编号），非数字仍走标题模糊搜索
        if (/^\d+$/.test(keyword)) {
          searchConditions.push({ field: 'id', op: 'eq', value: Number(keyword) });
        }
        filters.push({ or: searchConditions });
      }
      // 任务状态多选过滤：未全选时按 in 操作过滤，全选则不施加状态条件
      if (statusFilter.length > 0 && !sameSet(statusFilter, ALL_STATUS_VALUES)) {
        filters.push({ field: 'status', op: 'in', value: statusFilter });
      }
      // 优先级多选过滤：未全选时按 in 操作过滤，全选则不施加优先级条件
      if (priorityFilter.length > 0 && !sameSet(priorityFilter, ALL_PRIORITY_VALUES)) {
        filters.push({ field: 'priority', op: 'in', value: priorityFilter });
      }
      // 项目过滤：选中具体项目时按 projectId 精确过滤（空 = 全部，不施加条件）
      if (projectFilter) {
        filters.push({ field: 'projectId', op: 'eq', value: projectFilter });
      }
      // 处理人过滤：选中具体处理人时按 assignedTo 精确过滤（空 = 全部，不施加条件）
      // 后端对 assignedTo 双键解析（username / users.id 都认）
      if (assigneeFilter) {
        filters.push({ field: 'assignedTo', op: 'eq', value: assigneeFilter });
      }
      if (creatorFilter) {
        filters.push({ field: 'createdBy', op: 'eq', value: creatorFilter });
      }
      // 创建时间过滤：精确到分钟。起始补 :00（含所选分钟）、结束补 :59（含所选分钟）。
      // 空值不施加条件；值格式 YYYY-MM-DDTHH:mm（兼容旧 YYYY-MM-DD）。
      if (createdStart) {
        filters.push({ field: 'createdAt', op: 'ge', value: toBoundaryISO(createdStart, false) });
      }
      if (createdEnd) {
        filters.push({ field: 'createdAt', op: 'le', value: toBoundaryISO(createdEnd, true) });
      }
      // 解决时间过滤（resolved_at）
      if (resolvedStart) {
        filters.push({ field: 'resolvedAt', op: 'ge', value: toBoundaryISO(resolvedStart, false) });
      }
      if (resolvedEnd) {
        filters.push({ field: 'resolvedAt', op: 'le', value: toBoundaryISO(resolvedEnd, true) });
      }
      // 关单时间过滤（closed_at）
      if (closedStart) {
        filters.push({ field: 'closedAt', op: 'ge', value: toBoundaryISO(closedStart, false) });
      }
      if (closedEnd) {
        filters.push({ field: 'closedAt', op: 'le', value: toBoundaryISO(closedEnd, true) });
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
  }, [page, search, statusFilter, priorityFilter, relevanceFilter, projectFilter, assigneeFilter, creatorFilter, createdStart, createdEnd, resolvedStart, resolvedEnd, closedStart, closedEnd, username, userId, projectIds, sortBy, sortOrder, canViewAllTasks]);

  fetchTicketsRef.current = fetchTickets;

  // 筛选状态变化时同步到 URL
  useEffect(() => {
    const newParams = buildFilterParams({
      search, statusFilter, priorityFilter,
      relevanceFilter, projectFilter, assigneeFilter, creatorFilter, createdStart, createdEnd, resolvedStart, resolvedEnd, closedStart, closedEnd, page, sortBy, sortOrder,
    });
    if (newParams !== searchParams.toString()) {
      setSearchParams(newParams, { replace: true });
    }
  }, [search, statusFilter, priorityFilter, relevanceFilter, projectFilter, assigneeFilter, creatorFilter, createdStart, createdEnd, resolvedStart, resolvedEnd, closedStart, closedEnd, page, sortBy, sortOrder]);

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

  

  // 「全部」仅在用户拥有 frontend:task:all 权限时展示，用于查看全量工单。
  const relevanceOptions = useMemo(() => {
    const base = [
      { value: 'all', label: '项目相关' },
      { value: 'mine', label: '待我处理' },
      { value: 'related', label: '与我相关' },
    ];
    return canViewAllTasks
      ? [{ value: 'global', label: '全部' }, ...base]
      : base;
  }, [canViewAllTasks]);

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
                filters: buildRelevanceFilters(key, userId || username, projectIds),
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
  }, [relevanceOptions, username, userId, projectIds, canViewAllTasks]);
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

  // 项目过滤：单选切换（传项目 id；空字符串 = 「全部」）。切换后回到第一页。
  const handleProjectChange = (value: string) => {
    setProjectFilter(value);
    setPage(1);
  };

  // 当前选中项目的展示名（无选中或 id 不在名下项目列表时回退为「全部」）
  const selectedProjectLabel = useMemo(() => {
    if (!projectFilter) return '全部';
    const p = myProjects.find((it) => it.id === projectFilter);
    return p ? (p.name || p.project_code || projectFilter) : '全部';
  }, [projectFilter, myProjects]);

  // 项目下拉选项：首项「全部」+ 名下项目（名称优先，回退编码）
  const projectOptions = useMemo<Array<{ value: string; label: string }>>(
    () => [
      { value: '', label: '全部' },
      ...myProjects.map((p) => ({ value: p.id, label: p.name || p.project_code || p.id })),
    ],
    [myProjects],
  );

  // 项目列表加载完成前，URL 中的 projectFilter 可能指向已失效的项目；
  // 列表就绪后校验一次，命中不到则回退为「全部」，避免过滤出空结果。
  useEffect(() => {
    if (!projectFilter || myProjects.length === 0) return;
    const exists = myProjects.some((p) => p.id === projectFilter);
    if (!exists) setProjectFilter('');
  }, [myProjects, projectFilter]);

  // 处理人过滤：单选切换（传 username；空字符串 = 「全部」）。切换后回到第一页。
  const handleAssigneeChange = (value: string) => {
    setAssigneeFilter(value);
    setPage(1);
  };

  // 当前选中处理人的展示名（无选中或 username 不在候选列表时回退为「全部」）
  const selectedAssigneeLabel = useMemo(() => {
    if (!assigneeFilter) return '全部';
    const u = assignees.find((it) => it.username === assigneeFilter);
    return u ? (u.name || u.username) : assigneeFilter;
  }, [assigneeFilter, assignees]);

  // 处理人下拉选项：首项「全部」+ 候选用户（姓名优先，回退账号）
  const assigneeOptions = useMemo<Array<{ value: string; label: string }>>(
    () => [
      { value: '', label: '全部' },
      ...assignees.map((u) => ({ value: u.username, label: u.name || u.username })),
    ],
    [assignees],
  );

  // 处理人列表加载完成前，URL 中的 assigneeFilter 可能指向已失效的用户；
  // 列表就绪后校验一次，命中不到则回退为「全部」，避免过滤出空结果。
  useEffect(() => {
    if (!assigneeFilter || assignees.length === 0) return;
    const exists = assignees.some((u) => u.username === assigneeFilter);
    if (!exists) setAssigneeFilter('');
  }, [assignees, assigneeFilter]);

  // 创建人过滤：单选切换（传 username；空 = 「全部」），切换后回到第一页
  const handleCreatorChange = (value: string) => {
    setCreatorFilter(value);
    setPage(1);
  };
  // 当前选中创建人的展示名（无选中或 username 不在候选列表时回退为「全部」）
  const selectedCreatorLabel = useMemo(() => {
    if (!creatorFilter) return '全部';
    const u = assignees.find((it) => it.username === creatorFilter);
    return u ? (u.name || u.username) : creatorFilter;
  }, [creatorFilter, assignees]);
  // 创建人列表就绪后校验 URL 中的 creatorFilter，命中不到则回退为「全部」
  useEffect(() => {
    if (!creatorFilter || assignees.length === 0) return;
    const exists = assignees.some((u) => u.username === creatorFilter);
    if (!exists) setCreatorFilter('');
  }, [assignees, creatorFilter]);

  // 多选：单个状态点击切换选中/取消；'all' 表示全部选中
  const handleStatusToggle = (value: string) => {
    setStatusFilter((prev) => {
      if (value === 'all') {
        return sameSet(prev, ALL_STATUS_VALUES) ? [...DEFAULT_STATUS_VALUES] : [...ALL_STATUS_VALUES];
      }
      if (prev.includes(value)) {
        const next = prev.filter((v) => v !== value);
        return next.length > 0 ? next : [...DEFAULT_STATUS_VALUES]; // 至少保留默认集
      }
      return [...prev, value];
    });
    setPage(1);
  };

  // 多选：单个优先级点击切换选中/取消；'all' 表示全选/取消全选；空时回退为全选
  const handlePriorityToggle = (value: string) => {
    setPriorityFilter((prev) => {
      if (value === 'all') {
        return sameSet(prev, ALL_PRIORITY_VALUES) ? [] : [...ALL_PRIORITY_VALUES];
      }
      if (prev.includes(value)) {
        const next = prev.filter((v) => v !== value);
        return next.length > 0 ? next : [...ALL_PRIORITY_VALUES]; // 至少保留一项
      }
      return [...prev, value];
    });
    setPage(1);
  };

  // 打开筛选弹窗：以当前生效的过滤值初始化草稿，弹窗内改动只作用于草稿
  const openFilterMenu = () => {
    setDraft({
      relevance: relevanceFilter,
      status: [...statusFilter],
      project: projectFilter,
      assignee: assigneeFilter,
      creator: creatorFilter,
      priority: [...priorityFilter],
      createdStart,
      createdEnd,
      resolvedStart,
      resolvedEnd,
      closedStart,
      closedEnd,
    });
    setShowFilterMenu(true);
  };
  // 草稿字段更新（单选类）
  const setDraftField = (patch: Partial<{
    relevance: string; status: string[]; project: string; assignee: string; creator: string; priority: string[];
    createdStart: string; createdEnd: string;
    resolvedStart: string; resolvedEnd: string;
    closedStart: string; closedEnd: string;
  }>) => setDraft((d) => (d ? { ...d, ...patch } : d));
  const draftRelevanceChange = (value: string) => setDraftField({ relevance: value });
  const draftProjectChange = (value: string) => setDraftField({ project: value });
  const draftAssigneeChange = (value: string) => setDraftField({ assignee: value });
  const draftCreatorChange = (value: string) => setDraftField({ creator: value });
  const draftSetCreatedStart = (value: string) => setDraftField({ createdStart: value });
  const draftSetCreatedEnd = (value: string) => setDraftField({ createdEnd: value });
  const draftSetResolvedStart = (value: string) => setDraftField({ resolvedStart: value });
  const draftSetResolvedEnd = (value: string) => setDraftField({ resolvedEnd: value });
  const draftSetClosedStart = (value: string) => setDraftField({ closedStart: value });
  const draftSetClosedEnd = (value: string) => setDraftField({ closedEnd: value });
  // 草稿任务状态切换（与 handleStatusToggle 同逻辑，但作用于草稿）
  const draftStatusToggle = (value: string) => setDraft((d) => {
    if (!d) return d;
    if (value === 'all') {
      return { ...d, status: sameSet(d.status, ALL_STATUS_VALUES) ? [...DEFAULT_STATUS_VALUES] : [...ALL_STATUS_VALUES] };
    }
    if (d.status.includes(value)) {
      const next = d.status.filter((v) => v !== value);
      return { ...d, status: next.length > 0 ? next : [...DEFAULT_STATUS_VALUES] };
    }
    return { ...d, status: [...d.status, value] };
  });
  // 草稿优先级切换（与 handlePriorityToggle 同逻辑，但作用于草稿）
  const draftPriorityToggle = (value: string) => setDraft((d) => {
    if (!d) return d;
    if (value === 'all') {
      return { ...d, priority: sameSet(d.priority, ALL_PRIORITY_VALUES) ? [] : [...ALL_PRIORITY_VALUES] };
    }
    if (d.priority.includes(value)) {
      const next = d.priority.filter((v) => v !== value);
      return { ...d, priority: next.length > 0 ? next : [...ALL_PRIORITY_VALUES] };
    }
    return { ...d, priority: [...d.priority, value] };
  });
  // 清空草稿（弹窗内「清空选择」）：相关性回默认、状态回默认集（新建/进行中/已挂起/已解决）、
  // 优先级回「全部」、项目/处理人回「全部」、创建时间清空。仅作用于草稿，未点「确定」前不生效。
  const draftClear = () => setDraft({
    relevance: 'mine',
    status: [...DEFAULT_STATUS_VALUES],
    project: '',
    assignee: '',
    creator: '',
    priority: [...ALL_PRIORITY_VALUES],
    createdStart: '',
    createdEnd: '',
    resolvedStart: '',
    resolvedEnd: '',
    closedStart: '',
    closedEnd: '',
  });
  // 提交草稿生效：将草稿写入正式过滤状态并关闭弹窗（触发 fetchTickets 重新拉取）
  const commitDraft = () => {
    if (!draft) { setShowFilterMenu(false); return; }
    setRelevanceFilter(draft.relevance);
    setStatusFilter(draft.status);
    setProjectFilter(draft.project);
    setAssigneeFilter(draft.assignee);
    setCreatorFilter(draft.creator);
    setPriorityFilter(draft.priority);
    setCreatedStart(draft.createdStart);
    setCreatedEnd(draft.createdEnd);
    setResolvedStart(draft.resolvedStart);
    setResolvedEnd(draft.resolvedEnd);
    setClosedStart(draft.closedStart);
    setClosedEnd(draft.closedEnd);
    setPage(1);
    setShowFilterMenu(false);
  };

  // 弹窗内展示用草稿值（弹窗未打开或 draft 为空时回退到生效值，UI 不致空）
  const dRelevance = draft?.relevance ?? relevanceFilter;
  const dStatus = draft?.status ?? statusFilter;
  const dProject = draft?.project ?? projectFilter;
  const dAssignee = draft?.assignee ?? assigneeFilter;
  const dCreator = draft?.creator ?? creatorFilter;
  const dPriority = draft?.priority ?? priorityFilter;
  const dCreatedStart = draft?.createdStart ?? createdStart;
  const dCreatedEnd = draft?.createdEnd ?? createdEnd;
  const dResolvedStart = draft?.resolvedStart ?? resolvedStart;
  const dResolvedEnd = draft?.resolvedEnd ?? resolvedEnd;
  const dClosedStart = draft?.closedStart ?? closedStart;
  const dClosedEnd = draft?.closedEnd ?? closedEnd;
  // 弹窗内项目/处理人展示名（基于草稿值解析，未选回退「全部」）
  const popupProjectLabel = useMemo(() => {
    if (!dProject) return '全部';
    const p = myProjects.find((it) => it.id === dProject);
    return p ? (p.name || p.project_code || dProject) : '全部';
  }, [dProject, myProjects]);
  const popupAssigneeLabel = useMemo(() => {
    if (!dAssignee) return '全部';
    const u = assignees.find((it) => it.username === dAssignee);
    return u ? (u.name || u.username) : dAssignee;
  }, [dAssignee, assignees]);
  const popupCreatorLabel = useMemo(() => {
    if (!dCreator) return '全部';
    const u = assignees.find((it) => it.username === dCreator);
    return u ? (u.name || u.username) : dCreator;
  }, [dCreator, assignees]);

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
      <SubscriptionReminder username={username} />
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
            <button
              className="tasks-view__sync-btn"
              onClick={() => navigate('/module-tree')}
              aria-label="责任模块树"
              title="责任模块树"
            >
              🌳 模块树
            </button>
            <UserAvatarMenu />
          </div>
        }
      />

      {/* 筛选区（搜索 + 状态 tab）：固定在滚动区外，不随列表滚动上移（对齐历史工单） */}
      <div className="tasks-view__filters">
          <div className="tasks-view__search-row">
            <div className="tasks-view__search-card">
              <Search size={16} strokeWidth={2} />
              <input
                className="tasks-search"
                placeholder="搜索工单（支持编号/标题）…"
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
            <div ref={filterChipsRef} className="tasks-view__filter-chips">
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
              <span className="tasks-view__filter-divider" aria-hidden="true" />
              <button
                key="status_all"
                className={`tasks-view__filter-chip ${sameSet(statusFilter, ALL_STATUS_VALUES) ? 'is-active' : ''}`}
                onClick={() => { handleStatusToggle('all'); }}
              >
                全部
              </button>
              {statusOptions.map((option) => (
                <button
                  key={option.value}
                  className={`tasks-view__filter-chip ${statusFilter.includes(option.value) ? 'is-active' : ''}`}
                  onClick={() => { handleStatusToggle(option.value); }}
                >
                  {option.label}
                </button>
              ))}
              <span className="tasks-view__filter-divider" aria-hidden="true" />
              {/* 项目过滤：内联下拉（首项「全部」+ 可搜索） */}
              <ChipDropdown
                label={selectedProjectLabel}
                active={!!projectFilter}
                options={projectOptions}
                selectedValue={projectFilter}
                searchPlaceholder="搜索项目名称 / 编码…"
                emptyText="未找到匹配项目"
                onSelect={handleProjectChange}
              />
              <span className="tasks-view__filter-divider" aria-hidden="true" />
              {/* 处理人过滤：内联下拉（首项「全部」+ 可搜索） */}
              <ChipDropdown
                label={selectedAssigneeLabel}
                active={!!assigneeFilter}
                options={assigneeOptions}
                selectedValue={assigneeFilter}
                searchPlaceholder="搜索姓名 / 账号…"
                emptyText="未找到匹配处理人"
                onSelect={handleAssigneeChange}
              />
              <span className="tasks-view__filter-divider" aria-hidden="true" />
              {/* 创建人过滤：内联下拉（首项「全部」+ 可搜索） */}
              <ChipDropdown
                label={selectedCreatorLabel}
                active={!!creatorFilter}
                options={assigneeOptions}
                selectedValue={creatorFilter}
                searchPlaceholder="搜索姓名 / 账号…"
                emptyText="未找到匹配创建人"
                onSelect={handleCreatorChange}
              />
              <span className="tasks-view__filter-divider" aria-hidden="true" />
              <button
                key="priority_all"
                className={`tasks-view__filter-chip ${sameSet(priorityFilter, ALL_PRIORITY_VALUES) ? 'is-active' : ''}`}
                onClick={() => { handlePriorityToggle('all'); }}
              >
                全部
              </button>
              {priorityOptions.map((option) => (
                <button
                  key={option.value}
                  className={`tasks-view__filter-chip ${priorityFilter.includes(option.value) ? 'is-active' : ''}`}
                  onClick={() => { handlePriorityToggle(option.value); }}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <button className="tasks-filter-btn" onClick={openFilterMenu}>
              <SlidersHorizontal size={14} strokeWidth={2} />
              <span>筛选</span>
            </button>
          </div>
        </div>

      {/* 工单卡片列表：唯一滚动区 */}
      <div className="tasks-list-section">
        <div className="tasks-cards">
          {loading ? <Loading text="加载中…" /> : tickets.length === 0 ? (
            <div className="tasks-empty">暂无工单</div>
          ) : (
            tickets.map((t) => (
              <TicketCard key={t.id} t={t} onOpen={openDetail} avatarMap={avatarMap} />
            ))
          )}
          <Pagination current={page} total={total} pageSize={pageSize} onChange={setPage} />
        </div>
      </div>

      <Popup visible={showFilterMenu} onClose={() => setShowFilterMenu(false)} placement="bottom" showOverlay>
        <div className="filter-menu">
          <div className="filter-menu__body">
          <div className="filter-menu__section">
            <h4 className="filter-menu__title">相关性</h4>
            <div className="filter-menu__items">
              {relevanceOptions.map((option) => (
                <button
                  key={option.value}
                  className={`filter-menu__item ${dRelevance === option.value ? 'is-active' : ''}`}
                  onClick={() => { draftRelevanceChange(option.value); }}
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
            <h4 className="filter-menu__title">任务状态（多选）</h4>
            <div className="filter-menu__items">
              <button
                key="status_all"
                className={`filter-menu__item ${sameSet(dStatus, ALL_STATUS_VALUES) ? 'is-active' : ''}`}
                onClick={() => { draftStatusToggle('all'); }}
              >
                全部
              </button>
              {statusOptions.map((option) => (
                <button
                  key={option.value}
                  className={`filter-menu__item ${dStatus.includes(option.value) ? 'is-active' : ''}`}
                  onClick={() => { draftStatusToggle(option.value); }}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
          <div className="filter-menu__divider"></div>
          <div className="filter-menu__section">
            <h4 className="filter-menu__title">项目</h4>
            <FilterMenuDropdown
              label={popupProjectLabel}
              options={projectOptions}
              selectedValue={dProject}
              searchPlaceholder="搜索项目名称 / 编码…"
              emptyText="未找到匹配项目"
              onSelect={draftProjectChange}
            />
          </div>
          <div className="filter-menu__divider"></div>
          <div className="filter-menu__section">
            <h4 className="filter-menu__title">处理人</h4>
            <FilterMenuDropdown
              label={popupAssigneeLabel}
              options={assigneeOptions}
              selectedValue={dAssignee}
              searchPlaceholder="搜索姓名 / 账号…"
              emptyText="未找到匹配处理人"
              onSelect={draftAssigneeChange}
            />
          </div>
          <div className="filter-menu__divider"></div>
          <div className="filter-menu__section">
            <h4 className="filter-menu__title">创建人</h4>
            <FilterMenuDropdown
              label={popupCreatorLabel}
              options={assigneeOptions}
              selectedValue={dCreator}
              searchPlaceholder="搜索姓名 / 账号…"
              emptyText="未找到匹配创建人"
              onSelect={draftCreatorChange}
            />
          </div>
          <div className="filter-menu__divider"></div>
          <div className="filter-menu__section">
            <h4 className="filter-menu__title">创建时间（选择时间范围）</h4>
            <DateRangeField
              startValue={dCreatedStart}
              endValue={dCreatedEnd}
              onStartChange={draftSetCreatedStart}
              onEndChange={draftSetCreatedEnd}
              startRef={startDtInputRef}
              endRef={endDtInputRef}
            />
          </div>
          <div className="filter-menu__divider"></div>
          <div className="filter-menu__section">
            <h4 className="filter-menu__title">解决时间（选择时间范围）</h4>
            <DateRangeField
              startValue={dResolvedStart}
              endValue={dResolvedEnd}
              onStartChange={draftSetResolvedStart}
              onEndChange={draftSetResolvedEnd}
              startRef={resolvedStartDtInputRef}
              endRef={resolvedEndDtInputRef}
            />
          </div>
          <div className="filter-menu__divider"></div>
          <div className="filter-menu__section">
            <h4 className="filter-menu__title">关单时间（选择时间范围）</h4>
            <DateRangeField
              startValue={dClosedStart}
              endValue={dClosedEnd}
              onStartChange={draftSetClosedStart}
              onEndChange={draftSetClosedEnd}
              startRef={closedStartDtInputRef}
              endRef={closedEndDtInputRef}
            />
          </div>
          <div className="filter-menu__divider"></div>
          <div className="filter-menu__section">
            <h4 className="filter-menu__title">优先级（多选）</h4>
            <div className="filter-menu__items">
              <button
                key="priority_all"
                className={`filter-menu__item ${sameSet(dPriority, ALL_PRIORITY_VALUES) ? 'is-active' : ''}`}
                onClick={() => { draftPriorityToggle('all'); }}
              >
                全部
              </button>
              {priorityOptions.map((option) => (
                <button
                  key={option.value}
                  className={`filter-menu__item ${dPriority.includes(option.value) ? 'is-active' : ''}`}
                  onClick={() => { draftPriorityToggle(option.value); }}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
          </div>
          {/* 底部操作按钮：清空选择 / 确定（并排） */}
          <div className="filter-menu__actions">
            <button
              type="button"
              className="filter-menu__action filter-menu__action--ghost"
              onClick={draftClear}
            >
              清空选择
            </button>
            <button
              type="button"
              className="filter-menu__action filter-menu__action--primary"
              onClick={commitDraft}
            >
              确定
            </button>
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
