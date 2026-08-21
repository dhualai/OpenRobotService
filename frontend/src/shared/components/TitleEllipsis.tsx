// 标题溢出折叠/展开 —— 双端一致（点击箭头展开全量标题 / 再点收起）
//
// 背景：历史工单列表 / 工单详情页标题过长被 line-clamp 截断为 N 行后，关键信息看不到全貌。
//       用户明确要求 PC 端与移动端都用「折叠/展开」交互（而非 hover 浮层），行为统一：
//         - 溢出 → 标题末尾追加一个 ChevronDown 箭头按钮，点击展开显示完整标题，再点收起。
//         - 未溢出 → 不渲染任何控件，零侵入。
//
// 设计要点：
//   - 自动检测文本溢出（scrollHeight > 计算行高阈值），ResizeObserver 监听窗口/容器尺寸变化重测。
//   - 状态机 collapsed ↔ expanded：展开时由 CSS 规则取消 -webkit-line-clamp，让 box 自然增高
//     显示完整文本；箭头旋转 180° 反馈状态。
//   - 展开/收起按钮同时支持 PC 鼠标点击与移动端手指点击（onClick 双端通用），
//     尺寸用 22×22 触摸热区，移动端更易点中。
//
// 用法（按现有 .history-row__title / .detail-card__title 样式穿透，不重复定义）：
//   <TitleEllipsis text={t.title} lines={2} titleClassName="history-row__title" as="span" />
//   <TitleEllipsis text={ticket.title} lines={3} titleClassName="detail-card__title-inner" as="span" />
import { useLayoutEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';

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

export default function TitleEllipsis({
  text,
  lines = 2,
  titleClassName,
  as = 'div',
  fontSize = 14.5,
  lineHeight = 1.4,
}: TitleEllipsisProps) {
  const containerRef = useRef<HTMLElement | null>(null);
  const [isOverflow, setIsOverflow] = useState(false);
  const [expanded, setExpanded] = useState(false);

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

  // 渲染标题元素。expanded=true 时由 CSS（[data-expanded="1"]）取消 line-clamp，让 box 自然撑开。
  const Tag = as;
  const titleEl = (
    <Tag
      ref={(node: HTMLElement | null) => { containerRef.current = node; }}
      className={`title-ellipsis__text ${titleClassName || ''}`.trim()}
      data-overflow={isOverflow ? '1' : '0'}
      data-expanded={expanded ? '1' : '0'}
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
          className="title-ellipsis__toggle"
          aria-label={expanded ? '收起标题' : '展开标题'}
          aria-expanded={expanded}
          title={expanded ? '收起' : '展开'}
          onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }}
        >
          <ChevronDown size={14} strokeWidth={2} className="title-ellipsis__toggle-icon" />
        </button>
      )}
    </>
  );
}