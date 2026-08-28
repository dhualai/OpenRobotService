// 马卡龙数据组件：Donut 环形图 / Legend 图例 / Stat 指标卡
// 对照 macaron-minimal-ui 的 Bits.tsx 移植，色阶映射到 global.css 的 --mac-blue-* 令牌。
import type { ReactNode } from 'react';

const TONE_VARS: Record<string, string> = {
  'blue-1': 'var(--mac-blue-1)',
  'blue-2': 'var(--mac-blue-2)',
  'blue-3': 'var(--mac-blue-3)',
  'blue-4': 'var(--mac-blue-4)',
  'blue-5': 'var(--mac-blue-5)',
  'blue-soft': 'var(--mac-blue-soft)',
  // 工单状态五色调：等距拉开亮度的专用蓝阶（见 global.css --mac-status-*）
  'status-1': 'var(--mac-status-1)',
  'status-2': 'var(--mac-status-2)',
  'status-3': 'var(--mac-status-3)',
  'status-4': 'var(--mac-status-4)',
  'status-5': 'var(--mac-status-5)',
  gray: 'var(--mac-muted-fg)',
};

/** 色调名 → CSS 变量；未知色调回退 blue-3 */
export function macTone(tone: string): string {
  return TONE_VARS[tone] ?? TONE_VARS['blue-3']!;
}

export interface MacDonutSegment { value: number; tone: string; }

/** 扇区百分比标签文字色：浅色弧段（blue-4/5、status-4/5）用深蓝，深色弧段用白字 */
function percentLabelColor(tone: string): string {
  return ['blue-4', 'blue-5', 'status-4', 'status-5'].includes(tone)
    ? 'var(--mac-blue-1)'
    : '#fff';
}

/** 环形图（原型 Donut）：viewBox 140，圆环半径 54，段间 gap，中心数值+标签；
 *  percentLabels 开启后在各扇区中点上渲染百分比标签（占比过小的扇区不渲染，避免重叠） */
export function MacDonut({
  segments,
  size = 140,
  thickness = 20,
  gap = 3,
  centerValue,
  centerLabel,
  percentLabels = false,
  minPercentLabel = 8,
}: {
  segments: MacDonutSegment[];
  size?: number;
  thickness?: number;
  gap?: number;
  centerValue?: string | number;
  centerLabel?: string;
  /** 在扇区中点上渲染百分比标签（如 "34%"） */
  percentLabels?: boolean;
  /** 扇区占比低于该百分比时不渲染标签 */
  minPercentLabel?: number;
}) {
  const total = segments.reduce((s, x) => s + x.value, 0) || 1;
  const r = 54;
  const c = 2 * Math.PI * r;
  let offset = 0;

  // 各扇区弧（含起点偏移，供标签定位）；SVG 圆从 3 点钟方向顺时针绘制，
  // 负 strokeDashoffset 沿顺时针推进，故角度 0° 在 3 点钟、顺时针为正
  const arcs = segments.map((s, i) => {
    const len = (s.value / total) * c;
    const start = offset;
    offset += len;
    return {
      arc: (
        <circle
          key={i}
          cx="70"
          cy="70"
          r={r}
          fill="none"
          stroke={macTone(s.tone)}
          strokeWidth={thickness}
          strokeDasharray={`${Math.max(len - gap, 0)} ${c}`}
          strokeDashoffset={-start}
        />
      ),
      start,
      len,
      tone: s.tone,
    };
  });

  // 百分比标签：沿弧长取中点角度，落在圆环中线（半径 r）上
  const labels = percentLabels
    ? arcs
        .map(({ start, len, tone }, i) => {
          const pct = Math.round((segments[i].value / total) * 100);
          if (pct < minPercentLabel || pct <= 0) return null;
          const angle = ((start + len / 2) / c) * Math.PI * 2;
          const isLight = percentLabelColor(tone) !== '#fff';
          return {
            x: 70 + r * Math.cos(angle),
            y: 70 + r * Math.sin(angle),
            pct,
            // 白字带深色描边（paintOrder 描边垫底），浅色弧段深字无需描边
            fill: percentLabelColor(tone),
            stroke: isLight ? 'none' : 'rgba(21,89,121,0.45)',
          };
        })
        .flatMap((l) => (l ? [l] : []))
    : [];

  return (
    <div className="mac-donut" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox="0 0 140 140" className="mac-donut__svg">
        {/* 底环：muted 浅灰轨道 */}
        <circle cx="70" cy="70" r={r} fill="none" stroke="var(--mac-secondary)" strokeWidth={thickness} />
        {arcs.map((a) => a.arc)}
        {labels.map((l, i) => (
          <text
            key={i}
            x={l.x}
            y={l.y}
            textAnchor="middle"
            dominantBaseline="central"
            fontSize={11}
            fontWeight={600}
            fill={l.fill}
            stroke={l.stroke}
            strokeWidth={2.5}
            paintOrder="stroke"
            // 整图被 .mac-donut__svg 旋转 -90°（起点转到正上方），
            // 标签绕自身位置反旋 +90°，保证百分比数字保持正立
            transform={`rotate(90 ${l.x} ${l.y})`}
          >
            {l.pct}%
          </text>
        ))}
      </svg>
      {centerValue !== undefined && (
        <div className="mac-donut__center">
          <div>
            <div className="mac-donut__value">{centerValue}</div>
            {centerLabel ? <div className="mac-donut__label">{centerLabel}</div> : null}
          </div>
        </div>
      )}
    </div>
  );
}

