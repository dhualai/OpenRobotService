// 马卡龙月柱状图（对照 macaron MonthBars）：按月展示项目数量，
// 默认「近一年」13 个月可滑动窗口（当月在最右侧，可向左滑动查看更早月份），
// 可按年筛选 12 个月。
// 每根柱子是堆叠的两段：下段=已承接（value，深色），上段=待定（pending_value，同色调更浅），
// 浅色由 CSS 用 --bar-color 混白得到，柱高按两者之和取，Y 轴上限也按之和计算。
import { useMemo, useRef, useState, useEffect } from 'react';
import type { ProjectMonthlyItem } from '@/api/dashboard';

/** 以当前月为终点、向前 12 个月的可滑动窗口；默认视口滚动到最右（当月最右侧）。 */
export function ProjectMonthBars({
  data,
  years,
  style,
  onSelect,
}: {
  data: ProjectMonthlyItem[];
  years: number[];
  style?: React.CSSProperties;
  /** 点击某个月柱时回调（参数为 YYYY-MM），用于跳转到该月项目列表 */
  onSelect?: (key: string) => void;
}) {
  const now = useMemo(() => new Date(), []);
  const currentKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  const [year, setYear] = useState<number | 'recent'>('recent');
  const [active, setActive] = useState<string>(currentKey);
  const scroller = useRef<HTMLDivElement>(null);

  const items = useMemo(() => {
    if (year === 'recent') {
      const base = new Date(now.getFullYear(), now.getMonth(), 1);
      const keys: string[] = [];
      // 窗口 = 当月向前 12 个月（共 13 根柱），当月排在最右
      for (let i = -12; i <= 0; i++) {
        const d = new Date(base.getFullYear(), base.getMonth() + i, 1);
        keys.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`);
      }
      return keys.map(
        (k) =>
          data.find((d) => d.key === k) ?? {
            key: k,
            year: Number(k.slice(0, 4)),
            month: Number(k.slice(5)),
            value: 0,
            pending_value: 0,
          },
      );
    }
    return Array.from({ length: 12 }, (_, i) => {
      const k = `${year}-${String(i + 1).padStart(2, '0')}`;
      return (
        data.find((d) => d.key === k) ?? {
          key: k,
          year: year as number,
          month: i + 1,
          value: 0,
          pending_value: 0,
        }
      );
    });
  }, [data, year, now]);

  const max = Math.max(...items.map((i) => i.value + (i.pending_value ?? 0)), 4);
  const ticks = 4;
  const step = Math.ceil(max / ticks);
  const top = step * ticks;

  // 滚动定位：「近一年」默认滚到最右（当月在最右侧）；按年筛选时定位到当前月（若在该年）
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (year === 'recent') {
      el.scrollLeft = el.scrollWidth - el.clientWidth;
      return;
    }
    const target = el.querySelector<HTMLElement>(`[data-key="${active}"]`);
    if (target) {
      el.scrollLeft = Math.max(0, target.offsetLeft - el.clientWidth / 2 + target.clientWidth / 2);
    }
  }, [year, active]);

  return (
    <div style={style}>
      <div className="mac-monthbars__head">
        <span className="mac-monthbars__head-label">按月统计</span>
        {/* 图例：说明柱子上方浅色段是「是否承接=待定」的项目 */}
        <span className="mac-monthbars__legend">
          <i className="mac-monthbars__legend-dot" />
          已承接
          <i className="mac-monthbars__legend-dot mac-monthbars__legend-dot--pending" />
          待定
        </span>
        <select
          value={String(year)}
          onChange={(e) => setYear(e.target.value === 'recent' ? 'recent' : Number(e.target.value))}
          className="mac-monthbars__select"
          aria-label="按年筛选"
        >
          <option value="recent">近一年</option>
          {years.map((y) => (
            <option key={y} value={y}>
              {y} 年
            </option>
          ))}
        </select>
      </div>

      <div className="mac-monthbars__body">
        <div className="mac-monthbars__yaxis">
          {Array.from({ length: ticks + 1 }, (_, i) => (
            <span key={i}>{top - i * step}</span>
          ))}
        </div>
        <div ref={scroller} className="mac-monthbars__scroll">
          <div className="mac-monthbars__track">
            {items.map((it) => {
              const isActive = it.key === active;
              const isFuture = it.key > currentKey;
              const pendingValue = it.pending_value ?? 0;
              const total = it.value + pendingValue;
              // 总高含空月份的 4px 占位；两段按占比切分总高（而非各自独立换算），
              // 保证像素和恒等于总高。待定为 0 时整根都是已承接色，空月份占位同理；
              // 待定不为 0 时至少留 2px，避免只有 1 个待定项目时看不见。
              const h = Math.max(Math.round((total / top) * 130), 4);
              const hPending = pendingValue > 0 ? Math.max(Math.round((pendingValue / total) * h), 2) : 0;
              const hValue = h - hPending;
              return (
                <button
                  key={it.key}
                  data-key={it.key}
                  type="button"
                  onClick={() => {
                    setActive(it.key);
                    onSelect?.(it.key);
                  }}
                  className="mac-monthbars__col"
                >
                  <span className="mac-monthbars__barwrap">
                    {isActive ? (
                      <span className="mac-monthbars__tooltip" style={{ bottom: h + 8 }}>
                        {pendingValue > 0 ? `已承接 ${it.value} · 待定 ${pendingValue}` : `${it.value} 个项目`}
                      </span>
                    ) : null}
                    <span
                      className="mac-monthbars__bar"
                      style={{
                        height: h,
                        // 已承接段直接用 --bar-color，待定段由 CSS 混白得到更浅的同色
                        ['--bar-color' as string]: isActive
                          ? 'var(--mac-blue-3)'
                          : isFuture
                            ? 'var(--mac-gray-light)'
                            : 'var(--mac-blue-5)',
                      }}
                    >
                      {hPending > 0 ? (
                        <span className="mac-monthbars__seg mac-monthbars__seg--pending" style={{ height: hPending }} />
                      ) : null}
                      <span className="mac-monthbars__seg" style={{ height: hValue }} />
                    </span>
                  </span>
                  <span className={`mac-monthbars__month ${isActive ? 'is-active' : ''}`}>
                    {it.month} 月
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
      <p className="mac-monthbars__hint">
        左右滑动查看近 12 个月，更多请按年筛选；点击月份查看该月项目
      </p>
    </div>
  );
}
