import { useCallback, useEffect, useMemo, useRef, type RefObject } from 'react';

export interface InertiaScrollOptions {
  /** 位置追踪缓动系数（0~1）：越小轨迹越「丝滑」、惯性滑行越长。默认 0.16 */
  ease?: number;
  /** wheel 位移倍率（调节滚轮一格的滚动距离）。默认 1 */
  wheelMultiplier?: number;
  /** 边界橡皮筋行程（px）：目标允许越界的最大距离，0 关闭回弹。默认 120 */
  rubberBand?: number;
  /** 越界阻尼（0~1）：越界位移映射为实际渲染位移的比例（越小越「紧」）。默认 0.32 */
  rubberBandDamping?: number;
  /** 是否接管 wheel。默认自动：仅桌面精确指针且未开启「减弱动态效果」时启用 */
  enabled?: boolean;
}

export interface InertiaScrollHandle {
  /** 平滑滚动到容器内指定 scrollTop 位置（自动 clamp 到可滚范围） */
  scrollTo: (top: number) => void;
  /** 立即把内部状态对齐到容器当前滚动位置（外部大幅改动 DOM/内容后调用，防止跳变） */
  sync: () => void;
}

const clamp = (v: number, min: number, max: number) => Math.min(Math.max(v, min), max);

/**
 * 类 GSAP 惯性滚动 Hook —— 为任意滚动容器提供「lerp 缓动 + 松手惯性 + 边界橡皮筋」的
 * 桌面 wheel 滚动体验（对齐移动端原生 momentum 手感）。
 *
 * - 仅接管 wheel（鼠标滚轮/触摸板）：preventDefault 后由 rAF 循环以指数衰减追踪目标位置，
 *   天然形成惯性滑行；触摸滚动保留系统原生实现（WKWebView 原生 momentum 足够好，
 *   JS 模拟触摸惯性反而易与系统手势打架）。
 * - 边界橡皮筋：目标位置允许小幅越界（按阻尼压缩渲染位移），停止输入 120ms 后缓动回弹。
 * - 与程序/原生滚动共存：监听 scroll，识别「非本 hook 写入」的 scrollTop 变化
 *   （拖滚动条、键盘、其它代码赋值、触摸惯性）并全量同步内部状态，绝不与用户抢滚动。
 * - 环境自检：仅精确指针（桌面）且未开启 prefers-reduced-motion 时挂监听，否则零开销。
 *
 * 用法：
 *   const ref = useRef<HTMLDivElement>(null);
 *   useInertiaScroll(ref); // 默认参数即可；需要程序平滑滚动时解构 scrollTo 使用
 */
