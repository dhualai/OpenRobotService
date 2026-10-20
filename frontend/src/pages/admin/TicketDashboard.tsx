// 工单数据看板 —— 工单相关的四张数据看板（2026-10-07 用户口径，从后台管理首页拆出）：
//   ① 工单监测     —— 六种状态环图 + 图例（图例可点，下钻 /admin/dashboard/tickets/:key）
//   ② 工单类型分布 —— 类型环图（百分比标在扇区上）+ 各类型平均完单耗时条形图 + 颜色图例
//   ③ 接单人响应时间 —— 响应时间分桶环图（处理人第一次点开工单耗时 - 新建时间）
//   ④ 提单人公司/部门分布 —— 按提单人公司/部门归组的工单数量环图（公司下拉：不选=按公司，选=按该公司部门）
// 入口：后台管理首页「工单数据看板」小节右侧「其他数据统计」→ /admin/ticket-dashboard。
// 数据走 /dashboard/summary-all 聚合接口，与首页共用 dashboardCache
// （先渲染上次快照、返回后覆盖刷新）；②③④ 沿用原首页权限（frontend:admin:other:show），① 所有人可见。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Navbar } from 'tdesign-mobile-react';
import { useNavigate } from 'react-router-dom';
import { TICKET_STATUS_LIST } from '@/shared/constants/dashboard';
import {
  fetchDashboardSummaryAll, fetchDarLatest, fetchTicketCreatorOrgAnalysis,
  type TicketSummary, type TicketSourceAnalysis, type TicketResponseTime, type TicketAvgCloseTime,
  type DarReportSummary, type TicketCreatorOrgAnalysis,
} from '@/api/dashboard';
import { TICKET_TYPE_DISPLAY_MAP } from '@/shared/constants/ticket';
import { MacDonut, MacLegend, macTone } from '@/shared/components/macaronBits';
import { MacChevronRight } from '@/shared/components/macaronIcons';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';
import QuestionModuleCard from './QuestionModuleCard';
import {
  buildDashboardFilterKey, loadDashboardCache, saveDashboardCache,
} from '@/stores/dashboardCache';

// 工单状态环图/图例按色阶由深到浅排列，颜色按处理流程分配：
// 待处理(最深) → 处理中 → 暂停/挂起 → 已解决 → 已关闭 → 已取消(最浅)
const STATUS_TONE_ORDER = ['status-1', 'status-2', 'status-3', 'status-4', 'status-5', 'status-6'];
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

// 提单人组织分布 tone：组织按工单数排序循环取 status 蓝阶（与全页同族蓝、亮度等距拉开），
// 「其他/未分配公司/未分配部门」归灰色，与真实组织区分
const ORG_TONES = ['status-1', 'status-2', 'status-3', 'status-4', 'status-5', 'status-6'];
function orgTone(label: string, index: number): string {
  return label === '其他' || label === '未分配公司' || label === '未分配部门'
    ? 'gray'
    : ORG_TONES[index % ORG_TONES.length];
}

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

