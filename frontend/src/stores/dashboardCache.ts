// 后台看板数据本地缓存（stale-while-revalidate 策略）
//
// 产品口径：进入后台管理时先用「上一次的看板数据」立即渲染图表，
// 本次 summary-all 接口返回后再覆盖刷新；接口慢（2~3s）期间用户看到的
// 是上一份快照而非空表。
//
// 缓存按「用户名 + 数据口径」双重校验：
// - username：换账号不串数据；
// - filterKey：数据口径 = 全部项目('all') 或 当前用户关联项目 ID 集合，
//   权限/关联项目变化后旧缓存不生效。
// 过期（MAX_AGE_MS）或解析失败一律视为无缓存，回退空态渲染，不阻塞。
import type { DashboardSummaryAll } from '@/api/dashboard';

export interface DashboardCacheEntry {
  username: string;
  filterKey: string; // 'all' | 排序后的 projectIds 逗号串（口径标识）
  data: DashboardSummaryAll;
  cachedAt: number; // epoch ms，仅用于过期判断
}

const CACHE_KEY = 'admin_dashboard_summary_cache_v1';
/** 缓存有效期：7 天。看板数据时效性弱，过期后直接丢弃走空态 */
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

/** 数据口径标识：canViewAll 为全部项目；否则按关联项目 ID 集合（排序后拼接，避免顺序抖动） */
export function buildDashboardFilterKey(canViewAll: boolean, projectIds: string[]): string {
  if (canViewAll) return 'all';
  return [...projectIds].sort().join(',');
}

/** 读取与当前用户+口径匹配且未过期的缓存；不匹配/过期/损坏返回 null */
export function loadDashboardCache(username: string, filterKey: string): DashboardCacheEntry | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const entry = JSON.parse(raw) as DashboardCacheEntry;
    if (!entry?.data || !entry.username || !entry.filterKey) return null;
    if (entry.username !== username || entry.filterKey !== filterKey) return null;
    if (Date.now() - entry.cachedAt > MAX_AGE_MS) return null;
    return entry;
  } catch {
    return null;
  }
}

/** 写入缓存（每次 summary-all 成功返回后调用）；失败静默（隐私模式等场景） */
export function saveDashboardCache(
  username: string,
  filterKey: string,
  data: DashboardSummaryAll,
): void {
  try {
    const entry: DashboardCacheEntry = {
      username,
      filterKey,
      data,
      cachedAt: Date.now(),
    };
    localStorage.setItem(CACHE_KEY, JSON.stringify(entry));
  } catch { /* 存储不可用时静默跳过 */ }
}
