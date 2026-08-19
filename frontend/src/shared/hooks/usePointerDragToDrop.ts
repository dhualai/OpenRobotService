import { useCallback, useEffect, useRef } from 'react';

export interface PointerDragToDropOptions {
  /** 落点命中目标节点时的回调（拖拽结束，松手落在目标节点上） */
  onDrop?: (draggedId: string, targetId: string) => void;
  /** 拖拽高亮目标 id 变化时回调（用于高亮渲染） */
  onHoverChange?: (targetId: string | null) => void;
  /** 判定一个元素是否为「可落点」目标（默认读取 data-drop-id 属性） */
  resolveTargetId?: (el: HTMLElement) => string | null;
  /** 长按进入拖拽的延时（ms）。触屏长按避免与滚动/点击冲突；鼠标按下即进入。默认 220 */
  longPressDelay?: number;
}

export interface PointerDragToDropHandle {
  /** 绑定到可拖拽节点上的 props（每个节点都传自身 id） */
  bind: (id: string) => {
    'data-drag-id': string;
    'data-drop-id': string;
    onPointerDown: (e: React.PointerEvent) => void;
    onContextMenu: (e: React.MouseEvent) => void;
  };
}

// ── 全局样式（模块加载时注入一次）：根治移动端长按拖拽被原生行为打断 ──
// 触屏长按卡片时，iOS/Android WebView 默认会启动原生文本选择并弹出
// 「复制/全选/搜一搜」菜单，系统随即接管手势（触发 pointercancel），
// 自定义拖拽被打断。以下 CSS 禁掉文本选择、长按系统呼出（iOS）与原生拖拽。
// 用 [data-drag-id] 属性选择器精准命中拖拽源，无需调用方改样式。
let _dragCssInjected = false;
function injectDragSourceCss() {
  if (_dragCssInjected || typeof document === 'undefined') return;
  _dragCssInjected = true;
  const style = document.createElement('style');
  style.id = 'pointer-drag-source-css';
  style.textContent = [
    // 拖拽源：禁文本选择/系统呼出/原生拖拽（根治长按弹「复制/全选/搜一搜」）
    '[data-drag-id]{-webkit-user-select:none;user-select:none;-webkit-touch-callout:none;-webkit-user-drag:none;user-drag:none;}',
    // 拖拽中的源卡片：半透明 + 去饱和，明确「这张卡正被拖走」
    '[data-drag-id].is-drag-source{opacity:.35;filter:saturate(.6);}',
    // 幽灵卡片：克隆拖拽源，fixed 定位跟随指针；浮起阴影 + 180ms 弹性放大动画；
    // pointer-events:none 保证不遮挡 elementFromPoint 落点判定。
    // 注意：透明度固定在类上而非 keyframes（避免动画结束 opacity 跳变）
    '.pointer-drag-ghost{position:fixed;z-index:9999;pointer-events:none;margin:0;opacity:.92;'
      + 'box-shadow:0 8px 24px rgba(0,0,0,.22);will-change:transform;'
      + 'animation:pointer-drag-ghost-pop .18s cubic-bezier(.2,.8,.4,1.4);}',
    '@keyframes pointer-drag-ghost-pop{from{transform:scale(.92);}to{transform:scale(1.05);}}',
  ].join('');
  document.head.appendChild(style);
}

/**
 * Pointer Events 通用「拖拽落点」hook —— 让鼠标拖拽与触屏拖拽统一工作。
 *
 * 背景：HTML5 原生 draggable + dataTransfer 在 iOS/Android 触屏下几乎不触发，
 * 无法在移动端完成「拖拽 A 到 B」这类交互。本 hook 改用 Pointer Events：
 *   - 桌面鼠标：pointerdown（左键）即进入拖拽候选，移动超过阈值后开始拖拽。
 *   - 触屏：pointerdown 后先长按（默认 220ms）判定为拖拽意图，再移动手指；
 *     未达长按时长视为普通点击，不干扰卡片点击/滚动。
 *   - 拖拽过程中用 document.elementFromPoint 定位松手落点，命中带 data-drop-id
 *     的节点即回调 onDrop(draggedId, targetId)。
 *
 * 用法：
 *   const drag = usePointerDragToDrop({ onDrop: (a, b) => setSupervisor(a, b), onHoverChange: setDragOverId });
 *   <div {...drag.bind(user.id)}>...</div>
 */
