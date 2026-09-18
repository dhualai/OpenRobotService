// 项目工单卡（项目详情页）—— 对照原型 components/project/ProjectTicketsCard.tsx，
// 位于「项目信息管理」与「项目动态」之间，卡片三部分：
//   ① 顶部三格汇总：总工单数 / 正在处理 / 已完成（状态 key 与仪表盘同口径）；
//   ② 核心阻滞工单（AI 配置判定，未配置时按优先级/超期默认排序）；
//      条目整块可点，跳转该工单详情页 /tasks/:id（与仪表盘「工单明细」列表同交互）；
//   ③ 工单变化趋势（近 8 周每周新建工单数，柱状图，echarts）。
//
// 数据源：GET /api/admin/project-tickets/projects/{id}/overview（系统任务 tasks 表，
// 与「工单状态监测」页的 AI tickets 表是两个数据源）。
// 「配置阻滞权重」入口仅管理员及超级管理员可见（与后端 get_current_admin_user 同一判据：
// permissions 含 admin）；点开弹窗输入判定要求，服务端把项目 + 工单基础数据交大模型
// 判定核心阻滞工单并落库，返回更新后的阻滞板块直接替换展示。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Popup, Textarea, Toast } from 'tdesign-mobile-react';
import {
  configureBlockingWeightsApi,
  fetchProjectTicketsOverviewApi,
  type ProjectTicketsBlocking,
  type ProjectTicketsOverview,
} from '@/api/projectTickets';
import { useAuthStore } from '@/stores/auth';
import ReactECharts from '@/shared/components/ReactECharts';
import { macTone } from '@/shared/components/macaronBits';
import {
  MacAlertTriangle, MacChevronDown, MacChevronUp, MacSparkles,
} from '@/shared/components/macaronIcons';
import { TICKET_STATUS_MAP } from '@/shared/constants/dashboard';
import { PRIORITY_DISPLAY_MAP } from '@/shared/constants/ticket';
import { navigateInWechat } from '@/shared/utils/wechatJsSdk';

// 后端条目给的是原始 TaskStatus 枚举值；卡片展示统一转到与仪表盘一致的前端状态 key
// （pending→暂停/挂起、canceled→已取消），标签/配色直接复用仪表盘常量。
const RAW_STATUS_TO_KEY: Record<string, string> = {
  new: 'new',
  in_progress: 'in_progress',
  pending: 'paused',
  resolved: 'resolved',
  closed: 'closed',
  canceled: 'cancelled',
};

/** 近 8 周柱状图：横轴 M/D，青色系单序列（与后台管理其它柱状图同风格） */
const TREND_BAR_COLOR = '#3697c3';

