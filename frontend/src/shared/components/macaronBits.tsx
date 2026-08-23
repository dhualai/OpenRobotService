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
function macTone(tone: string): string {
  return TONE_VARS[tone] ?? TONE_VARS['blue-3']!;
}

export interface MacDonutSegment { value: number; tone: string; }

/** 环形图（原型 Donut）：viewBox 140，圆环半径 54，段间 gap，中心数值+标签 */
export function MacDonut({
  segments,
  size = 140,
  thickness = 20,
  gap = 3,
  centerValue,
  centerLabel,
}: {
  segments: MacDonutSegment[];
  size?: number;
  thickness?: number;
  gap?: number;
  centerValue?: string | number;
  centerLabel?: string;
}) {
  const total = segments.reduce((s, x) => s + x.value, 0) || 1;
  const r = 54;
  const c = 2 * Math.PI * r;
  let offset = 0;

  return (
    <div className="mac-donut" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox="0 0 140 140" className="mac-donut__svg">
        {/* 底环：muted 浅灰轨道 */}
        <circle cx="70" cy="70" r={r} fill="none" stroke="var(--mac-secondary)" strokeWidth={thickness} />
        {segments.map((s, i) => {
          const len = (s.value / total) * c;
          const el = (
            <circle
              key={i}
              cx="70"
              cy="70"
              r={r}
              fill="none"
              stroke={macTone(s.tone)}
              strokeWidth={thickness}
              strokeDasharray={`${Math.max(len - gap, 0)} ${c}`}
              strokeDashoffset={-offset}
            />
          );
          offset += len;
          return el;
        })}
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
