// 后台管理首页仪表盘 —— 上中下三段式看板
// 上：工单状态监测 —— 蓝阶环图 + 图例（百分比/数量）+ 四指标卡（点击图例下钻明细）
// 中：跨项目看板 —— 按月项目数量柱状图 + 同步按钮 + 四指标卡 + 紧急度四象限卡片
// 下：更多功能 —— 项目管理 / 数据管理 / 日报周报 / 其他 快捷入口
//
// 数据接口见 src/api/dashboard.ts；接口未就绪时一律优雅降级为「0/暂无数据」，不阻塞页面渲染。
import { useState, useEffect, useCallback, useMemo } from 'react';
import { Navbar, Loading, Toast } from 'tdesign-mobile-react';
import { useNavigate } from 'react-router-dom';
import {
  TICKET_STATUS_LIST, URGENCY_LIST,
} from '@/shared/constants/dashboard';
import {
  fetchTicketSummary, fetchProjectMonthly, fetchUrgencySummary, syncWecomProjects,
  type TicketSummary, type ProjectMonthlySummary, type UrgencySummary,
} from '@/api/dashboard';
import UserAvatarMenu from '@/shared/components/UserAvatarMenu';
import SubscriptionReminder from '@/shared/components/SubscriptionReminder';
import { MacDonut, MacLegend, MacStat } from '@/shared/components/macaronBits';
import { MacChevronRight, MacRefreshCw } from '@/shared/components/macaronIcons';
import { ProjectMonthBars } from '@/shared/components/macaronMonthBars';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { currentYearMonth, normalizeSettlementPeriod } from '@/shared/utils/settlement';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';

interface ProjectListItem {
  risks: number;
  contact_person: string;
  settlement_period?: string | null;
}

interface MoreFunctionEntry { path: string; label: string; kind: MoreEntryIconKind; tone: string; }

type MoreEntryIconKind = 'projects' | 'data' | 'reports' | 'other';

