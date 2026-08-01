import { useRef, useCallback } from 'react';

interface SwipeToCloseOptions {
  /** 触发关闭所需的最小横向向右位移（px），默认 50 */
  threshold?: number;
  /** 是否仅从屏幕左边缘起滑才触发（更贴近原生返回手势），默认 false（遮罩内任意位置横向右滑均可关闭） */
  edgeOnly?: boolean;
  /** edgeOnly 时的边缘判定宽度（px），默认 24 */
  edgeWidth?: number;
}

/**
 * 遮罩/弹层「右滑关闭」手势 hook。
 *
 * 用于历史工单列表这类「覆盖在页面之上、但未切换路由」的浮层：
 * 用户从浮层内横向右滑时关闭浮层，而不是触发浏览器/微信的 history.back()（否则会退出应用）。
 *
 * 仅依赖 touch 事件判断横向位移，不调用 preventDefault，故与浮层内的纵向滚动（PullToRefresh）互不干扰：
 * 纵向滑动时 |dy| > dx，不会误触发关闭；只有明显的横向右滑（dx > |dy| 且超过阈值）才关闭。
 *
 * 注意：从屏幕最左边缘起滑时，iOS 微信的系统级边缘右滑会优先拦截（页面收不到 touch），
 * 此时仍会触发系统返回；本 hook 覆盖的是「浮层区域内（非最左边缘）」的右滑手势。
 */
export function useSwipeToClose(onClose: () => void, options: SwipeToCloseOptions = {}) {
  const { threshold = 50, edgeOnly = false, edgeWidth = 24 } = options;
  const startX = useRef(0);
  const startY = useRef(0);
  const tracking = useRef(false);

  const onTouchStart = useCallback(
    (e: React.TouchEvent) => {
      const t = e.touches[0];
      startX.current = t.clientX;
      startY.current = t.clientY;
      // 仅在「任意位置」模式，或从屏幕左边缘起滑时，才跟踪本次手势
      tracking.current = !edgeOnly || t.clientX <= edgeWidth;
    },
    [edgeOnly, edgeWidth],
  );

  const onTouchMove = useCallback(() => {
    // 仅做预判标记：触摸移动中若已呈明显横向右滑，置位以便 end 时快速判定。
    // 实际判定在 touchend，避免 move 阶段误关。
  }, []);

  const onTouchEnd = useCallback(
    (e: React.TouchEvent) => {
      if (!tracking.current) return;
      const t = e.changedTouches[0];
      const dx = t.clientX - startX.current;
      const dy = t.clientY - startY.current;
      // 横向向右、且横向位移明显大于纵向 → 判定为右滑关闭
      if (dx > threshold && dx > Math.abs(dy)) {
        onClose();
      }
      tracking.current = false;
    },
    [onClose, threshold],
  );

  return { onTouchStart, onTouchMove, onTouchEnd };
}
