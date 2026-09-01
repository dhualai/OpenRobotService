// 后台管理首页仪表盘 —— 上中下三段式看板
// 上：工单状态监测 —— 蓝阶环图 + 图例（百分比/数量）+ 四指标卡（点击图例下钻明细）
// 中：跨项目看板 —— 按月项目数量柱状图 + 同步按钮 + 四指标卡 + 紧急度四象限卡片
// 下：更多功能 —— 项目管理 / 数据资源 / 日报周报 / 其他 快捷入口
//
// 数据接口见 src/api/dashboard.ts；接口未就绪时一律优雅降级为「0/暂无数据」，不阻塞页面渲染。
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Navbar, Toast, Popup } from 'tdesign-mobile-react';
import { UserCircleIcon } from 'tdesign-icons-react';
import { useNavigate } from 'react-router-dom';
import {
  TICKET_STATUS_LIST, URGENCY_LIST,
} from '@/shared/constants/dashboard';
import {
  fetchDashboardSummaryAll, syncWecomProjects,
  type TicketSummary, type ProjectMonthlySummary, type UrgencySummary, type TicketSourceAnalysis,
  type TicketResponseTime, type TicketAvgCloseTime, type ProjectBriefItem,
} from '@/api/dashboard';
import { TICKET_TYPE_DISPLAY_MAP } from '@/shared/constants/ticket';
import UserAvatarMenu from '@/shared/components/UserAvatarMenu';
import SubscriptionReminder from '@/shared/components/SubscriptionReminder';
import { MacDonut, MacLegend, MacStat, macTone } from '@/shared/components/macaronBits';
import { MacChevronRight, MacRefreshCw } from '@/shared/components/macaronIcons';
import { ProjectMonthBars } from '@/shared/components/macaronMonthBars';
import { currentYearMonth, normalizeSettlementPeriod } from '@/shared/utils/settlement';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';
import {
  buildDashboardFilterKey, loadDashboardCache, saveDashboardCache,
} from '@/stores/dashboardCache';
import { avatarUrl } from '@/api/profile';

interface MoreFunctionEntry { path: string; label: string; kind: MoreEntryIconKind; tone: string; group?: 'data-resource'; }

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
  { path: '/admin/data-resource', label: '数据资源', kind: 'data', tone: 'blue-2', group: 'data-resource' },
  { path: '/admin/daily-summary', label: '日报周报', kind: 'reports', tone: 'blue-3' },
  { path: '/admin/entries', label: '其他', kind: 'other', tone: 'blue-4' },
];

// 工单状态环图/图例按色阶由深到浅排列（对照 macaron 原型：处理中→暂停挂起→已关闭→已解决→已取消）
const STATUS_TONE_ORDER = ['status-1', 'status-2', 'status-3', 'status-4', 'status-5'];
const SORTED_TICKET_STATUS_LIST = [...TICKET_STATUS_LIST].sort(
  (a, b) => STATUS_TONE_ORDER.indexOf(a.tone) - STATUS_TONE_ORDER.indexOf(b.tone),
);

// 工单分布类卡片：类型分布按固定色阶（同类颜色稳定），响应时间按 快到慢 递进取色
const SOURCE_TONES = ['blue-1', 'blue-2', 'blue-3', 'blue-4', 'blue-5'];
const TYPE_TONE_ORDER = ['bug', 'feature', 'support', 'problem', 'other'];
// 接单人响应时间分桶 tone：越快越深（blue-1 最快 → blue-4 最慢「其他」）
const RESPONSE_TONE_MAP: Record<string, string> = {
  within_15m: 'blue-1',
  within_1h: 'blue-2',
  within_4h: 'blue-3',
  other: 'blue-4',
};

// 工单类型 → 固定色阶（环图扇区 / 条形图 / 下方颜色图例共用，同类颜色稳定）
function typeTone(key: string): string {
  return TYPE_TONE_ORDER.includes(key)
    ? SOURCE_TONES[TYPE_TONE_ORDER.indexOf(key)]
    : 'gray';
}

