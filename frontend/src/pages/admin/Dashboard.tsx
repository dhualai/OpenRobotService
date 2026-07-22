// 后台管理首页仪表盘 —— 上中下三段式看板
// 上：工单状态饼图 + 四项统计卡（点击状态可下钻明细）
// 中：跨项目看板 —— 调度阶段饼图 + 紧急度四象限（点击标签可下钻明细）
// 下：更多功能 —— 项目管理 / 数据管理 / 角色管理 / 其他 快捷入口
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
  fetchTicketSummary, fetchProjectStageSummary, fetchUrgencySummary,
  type TicketSummary, type ProjectStageSummary, type UrgencySummary,
} from '@/api/dashboard';

interface MoreFunctionEntry { path: string; label: string; emoji: string; }

const MORE_FUNCTION_ENTRIES: MoreFunctionEntry[] = [
  { path: '/admin/project-manage', label: '项目管理', emoji: '📁' },
  { path: '/admin/data-import', label: '数据管理', emoji: '🗄️' },
  { path: '/admin/roles', label: '角色管理', emoji: '🏷️' },
  { path: '/admin/entries', label: '其他', emoji: '⋯' },
];

export default function Dashboard() {
  const navigate = useNavigate();
  const [ticketSummary, setTicketSummary] = useState<TicketSummary | null>(null);
  const [stageSummary, setStageSummary] = useState<ProjectStageSummary | null>(null);
  const [urgencySummary, setUrgencySummary] = useState<UrgencySummary | null>(null);
  const [loading, setLoading] = useState(true);

  const loadAll = useCallback(async () => {
    setLoading(true);
    const [tickets, stages, urgency] = await Promise.all([
      fetchTicketSummary(),
      fetchProjectStageSummary(),
      fetchUrgencySummary(),
    ]);
    setTicketSummary(tickets);
    setStageSummary(stages);
    setUrgencySummary(urgency);
    setLoading(false);
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  if (loading) return <Loading text="加载看板..." />;

  return (
    <div className="dashboard-page">
      <Navbar
        title="后台管理"
        fixed
        right={
          <span
            style={{ fontSize: 13, color: '#0052d9', padding: '0 12px' }}
            onClick={() => navigate('/admin/entries')}
          >
            更多功能 ›
          </span>
        }
      />

      <div style={{ padding: '16px 16px 32px' }}>
        {/* ============ 上：工单状态监测概览 ============ */}
        <SectionTitle emoji="🎫" title="工单状态监测" onMore={() => navigate('/admin/ticket-monitor')} />
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
        <SectionTitle emoji="📊" title="跨项目看板" onMore={() => navigate('/admin/project-progress')} />
        <div className="dashboard-section">
          <p className="dashboard-section__subtitle">调度项目看板</p>
          <div className="dashboard-section__row">
            <div className="dashboard-section__chart">
              <ProjectStagePie data={stageSummary} onSliceClick={(key) => navigate(`/admin/dashboard/projects/stage/${key}`)} />
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
            {MORE_FUNCTION_ENTRIES.map((e) => (
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

function SectionTitle({ emoji, title, onMore }: { emoji: string; title: string; onMore?: () => void }) {
  return (
    <div className="dashboard-section-title">
      <span>{emoji} {title}</span>
      {onMore && <span className="dashboard-section-title__more" onClick={onMore}>查看明细 ›</span>}
    </div>
  );
}

function StatItem({ label, value, color }: { label: string; value: number | string; color: string }) {
  return (
    <div className="dashboard-stat-item">
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