export default function ProjectTicketsCard({ projectId }: { projectId: string }) {
  // 「配置阻滞权重」仅管理员/超级管理员可见（与后端 get_current_admin_user 的判据一致）
  const canConfigure = useAuthStore((s) => Array.isArray(s.permissions) && s.permissions.includes('admin'));

  const navigate = useNavigate();

  const [overview, setOverview] = useState<ProjectTicketsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [retryToken, setRetryToken] = useState(0);
  const [collapsed, setCollapsed] = useState(false);

  const [configOpen, setConfigOpen] = useState(false);
  const [promptDraft, setPromptDraft] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // 从编辑页返回（重新挂载）、切换项目与手动重试时重新拉取
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(false);
    fetchProjectTicketsOverviewApi(projectId)
      .then((data) => { if (!cancelled) setOverview(data); })
      .catch(() => { if (!cancelled) { setOverview(null); setLoadError(true); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [projectId, retryToken]);

  const openConfig = useCallback(() => {
    setPromptDraft(overview?.blocking.prompt || '');
    setConfigOpen(true);
  }, [overview]);

  // 提交：服务端进行 AI 判定（可能耗时数秒），成功后用返回的阻滞板块替换展示
  const submitConfig = useCallback(async () => {
    if (submitting) return;
    if (!promptDraft.trim()) {
      Toast({ message: '请先输入阻滞权重的判定要求', theme: 'warning' });
      return;
    }
    setSubmitting(true);
    try {
      const blocking: ProjectTicketsBlocking = await configureBlockingWeightsApi(projectId, promptDraft.trim());
      setOverview((prev) => (prev ? { ...prev, blocking } : prev));
      setConfigOpen(false);
      Toast({ message: '阻滞权重已更新，核心阻滞工单已按 AI 判定展示', theme: 'success' });
    } catch (err) {
      Toast({ message: `配置失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmitting(false);
    }
  }, [projectId, promptDraft, submitting]);

  // 顶部三格汇总的口径（状态 key 与仪表盘「工单状态监测」一致）：
  // 正在处理 = 处理中 + 暂停/挂起（即仪表盘「待处理」口径，新建尚未开始处理不计入）；
  // 已完成 = 已解决 + 已关闭（已取消不算完成，只在总数中体现）。
  const inProgressCount = (overview?.by_status.in_progress ?? 0) + (overview?.by_status.paused ?? 0);
  const completedCount = (overview?.by_status.resolved ?? 0) + (overview?.by_status.closed ?? 0);

  const weekly = overview?.weekly ?? [];
  const trendOption = useMemo(() => ({
    color: [TREND_BAR_COLOR],
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 4, right: 4, top: 16, bottom: 0, containLabel: true },
    xAxis: {
      type: 'category',
      data: weekly.map((item) => {
        const [, month, day] = item.week_start.split('-');
        return `${Number(month)}/${Number(day)}`;
      }),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e8eaea' } },
      axisLabel: { color: '#888d8f', fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: '#f1f4f4' } },
      axisLabel: { color: '#888d8f', fontSize: 10 },
    },
    series: [{
      type: 'bar',
      name: '新建工单',
      data: weekly.map((item) => item.count),
      barMaxWidth: 22,
      itemStyle: { color: TREND_BAR_COLOR, borderRadius: [4, 4, 0, 0] },
    }],
  }), [weekly]);

  const blocking = overview?.blocking;
  const blockingTickets = blocking?.tickets ?? [];

  return (
    <section className="mac-card mac-card--pad" style={{ marginBottom: 12 }}>
      <div className="mac-info__head">
        <h3 className="mac-info__title">项目工单</h3>
        <div className="mac-info__actions">
          <button
            type="button"
            className="mac-btn mac-btn--ghost mac-info__collapse"
            onClick={() => setCollapsed((value) => !value)}
            aria-label={collapsed ? '展开' : '折叠'}
          >
            {collapsed ? <MacChevronDown size={16} /> : <MacChevronUp size={16} />}
          </button>
        </div>
      </div>

      {loading ? (
        <div className="mac-info__state">正在加载项目工单…</div>
      ) : loadError || !overview ? (
        <div className="mac-info__state">
          项目工单加载失败
          <div className="mac-info__state-sub">请检查网络后重试</div>
          <button
            type="button"
            className="mac-btn mac-btn--outline"
            style={{ marginTop: 12 }}
            onClick={() => setRetryToken((value) => value + 1)}
          >
            重新加载
          </button>
        </div>
      ) : (
        <>
          {!collapsed && (
            <>
              {/* ① 顶部三格汇总：总工单数 / 正在处理 / 已完成（口径见上方注释） */}
              <div className="mac-tix__stats">
                <div className="mac-tix__stat mac-tix__stat--total">
                  <span className="mac-tix__num">{overview.total}</span>
                  <span className="mac-tix__label">总工单数</span>
                </div>
                <div className="mac-tix__stat">
                  <span className="mac-tix__num">{inProgressCount}</span>
                  <span className="mac-tix__label">正在处理</span>
                </div>
                <div className="mac-tix__stat">
                  <span className="mac-tix__num">{completedCount}</span>
                  <span className="mac-tix__label">已完成</span>
                </div>
              </div>

              {/* ② 核心阻滞工单（与上一区块之间加浅灰分节线） */}
              <div className="mac-tix__sep" aria-hidden="true" />
              <div className="mac-tix__block">
                <div className="mac-tix__block-head">
                  <span className="mac-tix__block-title">
                    <MacAlertTriangle size={14} />核心阻滞工单
                  </span>
                  <div className="mac-tix__block-ops">
                    <span className={`mac-tix__mode${blocking?.mode === 'ai' ? ' is-ai' : ''}`}>
                      {blocking?.mode === 'ai' ? 'AI 判定' : '默认排序'}
                    </span>
                    {canConfigure && (
                      <button type="button" className="mac-btn mac-btn--ghost mac-tix__config" onClick={openConfig}>
                        <MacSparkles size={13} />配置阻滞权重
                      </button>
                    )}
                  </div>
                </div>

                {blocking?.mode === 'ai' && blocking.summary ? (
                  <p className="mac-tix__ai-summary">{blocking.summary}</p>
                ) : null}

                {blockingTickets.length === 0 ? (
                  <div className="mac-tix__empty">暂无未完成的阻滞工单</div>
                ) : (
                  blockingTickets.map((ticket) => {
                    const meta = TICKET_STATUS_MAP[RAW_STATUS_TO_KEY[ticket.status] ?? ticket.status];
                    const reason = blocking?.reasons?.[String(ticket.id)];
                    // 整块可点：跳转该工单详情页（/tasks/:id，与仪表盘「工单明细」列表同交互）
                    return (
                      <article
                        key={ticket.id}
                        className="mac-tix__ticket"
                        onClick={() => navigateInWechat(navigate, `/tasks/${ticket.id}`)}
                      >
                        <div className="mac-tix__ticket-head">
                          <span className="mac-tix__ticket-title">{ticket.title}</span>
                          <span className="mac-tix__ticket-tail">
                            <span className={`mac-tix__pri mac-tix__pri--${ticket.priority || 'medium'}`}>
                              {PRIORITY_DISPLAY_MAP[ticket.priority] ?? ticket.priority}
                            </span>
                            <span className="mac-tix__ticket-go" aria-hidden="true">›</span>
                          </span>
                        </div>
                        <div className="mac-tix__ticket-meta">
                          <span>#{ticket.id}</span>
                          {meta && (
                            <span className="mac-tix__status">
                              <i className="mac-tix__status-dot" style={{ background: macTone(meta.tone) }} />
                              {meta.label}
                            </span>
                          )}
                          {ticket.overdue && <span className="mac-tix__overdue">已超期</span>}
                          <span>提单人 {ticket.creator_name || '—'}</span>
                          <span>接单人 {ticket.assignee_name || '—'}</span>
                        </div>
                        {ticket.description && (
                          <p className="mac-tix__ticket-desc">{ticket.description}</p>
                        )}
                        {reason && (
                          <p className="mac-tix__reason">
                            <MacSparkles size={12} />{reason}
                          </p>
                        )}
                      </article>
                    );
                  })
                )}
                {blocking?.mode !== 'ai' && (
                  <p className="mac-tix__hint">
                    当前按优先级、截止时间与创建时间默认排序{canConfigure ? '，点「配置阻滞权重」让 AI 判定' : ''}
                  </p>
                )}
              </div>

              {/* ③ 工单变化趋势：近 8 周新建工单数（同样以分节线隔开） */}
              <div className="mac-tix__sep" aria-hidden="true" />
              <div className="mac-tix__trend">
                <div className="mac-tix__trend-title">工单变化趋势（每周新建）</div>
                <ReactECharts option={trendOption} style={{ height: 160, width: '100%' }} />
              </div>
            </>
          )}
        </>
      )}

      {/* 配置阻滞权重：输入判定要求 → 服务端 AI 判定核心阻滞工单 */}
      <Popup visible={configOpen} onClose={() => !submitting && setConfigOpen(false)} placement="bottom" showOverlay>
        <div className="mac-sheet mac-tix__config-sheet">
          <h4 className="mac-sheet__title">配置阻滞权重</h4>
          <p className="mac-tix__config-hint">
            将把项目基础信息与该项目工单数据交给 AI，按你的要求判定最影响项目推进的核心阻滞工单，
            判定结果用于「核心阻滞工单」展示。
          </p>
          <Textarea
            value={promptDraft}
            onChange={(value) => setPromptDraft(String(value))}
            placeholder="例如：优先挑选影响现场验收、客户多次催办且长期未解决的工单；正在挂起的工单权重更高"
            maxlength={1000}
            autosize={{ minRows: 4, maxRows: 8 }}
          />
          <div className="mac-tix__config-actions">
            <button
              type="button"
              className="mac-btn mac-btn--outline"
              disabled={submitting}
              onClick={() => setConfigOpen(false)}
            >
              取消
            </button>
            <button
              type="button"
              className="mac-btn mac-btn--primary"
              disabled={submitting}
              onClick={submitConfig}
            >
              {submitting ? 'AI 判定中...' : '提交判定'}
            </button>
          </div>
        </div>
      </Popup>
    </section>
  );
}
