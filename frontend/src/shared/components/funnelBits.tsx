// 漏斗通用件：派单调试（DispatchDev）与 AI 直答率（DarFunnelPanel）共用。
// 层条按顶层真实数量缩放，层与层之间只放「扣除说明」，不画收窄过渡面。
// 样式类 dispatch-dev__funnel-* 在 shared/styles/global.css，两个面板都用。

export function pct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return `${(Number(v) * 100).toFixed(1)}%`;
}

export function rateOf(num: number, den: number): number | null {
  if (!den) return null;
  return num / den;
}

export function funnelWidthPct(value: number, base: number): number {
  if (!base || base <= 0) return 100;
  const raw = (Math.max(0, value) / base) * 100;
  if (value <= 0) return 8;
  return Math.min(100, raw);
}

/** 漏斗层间：只放扣除说明，不画收窄的过渡面。 */
export function FunnelBridge(props: {
  deductLabel: string;
  deduct: number;
  remainLabel?: string;
  remain: number;
  unit?: string;
  expose?: boolean;
  onDeductClick?: () => void;
  deductActive?: boolean;
}) {
  const unit = props.unit || '张';
  const remainLabel = props.remainLabel || '剩余';
  const short = props.expose
    ? `仅曝光 ${props.deductLabel} ${props.deduct}${unit}`
    : `−${props.deduct}${unit} ${props.deductLabel}`;
  const full = `${props.expose ? '仅曝光' : '扣除'} ${props.deductLabel} −${props.deduct}${unit}，${remainLabel} ${props.remain}${unit}`;
  const cls = `dispatch-dev__funnel-link${props.expose ? ' is-expose' : ''}${props.deductActive ? ' is-active' : ''}`;
  const bridgeCls = `dispatch-dev__funnel-bridge${props.expose ? ' is-expose' : ''}`;
  const chip = props.onDeductClick ? (
    <button type="button" className={cls} title={full} onClick={props.onDeductClick}>{short}</button>
  ) : (
    <span className={cls} title={full}>{short}</span>
  );
  return (
    <div className={bridgeCls}>
      {chip}
    </div>
  );
}

/** 漏斗一层：名称在左，纯色条按数量居中，右侧文字可自定义（默认显示率值）。 */
export function FunnelStepRow(props: {
  widthPct: number;
  title: string;
  countLabel: string;
  rate?: number | null;
  rateHint?: string;
  rateLabel?: string;
  /** 右侧自定义内容（如「占全部 79.7%」）；给了就不再渲染 rate */
  right?: string;
  tone?: 'neutral' | 'mid' | 'warn';
  active?: boolean;
  onClick?: () => void;
}) {
  const stepClass = [
    'dispatch-dev__funnel-step',
    props.tone === 'mid' ? 'is-mid' : '',
    props.tone === 'warn' ? 'is-warn' : '',
    props.active ? 'is-active' : '',
  ].filter(Boolean).join(' ');
  const bar = props.onClick ? (
    <button type="button" className={stepClass} style={{ width: `${props.widthPct}%` }} onClick={props.onClick}>
      <strong>{props.countLabel}</strong>
    </button>
  ) : (
    <div className={stepClass} style={{ width: `${props.widthPct}%` }}>
      <strong>{props.countLabel}</strong>
    </div>
  );
  return (
    <div className="dispatch-dev__funnel-row">
      <div className="dispatch-dev__funnel-name">{props.title}</div>
      <div className="dispatch-dev__funnel-track">{bar}</div>
      <div className="dispatch-dev__funnel-rate" title={props.rateHint}>
        {props.right ? (
          <em>{props.right}</em>
        ) : (
          <>
            <em>{props.rateLabel || '错派'} {pct(props.rate)}</em>
            <i>{props.rateHint}</i>
          </>
        )}
      </div>
    </div>
  );
}