// 工单分布类卡片：环形图 + 可选图例的组合块（环图中心=总数，图例含百分比/数量）
function SourceDonut({
  title,
  items,
  centerLabel = '工单数',
  showPercentLabels = false,
  showLegend = true,
}: {
  title: string;
  items: { label: string; value: number; tone: string }[];
  centerLabel?: string;
  /** 在环图扇区中点上渲染百分比标签 */
  showPercentLabels?: boolean;
  /** 是否渲染右侧图例（百分比/数量）；类型分布图百分比已标在扇区上，省略图例 */
  showLegend?: boolean;
}) {
  const total = items.reduce((s, i) => s + i.value, 0);
  return (
    <div className="mac-source-block">
      <h4 className="mac-source-block__title">{title}</h4>
      <div className="mac-source-block__body">
        <MacDonut
          segments={items.map((i) => ({ value: i.value, tone: i.tone }))}
          centerValue={total}
          centerLabel={centerLabel}
          percentLabels={showPercentLabels}
        />
        {showLegend && (
          <MacLegend
            items={items.map((i) => ({
              key: i.label,
              label: i.label,
              value: i.value,
              tone: i.tone,
              percent: total > 0 ? Math.round((i.value / total) * 100) : 0,
            }))}
          />
        )}
      </div>
    </div>
  );
}

// 秒 → 人类可读耗时：<1h 分钟、<24h 小时、否则天（保留 1 位小数）
function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}分钟`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}小时`;
  return `${Math.round((seconds / 86400) * 10) / 10}天`;
}

