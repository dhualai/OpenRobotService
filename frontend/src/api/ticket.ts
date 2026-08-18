/**
 * 工单 API —— /api/tasks/*
 *
 * - 催办：POST /cuiban-notification（现成，TicketDetailPage handleUrge 已用）
 * - 评论：GET/POST /{ticket_id}/comments（现成，评论绑定 ticket_id）
 * - 附件：POST /comments/attachments（temp_id 关联，MinIO 存储，发评论时一并入库）
 * - 创建：POST /（系统任务创建，兜底双工单场景下工单2 直接指定 assigned_to=项目负责人）
 */
import { createRequest, getToken } from '@/api/client';
import API_CONFIG from '@/config/api';

const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');

export interface TicketComment {
  id: number;
  ticket_id: number;
  content: string;
  is_public: boolean;
  /** 附件列表（后端存的是 object_path 字符串数组，如 ["bucket/temp/file.png"]） */
  attachments?: unknown[];
  created_by: string;
  created_by_name?: string;
  created_at: string;
  updated_at: string;
}

/** 工单类型（与后端 TicketType 枚举对齐） */
export type TicketType = 'problem' | 'bug' | 'feature' | 'support' | 'other';
/** 工单优先级（与后端 TicketPriority 枚举对齐） */
export type TicketPriority = 'low' | 'medium' | 'high' | 'urgent';

export interface CreateTicketParams {
  title: string;
  description: string;
  ticket_type?: TicketType;
  priority?: TicketPriority;
  project_name?: string;
  project_id?: string;
  assigned_to?: string;       // 接单人 users.id（过渡期后端也认 username）
  deadline_at?: string;       // 最晚解决时间（ISO 字符串，兜底双工单工单2 复用弹窗选择值）
  customer?: string;
  metadata_info?: Record<string, unknown>;
  tags?: string[];
}

export interface CreatedTicket {
  id: number;
  title: string;
  assigned_to?: string | null;
  [k: string]: unknown;
}

/** 一键催办：通知指定用户 */
export const urgeTicket = (ticketId: number | string, assignedTo: string) =>
  request('/cuiban-notification', {
    method: 'POST',
    body: JSON.stringify({ ticket_id: Number(ticketId), notify_type: 1, assigned_to: assignedTo }),
  });

/** 上报：通知指定用户 + 管理员 */
export const reportTicket = (ticketId: number | string, assignedTo: string) =>
  request('/cuiban-notification', {
    method: 'POST',
    body: JSON.stringify({ ticket_id: Number(ticketId), notify_type: 1, assigned_to: assignedTo, to_admin: true }),
  });

/** 撤回：将工单状态置为已取消（Canceled）
 *  后端 PATCH /{task_id}/status 的 status 参数用 Body(..., embed=True) 声明，
 *  因此请求体必须是 JSON 对象 { status: "canceled" }（不能是裸字符串），否则 FastAPI 报 422 Field required。 */
export const cancelTicket = (ticketId: number | string) =>
  request(`/${Number(ticketId)}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status: 'canceled' }),
  });

/** 重新派单：强制工单回到待派单状态并触发 AI 智能派单重新推荐处理人。
 *  preferredAssignee 为用户倾向的派单人（users.id，必填）；remark 为可选备注。 */
export const reDispatchTicket = (
  ticketId: number | string,
  preferredAssignee: string,
  remark?: string,
) =>
  request(`/${Number(ticketId)}/re-dispatch`, {
    method: 'POST',
    body: JSON.stringify({ preferred_assignee: preferredAssignee, remark: remark || null }),
  });

/** 评论列表（按工单绑定） */
export const listComments = (ticketId: number | string) =>
  request<TicketComment[]>(`/${Number(ticketId)}/comments`);

/** 发评论；attachments 为附件上传时用的 temp_id 数组，后端 add_comment 会换成真实路径入库 */
export const addComment = (
  ticketId: number | string,
  content: string,
  attachments: string[] = [],
  isPublic = true,
) =>
  request<TicketComment>(`/${Number(ticketId)}/comments`, {
    method: 'POST',
    body: JSON.stringify({ content, is_public: isPublic, attachments }),
  });

/** 上传评论附件（FormData；temp_id 用于随后发评论时关联）。鉴权带 Bearer token */
export const uploadCommentAttachment = async (file: File, tempId: string): Promise<void> => {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('temp_id', tempId);
  // 优先取 client.ts 内存 token，其次 localStorage；确保与 createRequest 使用同一个 token
  const token = getToken() || localStorage.getItem('auth_token') || '';
  const res = await fetch(`${API_CONFIG.TASKS.BASE_URL}/comments/attachments`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });
  if (!res.ok) throw new Error(`附件上传失败: ${res.status}`);
};

/** 创建系统任务（工单）。返回创建后的工单（含 id）。
 *  用途：兜底双工单场景下，工单2（申请单）走系统任务创建接口，直接指定 assigned_to=项目负责人。
 *  依赖后端改动：create_ticket 尊重 ticket_data.assigned_to（不再硬编码 created_by）。 */
export async function createTicket(params: CreateTicketParams): Promise<CreatedTicket> {
  return request<CreatedTicket>('/', {
    method: 'POST',
    body: JSON.stringify({
      title: params.title,
      description: params.description,
      ticket_type: params.ticket_type ?? 'support',
      priority: params.priority ?? 'medium',
      project_name: params.project_name ?? '',
      project_id: params.project_id ?? '',
      assigned_to: params.assigned_to ?? null,
      deadline_at: params.deadline_at ?? null,
      customer: params.customer ?? null,
      metadata_info: params.metadata_info ?? null,
      tags: params.tags ?? null,
    }),
  });
}

/** 工单操作日志类型（与后端 OperationType 枚举对齐） */
export type OperationType =
  | 'create'
  | 'status_change'
  | 'assign'
  | 'escalate'
  | 'return'
  | 'reassign'
  | 'update'
  | 'comment'
  | 'view'
  | 'ai_diagnose'
  | 'ai_assign';

export interface OperationLog {
  id: number;
  task_id: number;
  operation_type: OperationType;
  operator: string;
  operator_name?: string | null;
  to_status?: string | null;
  detail?: Record<string, unknown> | null;
  description?: string | null;
  created_at: string;
  ended_at?: string | null;
  duration_seconds?: number | null;
}

/** 获取工单操作日志列表（按时间倒序） */
export const getOperationLogs = (taskId: number | string) =>
  request<OperationLog[]>(`/${Number(taskId)}/operation-logs`, { skipCache: true });

/** 将秒数格式化为人类可读的停留时长，如 "5 分 30 秒" / "45 秒" / "1 小时 5 分" */
export const formatDuration = (seconds: number | null | undefined): string => {
  if (!seconds || seconds <= 0) return '';
  const s = Math.floor(seconds);
  if (s < 60) return `${s} 秒`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = s % 60;
  if (h > 0) return rest > 0 ? `${h} 小时 ${m} 分` : `${h} 小时 ${m} 分`;
  return rest > 0 ? `${m} 分 ${rest} 秒` : `${m} 分钟`;
};
