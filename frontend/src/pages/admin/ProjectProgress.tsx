// 项目进度管理 —— 聚合项目列表 + 风险状态，侧重视觉化项目进度
// 样式参考 macaron projects.index 页：双指标卡 + 卡片搜索框 + surface-card 项目卡
// （阶段标签 + 进度条 + 四格小指标），保留长按删除与看板筛选下钻。
// 卡片右上角展示该项目工单数（后端 ticket_count，来自 /projects/ 与 /projects/me）。
// 2026-09-22 增：长按可置顶（个人置顶，后端 project_pin 表按人隔离）、
// 列表排序（默认 / 按核算期 / 按工单数）与筛选（项目经理 / 对接人 / 地区 / 项目阶段 / AGV数量，
// 每个维度是一个下拉框；项目经理与对接人的候选值多且是自由文本，框内可模糊搜索，
// 地区 / 项目阶段 / AGV 取值固定且少，直接选）。
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Toast, Loading, Popup, Dialog } from 'tdesign-mobile-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { aiGet } from '@/api/ai';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { currentYearMonth, normalizeSettlementPeriod } from '@/shared/utils/settlement';
import { calcLifecycleProgress, PROJECT_ABORTED } from '@/shared/utils/projectLifecycle';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';
import { MacStat } from '@/shared/components/macaronBits';
import { MacSearch, MacFolderClosed, MacCheck, MacChevronDown, MacX } from '@/shared/components/macaronIcons';

interface TaskExecutionStats {
  // 项目没有任何采集数据时（后端返回 NO_TASK_EXECUTION_STATS）这些字段为 null，卡片显示「-」
  total_tasks: number | null;
  finished_tasks: number | null;
  completion_rate: number | null;
  // 这组统计对应的数据日期（后端取该项目已导入的最新一天，YYYY-MM-DD）；
  // 各项目导入频率不同，可能是一周前甚至更早，卡片左侧按此日期标注
  data_date?: string | null;
}

interface ProjectItem {
  id: string;
  project_code: string;
  system_id?: string | null;
  name: string;
  status: string;
  contact_person: string;
  project_manager?: string | null;
  project_region?: string | null;      // 项目区域/地点，来自企业微信同步（如「大陆(China Mainland)」）
  total_vehicle_count?: number | null; // 总车数（AGV 数量），来自企业微信同步
  risks: number;
  project_summary: string;
  task_execution_status: string;
  ticket_count?: number | null; // 该项目工单数（tasks 表，口径同仪表盘「总工单数」）
  task_execution_stats?: TaskExecutionStats | null;
  latest_manual_switch_count?: number | null;
  settlement_period?: string | null; // 业绩核算期，手工填写常见 YYYYMM（如 202608），兼容 YYYY-MM，来自企业微信同步
  deployment_date?: string | null;   // 部署时间
  final_delivery_date?: string | null; // 最终交付时间
}

// 企业微信实时台账记录（GET /api/ai/wecom/projects 返回，values 的键为企业微信智能表格列名）
interface WecomProjectRecord {
  record_id: string;
  values?: Record<string, unknown>;
}


// 与跨项目看板的统计入口对应的筛选类型：
// 项目总数/本月新增项目数/风险项目数/缺少对接人项目数 + 调度项目看板点击某月柱（month）
type ProjectFilter = 'new' | 'risk' | 'no_contact' | 'month';

const FILTER_LABELS: Record<ProjectFilter, string> = {
  new: '本月新增项目数',
  risk: '风险项目数',
  no_contact: '对接人缺省',
  month: '指定核算期项目',
};

// ── 列表排序 ──
// 置顶项目始终排在最前（按置顶时间，最近置顶的在前，见下方 sortProjects），
// 组内顺序由这里选的排序方式决定。
type SortKey = 'default' | 'period' | 'tickets';

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: 'default', label: '默认' },
  { key: 'period', label: '按时间' },
  { key: 'tickets', label: '按工单数' },
];

// ── 列表筛选 ──
// 每个维度内多选取并集（同维度选了 A 和 B → A 或 B 都留下），
// 维度之间取交集（项目经理选了张三 + 地区选了欧洲 → 两个条件都要满足）。
type FilterDim = 'manager' | 'contact' | 'region' | 'status' | 'agv';

