// 标题溢出提示 —— 双端不同交互（PC hover 浮层 / 移动端 展开收起）
//
// 背景：历史工单列表 / 工单详情页标题过长被 line-clamp 截断为 2 行后，关键信息看不到全貌。
//       按设备能力区分交互：
//         - PC（hover + pointer:fine）：Info 图标 + hover 浮层显示完整文本（轻量、鼠标体验好）
//         - 移动端（touch + pointer:coarse）：箭头按钮 + 点击展开/收起（避免浮层在手指点击下遮挡/误触）
//
// 设计要点：
//   - 自动检测文本溢出（scrollHeight > clientHeight），未溢出时不渲染任何控件，零侵入。
//   - PC：mouseenter 显示 / mouseleave 隐藏（80/120ms 防抖，避免跨过间隙闪烁）。
//   - 移动端：状态机 collapsed ↔ expanded；点击 ChevronDown/Up 切换，展开后取消 line-clamp，
//     让 CSS 自然撑开显示完整文本；箭头旋转 180° 表示状态。
//   - 设备类型用 matchMedia('(hover: hover) and (pointer: fine)') 判定（PC 笔记本/外接鼠标
//     都会命中；移动端/触屏笔记本默认不命中），并监听 change 事件实现横竖屏切换自适应。
//   - 浮层用 createPortal 渲染到 document.body，避免被父级 overflow:hidden 裁切；定位依据
//     getBoundingClientRect + 视口尺寸自动翻向上/下方，左右边缘防溢出。
//
// 用法（按现有 .history-row__title / .detail-card__title 样式穿透，不重复定义）：
//   <TitleEllipsis text={t.title} lines={2} titleClassName="history-row__title" as="span" />
//   <TitleEllipsis text={ticket.title} lines={3} titleClassName="detail-card__title-inner" as="span" />
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Info, ChevronDown } from 'lucide-react';

export interface TitleEllipsisProps {
  /** 完整文本（必传；当为空时不渲染任何东西） */
  text: string;
  /** 截断的最大行数；默认 2。需与外层样式 -webkit-line-clamp 一致才能正确判断溢出 */
  lines?: number;
  /** 透传到内层容器的 className（一般是 .history-row__title / .detail-card__title-inner） */
  titleClassName?: string;
  /** 容器元素；默认 div。详情页大标题用 h2，列表行内用 span */
  as?: 'div' | 'span' | 'h2' | 'h3';
  /** 标题的字体大小（px）；用于计算一行高度判定是否溢出。默认 14.5 */
  fontSize?: number;
  /** 标题的 line-height（数值或 '1.4' 这种字符串）；默认 1.4 */
  lineHeight?: number;
}

interface TooltipPos {
  top: number;
  left: number;
  placement: 'top' | 'bottom';
}

const TOOLTIP_GAP = 6;
const TOOLTIP_MAX_WIDTH = 320;
const TOOLTIP_MARGIN = 8;
const VIEWPORT_PADDING = 8;

/**
 * 判定当前是否"PC 端 hover 浮层"模式。命中条件：浏览器同时支持 hover 与精细指针
 * （笔记本/台式机/外接鼠标）；不命中（移动端/触屏笔记本）→展开收起模式。
 * SSR 环境下无 window 时默认 false（保守走移动端逻辑，避免在 Node 渲染出 hover 相关 JSX）。
 */
