// 开发者模式 →「AI 直答率」页签（ORS-716）。
//
// 数据来自 dar_weekly 推送的周报摘要（backend/_summary 透出）：
//   funnel / funnel_weekly  —— 层级漏斗（累计 + 最近 8 个完整自然周）
//   fail_top                —— 未直答/未覆盖 Top 品类（类型/条数/样例，与漏斗同口径）
// 统计口径与工作台首页那张卡同源，不在前端重算。
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loading } from 'tdesign-mobile-react';

import { fetchDarLatest, type DarFunnelBlock, type DarReportSummary } from '@/api/dashboard';
import { FunnelBridge, FunnelStepRow, funnelWidthPct, pct, rateOf } from '@/shared/components/funnelBits';

const DAR_LAYER_LABEL: Record<string, string> = {
  answered: '直答正确',
  unanswered: '未直答',
  uncovered: '未覆盖',
  ticket: '直接转工单',
  chitchat: '闲聊',
  undetermined: '未判定',
  suggested: '推荐池混合',
  tester: '测试会话',
};

// 已判定三个分叉的配色：直答正确 / 未直答 / 未覆盖
const SPLIT_COLORS = { correct: '#2f7d5d', wrong: '#d97706', uncovered: '#9aa7b0' };

type WeekBucket = NonNullable<DarReportSummary['funnel_weekly']>[number];

function weekRate(w: WeekBucket): number | null {
  return rateOf(Number(w?.funnel?.correct ?? 0), Number(w?.funnel?.judged ?? 0));
}

/** 数字卡：label / value / hint 各占一行、等高，四张卡的数字基线才对得齐。 */
function StatTile({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="dispatch-dev__card" style={{ flex: '1 1 150px', minWidth: 150, padding: '10px 12px' }}>
      <div style={{
        fontSize: 12, lineHeight: '16px', height: 16, color: 'var(--muted-foreground, #98a2b3)',
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, color: 'var(--primary)', lineHeight: '34px', height: 34 }}>{value}</div>
      <div style={{
        fontSize: 11, lineHeight: '16px', height: 16, color: 'var(--muted-foreground, #98a2b3)',
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      }}>{hint}</div>
    </div>
  );
}

/** 近 N 周直答率折线（单序列、纵轴按区间缩放并注明）。
 *  当前选中的周会高亮（大点 + 竖虚线 + 标值），否则切周时只有别的块在变、图不动会很怪。 */
function RateTrend({ weeks, activeWeek }: { weeks: WeekBucket[]; activeWeek?: string }) {
  if (weeks.length < 2) return null;
  const vals = weeks.map((w) => Number((weekRate(w) ?? 0) * 100));
  const W = 340, H = 64, PAD = 12;
  const mn = Math.min(...vals), mx = Math.max(...vals);
  const span = Math.max(mx - mn, 1);
  const x = (i: number) => PAD + (i * (W - PAD * 2)) / (vals.length - 1);
  const y = (v: number) => H - PAD - ((v - mn) / span) * (H - PAD * 2);
  const sel = activeWeek ? weeks.findIndex((w) => w.week === activeWeek) : -1;
  const mark = sel >= 0 ? sel : vals.length - 1;
  const markY = y(vals[mark]);
  const labelY = markY < 16 ? markY + 14 : markY - 8;
  return (
    <>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        role="img"
        aria-label={`近 ${weeks.length} 周直答率：${weeks.map((w, i) => `${w.label} ${vals[i].toFixed(1)}%`).join('，')}`}
        style={{ display: 'block', marginTop: 4 }}
      >
        {sel >= 0 && (
          <line
            x1={x(sel)} y1={PAD - 6} x2={x(sel)} y2={H - PAD + 4}
            stroke="var(--primary)" strokeWidth={1} strokeDasharray="2 3" opacity={0.45}
          />
        )}
        <polyline
          points={vals.map((v, i) => `${x(i)},${y(v)}`).join(' ')}
          fill="none" stroke="var(--primary)" strokeWidth={2}
          strokeLinejoin="round" strokeLinecap="round"
        />
        {vals.map((v, i) => (
          <g key={weeks[i].week}>
            <circle
              cx={x(i)} cy={y(v)} r={i === sel ? 4.5 : 3}
              fill={i === sel ? 'var(--primary)' : '#fff'}
              stroke="var(--primary)" strokeWidth={1.6}
            />
            <title>{`${weeks[i].label}：${v.toFixed(1)}%（${weeks[i].funnel.correct}/${weeks[i].funnel.judged}）`}</title>
          </g>
        ))}
        <text x={x(mark)} y={labelY} textAnchor="middle"
              fontSize={11} fontWeight={600} fill="var(--primary)">
          {vals[mark].toFixed(1)}%
        </text>
      </svg>
      <div style={{ fontSize: 11, color: 'var(--muted-foreground, #98a2b3)', textAlign: 'center' }}>
        近 {weeks.length} 周 {weeks[0].label}~{weeks[weeks.length - 1].label}，
        区间 {mn.toFixed(1)}% ~ {mx.toFixed(1)}%（纵轴按此区间缩放）
      </div>
    </>
  );
}

