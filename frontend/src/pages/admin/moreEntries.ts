export interface MoreFunctionEntry {
  path: string;
  label: string;
  kind: MoreEntryIconKind;
  tone: string;
  group?: 'data-resource';
  /** 外部独立应用地址：走新标签页打开，不走前端路由 */
  external?: string;
}

export type MoreEntryIconKind = 'projects' | 'data' | 'reports' | 'other';

/**
 * 「更多功能」区的四块入口砖。
 *
 * 全部是摇人吧内部路由；外部独立应用（如智库「功能拓扑」）**不放在这里**，
 * 而是收在「其他」里作为一级菜单（见 pages/admin/AdminEntries.tsx）。
 */
export const MORE_FUNCTION_ENTRIES: MoreFunctionEntry[] = [
  { path: '/admin/project-manage', label: '项目管理', kind: 'projects', tone: 'blue-1' },
  { path: '/admin/data-resource', label: '数据资源', kind: 'data', tone: 'blue-2', group: 'data-resource' },
  { path: '/admin/daily-summary', label: '日报周报', kind: 'reports', tone: 'blue-3' },
  { path: '/admin/entries', label: '其他', kind: 'other', tone: 'blue-4' },
];