export interface MacLegendItem {
  key: string;
  label: string;
  value: number;
  tone: string;
  percent: number;
  /** 后端尚未接入：图例行加「·待接入」角标 */
  pending?: boolean;
  /** 自定义图例行内容（如附加角标） */
  extra?: ReactNode;
}

/** 图例（原型 Legend）：色点 + 标签 + 右对齐百分比/数值；onItemClick 提供时整行可点 */
export function MacLegend({
  items,
  onItemClick,
}: {
  items: MacLegendItem[];
  onItemClick?: (key: string) => void;
}) {
  return (
    <ul className="mac-legend">
      {items.map((it) => (
        <li
          key={it.key}
          className={`mac-legend__item ${onItemClick ? 'is-clickable' : ''}`}
          onClick={onItemClick ? () => onItemClick(it.key) : undefined}
        >
          <span className="mac-legend__dot" style={{ background: macTone(it.tone) }} />
          <span className="mac-legend__label">
            {it.label}
            {it.pending && <sup className="mac-legend__pending">·待接入</sup>}
            {it.extra}
          </span>
          <span className="mac-legend__pct">{it.percent}%</span>
          <span className="mac-legend__val">{it.value}</span>
        </li>
      ))}
    </ul>
  );
}

/** 指标卡（原型 Stat）：色调数字 + 灰色小标签，可选点击 */
export function MacStat({
  value,
  label,
  tone = 'blue-3',
  onClick,
}: {
  value: string | number;
  label: string;
  tone?: string;
  onClick?: () => void;
}) {
  return (
    <div className="mac-stat" style={onClick ? { cursor: 'pointer' } : undefined} onClick={onClick}>
      <div className="mac-stat__value" style={{ color: macTone(tone) }}>{value}</div>
      <div className="mac-stat__label">{label}</div>
    </div>
  );
}

/** 小号开关（对照 macaron ui/switch：36×20，选中蒂芙尼蓝 + 白色滑块） */
export function MacSwitch({ checked, onChange }: { checked: boolean; onChange?: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      className={`mac-switch ${checked ? 'is-checked' : ''}`}
      onClick={() => onChange?.(!checked)}
    >
      <span className="mac-switch__thumb" />
    </button>
  );
}