export default function DarFunnelPanel() {
  const [dar, setDar] = useState<DarReportSummary | null>(null);
  const nav = useNavigate();
  const [loading, setLoading] = useState(true);
  // '' = 默认最近一周；'all' = 全部合计（所有历史数据）
  const [sel, setSel] = useState<string>('');

  useEffect(() => {
    fetchDarLatest('prod').then((d) => setDar(d)).finally(() => setLoading(false));
  }, []);

  const weeks = useMemo(
    () => [...(dar?.funnel_weekly ?? [])].sort((a, b) => a.week_start.localeCompare(b.week_start)),
    [dar],
  );

  // 默认选最近一周；选中的周不存在（老数据）时也回落到最近一周
  const activeKey = useMemo(() => {
    if (sel === 'all') return 'all';
    if (sel && weeks.some((w) => w.week === sel)) return sel;
    return weeks.length ? weeks[weeks.length - 1].week : 'all';
  }, [sel, weeks]);

  const active: DarFunnelBlock | null = useMemo(() => {
    if (!dar) return null;
    // 「全部合计」= 报告里的累计漏斗（所有历史数据），不是 8 周相加
    if (activeKey === 'all') return dar.funnel ?? null;
    return weeks.find((w) => w.week === activeKey)?.funnel ?? dar.funnel ?? null;
  }, [dar, activeKey, weeks]);

  const activeLabel = useMemo(() => {
    if (activeKey === 'all') return '全部合计（所有历史数据）';
    const w = weeks.find((x) => x.week === activeKey);
    if (!w) return '累计';
    return w.week === weeks[weeks.length - 1]?.week ? `${w.label}（最近一周）` : w.label;
  }, [activeKey, weeks]);

  if (loading) return <div className="dispatch-dev__empty"><Loading text="加载中..." /></div>;
  if (!dar) return <div className="dispatch-dev__empty">还没有直答率周报，先跑一次 dar_weekly 的 report 步</div>;
  if (!active) return <div className="dispatch-dev__empty">周报里没有漏斗数据，用新版 dar_weekly 重跑一次 report 步</div>;

  const rate = rateOf(active.correct, active.judged);
  const enterQA = active.real - active.ticket;
  const dropPre = active.tester + active.chitchat + active.suggested;
  const layerRows = Object.entries(active.layers || {})
    .filter(([, n]) => Number(n) > 0)
    .sort((a, b) => Number(b[1]) - Number(a[1]));
  // 未直答 Top 品类跟着区间走：全部合计用累计，选某周用那一周的
  const failTop = activeKey === 'all'
    ? (dar.fail_top ?? [])
    : (weeks.find((w) => w.week === activeKey)?.fail_top ?? []);
  const splits = [
    { k: '直答正确', n: active.correct, color: SPLIT_COLORS.correct },
    { k: '未直答', n: active.wrong, color: SPLIT_COLORS.wrong },
    { k: '未覆盖', n: active.uncovered, color: SPLIT_COLORS.uncovered },
  ];

  return (
    <div>
      <section className="dispatch-dev__card">
        <div className="dispatch-dev__head">
          <span className="dispatch-dev__title">AI 直答率 · 每周漏斗</span>
          <span style={{ fontSize: 12, color: 'var(--muted-foreground, #98a2b3)' }}>
            {dar.week_date.slice(0, 4)}-{dar.week_date.slice(4, 6)}-{dar.week_date.slice(6, 8)} 周报
          </span>
          <button
            type="button"
            onClick={() => nav('/admin/kb/supplement')}
            style={{ marginLeft: 'auto', fontSize: 12.5, color: '#2563eb', background: 'none', border: 'none', cursor: 'pointer' }}
            title="未覆盖问题 → 待补充知识点（部门补答案 → 审核 → 入库）"
          >
            去补知识 →
          </button>
        </div>

        <div className="dispatch-dev__week-pills">
          <button
            type="button"
            className={`dispatch-dev__pill${activeKey === 'all' ? ' is-on' : ''}`}
            onClick={() => setSel('all')}
          >
            全部合计
          </button>
          {[...weeks].reverse().map((w) => (
            <button
              key={w.week}
              type="button"
              className={`dispatch-dev__pill${activeKey === w.week ? ' is-on' : ''}`}
              onClick={() => setSel(w.week)}
            >
              {w.label}
            </button>
          ))}
        </div>

        <div style={{ fontSize: 12, color: 'var(--muted-foreground, #98a2b3)', margin: '2px 0 8px' }}>
          当前区间：{activeLabel}
        </div>

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
          <StatTile label="直答率" value={pct(rate)} hint={`${active.correct} / ${active.judged} 段`} />
          <StatTile label="已判定段" value={`${active.judged}`} hint="直答率分母" />
          <StatTile label="直答正确" value={`${active.correct}`} hint={`未直答 ${active.wrong} · 未覆盖 ${active.uncovered}`} />
          <StatTile label="真实咨询" value={`${active.real}`} hint={`其中直接转工单 ${active.ticket}`} />
        </div>

        <div className="dispatch-dev__funnels dar-funnel">
          <div className="dispatch-dev__funnel">
            <div className="dispatch-dev__funnel-head">
              <strong>回答漏斗</strong>
              <em>层宽 = 本层段数 / 全部段</em>
            </div>
            <FunnelStepRow
              widthPct={100}
              title="全部段"
              countLabel={`${active.total} 段`}
              right="100%"
            />
            <FunnelBridge
              deductLabel="测试 / 闲聊 / 推荐池混合"
              deduct={dropPre}
              remain={active.real}
              remainLabel="真实咨询"
              unit="段"
            />
            <FunnelStepRow
              widthPct={funnelWidthPct(active.real, active.total)}
              title="真实咨询"
              countLabel={`${active.real} 段`}
              right={`占全部 ${pct(active.total ? active.real / active.total : null)}`}
              tone="mid"
            />
            <FunnelBridge
              deductLabel="直接转工单"
              deduct={active.ticket}
              remain={enterQA}
              remainLabel="进入问答"
              unit="段"
            />
            <FunnelStepRow
              widthPct={funnelWidthPct(enterQA, active.total)}
              title="进入问答"
              countLabel={`${enterQA} 段`}
              right={`占全部 ${pct(active.total ? enterQA / active.total : null)}`}
              tone="mid"
            />
            <FunnelBridge
              deductLabel="未判定"
              deduct={active.undetermined}
              remain={active.judged}
              remainLabel="已判定"
              unit="段"
            />
            {/* 直答率只在分母这一层出现一次 */}
            <FunnelStepRow
              widthPct={funnelWidthPct(active.judged, active.total)}
              title="已判定"
              countLabel={`${active.judged} 段`}
              rate={rate}
              rateLabel="直答率"
              rateHint={`${active.correct}/${active.judged}`}
              tone="warn"
            />
            {/* 已判定的构成：一条分段条 + 居中图例 */}
            <div style={{ marginTop: 6, paddingLeft: 2 }}>
              <div style={{ display: 'flex', gap: 2, height: 12, borderRadius: 6, overflow: 'hidden' }}>
                {splits.filter((x) => x.n > 0).map((x) => (
                  <div
                    key={x.k}
                    title={`${x.k} ${x.n} 段 · ${pct(rateOf(x.n, active.judged))}`}
                    style={{ flex: `${x.n} 0 0`, background: x.color }}
                  />
                ))}
              </div>
              <div style={{
                display: 'flex', gap: 16, marginTop: 6, fontSize: 12,
                flexWrap: 'wrap', justifyContent: 'center',
              }}>
                {splits.map((x) => (
                  <span key={x.k} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <i style={{
                      width: 8, height: 8, borderRadius: 2, background: x.color,
                      display: 'inline-block',
                    }} />
                    <span style={{ color: 'var(--muted-foreground, #98a2b3)' }}>{x.k}</span>
                    <strong style={{ fontWeight: 600 }}>{x.n}</strong>
                    <span style={{ color: 'var(--muted-foreground, #98a2b3)' }}>
                      {pct(rateOf(x.n, active.judged))}
                    </span>
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="dispatch-dev__card">
        <div className="dispatch-dev__head"><span className="dispatch-dev__title">直答率走势</span></div>
        {weeks.length >= 2
          ? <RateTrend weeks={weeks} activeWeek={activeKey === 'all' ? undefined : activeKey} />
          : <div className="dispatch-dev__empty">周数不足</div>}
      </section>

      <section className="dispatch-dev__card">
        <div className="dispatch-dev__head"><span className="dispatch-dev__title">各层分布</span></div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {layerRows.map(([k, n]) => {
            const v = Number(n);
            return (
              <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                <span style={{ width: 84, color: 'var(--muted-foreground, #98a2b3)' }}>
                  {DAR_LAYER_LABEL[k] || k}
                </span>
                <div style={{ flex: 1, height: 10, background: 'var(--mac-secondary, #f1f3f6)', borderRadius: 5 }}>
                  <div style={{
                    width: `${active.total ? Math.max(2, (v / active.total) * 100) : 0}%`,
                    height: '100%', borderRadius: 5, background: 'var(--primary)',
                  }} />
                </div>
                <span style={{ width: 96, textAlign: 'right' }}>
                  {v} 段 · {pct(active.total ? v / active.total : null)}
                </span>
              </div>
            );
          })}
        </div>
      </section>

      {failTop.length > 0 && (
        <section className="dispatch-dev__card">
          <div className="dispatch-dev__head"><span className="dispatch-dev__title">未直答 Top 品类</span></div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {failTop.map((g) => (
              <div
                key={g.type}
                style={{ paddingBottom: 10, borderBottom: '1px solid var(--mac-border, #eef1f3)' }}
              >
                <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>{g.type}</span>
                  <span style={{ fontSize: 12, color: 'var(--muted-foreground, #98a2b3)' }}>{g.n} 段</span>
                </div>
                <div style={{ display: 'flex', gap: 2, height: 6, borderRadius: 3, overflow: 'hidden', margin: '6px 0 4px' }}>
                  <div style={{ flex: `${g.wrong ?? 0} 0 0`, background: SPLIT_COLORS.wrong }} />
                  <div style={{ flex: `${g.uncovered ?? 0} 0 0`, background: SPLIT_COLORS.uncovered }} />
                </div>
                <div style={{ display: 'flex', gap: 14, fontSize: 11, color: 'var(--muted-foreground, #98a2b3)' }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                    <i style={{ width: 8, height: 8, borderRadius: 2, background: SPLIT_COLORS.wrong }} />
                    未直答 {g.wrong ?? 0}
                  </span>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                    <i style={{ width: 8, height: 8, borderRadius: 2, background: SPLIT_COLORS.uncovered }} />
                    未覆盖 {g.uncovered ?? 0}
                  </span>
                </div>
                {g.samples?.map((s2, i) => (
                  <div key={i} style={{ fontSize: 12, lineHeight: 1.55, marginTop: 3 }}>
                    <span style={{
                      color: s2.k === '未直答' ? SPLIT_COLORS.wrong : '#7b8790',
                      marginRight: 6,
                    }}>{s2.k}</span>
                    <span style={{ color: 'var(--muted-foreground, #98a2b3)' }}>{s2.q}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