export function usePointerDragToDrop({
  onDrop,
  onHoverChange,
  resolveTargetId,
  longPressDelay = 220,
}: PointerDragToDropOptions = {}): PointerDragToDropHandle {
  injectDragSourceCss();

  const onDropRef = useRef(onDrop);
  const onHoverChangeRef = useRef(onHoverChange);
  const resolveRef = useRef(resolveTargetId);
  onDropRef.current = onDrop;
  onHoverChangeRef.current = onHoverChange;
  resolveRef.current = resolveTargetId;

  const state = useRef({
    active: false, // 是否已进入拖拽态
    moved: false, // 是否发生位移（用于抑制点击）
    dragging: false, // 是否已越过阈值/长按判定，真正开始拖拽
    pointerId: -1,
    startX: 0,
    startY: 0,
    draggedId: '', // 拖拽源节点 id
    hoverId: null as string | null, // 当前悬停落点 id
    longPressTimer: 0, // 触屏长按计时器句柄
    sourceEl: null as HTMLElement | null,
    isMouse: true, // pointerType 是否为鼠标/笔（触屏走长按判定）
    ghostEl: null as HTMLElement | null, // 跟随指针的「幽灵卡片」克隆节点
    ghostStartX: 0, // 幽灵卡片创建时的指针坐标（平移基准）
    ghostStartY: 0,
  });

  /** 读取元素上的落点目标 id（可自定义解析，默认读 data-drop-id） */
  const getTargetId = useCallback((el: HTMLElement): string | null => {
    if (resolveRef.current) return resolveRef.current(el);
    const id = el.getAttribute('data-drop-id');
    return id || null;
  }, []);

  /** 根据指针坐标查找命中的落点节点 */
  const hitTest = useCallback(
    (clientX: number, clientY: number, draggedId: string): string | null => {
      let el = document.elementFromPoint(clientX, clientY) as HTMLElement | null;
      // 向上冒泡查找最近带 data-drop-id 的元素，跳过拖拽源自身
      while (el && el !== document.body) {
        const id = getTargetId(el);
        if (id && id !== draggedId) return id;
        el = el.parentElement;
      }
      return null;
    },
    [getTargetId],
  );

  const setHover = useCallback(
    (id: string | null) => {
      const s = state.current;
      if (s.hoverId === id) return;
      s.hoverId = id;
      onHoverChangeRef.current?.(id);
    },
    [],
  );

  // ── 拖拽动效：幽灵卡片（克隆拖拽源，fixed 定位跟随指针）+ 源卡片半透明 ──
  // 拖拽视觉反馈三件套：原卡片变淡（is-drag-source）、克隆卡片浮起跟随手指、
  // 松手/取消时清理。ghost 需 pointer-events:none，避免遮挡 elementFromPoint 落点判定。
  const startGhost = useCallback((clientX: number, clientY: number) => {
    const s = state.current;
    if (s.ghostEl || !s.sourceEl) return;
    const src = s.sourceEl;
    const rect = src.getBoundingClientRect();
    const ghost = src.cloneNode(true) as HTMLElement;
    // 克隆节点去除所有 id，避免与源 DOM 的 id 冲突
    ghost.removeAttribute('id');
    ghost.querySelectorAll('[id]').forEach((n) => n.removeAttribute('id'));
    ghost.classList.add('pointer-drag-ghost');
    ghost.style.left = `${rect.left}px`;
    ghost.style.top = `${rect.top}px`;
    ghost.style.width = `${rect.width}px`;
    ghost.style.transform = 'scale(1.05)';
    document.body.appendChild(ghost);
    s.ghostEl = ghost;
    s.ghostStartX = clientX;
    s.ghostStartY = clientY;
    src.classList.add('is-drag-source');
  }, []);

  const moveGhost = useCallback((clientX: number, clientY: number) => {
    const s = state.current;
    if (!s.ghostEl) return;
    const dx = clientX - s.ghostStartX;
    const dy = clientY - s.ghostStartY;
    s.ghostEl.style.transform = `translate(${dx}px, ${dy}px) scale(1.05)`;
  }, []);

  const removeGhost = useCallback(() => {
    const s = state.current;
    s.ghostEl?.remove();
    s.ghostEl = null;
    s.sourceEl?.classList.remove('is-drag-source');
  }, []);

  /** 结束拖拽并结算（松手落点） */
  const finishDrag = useCallback(
    (clientX: number, clientY: number) => {
      const s = state.current;
      if (!s.active) return;
      const draggedId = s.draggedId;
      const targetId = hitTest(clientX, clientY, draggedId);
      // 清除悬停高亮
      setHover(null);
      // 清理状态
      if (s.longPressTimer) {
        window.clearTimeout(s.longPressTimer);
        s.longPressTimer = 0;
      }
      s.active = false;
      s.dragging = false;
      s.moved = false;
      s.draggedId = '';
      removeGhost();
      try {
        if (s.pointerId !== -1 && s.sourceEl?.hasPointerCapture?.(s.pointerId)) {
          s.sourceEl.releasePointerCapture(s.pointerId);
        }
      } catch {
        /* 忽略 */
      }
      s.sourceEl = null;
      s.pointerId = -1;
      if (targetId && draggedId && targetId !== draggedId) {
        onDropRef.current?.(draggedId, targetId);
      }
    },
    [hitTest, setHover, removeGhost],
  );

  const cancelDrag = useCallback(() => {
    const s = state.current;
    if (s.longPressTimer) {
      window.clearTimeout(s.longPressTimer);
      s.longPressTimer = 0;
    }
    s.active = false;
    s.dragging = false;
    s.moved = false;
    s.draggedId = '';
    setHover(null);
    removeGhost();
    try {
      if (s.pointerId !== -1 && s.sourceEl?.hasPointerCapture?.(s.pointerId)) {
        s.sourceEl.releasePointerCapture(s.pointerId);
      }
    } catch {
      /* 忽略 */
    }
    s.sourceEl = null;
    s.pointerId = -1;
  }, [setHover, removeGhost]);

  // 全局 pointermove / pointerup / pointercancel 监听（拖拽期间追踪指针）
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const s = state.current;
      if (!s.active || e.pointerId !== s.pointerId) return;
      const dx = e.clientX - s.startX;
      const dy = e.clientY - s.startY;
      if (!s.dragging) {
        // 触屏未达长按时长就移动超阈值 → 用户意图是滚动列表，取消拖拽候选（放行滚动）
        if (!s.isMouse && !s.longPressTimer) {
          cancelDrag();
          return;
        }
        // 判定是否进入拖拽：超过 6px 位移（鼠标；触屏需长按已到点）
        if (Math.hypot(dx, dy) > 6) {
          s.dragging = true;
          s.moved = true;
          startGhost(e.clientX, e.clientY);
        } else {
          return;
        }
      }
      moveGhost(e.clientX, e.clientY);
      setHover(hitTest(e.clientX, e.clientY, s.draggedId));
    };

    const onUp = (e: PointerEvent) => {
      const s = state.current;
      if (!s.active || e.pointerId !== s.pointerId) return;
      finishDrag(e.clientX, e.clientY);
    };

    const onCancel = (e: PointerEvent) => {
      const s = state.current;
      if (!s.active || e.pointerId !== s.pointerId) return;
      cancelDrag();
    };

    // 触屏拖拽激活期间阻止页面滚动：touchmove 必须非 passive 才能 preventDefault
    //（pointermove 无法阻止滚动手势）。正常状态（非拖拽中）不拦截，页面滚动不受影响。
    const onTouchMove = (e: TouchEvent) => {
      const s = state.current;
      if (s.dragging && e.cancelable) {
        e.preventDefault();
      }
    };

    document.addEventListener('pointermove', onMove, { passive: true });
    document.addEventListener('pointerup', onUp, { passive: true });
    document.addEventListener('pointercancel', onCancel, { passive: true });
    document.addEventListener('touchmove', onTouchMove, { passive: false });
    return () => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      document.removeEventListener('pointercancel', onCancel);
      document.removeEventListener('touchmove', onTouchMove);
    };
  }, [finishDrag, cancelDrag, setHover, hitTest, startGhost, moveGhost]);

  // 拖拽中抑制子元素 click（拖拽位移后松手不应触发卡片点击）
  useEffect(() => {
    const onClickCapture = (e: MouseEvent) => {
      const s = state.current;
      if (s.moved) {
        e.preventDefault();
        e.stopPropagation();
        s.moved = false;
      }
    };
    document.addEventListener('click', onClickCapture, true);
    return () => document.removeEventListener('click', onClickCapture, true);
  }, []);

  const bind = useCallback(
    (id: string) => {
      return {
        'data-drag-id': id,
        'data-drop-id': id,
        onPointerDown: (e: React.PointerEvent) => {
          // 仅主键（鼠标左键 / 单指触摸）触发拖拽
          if (e.button !== 0 && e.pointerType === 'mouse') return;
          const s = state.current;
          const isMouse = e.pointerType === 'mouse' || e.pointerType === 'pen';
          s.pointerId = e.pointerId;
          s.startX = e.clientX;
          s.startY = e.clientY;
          s.draggedId = id;
          s.moved = false;
          s.active = true;
          s.dragging = false;
          s.sourceEl = e.currentTarget as HTMLElement;
          s.isMouse = isMouse;

          // 触屏：长按判定（避免与滚动/点击冲突）；鼠标：立即进入候选（由移动阈值判定）
          if (!isMouse) {
            s.longPressTimer = window.setTimeout(() => {
              const st = state.current;
              if (!st.active || st.draggedId !== id) return;
              st.dragging = true;
              st.moved = true;
              st.longPressTimer = 0; // 长按已到点（标记，供 onMove 判定放行拖拽）
              // 长按到点立即生成幽灵卡片（拾起反馈，未移动也可见「已拾起」）
              startGhost(st.startX, st.startY);
              // 触屏长按后尝试捕获，避免滚动干扰（注：currentTarget 在异步回调中已失效，
              // 用 sourceEl 兜底；捕获失败不影响拖拽，document 级监听照常追踪）
              try {
                st.sourceEl?.setPointerCapture(st.pointerId);
              } catch {
                /* 忽略 */
              }
            }, longPressDelay);
          } else {
            try {
              (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
            } catch {
              /* 忽略 */
            }
          }
        },
        // Android 长按会触发 contextmenu（原生菜单），拖拽源上直接阻止
        onContextMenu: (e: React.MouseEvent) => {
          if (state.current.dragging) e.preventDefault();
        },
      };
    },
    [longPressDelay, startGhost],
  );

  return { bind };
}