export function useInertiaScroll(
  ref: RefObject<HTMLElement | null>,
  {
    ease = 0.16,
    wheelMultiplier = 1,
    rubberBand = 120,
    rubberBandDamping = 0.32,
    enabled,
  }: InertiaScrollOptions = {},
): InertiaScrollHandle {
  const st = useRef({
    target: 0,      // 目标位置（允许越界，回弹阶段向边界收敛）
    current: 0,     // 当前渲染位置（lerp 追踪 renderTarget）
    raf: 0,         // rAF 句柄（0 = 循环未运行）
    lastSetTop: -1, // 本 hook 最近一次写入的 scrollTop（读回值），用于识别外部滚动
    lastWheel: 0,   // 最近一次 wheel 时间戳（越界回弹避让活跃输入）
  });

  const maxScroll = useCallback(() => {
    const el = ref.current;
    return el ? Math.max(0, el.scrollHeight - el.clientHeight) : 0;
  }, [ref]);

  /** 渲染目标：界内直接取 target；越界部分按阻尼压缩（橡皮筋视觉效果） */
  const renderTarget = useCallback(() => {
    const { target } = st.current;
    const max = maxScroll();
    if (target < 0) return target * rubberBandDamping;
    if (target > max) return max + (target - max) * rubberBandDamping;
    return target;
  }, [maxScroll, rubberBandDamping]);

  const tick = useCallback(() => {
    const el = ref.current;
    if (!el) { st.current.raf = 0; return; }
    const s = st.current;
    const max = maxScroll();
    // 松手回弹：120ms 无 wheel 输入且目标越界 → 目标向最近边界缓动收敛
    const overTop = s.target < 0;
    const overBottom = s.target > max;
    if ((overTop || overBottom) && performance.now() - s.lastWheel > 120) {
      const edge = overTop ? 0 : max;
      s.target += (edge - s.target) * 0.14;
      if (Math.abs(edge - s.target) < 1) s.target = edge;
    }
    const rt = renderTarget();
    s.current += (rt - s.current) * ease;
    // 完全收敛且目标已回到界内 → 吸附边界、结束循环（零空闲开销）
    if (Math.abs(rt - s.current) < 0.4 && s.target >= -0.5 && s.target <= max + 0.5) {
      s.current = s.target = clamp(s.target, 0, max);
      el.scrollTop = s.current;
      s.lastSetTop = el.scrollTop;
      s.raf = 0;
      return;
    }
    el.scrollTop = s.current;
    s.lastSetTop = el.scrollTop;
    s.raf = requestAnimationFrame(tick);
  }, [ref, ease, maxScroll, renderTarget]);

  const wake = useCallback(() => {
    if (!st.current.raf) st.current.raf = requestAnimationFrame(tick);
  }, [tick]);

  const onWheel = useCallback((e: WheelEvent) => {
    const el = ref.current;
    if (!el) return;
    if (e.deltaX !== 0 || e.ctrlKey) return; // 横向滚动 / 缩放手势不接管
    const max = maxScroll();
    if (max === 0) return; // 容器不可滚 → 放行给外层，避免页面「卡死」
    e.preventDefault();
    // deltaMode 归一化：1=行(≈40px) 2=页(视口高)，0=像素（触摸板/多数浏览器）
    const unit = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? el.clientHeight : 1;
    const s = st.current;
    s.target = clamp(s.target + e.deltaY * unit * wheelMultiplier, -rubberBand, max + rubberBand);
    s.lastWheel = performance.now();
    wake();
  }, [ref, wheelMultiplier, rubberBand, maxScroll, wake]);

  // 外部滚动同步：scrollTop 变化且非本 hook 写入 → 用户拖滚动条/键盘/触摸惯性/其它代码赋值，
  // 全量对齐内部状态（此时若 rAF 在跑，会因目标=当前位置而自然收敛退出，绝不与用户抢滚动）
  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const s = st.current;
    if (Math.abs(el.scrollTop - s.lastSetTop) < 1.5) return;
    s.target = s.current = el.scrollTop;
  }, [ref]);

  const shouldEnable = useMemo(() => {
    if (enabled !== undefined) return enabled;
    if (typeof window === 'undefined' || !window.matchMedia) return false;
    return window.matchMedia('(pointer: fine)').matches
      && !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }, [enabled]);

  useEffect(() => {
    const el = ref.current;
    if (!el || !shouldEnable) return;
    // 状态容器引用（从不整体重赋值，字段可变），effect 体内捕获供 cleanup 安全复用
    const s = st.current;
    s.target = s.current = el.scrollTop;
    el.addEventListener('wheel', onWheel, { passive: false });
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('scroll', onScroll);
      if (s.raf) {
        cancelAnimationFrame(s.raf);
        s.raf = 0;
      }
    };
  }, [ref, onWheel, onScroll, shouldEnable]);

  const scrollTo = useCallback((top: number) => {
    const el = ref.current;
    if (!el) return;
    const max = Math.max(0, el.scrollHeight - el.clientHeight);
    st.current.target = clamp(top, 0, max);
    wake();
  }, [ref, wake]);

  const sync = useCallback(() => {
    const el = ref.current;
    if (el) st.current.target = st.current.current = el.scrollTop;
  }, [ref]);

  return { scrollTo, sync };
}
