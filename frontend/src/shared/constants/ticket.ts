// 状态映射
export const STATUS_DISPLAY_MAP: Record<string, string> = {
  new: '新建',
  in_progress: '进行中',
  pending: '已挂起',
  resolved: '已解决',
  canceled: '已取消',
  closed: '已关闭',
};

export const STATUS_VALUE_MAP: Record<string, string> = {
  '新建': 'new',
  '进行中': 'in_progress',
  '已挂起': 'pending',
  '已解决': 'resolved',
  '已取消': 'canceled',
  '已关闭': 'closed',
};

export const PRIORITY_VALUE_MAP: Record<string, string> = {
  '低': 'low',
  '中': 'medium',
  '高': 'high',
  '紧急': 'urgent',
};

export const PRIORITY_DISPLAY_MAP: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
  urgent: '紧急',
};

export const TICKET_TYPE_DISPLAY_MAP: Record<string, string> = {
  bug: '缺陷',
  feature: '功能',
  support: '支持',
  question: '问题',
  problem: '问题',
  other: '其他',
};

export const TICKET_TYPE_VALUE_MAP: Record<string, string> = {
  '缺陷': 'bug',
  '功能': 'feature',
  '支持': 'support',
  '问题': 'problem',
  '其他': 'other',
};

export function normalizeStatus(backendStatus: string): string {
  if (!backendStatus) return '新建';
  const lower = backendStatus.toLowerCase();
  return STATUS_DISPLAY_MAP[lower] || backendStatus;
}

// ===== 工单操作（催办/上报/撤回）状态约束 =====
// 规则：
//   催办：新建(new) / 待处理(pending) 可用
//   上报：处理中(in_progress) 可用
//   撤回：新建(new) / 待处理(pending) 可用
//   已解决(resolved) / 已取消(canceled) / 已关闭(closed)：三个按钮均不显示
// 兼容历史状态值：pending_dispatch(待派单)、dispatched(已派单) 属「新建/待处理」组；
// 未收录的非终态值从宽按「新建/待处理」处理（可催办/撤回、不可上报）。

export type TicketStatusLike = string | null | undefined;

/** 处理中（仅该状态可上报） */
const REPORTABLE_STATUSES = new Set(['in_progress']);
/** 终态（操作按钮整体不显示） */
const TERMINAL_STATUSES = new Set(['resolved', 'canceled', 'cancelled', 'closed']);

const normalizeKey = (status: TicketStatusLike): string => (status || '').trim().toLowerCase();

/** 是否终态（已解决/已取消/已关闭）——终态时催办/上报/撤回按钮整体不显示 */
export function isTerminalTicketStatus(status: TicketStatusLike): boolean {
  return TERMINAL_STATUSES.has(normalizeKey(status));
}

/** 催办可用：新建 / 待处理 */
export function canUrgeTicket(status: TicketStatusLike): boolean {
  if (isTerminalTicketStatus(status)) return false;
  return !REPORTABLE_STATUSES.has(normalizeKey(status));
}

/** 上报可用：仅处理中 */
export function canReportTicket(status: TicketStatusLike): boolean {
  return REPORTABLE_STATUSES.has(normalizeKey(status));
}

/** 撤回可用：新建 / 待处理（规则同催办） */
export function canCancelTicket(status: TicketStatusLike): boolean {
  return canUrgeTicket(status);
}

/** 撤回按钮「显示」规则：仅「新建」组（new / 待派单 / 已派单）展示，其余状态隐藏。
 * 与 canCancelTicket（操作权限）区分——权限判定仍允许一定范围，但 UI 选择在非新建时直接隐藏按钮，
 * 避免在微信端出现「不可点击但未置灰」的禁用态。 */
export function canShowCancelButton(status?: string | null): boolean {
  const key = normalizeKey(status);
  return key === 'new' || key === 'pending_dispatch' || key === 'dispatched';
}

// ===== 状态颜色（TaskDetailPage / TasksView / TicketDetailPage 共用）=====
export const STATUS_COLOR_MAP: Record<string, string> = {
  new: '#0052d9',
  in_progress: '#2ba471',
  pending: '#e37318',
  paused: '#e37318',
  resolved: '#00a870',
  closed: '#999999',
  canceled: '#d54941',
  cancelled: '#d54941',
};

export const getStatusColor = (status: string): string => {
  const key = (status || '').toLowerCase();
  return STATUS_COLOR_MAP[key] || '#666666';
};