/** 更多功能入口图标：与 macaron admin 页的 lucide 图标同款线条 */
function MoreEntryIcon({ kind }: { kind: MoreEntryIconKind }) {
  const common = {
    width: 18,
    height: 18,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
  if (kind === 'projects') {
    // lucide folder-kanban
    return (
      <svg {...common}>
        <path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z" />
        <path d="M8 10v4" />
        <path d="M12 10v2" />
        <path d="M16 10v6" />
      </svg>
    );
  }
  if (kind === 'data') {
    // lucide database
    return (
      <svg {...common}>
        <ellipse cx="12" cy="5" rx="9" ry="3" />
        <path d="M3 5V19A9 3 0 0 0 21 19V5" />
        <path d="M3 12A9 3 0 0 0 21 12" />
      </svg>
    );
  }
  if (kind === 'reports') {
    // lucide file-bar-chart
    return (
      <svg {...common}>
        <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
        <path d="M15 2v5h5" />
        <path d="M8 18v-1" />
        <path d="M12 18v-6" />
        <path d="M16 18v-3" />
      </svg>
    );
  }
  // lucide more-horizontal
  return (
    <svg {...common}>
      <circle cx="12" cy="12" r="1" />
      <circle cx="19" cy="12" r="1" />
      <circle cx="5" cy="12" r="1" />
    </svg>
  );
}

const MORE_FUNCTION_ENTRIES: MoreFunctionEntry[] = [
  { path: '/admin/project-manage', label: '项目管理', kind: 'projects', tone: 'blue-1' },
  { path: '/admin/data-import', label: '数据管理', kind: 'data', tone: 'blue-2' },
  { path: '/admin/daily-summary', label: '日报周报', kind: 'reports', tone: 'blue-3' },
  { path: '/admin/entries', label: '其他', kind: 'other', tone: 'blue-4' },
];

// 工单状态环图/图例按色阶由深到浅排列（对照 macaron 原型：处理中→暂停挂起→已关闭→已解决→已取消）
const STATUS_TONE_ORDER = ['status-1', 'status-2', 'status-3', 'status-4', 'status-5'];
const SORTED_TICKET_STATUS_LIST = [...TICKET_STATUS_LIST].sort(
  (a, b) => STATUS_TONE_ORDER.indexOf(a.tone) - STATUS_TONE_ORDER.indexOf(b.tone),
);

export default function Dashboard() {
  const navigate = useNavigate();
  const { hasPermission, projectIds, username } = useAuthStore();
  const canAccessAdminEntries = hasPermission('frontend:admin:other:show');
  // 拥有此权限的用户不受「仅看自己关联项目」限制，可查看全部项目和工单
  const canViewAll = hasPermission(PERMISSION_VIEW_ALL);
  const [ticketSummary, setTicketSummary] = useState<TicketSummary | null>(null);
  const [monthlySummary, setMonthlySummary] = useState<ProjectMonthlySummary | null>(null);
  const [urgencySummary, setUrgencySummary] = useState<UrgencySummary | null>(null);
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);

  const loadAll = useCallback(async () => {
    setLoading(true);
    const adminRequest = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');
    // canViewAll 时不传 projectIds（后端不过滤，返回全部）；否则仅统计当前用户关联项目
    // 项目列表同理：canViewAll 走 /projects/，否则走 /projects/me 由后端按 token 过滤
    const filterIds = canViewAll ? undefined : projectIds;
    const projectsUrl = canViewAll ? '/projects/?include_analysis=true' : '/projects/me?include_analysis=true';
    const [tickets, monthly, urgency, projectList] = await Promise.all([
      fetchTicketSummary(filterIds),
      fetchProjectMonthly(filterIds),
      fetchUrgencySummary(filterIds),
      adminRequest<ProjectListItem[]>(projectsUrl).catch(() => []),
    ]);
    setTicketSummary(tickets);
    setMonthlySummary(monthly);
    setUrgencySummary(urgency);
    setProjects(normalizeList<ProjectListItem>(projectList));
    setLoading(false);
  }, [projectIds, canViewAll]);

  const handleSync = useCallback(async () => {
    setSyncing(true);
    const result = await syncWecomProjects();
    if (result) {
      Toast({ message: `同步完成：新增 ${result.created}，更新 ${result.updated}`, theme: 'success' });
      await loadAll();
    } else {
      Toast({ message: '同步失败，请稍后重试', theme: 'error' });
    }
    setSyncing(false);
  }, [loadAll]);

  // 柱状图年份筛选列表：接口返回的出现年份 ∪ 当前年（保证可切到今年查看空月份）
  const monthlyYears = useMemo(() => {
    const set = new Set<number>(monthlySummary?.years ?? []);
    set.add(new Date().getFullYear());
    return Array.from(set).sort((a, b) => a - b);
  }, [monthlySummary]);

  useEffect(() => { loadAll(); }, [loadAll]);

  if (loading) return <Loading text="加载看板..." />;

  return (
    <div className="dashboard-page">
      <SubscriptionReminder username={username} />
      <Navbar
        title="后台管理"
        right={<UserAvatarMenu />}
        fixed
      />

      <div style={{ padding: '16px 16px 32px' }}>
        {/* ============ 上：工单状态监测概览 ============ */}
        {/* 结构性重设计（对照 macaron admin 工单状态监测）：蓝阶环图 + 图例（含百分比/数量）+ 四指标卡 */}
        <SectionTitle title="工单状态监测" onMore={() => navigate('/tasks')} />
        <section className="mac-card mac-card--pad">
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <MacDonut
              segments={SORTED_TICKET_STATUS_LIST.map((s) => ({
                value: ticketSummary?.by_status[s.key] ?? 0,
                tone: s.tone,
              }))}
              centerValue={ticketSummary?.total ?? 0}
              centerLabel="工单总数"
            />
            <MacLegend
              items={SORTED_TICKET_STATUS_LIST.map((s) => {
                const value = ticketSummary?.by_status[s.key] ?? 0;
                const total = ticketSummary?.total ?? 0;
                return {
                  key: s.key,
                  label: s.label,
                  value,
                  tone: s.tone,
                  percent: total > 0 ? Math.round((value / total) * 100) : 0,
                  pending: !s.backendReady,
                };
              })}
              onItemClick={(key) => navigate(`/admin/dashboard/tickets/${key}`)}
            />
          </div>
          <div className="mac-stat-row">
            <MacStat value={ticketSummary?.total ?? 0} label="总工单数" tone="blue-1" />
            <MacStat value={ticketSummary?.pending_count ?? 0} label="待处理" tone="blue-2" />
            <MacStat value={ticketSummary?.overdue_count ?? 0} label="超时工单" tone="blue-3" />
            <MacStat value={formatPercent(ticketSummary?.resolved_rate)} label="解决率" tone="blue-4" />
          </div>
        </section>

        {/* ============ 中：跨项目看板 ============ */}
        {/* 结构性重设计（对照 macaron admin 跨项目看板）：按月柱状图替换原阶段饼图，
            接口从 /projects/summary（按阶段）更换为 /projects/monthly（按月） */}
        <SectionTitle title="跨项目看板" onMore={() => navigate('/admin/project-progress')} />
        <section className="mac-card mac-card--pad">
          <div style={{ display: 'flex', alignItems: 'center' }}>
            <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: 'var(--mac-muted-fg)' }}>调度项目看板</h3>
            <button
              type="button"
              className="mac-sync-btn"
              onClick={handleSync}
              disabled={syncing}
            >
              <MacRefreshCw size={14} />
              {syncing ? '同步中...' : '同步最新数据'}
            </button>
          </div>

          <ProjectMonthBars
            data={monthlySummary?.monthly ?? []}
            years={monthlyYears}
            style={{ marginTop: 12 }}
            onSelect={(key) => navigate(`/admin/project-progress?filter=month&period=${key}`)}
          />

          <div className="mac-stat-row">
            <MacStat value={projects.length} label="项目总数" tone="blue-1" onClick={() => navigate('/admin/project-progress')} />
            <MacStat value={projects.filter((p) => normalizeSettlementPeriod(p.settlement_period) === currentYearMonth()).length} label="本月新增" tone="blue-2" onClick={() => navigate('/admin/project-progress?filter=new')} />
            <MacStat value={projects.filter((p) => p.risks > 0).length} label="风险项目" tone="blue-3" onClick={() => navigate('/admin/project-progress?filter=risk')} />
            <MacStat value={projects.filter((p) => !p.contact_person).length} label="对接人缺省" tone="blue-4" onClick={() => navigate('/admin/project-progress?filter=no_contact')} />
          </div>

          <h3 style={{ margin: '20px 0 0', fontSize: 13, fontWeight: 600, color: 'var(--mac-muted-fg)' }}>项目紧急度看板</h3>
          <div className="mac-urgency-grid">
            {URGENCY_LIST.map((u) => (
              <div
                key={u.key}
                className="mac-urgency-card"
                data-tone={u.tone}
                onClick={() => navigate(`/admin/dashboard/projects/urgency/${u.key}`)}
              >
                <div className="mac-urgency-card__value">{urgencySummary?.by_urgency[u.key] ?? 0}</div>
                <div className="mac-urgency-card__label">{u.label}</div>
              </div>
            ))}
          </div>
        </section>

        {/* ============ 下：更多功能 ============ */}
        <SectionTitle title="更多功能" />
        {/* 四个入口小砖块直接排在页面上（参考 macaron admin：surface-card + 淡色图标圆角块） */}
        <div className="dashboard-more-grid">
          {MORE_FUNCTION_ENTRIES
            .filter((e) => e.path !== '/admin/entries' || canAccessAdminEntries)
            .map((e) => (
            <div key={e.path} className="dashboard-more-card" data-tone={e.tone} onClick={() => navigate(e.path)}>
              <span className="dashboard-more-card__icon"><MoreEntryIcon kind={e.kind} /></span>
              <span className="dashboard-more-card__label">{e.label}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function SectionTitle({ title, onMore, children }: { title: string; onMore?: () => void; children?: React.ReactNode }) {
  return (
    <div className="mac-section-title">
      <span className="mac-section-title__bar" />
      <h2 className="mac-section-title__text">{title}</h2>
      <div className="mac-section-title__actions">
        {children}
        {onMore && (
          <span className="mac-section-title__more" onClick={onMore}>
            查看明细 <MacChevronRight size={14} />
          </span>
        )}
      </div>
    </div>
  );
}

function formatPercent(v: number | undefined): string {
  if (v === undefined || v === null) return '0%';
  return `${Math.round(v * 100)}%`;
}
