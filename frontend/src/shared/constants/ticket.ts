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