export default function TicketDashboard() {
  const navigate = useNavigate();
  const { hasPermission, projectIds, username } = useAuthStore();
  // 与首页同口径：②③ 两张分布图只对有后台「其他」入口权限的人展示
  const canAccessAdminEntries = hasPermission('frontend:admin:other:show');
  // 拥有此权限的用户不受「仅看自己关联项目」限制，可查看全部项目和工单
  const canViewAll = hasPermission(PERMISSION_VIEW_ALL);
  // stale-while-revalidate：优先用上次缓存的看板数据立即渲染图表（与首页共用缓存），
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
  // ④ 提单人公司/部门分布：独立于 summary-all 拉取（公司下拉切换维度需单独重拉，不进聚合缓存）
  const [orgAnalysis, setOrgAnalysis] = useState<TicketCreatorOrgAnalysis | null>(null);
  const [orgCompanyId, setOrgCompanyId] = useState<string>('');

  const loadAll = useCallback(async () => {
    // canViewAll 时不传 projectIds（后端不过滤，返回全部）；否则仅统计当前用户关联项目
    const filterIds = canViewAll ? undefined : projectIds;
    fetchDashboardSummaryAll(filterIds)
      .then((all) => {
        setTicketSummary(all.tickets);
        setSourceAnalysis(all.source);
        setResponseTime(all.response_time);
        setAvgCloseTime(all.avg_close_time);
        saveDashboardCache(username ?? '', buildDashboardFilterKey(canViewAll, projectIds ?? []), all);
      })
      .catch(() => {
        // 接口失败保持当前（缓存/空态）展示，不打断页面
      });
  }, [projectIds, canViewAll, username]);

  useEffect(() => { loadAll(); }, [loadAll]);

  const loadOrg = useCallback(async () => {
    // 口径与全页一致：canViewAll 时不传 projectIds；orgCompanyId 空=按公司，非空=按该公司部门
    const filterIds = canViewAll ? undefined : projectIds;
    fetchTicketCreatorOrgAnalysis(filterIds, orgCompanyId || undefined)
      .then(setOrgAnalysis)
      .catch(() => {
        // 接口失败保持当前展示，不打断页面
      });
  }, [projectIds, canViewAll, orgCompanyId]);

  useEffect(() => { loadOrg(); }, [loadOrg]);

  // AI 直答率周报（dar_weekly 自动推送到后端，最新一期；无数据时区块整体隐藏）
  const [dar, setDar] = useState<DarReportSummary | null>(null);
  useEffect(() => {
    if (!canAccessAdminEntries) return;
    fetchDarLatest('prod').then(setDar);
  }, [canAccessAdminEntries]);

  return (
    <div>
      <Navbar title="工单数据看板" leftArrow onLeftClick={() => navigate(-1)} fixed />
      <div style={{ padding: '16px 16px 32px', paddingTop: 64 }}>
        {/* ============ ① 工单监测：状态分布环图 + 图例（点图例下钻该状态工单明细） ============ */}
        <section className="mac-card mac-card--pad">
          <div className="mac-source-block__title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span>工单监测</span>
            <span
              className="mac-section-title__more"
              style={{ marginLeft: 'auto' }}
              onClick={() => navigate('/tasks')}
            >
              查看明细 <MacChevronRight size={14} />
            </span>
          </div>
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
        </section>

        {/* ============ ② 工单类型分布（左环图，百分比标在扇区上）+ 各类型平均完单耗时（右条形图） ============ */}
        {/* 环图小扇区（<8%）数字放在色块侧边渲染，图例同样补上百分比，双保险保证手机端数字可读 */}
        {canAccessAdminEntries && (
          <>
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
            {/* ============ ③ 接单人响应时间（处理人第一次点开工单时间 - 新建时间，按区间分桶） ============ */}
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
            {/* ============ ④ 提单人公司/部门分布（按提单人公司/部门归组累加工单数量；
                 下拉不选默认按公司统计，选择公司后按该公司下的部门统计；范围与全页一致） ============ */}
            <section className="mac-card mac-card--pad" style={{ marginTop: 12 }}>
              <div className="mac-source-block__title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span>{orgAnalysis?.dimension === 'department' ? '提单人部门分布' : '提单人公司分布'}</span>
                {/* 公司下拉筛选：不选按公司归组；选中后切换为该公司部门维度 */}
                <select
                  value={orgCompanyId}
                  onChange={(e) => setOrgCompanyId(e.target.value)}
                  style={{
                    marginLeft: 'auto', maxWidth: '52%', padding: '4px 8px', fontSize: 12,
                    color: 'var(--mac-fg)', background: 'var(--mac-secondary)',
                    border: '1px solid var(--mac-border)', borderRadius: 8,
                  }}
                >
                  <option value="">全部公司</option>
                  {(orgAnalysis?.companies ?? []).map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              </div>
              <div className="mac-source-block__body">
                <MacDonut
                  segments={(orgAnalysis?.by_org ?? []).map((o, i) => ({
                    value: o.count,
                    tone: orgTone(o.label, i),
                  }))}
                  centerValue={(orgAnalysis?.by_org ?? []).reduce((s, o) => s + o.count, 0)}
                  centerLabel="工单数"
                />
                <MacLegend
                  items={(orgAnalysis?.by_org ?? []).map((o, i) => {
                    const total = (orgAnalysis?.by_org ?? []).reduce((s, x) => s + x.count, 0);
                    return {
                      key: o.label,
                      label: o.label,
                      value: o.count,
                      tone: orgTone(o.label, i),
                      percent: total > 0 ? Math.round((o.count / total) * 100) : 0,
                    };
                  })}
                />
              </div>
            </section>
          </>
        )}
        {canAccessAdminEntries && dar && (
          <section className="mac-card mac-card--pad" style={{ marginTop: 12 }}>
            <div className="mac-source-block__title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span>AI 直答率</span>
              <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--muted-foreground, #98a2b3)' }}>
                {dar.week_date.slice(0, 4)}-{dar.week_date.slice(4, 6)}-{dar.week_date.slice(6, 8)} 周报
              </span>
            </div>
            {(() => {
              const wb = dar.workbench;
              if (wb?.hero?.length) {
                // 有工作台快照：整块按工作台首页那张卡渲染，数字与工作台完全一致
                const main = wb.hero.find((c) => c.label.includes('L2')) || wb.hero[0];
                const rest = [...wb.hero.filter((c) => c !== main), ...(wb.small || [])];
                return (
                  <>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, margin: '10px 0 2px' }}>
                      <span style={{ fontSize: 34, fontWeight: 700, color: 'var(--primary)', lineHeight: 1 }}>
                        {main.value}
                      </span>
                      <span style={{ fontSize: 13, color: 'var(--muted-foreground, #98a2b3)' }}>
                        {main.label} · 端到端直答率
                      </span>
                    </div>
                    {main.sub && (
                      <div style={{ fontSize: 12, color: 'var(--muted-foreground, #98a2b3)', marginTop: 2 }}>
                        {main.sub}
                      </div>
                    )}
                    <div
                      className="mac-stat-row"
                      style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))' }}
                    >
                      {rest.map((c) => (
                        <div key={c.label} title={c.sub || ''}>
                          <div style={{ fontSize: 12, color: 'var(--muted-foreground, #98a2b3)' }}>{c.label}</div>
                          <div style={{ fontSize: 16, fontWeight: 600 }}>{c.value}</div>
                        </div>
                      ))}
                    </div>
                    <div style={{ marginTop: 8, fontSize: 12, color: 'var(--muted-foreground, #98a2b3)' }}>
                      {wb.manual_progress && <>人工标注进度：{wb.manual_progress}</>}
                      {wb.manual_progress && wb.export_at && <> · </>}
                      {wb.export_at && <>数据版本：{wb.export_at}</>}
                    </div>
                  </>
                );
              }
              const wowL2 = dar.wow?.['L2_人工'];
              const up = wowL2 ? wowL2.delta >= 0 : true;
              return (
                <>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, margin: '10px 0 2px' }}>
                    {/* 主口径：端到端直答率（L2 人工，同分母环比取整） */}
                    <span style={{ fontSize: 34, fontWeight: 700, color: 'var(--primary)', lineHeight: 1 }}>
                      {wowL2 ? `${wowL2.curr.toFixed(1)}%` : (dar.dar_rates?.['L2_人工'] || '—').split('｜')[0].replace('端到端 ', '')}
                    </span>
                    <span style={{ fontSize: 13, color: 'var(--muted-foreground, #98a2b3)' }}>端到端直答率（人工口径）</span>
                    {wowL2 && (
                      <span style={{ fontSize: 13, fontWeight: 600, color: up ? '#1f8a5a' : '#d9534f' }}>
                        {up ? '↑' : '↓'} {Math.abs(wowL2.delta).toFixed(1)}pp
                      </span>
                    )}
                  </div>
                  <div className="mac-stat-row" style={{ marginTop: 10 }}>
                    {wowL2 && (
                      <div>
                        <div style={{ fontSize: 12, color: 'var(--muted-foreground, #98a2b3)' }}>上周</div>
                        <div style={{ fontSize: 16, fontWeight: 600 }}>{wowL2.prev.toFixed(1)}%</div>
                      </div>
                    )}
                    {dar.wow?.['L3_AI同段'] && (
                      <div>
                        <div style={{ fontSize: 12, color: 'var(--muted-foreground, #98a2b3)' }}>L3 AI 参考</div>
                        <div style={{ fontSize: 16, fontWeight: 600 }}>{dar.wow['L3_AI同段'].curr.toFixed(1)}%</div>
                      </div>
                    )}
                    <div>
                      <div style={{ fontSize: 12, color: 'var(--muted-foreground, #98a2b3)' }}>KB 缺口率</div>
                      <div style={{ fontSize: 16, fontWeight: 600 }}>{(dar.kb_gap || '—').split('（')[0]}</div>
                    </div>
                    {dar.this_week?.n_segs != null && (
                      <div>
                        <div style={{ fontSize: 12, color: 'var(--muted-foreground, #98a2b3)' }}>本周新增</div>
                        <div style={{ fontSize: 16, fontWeight: 600 }}>{dar.this_week.n_convs ?? '—'} 会话 / {dar.this_week.n_segs} 段</div>
                      </div>
                    )}
                  </div>
                  {dar.manual_progress && (
                    <div style={{ marginTop: 8, fontSize: 12, color: 'var(--muted-foreground, #98a2b3)' }}>
                      人工标注进度：{dar.manual_progress}
                    </div>
                  )}
                </>
              );
            })()}
          </section>
        )}
        {canViewAll && <QuestionModuleCard key={username} />}
      </div>
    </div>
  );
}
