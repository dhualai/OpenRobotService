/**
 * 系统任务列表 ↔ 详情往返时的滚动位置。
 *
 * 列表与详情是同级路由，进详情会卸掉 TasksView；MainLayout 又在 pathname
 * 变化时把外层 `.tabbar-shell__content` 滚回顶部。PC 微信还是整页跳转。
 * 用 sessionStorage 记住列表滚动，返回时再写回去。
 */

export const TASKS_LIST_SCROLL_KEY = 'ors:tasks-list-scroll';

export function isTasksListPath(pathname: string): boolean {
  return pathname === '/tasks' || pathname === '/tasks/';
}

/** /tasks/:id（不含 /operations 等更深子页） */
export function isTaskDetailPath(pathname: string): boolean {
  return /^\/tasks\/[^/]+$/.test(pathname);
}

export type TasksScrollAction = 'save' | 'restore' | 'reset';

/** 根据路由前后决定：离开列表时保存 / 回到列表时恢复 / 其它情况清零 */
export function tasksScrollAction(from: string, to: string): TasksScrollAction {
  if (isTasksListPath(from) && isTaskDetailPath(to)) return 'save';
  if (isTaskDetailPath(from) && isTasksListPath(to)) return 'restore';
  return 'reset';
}

export function saveTasksListScroll(scrollTop: number): void {
  try {
    sessionStorage.setItem(TASKS_LIST_SCROLL_KEY, String(Math.max(0, Math.round(scrollTop))));
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

/** 从外层滚动容器读取并保存（列表点进详情前调用，覆盖 PC 微信整页跳转） */
export function captureTasksListScrollFromDom(): void {
  if (typeof document === 'undefined') return;
  const el = document.querySelector('.tabbar-shell__content');
  if (el instanceof HTMLElement) saveTasksListScroll(el.scrollTop);
}
