// 后台管理首页仪表盘 —— 上中下三段式看板
// 上：工单状态饼图 + 四项统计卡（点击状态可下钻明细）
// 中：跨项目看板 —— 调度阶段饼图 + 紧急度四象限（点击标签可下钻明细）
// 下：更多功能 —— 项目管理 / 数据管理 / 日报周报 / 其他 快捷入口
//
// 数据接口均为「前端先行、后端待接入」，见 src/api/dashboard.ts 顶部说明。
// 接口未就绪时一律优雅降级为「0/暂无数据」，不阻塞页面渲染。
import { useState, useEffect, useCallback } from 'react';
import { Navbar, Loading } from 'tdesign-mobile-react';
import { useNavigate } from 'react-router-dom';
import ReactECharts from 'echarts-for-react';
import {
  TICKET_STATUS_LIST, PROJECT_STAGE_LIST, URGENCY_LIST,
} from '@/shared/constants/dashboard';
import {
  fetchTicketSummary, fetchProjectStageSummary, fetchUrgencySummary, syncWecomProjects,
  type TicketSummary, type ProjectStageSummary, type UrgencySummary, type SyncResult,
} from '@/api/dashboard';
import UserAvatarMenu from '@/shared/components/UserAvatarMenu';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { useAuthStore } from '@/stores/auth';

interface ProjectListItem {
  risks: number;
  contact_person: string;
  settlement_period?: string | null;
}

function currentYearMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

interface MoreFunctionEntry { path: string; label: string; emoji: string; }

const MORE_FUNCTION_ENTRIES: MoreFunctionEntry[] = [
  { path: '/admin/project-manage', label: '项目管理', emoji: '📁' },
  { path: '/admin/data-import', label: '数据管理', emoji: '🗄️' },
  { path: '/admin/daily-summary', label: '日报周报', emoji: '🤖' },
  { path: '/admin/entries', label: '其他', emoji: '⋯' },
];

