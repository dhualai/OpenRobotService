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
  };
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
    [hitTest, setHover],
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
    try {
      if (s.pointerId !== -1 && s.sourceEl?.hasPointerCapture?.(s.pointerId)) {
        s.sourceEl.releasePointerCapture(s.pointerId);
      }
    } catch {
      /* 忽略 */
    }
    s.sourceEl = null;
    s.pointerId = -1;
  }, [setHover]);

  // 全局 pointermove / pointerup / pointercancel 监听（拖拽期间追踪指针）
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const s = state.current;
      if (!s.active || e.pointerId !== s.pointerId) return;
      const dx = e.clientX - s.startX;
      const dy = e.clientY - s.startY;
      if (!s.dragging) {
        // 判定是否进入拖拽：超过 6px 位移
        if (Math.hypot(dx, dy) > 6) {
          s.dragging = true;
          s.moved = true;
        } else {
          return;
        }
      }
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

    document.addEventListener('pointermove', onMove, { passive: true });
    document.addEventListener('pointerup', onUp, { passive: true });
    document.addEventListener('pointercancel', onCancel, { passive: true });
    return () => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      document.removeEventListener('pointercancel', onCancel);
    };
  }, [finishDrag, cancelDrag, setHover, hitTest]);

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

          // 触屏：长按判定（避免与滚动/点击冲突）；鼠标：立即进入候选（由移动阈值判定）
          if (!isMouse) {
            s.longPressTimer = window.setTimeout(() => {
              if (!state.current.active || state.current.draggedId !== id) return;
              state.current.dragging = true;
              state.current.moved = true;
              // 触屏长按后尝试捕获，避免滚动干扰
              try {
                (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
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
      };
    },
    [longPressDelay],
  );

  return { bind };
}
