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

// ===== 优先级 → 最晚解决时长（小时）=====
// 紧急 1 天 / 高 3 天 / 中 5 天 / 低 14 天。
// 同时收录中英文 value（提单弹窗用中文、编辑弹窗用英文），供「最晚解决时间」区间计算复用。
export const PRIORITY_DEADLINE_HOURS: Record<string, number> = {
  紧急: 24,
  高: 72,
  中: 120,
  低: 336,
  urgent: 24,
  high: 72,
  medium: 120,
  low: 336,
};

/** 归一化优先级为英文 key（兼容中文/英文输入），无法识别返回 null。 */
export function normalizePriority(priority?: string | null): string | null {
  if (!priority) return null;
  const t = String(priority).trim();
  if (!t) return null;
  const en = PRIORITY_VALUE_MAP[t] || t.toLowerCase();
  return PRIORITY_DEADLINE_HOURS[en] != null ? en : null;
}

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
//   撤回：新建(new) / 待处理(pending) / 处理中(in_progress) 可用——提单人可在派单后/处理中撤回，防止派错单
//   已解决(resolved) / 已取消(canceled) / 已关闭(closed)：三个按钮均不显示
// 兼容历史状态值：pending_dispatch(待派单)、dispatched(已派单) 属「新建/待处理」组；
// 未收录的非终态值从宽按「新建/待处理」处理（可催办/撤回、不可上报）。
// 撤回操作权限：仅提单人(created_by === 当前用户) 或 管理员(isAdmin) 可执行，处理人不可撤回。

export type TicketStatusLike = string | null | undefined;

/** 处理中（仅该状态可上报） */
const REPORTABLE_STATUSES = new Set(['in_progress']);
/** 终态（操作按钮整体不显示） */
const TERMINAL_STATUSES = new Set(['resolved', 'canceled', 'cancelled', 'closed']);
/** 撤回可用状态：新建 / 待派单 / 已派单 / 待处理 / 处理中 */
const CANCELABLE_STATUSES = new Set(['new', 'pending_dispatch', 'dispatched', 'pending', 'in_progress']);

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

/** 撤回状态可用：新建 / 待派单 / 已派单 / 待处理 / 处理中——不区分操作人，仅判断状态。
 *  处理中可撤回用于「派错单」场景：提单人发现派错人后可及时撤回，避免错误流转。 */
export function canCancelTicket(status: TicketStatusLike): boolean {
  if (isTerminalTicketStatus(status)) return false;
  return CANCELABLE_STATUSES.has(normalizeKey(status));
}

/** 撤回操作权限：仅提单人(created_by === 当前用户) 或 管理员可撤回，处理人不可撤回。
 *  与 canCancelTicket（状态可用）配合使用：状态可用 + 操作人有权限，才显示可点击的撤回按钮。 */
export function canCancelTicketByUser(
  createdBy: string | null | undefined,
  currentUsername: string | null | undefined,
  isAdmin: boolean,
  currentUserId?: string | null,
): boolean {
  if (isAdmin) return true;
  if (!createdBy) return false;
  if (currentUsername && currentUsername === createdBy) return true;
  if (currentUserId && currentUserId === createdBy) return true;
  return false;
}

/** 撤回按钮「显示」规则：非终态且状态属于可撤回组（新建/待派单/已派单/待处理/处理中）即展示。
 *  与 canCancelTicket（操作权限）对齐——状态可撤回时按钮显示，再由 canCancelTicketByUser 判定操作人权限，
 *  无权限时按钮不显示（而非置灰），保持微信端简洁。 */
export function canShowCancelButton(status?: string | null): boolean {
  return canCancelTicket(status);
}

/** 是否允许修改优先级：仅「尚未派单」状态可修改（新建 new / 待派单 pending_dispatch）。
 * 工单一经派单（dispatched/in_progress/pending 等）即进入处理流程，优先级不再允许变更。 */
export function canEditPriority(status: TicketStatusLike): boolean {
  const key = normalizeKey(status);
  return key === 'new' || key === 'pending_dispatch';
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