export default function Dashboard() {
  const navigate = useNavigate();
  const { hasPermission } = useAuthStore();
  const canAccessAdminEntries = hasPermission('frontend:admin');
  const [ticketSummary, setTicketSummary] = useState<TicketSummary | null>(null);
  const [stageSummary, setStageSummary] = useState<ProjectStageSummary | null>(null);
  const [urgencySummary, setUrgencySummary] = useState<UrgencySummary | null>(null);
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<SyncResult | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    const adminRequest = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');
    const [tickets, stages, urgency, projectList] = await Promise.all([
      fetchTicketSummary(),
      fetchProjectStageSummary(),
      fetchUrgencySummary(),
      adminRequest<ProjectListItem[]>('/projects/?include_analysis=true').catch(() => []),
    ]);
    setTicketSummary(tickets);
    setStageSummary(stages);
    setUrgencySummary(urgency);
    setProjects(normalizeList<ProjectListItem>(projectList));
    setLoading(false);
  }, []);

  const handleSync = useCallback(async () => {
    setSyncing(true);
    setSyncResult(null);
    const result = await syncWecomProjects();
    if (result) {
      setSyncResult(result);
      await loadAll();
    }
    setSyncing(false);
  }, [loadAll]);

  useEffect(() => { loadAll(); }, [loadAll]);

  if (loading) return <Loading text="加载看板..." />;

  return (
    <div className="dashboard-page">
      <Navbar
        title="后台管理"
        right={<UserAvatarMenu />}
        fixed
      />

      <div style={{ padding: '16px 16px 32px' }}>
        {/* ============ 上：工单状态监测概览 ============ */}
        <SectionTitle emoji="🎫" title="工单状态监测" onMore={() => navigate('/tasks')} />
        <div className="dashboard-section">
          <div className="dashboard-section__row">
            <div className="dashboard-section__chart">
              <TicketStatusPie data={ticketSummary} onSliceClick={(key) => navigate(`/admin/dashboard/tickets/${key}`)} />
            </div>
            <div className="dashboard-section__stats">
              <StatItem label="总工单数" value={ticketSummary?.total ?? 0} color="#0052d9" />
              <StatItem label="待处理" value={ticketSummary?.pending_count ?? 0} color="#e37318" />
              <StatItem label="超时工单" value={ticketSummary?.overdue_count ?? 0} color="#d54941" />
              <StatItem label="解决率" value={formatPercent(ticketSummary?.resolved_rate)} color="#00a870" />
            </div>
          </div>
          {/* 状态标签行，点击下钻 */}
          <div className="dashboard-tag-row">
            {TICKET_STATUS_LIST.map((s) => (
              <span
                key={s.key}
                className="dashboard-tag"
                style={{ background: s.color + '18', color: s.color }}
                onClick={() => navigate(`/admin/dashboard/tickets/${s.key}`)}
              >
                {s.label} {ticketSummary?.by_status[s.key] ?? 0}
                {!s.backendReady && <sup className="dashboard-tag__pending">·待接入</sup>}
              </span>
            ))}
          </div>
        </div>

        {/* ============ 中：跨项目看板 ============ */}
        <SectionTitle emoji="📊" title="跨项目看板" onMore={() => navigate('/admin/project-progress')}>
          <button
            className="dashboard-section-title__sync-btn"
            onClick={handleSync}
            disabled={syncing}
          >
            {syncing ? '同步中...' : '同步最新数据'}
          </button>
        </SectionTitle>
        <div className="dashboard-section">
          <p className="dashboard-section__subtitle">调度项目看板</p>
          <div className="dashboard-section__row">
            <div className="dashboard-section__chart">
              <ProjectStagePie data={stageSummary} onSliceClick={(key) => navigate(`/admin/dashboard/projects/stage/${key}`)} />
            </div>
            <div className="dashboard-section__stats">
              <StatItem label="项目总数" value={projects.length} color="#0052d9" onClick={() => navigate('/admin/project-progress')} />
              <StatItem label="本月新增" value={projects.filter((p) => p.settlement_period === currentYearMonth()).length} color="#2ba471" onClick={() => navigate('/admin/project-progress?filter=new')} />
              <StatItem label="风险项目" value={projects.filter((p) => p.risks > 0).length} color="#d54941" onClick={() => navigate('/admin/project-progress?filter=risk')} />
              <StatItem label="对接人缺省" value={projects.filter((p) => !p.contact_person).length} color="#e37318" onClick={() => navigate('/admin/project-progress?filter=no_contact')} />
            </div>
          </div>
          <div className="dashboard-tag-row">
            {PROJECT_STAGE_LIST.map((s) => (
              <span
                key={s.key}
                className="dashboard-tag"
                style={{ background: s.color + '18', color: s.color }}
                onClick={() => navigate(`/admin/dashboard/projects/stage/${s.key}`)}
              >
                {s.label} {stageSummary?.by_stage[s.key] ?? 0}
                {!s.backendReady && <sup className="dashboard-tag__pending">·待接入</sup>}
              </span>
            ))}
          </div>

          <p className="dashboard-section__subtitle" style={{ marginTop: 16 }}>项目紧急度看板</p>
          <div className="dashboard-urgency-grid">
            {URGENCY_LIST.map((u) => (
              <div
                key={u.key}
                className="dashboard-urgency-card"
                style={{ borderColor: u.color }}
                onClick={() => navigate(`/admin/dashboard/projects/urgency/${u.key}`)}
              >
                <div className="dashboard-urgency-card__value" style={{ color: u.color }}>
                  {urgencySummary?.by_urgency[u.key] ?? 0}
                </div>
                <div className="dashboard-urgency-card__label">{u.label}</div>
              </div>
            ))}
          </div>
        </div>

        {/* ============ 下：更多功能 ============ */}
        <SectionTitle emoji="📋" title="更多功能" />
        <div className="dashboard-section">
          <div className="dashboard-more-grid">
            {MORE_FUNCTION_ENTRIES
              .filter((e) => e.path !== '/admin/entries' || canAccessAdminEntries)
              .map((e) => (
              <div key={e.path} className="dashboard-more-card" onClick={() => navigate(e.path)}>
                <span className="dashboard-more-card__emoji">{e.emoji}</span>
                <span className="dashboard-more-card__label">{e.label}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function SectionTitle({ emoji, title, onMore, children }: { emoji: string; title: string; onMore?: () => void; children?: React.ReactNode }) {
  return (
    <div className="dashboard-section-title">
      <span>{emoji} {title}</span>
      <div className="dashboard-section-title__actions">
        {children}
        {onMore && <span className="dashboard-section-title__more" onClick={onMore}>查看明细 ›</span>}
      </div>
    </div>
  );
}

function StatItem({ label, value, color, onClick }: { label: string; value: number | string; color: string; onClick?: () => void }) {
  return (
    <div className="dashboard-stat-item" style={onClick ? { cursor: 'pointer' } : undefined} onClick={onClick}>
      <div className="dashboard-stat-item__value" style={{ color }}>{value}</div>
      <div className="dashboard-stat-item__label">{label}</div>
    </div>
  );
}

function formatPercent(v: number | undefined): string {
  if (v === undefined || v === null) return '0%';
  return `${Math.round(v * 100)}%`;
}

// ============ 图表组件 ============

function TicketStatusPie({ data, onSliceClick }: { data: TicketSummary | null; onSliceClick: (key: string) => void }) {
  const seriesData = TICKET_STATUS_LIST.map((s) => ({
    name: s.label,
    value: data?.by_status[s.key] ?? 0,
    itemStyle: { color: s.color },
  }));
  const allZero = seriesData.every((d) => d.value === 0);

  const option = {
    tooltip: { trigger: 'item' as const },
    legend: { show: false },
    series: [{
      type: 'pie',
      radius: ['45%', '72%'],
      avoidLabelOverlap: true,
      label: { show: false },
      data: allZero ? [{ name: '暂无数据', value: 1, itemStyle: { color: '#eee' } }] : seriesData,
    }],
  };

  return (
    <ReactECharts
      option={option}
      style={{ height: 160 }}
      onEvents={{
        click: (params: { name: string }) => {
          if (allZero) return;
          const found = TICKET_STATUS_LIST.find((s) => s.label === params.name);
          if (found) onSliceClick(found.key);
        },
      }}
    />
  );
}

function ProjectStagePie({ data, onSliceClick }: { data: ProjectStageSummary | null; onSliceClick: (key: string) => void }) {
  const seriesData = PROJECT_STAGE_LIST.map((s) => ({
    name: s.label,
    value: data?.by_stage[s.key] ?? 0,
    itemStyle: { color: s.color },
  }));
  const allZero = seriesData.every((d) => d.value === 0);

  const option = {
    tooltip: { trigger: 'item' as const },
    legend: { show: false },
    series: [{
      type: 'pie',
      radius: ['30%', '65%'],
      avoidLabelOverlap: true,
      label: { fontSize: 10, formatter: '{b}' },
      data: allZero ? [{ name: '暂无数据', value: 1, itemStyle: { color: '#eee' } }] : seriesData,
    }],
  };

  return (
    <ReactECharts
      option={option}
      style={{ height: 220, width: '100%' }}
      onEvents={{
        click: (params: { name: string }) => {
          if (allZero) return;
          const found = PROJECT_STAGE_LIST.find((s) => s.label === params.name);
          if (found) onSliceClick(found.key);
        },
      }}
    />
  );
}