const FILTER_DIMS: { key: FilterDim; label: string }[] = [
  { key: 'manager', label: '项目经理' },
  { key: 'contact', label: '对接人' },
  { key: 'region', label: '地区' },
  { key: 'status', label: '项目阶段' },
  { key: 'agv', label: 'AGV 数量' },
];

const EMPTY_FILTERS: Record<FilterDim, string[]> = {
  manager: [], contact: [], region: [], status: [], agv: [],
};

// 筛选项里的「其他」兜底：项目在该维度没填值的都归到这里（空值也得能被筛出来）。
// 它不是真实取值，所以列选项时永远摆在最后一个。
const OTHER_VALUE = '其他';

// AGV 数量分档：车数是 1-162 的长尾分布（本地库 154 个项目中 1-10 台占八成），
// 所以前几档切得细、尾部合并；没填车数的单列一档，否则这批项目筛不出来。
const AGV_BUCKETS: { key: string; label: string; match: (n: number | null | undefined) => boolean }[] = [
  { key: 'le5', label: '5 台及以下', match: (n) => n != null && n <= 5 },
  { key: '6to10', label: '6-10 台', match: (n) => n != null && n > 5 && n <= 10 },
  { key: '11to30', label: '11-30 台', match: (n) => n != null && n > 10 && n <= 30 },
  { key: 'gt30', label: '30 台以上', match: (n) => n != null && n > 30 },
  { key: 'none', label: OTHER_VALUE, match: (n) => n == null },
];

const agvBucketLabel = (key: string): string =>
  AGV_BUCKETS.find((b) => b.key === key)?.label ?? key;

const dimLabel = (key: FilterDim): string =>
  FILTER_DIMS.find((d) => d.key === key)?.label ?? '';

// 需要搜索框的维度：只有项目经理 / 对接人的候选值多、且是自由文本（新名字随时可能冒出来）；
// 地区 / 项目阶段 / AGV 分档的取值固定且不多，下拉框里直接选，不给搜索框
const SEARCHABLE_DIMS: FilterDim[] = ['manager', 'contact'];

// ── 下拉框内的模糊搜索 ──
// 选项里既有中文人名，也有带括号的地区名（「大陆(China Mainland)」），所以按
// 「前缀 → 包含 → 顺序命中」三级打分：输入「大陆」或「china」都能命中那个地区，
// 输入「正实」也能命中「正在实施」（只要求字符按顺序出现，不要求连续）。
const normalizeQuery = (s: string) => s.toLowerCase().replace(/\s+/g, '');

/** 越小越靠前：0 前缀、1 包含、2 顺序命中、-1 不匹配（无关键词时全部返回 0，保持原顺序） */
function fuzzyScore(text: string, query: string): number {
  const q = normalizeQuery(query);
  if (!q) return 0;
  const t = normalizeQuery(text);
  if (t.startsWith(q)) return 0;
  if (t.includes(q)) return 1;
  // 单字的顺序命中几乎全中（等于没筛），只对 2 字以上做
  if (q.length < 2) return -1;
  let hit = 0;
  for (const ch of t) {
    if (ch === q[hit]) hit += 1;
    if (hit === q.length) return 2;
  }
  return -1;
}

// 项目的核算期排序键（YYYY-MM → YYYYMM 整数）；没填核算期的返回 -1，降序时排到最后
const periodRank = (p: ProjectItem): number => {
  const ym = normalizeSettlementPeriod(p.settlement_period);
  return ym ? Number(ym.replace('-', '')) : -1;
};

/**
 * 列表排序：置顶项目始终在最前（按置顶时间，最近置顶的在前），组内按所选方式排。
 *
 * 默认排序不做任何重排——`Array#sort` 稳定，比较返回 0 时保持后端返回的原始顺序
 * （后端按项目 id 排；改排序方式不会把无关项目的相对次序打乱）。
 */
