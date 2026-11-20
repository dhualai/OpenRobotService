/**
 * 后台管理「更多功能」入口清单（单一事实源）。
 *
 * 独立成纯数据模块，便于单测直接断言入口存在性与指向，
 * 也便于后续增删入口时不必改动 Dashboard 主体。
 */

export interface MoreFunctionEntry {
  path: string;
  label: string;
  kind: MoreEntryIconKind;
  tone: string;
  group?: 'data-resource';
  /** 外部独立应用地址（如智库 /t/app/kb/）：走新标签页打开，不走前端路由 */
  external?: string;
}

export type MoreEntryIconKind = 'projects' | 'data' | 'reports' | 'other' | 'topology';

/**
 * 智库（功能拓扑）是独立微服务，作为摇人吧子页面挂在 <base>/kb/ 下（与摇人吧同源）。
 * 地址按环境区分：staging=/t/app/kb/、production=/p/app/kb/，由 VITE_KB_PORTAL_URL 注入。
 */
export const KB_PORTAL_URL: string = import.meta.env.VITE_KB_PORTAL_URL || '/t/app/kb/';

export const MORE_FUNCTION_ENTRIES: MoreFunctionEntry[] = [
  { path: '/admin/project-manage', label: '项目管理', kind: 'projects', tone: 'blue-1' },
  { path: '/admin/data-resource', label: '数据资源', kind: 'data', tone: 'blue-2', group: 'data-resource' },
  { path: '/admin/daily-summary', label: '日报周报', kind: 'reports', tone: 'blue-3' },
  { path: '/kb/', label: '功能拓扑', kind: 'topology', tone: 'blue-4', external: KB_PORTAL_URL },
  { path: '/admin/entries', label: '其他', kind: 'other', tone: 'blue-5' },
];