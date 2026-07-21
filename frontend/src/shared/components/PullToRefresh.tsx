// 通用下拉刷新 / 触底加载组件（移动端）
// 特性：
//  - 滚动容器在顶部时，向下拖拽触发整页刷新（onRefresh）
//  - 滚动到底部时触发加载更多（onLoadMore），用于分页大数据量场景
//  - 通过 refreshKey 可由外部（如 store 联动）强制刷新
// 采用原生 touch 监听（passive:false）以便 preventDefault 屏蔽浏览器默认下拉回弹。
import { useEffect, useRef, useState, type ReactNode, type UIEvent } from 'react';

interface PullToRefreshProps {
  children: ReactNode;
  /** 下拉释放后 / refreshKey 变化时的刷新回调（需返回 Promise） */
  onRefresh?: () => Promise<void>;
  /** 滚动到底部时的加载更多回调（需返回 Promise） */
  onLoadMore?: () => Promise<void>;
  /** 是否还有更多数据（控制触底加载与“没有更多了”文案） */
  hasMore?: boolean;
  /** 外部强制刷新信号：值变化即触发一次刷新（首次挂载不触发） */
  refreshKey?: number;
  /** 是否渲染触底加载区域（列表为空时建议传 false） */
  showFooter?: boolean;
  /** 触发刷新的下拉距离阈值(px) */
  threshold?: number;
  className?: string;
}

const INDICATOR_HEIGHT = 56; // 刷新中指示器高度

export default function PullToRefresh({
  children,
  onRefresh,
  onLoadMore,
  hasMore = false,
  refreshKey,
  showFooter = true,
  threshold = 60,
  className,
}: PullToRefreshProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const startY = useRef(0);
  const pulling = useRef(false);

  // 以 ref 维护最新值，供原生事件回调读取，避免闭包过期
  const onRefreshRef = useRef(onRefresh);
  const onLoadMoreRef = useRef(onLoadMore);
  const hasMoreRef = useRef(hasMore);
  const showFooterRef = useRef(showFooter);
  const refreshingRef = useRef(false);
  const loadingMoreRef = useRef(false);
  const loadMoreLock = useRef(false);
  const pullDistanceRef = useRef(0);
  onRefreshRef.current = onRefresh;
  onLoadMoreRef.current = onLoadMore;
  hasMoreRef.current = hasMore;
  showFooterRef.current = showFooter;

  const [pullDistance, setPullDistance] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  const doRefresh = async () => {
    if (!onRefreshRef.current || refreshingRef.current) return;
    refreshingRef.current = true;
    setRefreshing(true);
    try {
      await onRefreshRef.current();
    } finally {
      refreshingRef.current = false;
      setRefreshing(false);
      setPullDistance(0);
    }
  };

  const doLoadMore = async () => {
    if (
      !showFooterRef.current ||
      !onLoadMoreRef.current ||
      loadingMoreRef.current ||
      !hasMoreRef.current ||
      loadMoreLock.current
    )
      return;
    loadMoreLock.current = true;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    try {
      await onLoadMoreRef.current();
    } finally {
      loadingMoreRef.current = false;
      setLoadingMore(false);
      // 短暂解锁，避免到底瞬间重复触发
      setTimeout(() => {
        loadMoreLock.current = false;
      }, 300);
    }
  };

  // 原生 touch 监听（touchmove 需 passive:false 才能 preventDefault）
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const onTouchStart = (e: TouchEvent) => {
      if (refreshingRef.current) return;
      startY.current = e.touches[0].clientY;
      pulling.current = el.scrollTop <= 0;
    };

    const onTouchMove = (e: TouchEvent) => {
      if (!pulling.current || refreshingRef.current) return;
      if (el.scrollTop > 0) {
        pulling.current = false;
        setPullDistance(0);
        return;
      }
      const deltaY = e.touches[0].clientY - startY.current;
      if (deltaY <= 0) {
        setPullDistance(0);
        return;
      }
      e.preventDefault(); // 屏蔽浏览器默认下拉回弹
      // 阻尼：下拉距离按 0.5 缩放并封顶，避免一次拉到底
      const distance = Math.min(threshold * 1.6, deltaY * 0.5);
      setPullDistance(distance);
    };

    const onTouchEnd = () => {
      if (!pulling.current) return;
      pulling.current = false;
      if (pullDistanceRef.current >= threshold) {
        doRefresh();
      } else {
        setPullDistance(0);
      }
    };

    el.addEventListener('touchstart', onTouchStart, { passive: true });
    el.addEventListener('touchmove', onTouchMove, { passive: false });
    el.addEventListener('touchend', onTouchEnd, { passive: true });
    return () => {
      el.removeEventListener('touchstart', onTouchStart);
      el.removeEventListener('touchmove', onTouchMove);
      el.removeEventListener('touchend', onTouchEnd);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threshold]);

  // 同步 pullDistance 到 ref（供 touchEnd 读取）
  useEffect(() => {
    pullDistanceRef.current = pullDistance;
  }, [pullDistance]);

  // 外部 refreshKey 变化 → 强制刷新（首次挂载不触发）
  const firstKey = useRef(true);
  useEffect(() => {
    if (refreshKey === undefined) return;
    if (firstKey.current) {
      firstKey.current = false;
      return;
    }
    doRefresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  const handleScroll = (e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    if (
      showFooterRef.current &&
      hasMoreRef.current &&
      onLoadMoreRef.current &&
      !loadingMoreRef.current &&
      !loadMoreLock.current &&
      el.scrollHeight - el.scrollTop - el.clientHeight < 40
    ) {
      doLoadMore();
    }
  };

  const indicatorText = refreshing
    ? '刷新中…'
    : pullDistance >= threshold
    ? '释放刷新'
    : '下拉刷新';
  const showIndicator = refreshing || pullDistance > 0;

  return (
    <div
      ref={scrollRef}
      className={`ptr-scroll ${className || ''}`}
      onScroll={handleScroll}
    >
      <div
        className="ptr-indicator"
        style={{
          height: refreshing ? INDICATOR_HEIGHT : pullDistance,
          opacity: showIndicator ? 1 : 0,
        }}
      >
        <span className="ptr-indicator__inner">
          {refreshing ? (
            <span className="ptr-spinner" />
          ) : (
            <span className={`ptr-arrow ${pullDistance >= threshold ? 'is-ready' : ''}`}>↓</span>
          )}
          <span>{indicatorText}</span>
        </span>
      </div>

      {children}

      {showFooter && onLoadMore && (
        <div className="ptr-loadmore">
          {loadingMore ? (
            <span className="ptr-loadmore__inner">
              <span className="ptr-spinner ptr-spinner--sm" /> 加载中…
            </span>
          ) : !hasMore ? (
            <span className="ptr-loadmore__end">没有更多了</span>
          ) : null}
        </div>
      )}
    </div>
  );
}