function sortProjects(
  list: ProjectItem[],
  sort: SortKey,
  pinnedRank: Map<string, number>,
): ProjectItem[] {
  const rankOf = (p: ProjectItem) => pinnedRank.get(p.id) ?? Number.MAX_SAFE_INTEGER;
  return [...list].sort((a, b) => {
    const ra = rankOf(a);
    const rb = rankOf(b);
    if (ra !== rb) return ra - rb;
    if (sort === 'period') {
      const diff = periodRank(b) - periodRank(a); // 核算期新的在前
      if (diff !== 0) return diff;
    } else if (sort === 'tickets') {
      const diff = (b.ticket_count ?? -1) - (a.ticket_count ?? -1); // 工单数多的在前，无工单数排最后
      if (diff !== 0) return diff;
    }
    return 0;
  });
}

// 置顶图标（lucide pin）：只在本页用，故不放进共享的 macaronIcons 图标库
const PinIcon = ({ size = 14 }: { size?: number }) => (
  <svg
    width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"
  >
    <path d="M12 17v5" />
    <path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z" />
  </svg>
);

export default function ProjectProgress() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const filter = (searchParams.get('filter') as ProjectFilter | null) || null;
  const period = searchParams.get('period');
  const [projects, setProjects] = useState<ProjectItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState('');
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');
  const { hasPermission } = useAuthStore();
  const canViewAll = hasPermission(PERMISSION_VIEW_ALL);

  const fetchProjects = useCallback(async () => {
    setLoading(true);
    try {
      const url = canViewAll ? '/projects/?include_analysis=true' : '/projects/me?include_analysis=true';
      const data = await request<ProjectItem[]>(url);
      setProjects(normalizeList<ProjectItem>(data));
    } catch (err) {
      Toast({ message: String(err), theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [canViewAll]);

  useEffect(() => { fetchProjects(); }, [fetchProjects]);

  // ── 排序 / 筛选（都是前端在已拉取的项目列表上做，不再打接口） ──
  const [sort, setSort] = useState<SortKey>('default');
  const [filters, setFilters] = useState<Record<FilterDim, string[]>>(EMPTY_FILTERS);
  // 当前展开的下拉框（null = 都没展开）；下拉框里的搜索词每次展开都清空
  const [openDim, setOpenDim] = useState<FilterDim | null>(null);
  const [optionSearch, setOptionSearch] = useState('');
  const activeFilterCount = FILTER_DIMS.filter((d) => filters[d.key].length > 0).length;

  // ── 个人置顶 ──
  // 后端 /projects/pins 返回当前登录人置顶的项目 id（最近置顶的在前，按人隔离）；
  // 接口不可用时静默降级为「无置顶」，列表照常按原顺序展示。
  const [pinnedIds, setPinnedIds] = useState<string[]>([]);
  const pinnedRank = useMemo(() => {
    const rank = new Map<string, number>();
    pinnedIds.forEach((id, index) => rank.set(id, index));
    return rank;
  }, [pinnedIds]);
  const [pinning, setPinning] = useState(false);

  const fetchPins = useCallback(async () => {
    try {
      const data = await request<{ project_ids?: string[] }>('/projects/pins');
      setPinnedIds(data?.project_ids ?? []);
    } catch {
      // 置顶是列表排序的锦上添花，拉不到就按无置顶展示（不打断项目列表本身）
    }
  }, []);

  useEffect(() => { fetchPins(); }, [fetchPins]);

  const togglePin = async (p: ProjectItem) => {
    if (pinning) return;
    const pinned = pinnedRank.has(p.id);
    setPinning(true);
    try {
      await request(`/projects/${p.id}/pin`, { method: pinned ? 'DELETE' : 'POST' });
      await fetchPins();
      Toast({ message: pinned ? '已取消置顶' : '已置顶到最前', theme: 'success' });
    } catch (err) {
      Toast({ message: `${pinned ? '取消置顶' : '置顶'}失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setPinning(false);
    }
  };

  const toggleFilterValue = (dim: FilterDim, value: string) => {
    setFilters((prev) => {
      const picked = prev[dim];
      return {
        ...prev,
        [dim]: picked.includes(value) ? picked.filter((v) => v !== value) : [...picked, value],
      };
    });
  };

  const clearFilterDim = (dim: FilterDim) => {
    setFilters((prev) => ({ ...prev, [dim]: [] }));
  };

  // 下拉框按钮上显示的文字：没选就显示维度名，选了显示「维度名: 值（+n）」
  const dimTriggerText = (dim: FilterDim): string => {
    const picked = filters[dim];
    if (!picked.length) return dimLabel(dim);
    const first = optionLabelOf(dim, picked[0]);
    const more = picked.length > 1 ? ` +${picked.length - 1}` : '';
    return `${dimLabel(dim)}: ${first}${more}`;
  };

  const optionLabelOf = (dim: FilterDim, value: string): string =>
    dim === 'agv' ? agvBucketLabel(value) : value;

  // ── 长按操作（置顶 / 删除） ──
  // 长按项目卡片 → 弹出操作卡片（置顶 | 取消置顶、删除项目）；
  // 点「删除项目」→ 二次确认对话框 → 真实删除
  const longPressTimer = useRef<number | null>(null);
  // 长按已触发后，抑制紧随其后的 onClick 导航到详情页
  const longPressFired = useRef(false);
  const [longPressProject, setLongPressProject] = useState<ProjectItem | null>(null);
  const [deleteConfirmProject, setDeleteConfirmProject] = useState<ProjectItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  const startLongPress = (p: ProjectItem) => () => {
    longPressFired.current = false;
    longPressTimer.current = window.setTimeout(() => {
      longPressFired.current = true;
      setLongPressProject(p);
    }, 600);
  };
  const cancelLongPress = () => {
    if (longPressTimer.current !== null) { clearTimeout(longPressTimer.current); longPressTimer.current = null; }
  };

  const confirmDelete = async () => {
    if (!deleteConfirmProject || deleting) return;
    setDeleting(true);
    try {
      await request(`/projects/${deleteConfirmProject.id}`, { method: 'DELETE' });
      Toast({ message: '项目已删除', theme: 'success' });
      setDeleteConfirmProject(null);
      setLongPressProject(null);
      await fetchProjects(); // 重新拉取，列表同步移除
    } catch (err) {
      Toast({ message: `删除失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setDeleting(false);
    }
  };

  // 企业微信实时台账的项目经理名单（GET /api/ai/wecom/projects，AI 服务）；
  // 卡片项目经理优先用台账值（按 项目编号 / record_id 匹配），台账不可用时回退本地 project_manager
  const [wecomManagerByCode, setWecomManagerByCode] = useState<Map<string, string>>(new Map());
  const [wecomManagerByRecord, setWecomManagerByRecord] = useState<Map<string, string>>(new Map());

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await aiGet<{ code: number; data?: { records?: WecomProjectRecord[] }; message?: string }>('/wecom/projects');
        if (!alive) return;
        const records = res?.data?.records || [];
        const byCode = new Map<string, string>();
        const byRecord = new Map<string, string>();
        for (const r of records) {
          const manager = String(r.values?.['项目经理'] ?? '').trim();
          if (!manager) continue;
          const code = String(r.values?.['项目编号'] ?? '').trim();
          if (code) byCode.set(code, manager);
          if (r.record_id) byRecord.set(r.record_id, manager);
        }
        setWecomManagerByCode(byCode);
        setWecomManagerByRecord(byRecord);
      } catch {
        // wecom 台账接口不可用时静默降级：沿用本地 project_manager
      }
    })();
    return () => { alive = false; };
  }, []);

  const wecomManagerOf = (p: ProjectItem): string | null => {
    const byCode = wecomManagerByCode.get(String(p.project_code ?? '').trim());
    if (byCode) return byCode;
    if (p.system_id) {
      const byRecord = wecomManagerByRecord.get(String(p.system_id).trim());
      if (byRecord) return byRecord;
    }
    return null;
  };

  // 项目在某个筛选维度上的取值（空值统一返回「其他」，才能被筛出来）。
  // 项目经理用卡片上实际显示的那个值（台账优先、回退本地字段），与用户看到的一致。
  function filterValueOf(p: ProjectItem, dim: FilterDim): string {
    switch (dim) {
      case 'manager':
        return wecomManagerOf(p) || p.project_manager || OTHER_VALUE;
      case 'contact':
        return p.contact_person || OTHER_VALUE;
      case 'region':
        return p.project_region || OTHER_VALUE;
      case 'status':
        return p.status || OTHER_VALUE;
      case 'agv': {
        const bucket = AGV_BUCKETS.find((b) => b.match(p.total_vehicle_count));
        return bucket ? bucket.key : 'none';
      }
    }
  }

  if (loading) return <Loading text="加载项目..." />;

  const activeCount = projects.filter((p) =>
    !['项目中止', '项目结束'].includes(p.status)
  ).length;

  const displayProjects = (() => {
    let list = projects;
    if (filter === 'new') {
      const ym = currentYearMonth();
      list = list.filter((p) => normalizeSettlementPeriod(p.settlement_period) === ym);
    } else if (filter === 'month' && period) {
      // 调度项目看板点击某月柱进入：按业绩核算期精确匹配该月（period 为 YYYY-MM）
      list = list.filter((p) => normalizeSettlementPeriod(p.settlement_period) === period);
    } else if (filter === 'risk') {
      list = list.filter((p) => p.risks > 0);
    } else if (filter === 'no_contact') {
      list = list.filter((p) => !p.contact_person);
    }
    if (keyword.trim()) {
      const kw = keyword.trim().toLowerCase();
      list = list.filter((p) => p.name && p.name.toLowerCase().includes(kw));
    }

    // 筛选框（项目经理 / 对接人 / 地区 / 项目阶段 / AGV数量）：
    // 同维度内多选取并集，维度之间取交集；项目经理按卡片实际显示的那个值匹配
    // （台账值优先、回退本地字段），否则「看到的名字」和「筛得出来的名字」会对不上
    for (const dim of FILTER_DIMS) {
      const picked = filters[dim.key];
      if (!picked.length) continue;
      list = list.filter((p) => picked.includes(filterValueOf(p, dim.key)));
    }

    return sortProjects(list, sort, pinnedRank);
  })();

  // 各筛选维度的候选项：只列当前项目列表里真实出现过的（含「其他」），
  // 这样不会出现点进去必然为空的死选项；带上命中项目数，按出现次数从多到少排，
  // 常用的在前（下拉框里同时在右侧标出每个选项能筛出多少个项目）。
  const filterOptions = (() => {
    const options: Record<FilterDim, { value: string; count: number }[]> = {
      manager: [], contact: [], region: [], status: [], agv: [],
    };
    const counts: Record<FilterDim, Map<string, number>> = {
      manager: new Map(), contact: new Map(), region: new Map(),
      status: new Map(), agv: new Map(),
    };
    for (const p of projects) {
      for (const dim of FILTER_DIMS) {
        const value = filterValueOf(p, dim.key);
        counts[dim.key].set(value, (counts[dim.key].get(value) || 0) + 1);
      }
    }
    for (const dim of FILTER_DIMS) {
      const entries = [...counts[dim.key].entries()];
      if (dim.key === 'agv') {
        // AGV 分档按档位固定顺序展示（从小到大），不按命中数量排
        options.agv = AGV_BUCKETS
          .filter((b) => counts.agv.has(b.key))
          .map((b) => ({ value: b.key, count: counts.agv.get(b.key) || 0 }));
        continue;
      }
      entries.sort((a, b) => {
        // 「其他」永远排最后：它只是空值的兜底，不是真实取值，
        // 按命中数排在中间容易被当成正常选项误点
        if ((a[0] === OTHER_VALUE) !== (b[0] === OTHER_VALUE)) return a[0] === OTHER_VALUE ? 1 : -1;
        return b[1] - a[1] || a[0].localeCompare(b[0], 'zh-Hans-CN');
      });
      options[dim.key] = entries.map(([value, count]) => ({ value, count }));
    }
    return options;
  })();

  // 下拉框里当前展示的选项：需要搜索的维度按模糊匹配过滤再按匹配质量排
  // （前缀 > 包含 > 顺序命中），同质量的保持上面「命中项目数从多到少」的原顺序；
  // 其余维度原样列出，直接点选
  const openOptions = openDim ? filterOptions[openDim] : [];
  const visibleOptions = openDim && SEARCHABLE_DIMS.includes(openDim)
    ? openOptions
        .map((opt) => ({ ...opt, score: fuzzyScore(optionLabelOf(openDim, opt.value), optionSearch) }))
        .filter((opt) => opt.score >= 0)
        .sort((a, b) => a.score - b.score)
    : openOptions;

  return (
    <div className="mac-page">
      {/* 概览卡片（对照原型：双 surface-card 指标卡） */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12, marginBottom: 12 }}>
        <div className="mac-card" style={{ padding: 16 }}>
          <MacStat value={projects.length} label="项目总数" tone="blue-2" />
        </div>
        <div className="mac-card" style={{ padding: 16 }}>
          <MacStat value={activeCount} label="活跃项目" tone="blue-3" />
        </div>
      </div>

      {/* 项目名称搜索 */}
      <div className="mac-search mac-search--card" style={{ marginBottom: 12 }}>
        <MacSearch size={16} />
        <input
          className="mac-search__input"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="搜索项目名称 · 长按可置顶/删除"
        />
      </div>

      {/* 筛选下拉框：一个维度一个下拉，点开是底部弹层（框内可模糊搜索选项、可多选） */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11.5, color: 'var(--mac-muted-fg)', marginLeft: 2 }}>筛选项</span>
        {FILTER_DIMS.map((dim) => {
          const picked = filters[dim.key];
          return (
            <button
              key={dim.key}
              type="button"
              className={`mac-filter-chip${picked.length > 0 ? ' is-active' : ''}`}
              onClick={() => { setOptionSearch(''); setOpenDim(dim.key); }}
            >
              {/* 选中的值可能很长（「大陆(China Mainland)」），截断显示；完整值在弹层里 */}
              <span style={{ maxWidth: 130, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {dimTriggerText(dim.key)}
              </span>
              <MacChevronDown size={12} />
            </button>
          );
        })}
        {/* 清空全部：逐个数下拉框清太慢，筛选生效时给一个一键复位 */}
        {activeFilterCount > 0 && (
          <button
            type="button"
            className="mac-filter-chip"
            onClick={() => setFilters(EMPTY_FILTERS)}
          >
            <MacX size={12} />
            清空筛选
          </button>
        )}
      </div>

      {/* 排序方式（就地生效，不做下拉） */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11.5, color: 'var(--mac-muted-fg)', marginLeft: 2 }}>排序</span>
        {SORT_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`mac-filter-chip${sort === opt.key ? ' is-active' : ''}`}
            onClick={() => setSort(opt.key)}
          >
            {opt.label}
          </button>
        ))}
        {/* 有筛选时提示当前命中数量：列表被筛短了，得让人知道是筛出来的还是真没项目 */}
        {activeFilterCount > 0 && (
          <span style={{ fontSize: 11.5, color: 'var(--mac-muted-fg)' }}>
            命中 {displayProjects.length}/{projects.length}
          </span>
        )}
      </div>

      {/* 筛选提示条：从跨项目看板某个统计数字/月份柱点进来时显示，可点击返回全部项目 */}
      {filter && (
        <div className="mac-filter-banner">
          <span>当前筛选：<strong>{filter === 'month' && period ? `${period} 核算期项目` : FILTER_LABELS[filter]}</strong>（{displayProjects.length}）</span>
          <button type="button" className="mac-filter-banner__back" onClick={() => setSearchParams({})}>
            查看全部
          </button>
        </div>
      )}

      {/* 项目列表 */}
      {displayProjects.length === 0 ? (
        <div className="mac-empty" style={{ padding: '40px 0' }}>
          {filter || keyword.trim() || activeFilterCount > 0 ? '暂无符合条件的项目' : '暂无项目数据'}
        </div>
      ) : (
        displayProjects.map((p) => {
          const hasRisk = p.risks > 0;
          const wecomManager = wecomManagerOf(p);
          const completionRate = p.task_execution_stats?.completion_rate;
          const timeProgress = calcLifecycleProgress(p.status);
          const pinned = pinnedRank.has(p.id);

          return (
            <div
              key={p.id}
              className="mac-proj-card"
              onClick={() => {
                // 长按刚触发时不再进入详情页，避免删除操作被导航打断
                if (longPressFired.current) { longPressFired.current = false; return; }
                navigate(`/admin/project-detail/${p.id}`);
              }}
              onTouchStart={startLongPress(p)}
              onTouchEnd={cancelLongPress}
              onTouchMove={cancelLongPress}
              onMouseDown={(e) => { if (e.button === 0) startLongPress(p)(); }}
              onMouseUp={cancelLongPress}
              onMouseLeave={cancelLongPress}
            >
              {/* 标题行：项目名左对齐，右上角是该项目的工单数（后端 ticket_count，口径同仪表盘「总工单数」）；
                  置顶的项目在项目名左侧带一个图钉标记，说明它为什么排在最前 */}
              <div className="mac-proj-card__head">
                {pinned && (
                  <span className="mac-chip mac-chip--tag-blue" style={{ flexShrink: 0 }} title="已置顶">
                    <PinIcon size={11} />置顶
                  </span>
                )}
                <div className="mac-proj-card__title">{p.name}</div>
                <span className="mac-proj-card__tickets">
                  <span className="mac-proj-card__tickets-num">{p.ticket_count ?? '-'}</span>
                  工单
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
                <span className="mac-chip mac-chip--tag mac-chip--blue">{p.status}</span>
                <span style={{ fontSize: 11.5, color: 'var(--mac-muted-fg)' }}>
                  {p.project_code} · 项目经理: {wecomManager || p.project_manager || '未指定'}
                </span>
              </div>

              {/* 项目时间进度（对照原型：与项目详情页同一口径 —— 按生命周期阶段线性计算；仅「项目中止」隐藏） */}
              {p.status !== PROJECT_ABORTED && (
                <div className="mac-progress">
                  <div className="mac-progress__head">
                    <span>项目时间进度</span>
                    <span className="mac-progress__pct">{timeProgress}%</span>
                  </div>
                  <div className="mac-progress__track">
                    <div className="mac-progress__fill" style={{ width: `${timeProgress}%` }} />
                  </div>
                </div>
              )}

              {/* 任务统计：任务总数 / 已完成任务 / 任务完成率 / 切手动次数
                  四格左侧的日期标签标注这组数据的真实日期——后端取各项目已导入的最新一天，
                  导入频率不同，可能不是当天（一周前甚至更早） */}
              <div className="mac-ministat-grid" style={{ gridTemplateColumns: 'auto repeat(4, 1fr)' }}>
                <div className="mac-ministat">
                  <div className="mac-ministat__value" style={{ fontSize: 11.5, whiteSpace: 'nowrap' }}>
                    {p.task_execution_stats?.data_date ?? '-'}
                  </div>
                  <div className="mac-ministat__label">数据日期</div>
                </div>
                <div className="mac-ministat">
                  <div className="mac-ministat__value">{p.task_execution_stats?.total_tasks ?? '-'}</div>
                  <div className="mac-ministat__label">任务总数</div>
                </div>
                <div className="mac-ministat">
                  <div className="mac-ministat__value">{p.task_execution_stats?.finished_tasks ?? '-'}</div>
                  <div className="mac-ministat__label">已完成任务</div>
                </div>
                <div className="mac-ministat">
                  <div className="mac-ministat__value">
                    {completionRate != null ? `${Math.round(completionRate * 100)}%` : '-'}
                  </div>
                  <div className="mac-ministat__label">任务完成率</div>
                </div>
                <div className="mac-ministat">
                  <div className="mac-ministat__value">{p.latest_manual_switch_count ?? '-'}</div>
                  <div className="mac-ministat__label">切手动次数</div>
                </div>
              </div>

              {/* 风险提示 */}
              {hasRisk && (
                <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid rgba(232,234,234,0.6)' }}>
                  <span style={{ fontSize: 12, color: '#ad4545' }}>⚠ {p.risks} 项未关闭风险</span>
                </div>
              )}
            </div>
          );
        })
      )}

      {/* 快捷入口 */}
      <div style={{ marginTop: 20 }}>
        <button
          type="button"
          className="mac-btn mac-btn--primary mac-btn--block"
          onClick={() => navigate('/admin/project-manage')}
        >
          <MacFolderClosed size={16} />
          全部项目管理
        </button>
      </div>

      {/* 长按项目 → 「置顶 / 取消置顶」「删除项目」操作卡片 */}
      <Popup visible={!!longPressProject} onClose={() => setLongPressProject(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title" style={{ fontSize: 15 }}>
            {longPressProject?.name}
          </h4>
          <button
            type="button"
            className="mac-list-item"
            disabled={pinning}
            onClick={() => {
              const target = longPressProject;
              setLongPressProject(null);
              if (target) togglePin(target);
            }}
          >
            <span style={{ fontSize: 14, fontWeight: 500, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <PinIcon size={14} />
              {longPressProject && pinnedRank.has(longPressProject.id) ? '取消置顶' : '置顶'}
            </span>
          </button>
          <button
            type="button"
            className="mac-list-item"
            style={{ background: '#fbecec', color: '#ad4545' }}
            onClick={() => {
              setDeleteConfirmProject(longPressProject);
              setLongPressProject(null);
            }}
          >
            <span style={{ fontSize: 14, fontWeight: 500 }}>删除项目</span>
          </button>
          <div style={{ textAlign: 'right', marginTop: 12 }}>
            <button
              type="button"
              className="mac-back-link"
              onClick={() => setLongPressProject(null)}
            >
              取消
            </button>
          </div>
        </div>
      </Popup>

      {/* 筛选下拉框：选项列表（项目经理 / 对接人 顶部多一个模糊搜索框）；
          同维度多选取并集，维度之间取交集；点选即时生效，弹层不自动关，方便连着勾几个 */}
      <Popup
        visible={!!openDim}
        onClose={() => setOpenDim(null)}
        placement="bottom"
        showOverlay
      >
        {openDim && (
          <div className="mac-sheet" style={{ maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
            <h4 className="mac-sheet__title" style={{ fontSize: 15 }}>{dimLabel(openDim)}</h4>
            {SEARCHABLE_DIMS.includes(openDim) && (
              <div className="mac-search" style={{ marginBottom: 12 }}>
                <MacSearch size={16} />
                <input
                  className="mac-search__input"
                  value={optionSearch}
                  onChange={(e) => setOptionSearch(e.target.value)}
                  placeholder={`输入关键词模糊查找${dimLabel(openDim)}`}
                />
              </div>
            )}
            <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8 }}>
              {visibleOptions.map((opt) => {
                const active = filters[openDim].includes(opt.value);
                return (
                  <button
                    key={opt.value}
                    type="button"
                    className={`mac-pick-item${active ? ' is-active' : ''}`}
                    onClick={() => toggleFilterValue(openDim, opt.value)}
                  >
                    <span className="mac-pick-item__name">{optionLabelOf(openDim, opt.value)}</span>
                    <span className="mac-pick-item__code">{opt.count} 个</span>
                    {active && (
                      <span className="mac-pick-item__check"><MacCheck size={16} /></span>
                    )}
                  </button>
                );
              })}
              {visibleOptions.length === 0 && (
                <div className="mac-empty">
                  {openOptions.length === 0 ? '暂无可选项' : '未找到匹配的选项'}
                </div>
              )}
            </div>
            <div style={{ display: 'flex', gap: 12, marginTop: 12 }}>
              <button
                type="button"
                className="mac-btn mac-btn--outline"
                style={{ flex: 1 }}
                onClick={() => clearFilterDim(openDim)}
              >
                清空此项
              </button>
              <button
                type="button"
                className="mac-btn mac-btn--primary"
                style={{ flex: 1 }}
                onClick={() => setOpenDim(null)}
              >
                完成
              </button>
            </div>
          </div>
        )}
      </Popup>

      {/* 二次确认对话框：确认删除项目 */}
      <Dialog
        visible={!!deleteConfirmProject}
        title="确认删除项目"
        confirmBtn={deleting ? '删除中...' : '删除'}
        cancelBtn="取消"
        onConfirm={confirmDelete}
        onCancel={() => setDeleteConfirmProject(null)}
        onClose={() => setDeleteConfirmProject(null)}
      >
        <p style={{ fontSize: 14, lineHeight: 1.7, color: 'var(--mac-fg)' }}>
          确定要删除项目「<strong>{deleteConfirmProject?.name}</strong>」吗？此操作不可恢复。
        </p>
      </Dialog>
    </div>
  );
}