function isHoverCapable(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia('(hover: hover) and (pointer: fine)').matches;
}

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
  const [isHoverDevice, setIsHoverDevice] = useState<boolean>(false); // 默认 false → 首屏按移动端渲染避免闪烁
  const [tooltipVisible, setTooltipVisible] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [pos, setPos] = useState<TooltipPos>({ top: 0, left: 0, placement: 'bottom' });

  // 监听设备能力变化（外接键鼠、窗口尺寸跨越断点等）：实时切换 hover 浮层 / 展开收起
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mql = window.matchMedia('(hover: hover) and (pointer: fine)');
    const apply = () => {
      const next = mql.matches;
      setIsHoverDevice(next);
      // 切换模式时收起/隐藏：避免残留的展开状态或浮层在新模式下造成视觉错位
      setExpanded(false);
      setTooltipVisible(false);
    };
    apply();
    mql.addEventListener('change', apply);
    return () => mql.removeEventListener('change', apply);
  }, []);

  // 检测是否真的溢出（行高 * lines vs scrollHeight 比较）。ResizeObserver 监听窗口/容器尺寸。
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
    const spaceBelow = vh - rect.bottom - VIEWPORT_PADDING;
    const placement: 'top' | 'bottom' = spaceBelow >= tipH + TOOLTIP_GAP ? 'bottom' : 'top';
    const top = placement === 'bottom'
      ? rect.bottom + TOOLTIP_GAP
      : rect.top - tipH - TOOLTIP_GAP + window.scrollY;
    let left = rect.left + window.scrollX;
    if (left + tipW + TOOLTIP_MARGIN > vw) left = Math.max(TOOLTIP_MARGIN, vw - tipW - TOOLTIP_MARGIN);
    if (left < TOOLTIP_MARGIN) left = TOOLTIP_MARGIN;
    return { top, left, placement };
  };

  useEffect(() => {
    if (!tooltipVisible) return;
    const update = () => {
      const p = calcPos();
      if (p) setPos(p);
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [tooltipVisible]);

  // PC hover 防抖
  const enterTimerRef = useRef<number | null>(null);
  const leaveTimerRef = useRef<number | null>(null);
  const onMouseEnter = () => {
    if (leaveTimerRef.current) { window.clearTimeout(leaveTimerRef.current); leaveTimerRef.current = null; }
    enterTimerRef.current = window.setTimeout(() => setTooltipVisible(true), 80);
  };
  const onMouseLeave = () => {
    if (enterTimerRef.current) { window.clearTimeout(enterTimerRef.current); enterTimerRef.current = null; }
    leaveTimerRef.current = window.setTimeout(() => setTooltipVisible(false), 120);
  };
  useEffect(() => () => {
    if (enterTimerRef.current) window.clearTimeout(enterTimerRef.current);
    if (leaveTimerRef.current) window.clearTimeout(leaveTimerRef.current);
  }, []);

  // 渲染标题元素。
  // - 移动端：expanded=true 移除 line-clamp 让 CSS 自然撑开（display 切到 block / 取消 -webkit-line-clamp）
  // - PC：line-clamp 始终保持（hover 浮层显示完整）
  const Tag = as;
  const titleEl = (
    <Tag
      ref={(node: HTMLElement | null) => { containerRef.current = node; }}
      className={`title-ellipsis__text ${titleClassName || ''}`.trim()}
      data-overflow={isOverflow ? '1' : '0'}
      data-expanded={expanded ? '1' : '0'}
      title={isOverflow && isHoverDevice ? text : undefined}
    >
      {text}
    </Tag>
  );

  return (
    <>
      {titleEl}
      {isOverflow && (
        isHoverDevice ? (
          // PC：Info 图标 + hover 浮层
          <button
            type="button"
            className="title-ellipsis__trigger"
            aria-label="查看完整标题"
            title="查看完整标题"
            onMouseEnter={onMouseEnter}
            onMouseLeave={onMouseLeave}
            onClick={(e) => e.stopPropagation()}
          >
            <Info size={14} strokeWidth={2} />
          </button>
        ) : (
          // 移动端：箭头按钮 + 点击展开/收起（展开时标题行不限行数，自然撑开显示全部）
          <button
            type="button"
            className="title-ellipsis__toggle"
            aria-label={expanded ? '收起标题' : '展开标题'}
            aria-expanded={expanded}
            title={expanded ? '收起' : '展开'}
            onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }}
          >
            <ChevronDown size={14} strokeWidth={2} className="title-ellipsis__toggle-icon" />
          </button>
        )
      )}
      {/* 浮层（仅 PC hover 模式 + tooltipVisible 时渲染） */}
      {tooltipVisible && isOverflow && isHoverDevice && createPortal(
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