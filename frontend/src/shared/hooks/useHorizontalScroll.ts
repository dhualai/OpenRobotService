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
  // 鼠标拖拽状态（drag 用 ref 避免 re-render，且 cleanup 可安全复用）
  const drag = useRef({
    active: false,        // 是否在拖拽中
    startX: 0,            // pointerdown 时 clientX
    startScroll: 0,      // pointerdown 时 scrollLeft
    moved: false,         // 是否发生过位移（区分点击 vs 拖拽，用于抑制误触发 click）
    pointerId: -1,        // pointer capture 句柄
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

    // ── 鼠标拖拽横滑（pointer events） ──
    const onPointerDown = (e: PointerEvent) => {
      if (!enableDrag || e.button !== 0) return; // 仅左键
      if (maxScroll() === 0) return;
      const s = drag.current;
      s.active = true;
      s.startX = e.clientX;
      s.startScroll = el.scrollLeft;
      s.moved = false;
      s.pointerId = e.pointerId;
      try { el.setPointerCapture(e.pointerId); } catch { /* 旧浏览器忽略 */ }
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
      try { el.releasePointerCapture(s.pointerId); } catch { /* 已释放忽略 */ }
      el.classList.remove('is-dragging');
    };

    el.addEventListener('wheel', onWheel, { passive: false });
    if (enableDrag) {
      el.addEventListener('pointerdown', onPointerDown);
      el.addEventListener('pointermove', onPointerMove);
      el.addEventListener('pointerup', endDrag);
      el.addEventListener('pointercancel', endDrag);
      // 拖拽中抑制子按钮 click：拖出位移时阻止后续 click 冒泡（避免误选 tab）
      el.addEventListener('click', (e) => {
        if (drag.current.moved) {
          e.preventDefault();
          e.stopPropagation();
          drag.current.moved = false;
        }
      }, true);
    }

    return () => {
      el.removeEventListener('wheel', onWheel);
      if (enableDrag) {
        el.removeEventListener('pointerdown', onPointerDown);
        el.removeEventListener('pointermove', onPointerMove);
        el.removeEventListener('pointerup', endDrag);
        el.removeEventListener('pointercancel', endDrag);
      }
    };
  }, [ref, wheelMultiplier, enableDrag, enabled]);
}
