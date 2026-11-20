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

/** 读写共用同一字段顺序，避免 JSON.stringify 键序不一致导致缓存永远 miss */
export function buildTasksListCacheKeyFromState(s: {
  search: string;
  statusFilter: string[];
  priorityFilter: string[];
  typeFilter: string[];
  relevanceFilter: string;
  projectFilter: string[];
  assigneeFilter: string;
  creatorFilter: string;
  createdStart: string;
  createdEnd: string;
  resolvedStart: string;
  resolvedEnd: string;
  closedStart: string;
  closedEnd: string;
  page: number;
  sortBy: string;
  sortOrder: string;
  view: string;
  user: string;
  projects: string[];
  canViewAll: boolean;
}): string {
  return buildTasksListCacheKey({
    search: s.search,
    statusFilter: s.statusFilter,
    priorityFilter: s.priorityFilter,
    typeFilter: s.typeFilter,
    relevanceFilter: s.relevanceFilter,
    projectFilter: s.projectFilter,
    assigneeFilter: s.assigneeFilter,
    creatorFilter: s.creatorFilter,
    createdStart: s.createdStart,
    createdEnd: s.createdEnd,
    resolvedStart: s.resolvedStart,
    resolvedEnd: s.resolvedEnd,
    closedStart: s.closedStart,
    closedEnd: s.closedEnd,
    page: s.page,
    sortBy: s.sortBy,
    sortOrder: s.sortOrder,
    view: s.view,
    user: s.user,
    projects: [...s.projects].sort(),
    canViewAll: s.canViewAll,
  });
}