// 各类型平均完单耗时：横向条形图（长度按最大耗时等比缩放，右侧标注格式化耗时）
function AvgCloseBars({
  title,
  items,
}: {
  title: string;
  items: { label: string; value: number; tone: string }[];
}) {
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <div className="mac-source-block">
      <h4 className="mac-source-block__title">{title}</h4>
      {items.length === 0 ? (
        <div style={{ padding: '28px 0', textAlign: 'center', fontSize: 12, color: 'var(--mac-muted-fg)' }}>
          暂无已关闭工单
        </div>
      ) : (
        <div>
          {items.map((it) => (
            <div key={it.label} className="mac-avgclose-bar">
              <span className="mac-avgclose-bar__label">{it.label}</span>
              <span className="mac-avgclose-bar__track">
                <span
                  className="mac-avgclose-bar__fill"
                  style={{
                    width: `${Math.max((it.value / max) * 100, 2)}%`,
                    background: macTone(it.tone),
                  }}
                />
              </span>
              <span className="mac-avgclose-bar__val">{formatDuration(it.value)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const { hasPermission, projectIds, username, name, avatarResourceId } = useAuthStore();
  const canAccessAdminEntries = hasPermission('frontend:admin:other:show');
  // 拥有此权限的用户不受「仅看自己关联项目」限制，可查看全部项目和工单
  const canViewAll = hasPermission(PERMISSION_VIEW_ALL);
  // stale-while-revalidate：优先用上次缓存的看板数据立即渲染图表（产品口径），
  // 本次 summary-all 返回后覆盖刷新；换账号/口径变化/过期则回退空态
  const filterKey = useMemo(
    () => buildDashboardFilterKey(canViewAll, projectIds ?? []),
    [canViewAll, projectIds],
  );
  const cachedRef = useRef(
    loadDashboardCache(username ?? '', filterKey)?.data ?? null,
  );
  const [ticketSummary, setTicketSummary] = useState<TicketSummary | null>(cachedRef.current?.tickets ?? null);
  const [sourceAnalysis, setSourceAnalysis] = useState<TicketSourceAnalysis | null>(cachedRef.current?.source ?? null);
  const [responseTime, setResponseTime] = useState<TicketResponseTime | null>(cachedRef.current?.response_time ?? null);
  const [avgCloseTime, setAvgCloseTime] = useState<TicketAvgCloseTime | null>(cachedRef.current?.avg_close_time ?? null);
  const [monthlySummary, setMonthlySummary] = useState<ProjectMonthlySummary | null>(cachedRef.current?.monthly ?? null);
  const [urgencySummary, setUrgencySummary] = useState<UrgencySummary | null>(cachedRef.current?.urgency ?? null);
  const [projects, setProjects] = useState<ProjectBriefItem[]>(cachedRef.current?.projects_brief ?? []);
  const [syncing, setSyncing] = useState(false);
  const [dataResourceSheetVisible, setDataResourceSheetVisible] = useState(false);

  const loadAll = useCallback(async () => {
    // canViewAll 时不传 projectIds（后端不过滤，返回全部）；否则仅统计当前用户关联项目
    const filterIds = canViewAll ? undefined : projectIds;
    // 首屏只发 summary-all 一个聚合请求（含轻量项目列表 projects_brief），
    // 不再单独请求 /projects?include_analysis=true（重分析字段首屏用不到）；
    // 返回前界面用上次缓存渲染（无缓存则空态），返回后覆盖刷新并写缓存
    fetchDashboardSummaryAll(filterIds)
      .then((all) => {
        setTicketSummary(all.tickets);
        setSourceAnalysis(all.source);
        setResponseTime(all.response_time);
        setAvgCloseTime(all.avg_close_time);
        setMonthlySummary(all.monthly);
        setUrgencySummary(all.urgency);
        setProjects(all.projects_brief ?? []);
        saveDashboardCache(username ?? '', buildDashboardFilterKey(canViewAll, projectIds ?? []), all);
      })
      .catch(() => {
        // 接口失败保持当前（缓存/空态）展示，不打断页面
      });
  }, [projectIds, canViewAll, username]);

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

  return (
    <div className="dashboard-page">
      <SubscriptionReminder username={username} />
      <Navbar
        title="后台管理"
        right={<UserAvatarMenu />}
        fixed
      />

      <div style={{ padding: '16px 16px 32px' }}>
        {/* ============ 顶：欢迎区（左侧头像 + 右侧两排：Hello / 用户名） ============ */}
        <div className="admin-welcome">
          <div className="admin-welcome__avatar">
            {avatarResourceId ? (
              <img src={avatarUrl(avatarResourceId)} alt="头像" />
            ) : (
              <UserCircleIcon size="40px" />
            )}
          </div>
          <div className="admin-welcome__text">
            <span className="admin-welcome__hello">Hello</span>
            <span className="admin-welcome__name">{name || username || '用户'}</span>
          </div>
        </div>

        {/* ============ 上：工单状态监测概览 ============ */}
        {/* 结构性重设计（对照 macaron admin 工单状态监测）：蓝阶环图 + 图例（含百分比/数量）+ 四指标卡 */}
        <SectionTitle title="工单监测" onMore={() => navigate('/tasks')} />
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
            <MacStat value={ticketSummary?.total ?? 0} label="总工单数" tone="blue-1" onClick={() => navigate('/admin/dashboard/tickets/all')} />
            <MacStat value={ticketSummary?.pending_count ?? 0} label="待处理" tone="blue-2" onClick={() => navigate('/admin/dashboard/tickets/pending')} />
            <MacStat value={ticketSummary?.overdue_count ?? 0} label="超时工单" tone="blue-3" onClick={() => navigate('/admin/dashboard/tickets/overdue')} />
            <MacStat value={formatPercent(ticketSummary?.resolved_rate)} label="解决率" tone="blue-4" />
          </div>
        </section>

        {/* ============ 中上：工单分布卡片（显示权限与「更多功能-其他」一致） ============ */}
        {canAccessAdminEntries && (
          <>
            {/* 工单类型分布（左环图，百分比标在扇区上、颜色图例在下方）+ 各类型平均完单耗时（右条形图） */}
            {/* 环图小扇区（<8%）数字放在色块侧边渲染，图例同样补上百分比，双保险保证手机端数字可读 */}
            <section className="mac-card mac-card--pad" style={{ marginTop: 12 }}>
              <div className="mac-source-split">
                <div className="mac-source-split__col">
                  <SourceDonut
                    title="工单类型分布"
                    showPercentLabels
                    showLegend={false}
                    items={(sourceAnalysis?.by_type ?? []).map((t) => ({
                      label: TICKET_TYPE_DISPLAY_MAP[t.key] ?? t.key,
                      value: t.count,
                      tone: typeTone(t.key),
                    }))}
                  />
                </div>
                <div className="mac-source-split__col">
                  {/* 完单耗时 = 关闭时间 - 创建时间，按类型取平均 */}
                  <AvgCloseBars
                    title="平均完单耗时"
                    items={(avgCloseTime?.by_type ?? []).map((t) => ({
                      label: TICKET_TYPE_DISPLAY_MAP[t.key] ?? t.key,
                      value: t.avg_seconds,
                      tone: typeTone(t.key),
                    }))}
                  />
                </div>
              </div>
              {/* 颜色图例：不同颜色代表不同工单类型，一排均分整行；每项附占比，
                  与环图扇区上的百分比同口径（Math.round），小扇区在图例上也能读到数字 */}
              <div className="mac-donut-legend">
                {(sourceAnalysis?.by_type ?? []).map((t, _i, arr) => {
                  const total = arr.reduce((s, x) => s + x.count, 0);
                  return (
                    <span key={t.key} className="mac-donut-legend__item">
                      <i className="mac-donut-legend__dot" style={{ background: macTone(typeTone(t.key)) }} />
                      {TICKET_TYPE_DISPLAY_MAP[t.key] ?? t.key}
                      <b className="mac-donut-legend__pct">{total > 0 ? Math.round((t.count / total) * 100) : 0}%</b>
                    </span>
                  );
                })}
              </div>
            </section>
            {/* 接单人响应时间（处理人第一次点开工单时间 - 新建时间，按区间分桶） */}
            <section className="mac-card mac-card--pad" style={{ marginTop: 12 }}>
              <SourceDonut
                title="接单人响应时间"
                centerLabel="已响应工单"
                items={(responseTime?.by_bucket ?? []).map((b) => ({
                  label: b.label,
                  value: b.count,
                  tone: RESPONSE_TONE_MAP[b.key] ?? 'gray',
                }))}
              />
            </section>
          </>
        )}

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
            <div key={e.path} className="dashboard-more-card" data-tone={e.tone} onClick={() => e.group === 'data-resource' ? setDataResourceSheetVisible(true) : navigate(e.path)}>
              <span className="dashboard-more-card__icon"><MoreEntryIcon kind={e.kind} /></span>
              <span className="dashboard-more-card__label">{e.label}</span>
            </div>
          ))}
        </div>

        {/* 数据资源：二级选择（资源管理 / 数据导入） */}
        <Popup visible={dataResourceSheetVisible} placement="bottom" onVisibleChange={setDataResourceSheetVisible}>
          <div style={{ padding: '8px 16px 20px', background: '#fff', borderRadius: '12px 12px 0 0' }}>
            <div style={{ textAlign: 'center', fontSize: 13, color: '#999', padding: '8px 0 12px' }}>数据资源</div>
            <div
              onClick={() => { console.log('[Dashboard] click 资源管理 → navigate /admin/file-explorer'); setDataResourceSheetVisible(false); navigate('/admin/file-explorer'); }}
              style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '14px 8px', borderBottom: '1px solid #f0f0f0', cursor: 'pointer' }}
            >
              <span style={{ fontSize: 18 }}>🗂️</span>
              <span style={{ fontSize: 15 }}>资源管理</span>
            </div>
            <div
              onClick={() => { setDataResourceSheetVisible(false); navigate('/admin/data-import'); }}
              style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '14px 8px', borderBottom: '1px solid #f0f0f0', cursor: 'pointer' }}
            >
              <span style={{ fontSize: 18 }}>📥</span>
              <span style={{ fontSize: 15 }}>数据导入</span>
            </div>
            <div
              onClick={() => setDataResourceSheetVisible(false)}
              style={{ textAlign: 'center', padding: '14px 0', color: '#999', fontSize: 15, cursor: 'pointer' }}
            >
              取消
            </div>
          </div>
        </Popup>
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
