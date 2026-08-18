import { useEffect, useRef, type RefObject } from 'react';

export interface HorizontalScrollOptions {
  /** wheel 位移倍率：调节滚轮一格的横滑距离。默认 1 */
  wheelMultiplier?: number;
  /** 是否启用鼠标拖拽横滑（pointer events）。默认 true，仅桌面精确指针生效 */
  enableDrag?: boolean;
  /** 是否接管 wheel。默认自动：仅桌面精确指针启用，移动端保留原生触摸滑动 */
  enabled?: boolean;
}

/**
 * 横向滚动 hook —— 让 `overflow-x: auto` 容器在 PC 桌面端获得与移动端一致的横滑体验。
 *
 * 问题：`overflow-x: auto` 在移动端可用手指直接触摸横滑，但 PC 鼠标滚轮默认只产生
 * 垂直滚动量（deltaY），容器不会自动消费，用户需按 Shift+滚轮才能横滑（部分浏览器不支持）。
 *
 * 本 hook 做两件事：
 * 1. **wheel → 横向**：监听 wheel，把 deltaY 转成 scrollLeft 增量并 preventDefault，
 *    让垂直滚轮直接横滑 tab 栏（仅当容器实际可横向滚动时接管，避免页面卡死）。
 * 2. **鼠标拖拽**：按住鼠标左键拖动即可横滑（pointer events），与触屏手势对齐。
 *
 * 实现要点（避免破坏点击）：
 * - 拖拽不用 `setPointerCapture`（它会把后续 click 重新定向到容器、吞掉子按钮点击），
 *   改为 document 级 pointermove/pointerup 监听，click 的 target 保持原按钮不变。
 * - 拖拽位移超过阈值（3px）才标记 moved，并在 capture 阶段 click 里仅对「确实拖过」的
 *   那次 click 做 stopPropagation，随后立即复位；正常点击不受任何影响。
 *
 * 仅在桌面精确指针（`pointer: fine`）启用，移动端保留原生触摸滑动，零干扰。
 *
 * 用法：
 *   const ref = useRef<HTMLDivElement>(null);
 *   useHorizontalScroll(ref);
 *   return <div ref={ref} className="history-tabs">...</div>;
 */
export function useHorizontalScroll(
  ref: RefObject<HTMLElement | null>,
  {
    wheelMultiplier = 1,
    enableDrag = true,
    enabled,
  }: HorizontalScrollOptions = {},
): void {
  const drag = useRef({
    active: false,        // 是否在拖拽中
    startX: 0,            // pointerdown 时 clientX
    startScroll: 0,       // pointerdown 时 scrollLeft
    moved: false,         // 是否发生过位移（区分点击 vs 拖拽，用于抑制误触发 click）
    pointerId: -1,        // 拖拽中的指针 id
  });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    // 环境自检：未显式指定 enabled 时，仅桌面精确指针启用
    const shouldEnable = enabled !== undefined
      ? enabled
      : (typeof window !== 'undefined' && window.matchMedia
          ? window.matchMedia('(pointer: fine)').matches
          : false);
    if (!shouldEnable) return;

    const maxScroll = () => Math.max(0, el.scrollWidth - el.clientWidth);

    // ── wheel → 横向 scrollLeft ──
    const onWheel = (e: WheelEvent) => {
      // 横向滚轮（触摸板双指横滑）/ 缩放手势不接管，交给原生
      if (e.deltaX !== 0 || e.ctrlKey) return;
      if (maxScroll() === 0) return; // 容器不可横滑 → 放行，避免页面卡死
      e.preventDefault();
      // deltaMode 归一化：1=行(≈40px) 2=页(视口宽)，0=像素（触摸板/多数浏览器）
      const unit = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? el.clientWidth : 1;
      el.scrollLeft = Math.max(0, el.scrollLeft + e.deltaY * unit * wheelMultiplier);
    };

    // 交互元素白名单：点击这些元素时跳过指针捕获，让原生 click 事件正常触发
    const INTERACTIVE_SELECTOR = 'button, input, textarea, select, a, [role="button"], [contenteditable="true"]';
    const isInteractiveTarget = (target: EventTarget | null): boolean => {
      if (!(target instanceof Element)) return false;
      // closest 会向上查找，即使点击到按钮内部的子元素（如 span）也能正确识别
      return target.closest(INTERACTIVE_SELECTOR) !== null;
    };

    // ── 鼠标拖拽横滑（pointer events，document 级监听，不 setPointerCapture，保留子按钮 click 命中）──
    const onPointerDown = (e: PointerEvent) => {
      if (!enableDrag || e.button !== 0) return; // 仅左键
      if (maxScroll() === 0) return;
      // 点击交互元素（按钮/链接/输入框等）时跳过指针捕获，保留原生 click 行为
      if (isInteractiveTarget(e.target)) return;
      const s = drag.current;
      s.active = true;
      s.startX = e.clientX;
      s.startScroll = el.scrollLeft;
      s.moved = false;
      s.pointerId = e.pointerId;
      el.classList.add('is-dragging');
    };
    const onPointerMove = (e: PointerEvent) => {
      const s = drag.current;
      if (!s.active || e.pointerId !== s.pointerId) return;
      const dx = e.clientX - s.startX;
      if (Math.abs(dx) > 3) s.moved = true; // 超过 3px 视为拖拽，抑制后续 click
      el.scrollLeft = Math.max(0, s.startScroll - dx);
    };
    const endDrag = (e: PointerEvent) => {
      const s = drag.current;
      if (!s.active || e.pointerId !== s.pointerId) return;
      s.active = false;
      el.classList.remove('is-dragging');
      // moved 标志延迟到 click 之后复位：click 在 pointerup 之后同步派发，需在那一刻仍能读到
      // 「本次是否拖拽过」；用 setTimeout(0) 在 click 处理完后清掉，避免残留污染下一次点击。
      if (s.moved) {
        setTimeout(() => { s.moved = false; }, 0);
      }
    };
    // 拖拽位移后的 click：在 capture 阶段拦截并阻断，避免拖拽松手时误触发 tab 选中；
    // 正常点击（moved=false）不受影响，click 照常冒泡到子按钮。
    const onClickCapture = (e: MouseEvent) => {
      if (drag.current.moved) {
        e.stopPropagation();
        e.preventDefault();
      }
    };

    el.addEventListener('wheel', onWheel, { passive: false });
    if (enableDrag) {
      el.addEventListener('pointerdown', onPointerDown);
      document.addEventListener('pointermove', onPointerMove);
      document.addEventListener('pointerup', endDrag);
      document.addEventListener('pointercancel', endDrag);
      el.addEventListener('click', onClickCapture, true);
    }

    return () => {
      el.removeEventListener('wheel', onWheel);
      if (enableDrag) {
        el.removeEventListener('pointerdown', onPointerDown);
        document.removeEventListener('pointermove', onPointerMove);
        document.removeEventListener('pointerup', endDrag);
        document.removeEventListener('pointercancel', endDrag);
        el.removeEventListener('click', onClickCapture, true);
      }
    };
  }, [ref, wheelMultiplier, enableDrag, enabled]);
}
