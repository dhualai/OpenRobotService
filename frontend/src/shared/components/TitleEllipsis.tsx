// 标题溢出悬浮提示 —— 给"溢出截断标题"加完整文本浮层（PC + 移动端兼容）
//
// 背景：历史工单列表 / 工单详情页标题过长会被 line-clamp 截断为 2 行，导致下方人员/操作区被
//       挤压且关键信息（"iOS 端组织架构不支持拖拽且乱码和进口红酒客户"）看不到全貌。用户期望
//       看到完整标题，但不希望标题区域展开/收起（标题区本来就不多）。
//
// 设计：
//   - 自动检测文本溢出（scrollHeight > clientHeight），未溢出时不显示任何提示，保持原 UI 不变。
//   - 溢出 → 在标题末尾追加一个小 Info 图标作为"有更多"提示，hover/触碰图标弹出浮层。
//   - PC：mouseenter 显示 / mouseleave 隐藏（带 80ms 防抖，防止跨过间隙闪烁）。
//   - 移动端：touch/click 切换显示；同时监听 document touchstart，触摸外部区域时关闭浮层。
//   - 浮层定位：默认向下 6px；若下方空间不足（视口底部 < tooltip 高度）则改向上；自动计算
//     左右边距避免被屏幕边缘裁剪（视口宽度 - tooltip 宽度的留白）。
//
// 用法（按现有 .history-row__title / .detail-card__title 样式穿透，不重复定义）：
//   <TitleEllipsis text={t.title} lines={2} titleClassName="history-row__title" as="span" />
//   <TitleEllipsis text={ticket.title} lines={3} titleClassName="detail-card__title" as="h2" />
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Info } from 'lucide-react';

export interface TitleEllipsisProps {
  /** 完整文本（必传；当为空时不渲染任何东西） */
  text: string;
  /** 截断的最大行数；默认 2。需与外层样式 -webkit-line-clamp 一致才能正确判断溢出 */
  lines?: number;
  /** 透传到内层容器的 className（一般是 .history-row__title / .detail-card__title） */
  titleClassName?: string;
  /** 容器元素；默认 div。详情页大标题用 h2，列表行内用 span */
  as?: 'div' | 'span' | 'h2' | 'h3';
  /** 标题的字体大小（px）；用于计算一行高度判定是否溢出。默认 14.5（与 .history-row__title 一致） */
  fontSize?: number;
  /** 标题的 line-height（数值或 '1.4' 这种字符串）；默认 1.4 */
  lineHeight?: number;
}

interface TooltipPos {
  top: number;
  left: number;
  placement: 'top' | 'bottom';
}

const TOOLTIP_GAP = 6; // 浮层与标题的间距
const TOOLTIP_MAX_WIDTH = 320; // 浮层最大宽度
const TOOLTIP_MARGIN = 8; // 浮层距离屏幕边缘的最小留白
const VIEWPORT_PADDING = 8; // 屏幕顶部/底部内边距参考

