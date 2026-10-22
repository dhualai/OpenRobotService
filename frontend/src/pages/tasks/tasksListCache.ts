/**
 * 系统任务列表内存缓存：详情与列表是同级路由，返回会卸掉 TasksView 再挂载。
 * 有缓存时先画出上一页卡片，再静默拉网，避免整页「加载中…」闪约 1 秒。
 */

export type TasksListCachePayload = {
  key: string;
  tickets: unknown[];
  total: number;
  relatedTickets: unknown[];
};

let cache: TasksListCachePayload | null = null;

export function readTasksListCache(key: string): TasksListCachePayload | null {
  if (!cache || cache.key !== key) return null;
  return cache;
}

export function writeTasksListCache(payload: TasksListCachePayload): void {
  cache = payload;
}

export function buildTasksListCacheKey(parts: Record<string, unknown>): string {
  return JSON.stringify(parts);
}
