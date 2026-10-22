/**
 * 系统任务列表 ↔ 详情往返时的滚动位置。
 *
 * 真正滚动的是 `.tasks-list-section`（filters 固定，卡片区 overflow:auto），
 * 不是外层 `.tabbar-shell__content`（tasks-view 自身 overflow:hidden）。
 * 列表与详情同级路由会卸掉 TasksView；PC 微信还是整页跳转。
 * 用 sessionStorage 记住列表滚动，返回时再写回去。
 */

export const TASKS_LIST_SCROLL_KEY = 'ors:tasks-list-scroll';
/** 系统任务页唯一纵向滚动容器（见 global.css .tasks-list-section） */
export const TASKS_LIST_SCROLL_SELECTOR = '.tasks-list-section';

export function isTasksListPath(pathname: string): boolean {
  return pathname === '/tasks' || pathname === '/tasks/';
}

/** /tasks/:id（不含 /operations 等更深子页） */
export function isTaskDetailPath(pathname: string): boolean {
  return /^\/tasks\/[^/]+$/.test(pathname);
}

/** 某张工单下的任意页：详情或 operations 等 */
export function isTaskTicketPath(pathname: string): boolean {
  return /^\/tasks\/[^/]+/.test(pathname) && !isTasksListPath(pathname);
}

export type TasksScrollAction = 'save' | 'restore' | 'keep' | 'reset';

/**
 * 根据路由前后决定动作。
 * - save：列表 → 详情（记下滚动）
 * - restore：详情（或子页）→ 列表
 * - keep：同路径首屏 / 详情↔operations —— 不动、更不能清掉已存滚动
 * - reset：离开任务详情流（切 Tab 等）—— 清零并忘掉
 */
export function tasksScrollAction(from: string, to: string): TasksScrollAction {
  if (from === to) return 'keep';
  if (isTasksListPath(from) && isTaskDetailPath(to)) return 'save';
  if (isTaskTicketPath(from) && isTasksListPath(to)) return 'restore';
  if (isTaskTicketPath(from) && isTaskTicketPath(to)) return 'keep';
  return 'reset';
}

export function saveTasksListScroll(scrollTop: number, opts?: { force?: boolean }): void {
  try {
    const next = Math.max(0, Math.round(scrollTop));
    // 列表卸掉后内容变矮，scrollTop 会被钳成 0；此时勿覆盖点进前已抓到的真实位置。
    // force：列表点击当下的真实位置（含滚在顶部的 0）必须写入。
    if (next === 0 && !opts?.force) {
      const existing = sessionStorage.getItem(TASKS_LIST_SCROLL_KEY);
      const prev = existing == null || existing === '' ? null : Number(existing);
      if (prev != null && Number.isFinite(prev) && prev > 0) return;
    }
    sessionStorage.setItem(TASKS_LIST_SCROLL_KEY, String(next));
  } catch {
    /* 隐私模式等写失败时忽略 */
  }
}

export function readTasksListScroll(): number | null {
  try {
    const raw = sessionStorage.getItem(TASKS_LIST_SCROLL_KEY);
    if (raw == null || raw === '') return null;
    const n = Number(raw);
    return Number.isFinite(n) && n >= 0 ? n : null;
  } catch {
    return null;
  }
}

export function clearTasksListScroll(): void {
  try {
    sessionStorage.removeItem(TASKS_LIST_SCROLL_KEY);
  } catch {
    /* ignore */
  }
}

export function getTasksListScrollEl(): HTMLElement | null {
  if (typeof document === 'undefined') return null;
  const el = document.querySelector(TASKS_LIST_SCROLL_SELECTOR);
  return el instanceof HTMLElement ? el : null;
}

/** 从列表滚动容器读取并保存（点进详情前调用；覆盖 PC 微信整页跳转） */
export function captureTasksListScrollFromDom(): void {
  const el = getTasksListScrollEl();
  if (el) saveTasksListScroll(el.scrollTop, { force: true });
}

/**
 * 列表数据渲染后再写回滚动（列表高度够了才撑得住 scrollTop）。
 * 成功则清掉存储，避免下次误跳。
 */
export function restoreTasksListScrollFromDom(): boolean {
  const saved = readTasksListScroll();
  if (saved == null || saved <= 0) return false;
  const el = getTasksListScrollEl();
  if (!el) return false;
  el.scrollTop = saved;
  // 高度仍不够时先别清，留给下一次 tickets 更新后再试
  if (el.scrollHeight <= el.clientHeight + 8) return false;
  clearTasksListScroll();
  return true;
}