export default function TitleEllipsis({
  text,
  lines = 2,
  titleClassName,
  as = 'div',
  fontSize = 14.5,
  lineHeight = 1.4,
}: TitleEllipsisProps) {
  const containerRef = useRef<HTMLElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const [isOverflow, setIsOverflow] = useState(false);
  const [visible, setVisible] = useState(false);
  const [pos, setPos] = useState<TooltipPos>({ top: 0, left: 0, placement: 'bottom' });

  // 检测是否真的溢出（行高 * lines vs scrollHeight 比较）。
  // ResizeObserver 监听容器尺寸变化（窗口旋转 / 列表宽度变化）也触发重测。
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const check = () => {
      const lineH = typeof lineHeight === 'number' ? lineHeight : parseFloat(String(lineHeight)) || 1.4;
      const maxH = fontSize * lineH * lines + 1; // +1 容忍亚像素误差
      setIsOverflow(el.scrollHeight > maxH + 0.5 || el.scrollWidth > el.clientWidth + 0.5);
    };
    check();
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(check) : null;
    if (ro) ro.observe(el);
    window.addEventListener('resize', check);
    return () => {
      ro?.disconnect();
      window.removeEventListener('resize', check);
    };
  }, [text, lines, fontSize, lineHeight]);

  // 计算浮层位置：依据 containerRef 的 getBoundingClientRect + 视口尺寸
  const calcPos = (): TooltipPos | null => {
    const el = containerRef.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    const tipW = tooltipRef.current?.offsetWidth || TOOLTIP_MAX_WIDTH;
    const tipH = tooltipRef.current?.offsetHeight || 40;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    // 下方空间不足则翻转到上方
    const spaceBelow = vh - rect.bottom - VIEWPORT_PADDING;
    const placement: 'top' | 'bottom' = spaceBelow >= tipH + TOOLTIP_GAP ? 'bottom' : 'top';
    const top = placement === 'bottom'
      ? rect.bottom + TOOLTIP_GAP
      : rect.top - tipH - TOOLTIP_GAP + window.scrollY;
    // 左：与标题左对齐，但防止被屏幕右边裁掉
    let left = rect.left + window.scrollX;
    if (left + tipW + TOOLTIP_MARGIN > vw) left = Math.max(TOOLTIP_MARGIN, vw - tipW - TOOLTIP_MARGIN);
    if (left < TOOLTIP_MARGIN) left = TOOLTIP_MARGIN;
    return { top, left, placement };
  };

  // 显示时计算定位；visible/pos 任一变更都重算一次（等 tooltip 渲染后才有真实高度）
  useEffect(() => {
    if (!visible) return;
    const update = () => {
      const p = calcPos();
      if (p) setPos(p);
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true); // capture：捕获祖先滚动
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [visible]);

  // 移动端：触摸外部区域关闭浮层；只在 visible 时监听，关闭后立即释放
  useEffect(() => {
    if (!visible) return;
    const onDocTouch = (ev: TouchEvent) => {
      const t = ev.target as Node | null;
      if (!t) return;
      if (containerRef.current?.contains(t)) return;
      if (tooltipRef.current?.contains(t)) return;
      setVisible(false);
    };
    document.addEventListener('touchstart', onDocTouch, { passive: true });
    return () => document.removeEventListener('touchstart', onDocTouch);
  }, [visible]);

  // PC hover 防抖（避免从图标快速移到 tooltip 时闪烁）
  const enterTimerRef = useRef<number | null>(null);
  const leaveTimerRef = useRef<number | null>(null);
  const onMouseEnter = () => {
    if (leaveTimerRef.current) { window.clearTimeout(leaveTimerRef.current); leaveTimerRef.current = null; }
    enterTimerRef.current = window.setTimeout(() => setVisible(true), 80);
  };
  const onMouseLeave = () => {
    if (enterTimerRef.current) { window.clearTimeout(enterTimerRef.current); enterTimerRef.current = null; }
    leaveTimerRef.current = window.setTimeout(() => setVisible(false), 120);
  };
  useEffect(() => () => {
    if (enterTimerRef.current) window.clearTimeout(enterTimerRef.current);
    if (leaveTimerRef.current) window.clearTimeout(leaveTimerRef.current);
  }, []);

  // 移动端：点击图标切换显示
  const onIconClick = (e: React.MouseEvent | React.TouchEvent) => {
    e.stopPropagation();
    setVisible((v) => !v);
  };

  // 渲染标题元素（按 as 切换，避免 innerHTML）
  const Tag = as;
  const titleEl = (
    <Tag
      ref={containerRef as React.RefObject<HTMLElement>}
      className={`title-ellipsis__text ${titleClassName || ''}`.trim()}
      data-overflow={isOverflow ? '1' : '0'}
      title={isOverflow ? text : undefined}
    >
      {text}
    </Tag>
  );

  return (
    <>
      {titleEl}
      {isOverflow && (
        <button
          type="button"
          className="title-ellipsis__trigger"
          aria-label="查看完整标题"
          title="查看完整标题"
          onMouseEnter={onMouseEnter}
          onMouseLeave={onMouseLeave}
          onClick={onIconClick}
        >
          <Info size={14} strokeWidth={2} />
        </button>
      )}
      {visible && isOverflow && createPortal(
        <div
          ref={tooltipRef}
          className={`title-ellipsis__tooltip title-ellipsis__tooltip--${pos.placement}`}
          style={{ top: pos.top, left: pos.left, position: 'absolute' }}
          role="tooltip"
          onMouseEnter={onMouseEnter}
          onMouseLeave={onMouseLeave}
        >
          {text}
        </div>,
        document.body,
      )}
    </>
  );
}