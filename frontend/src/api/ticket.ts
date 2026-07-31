/**
 * 工单催办 / 评论 / 附件 API —— /api/tasks/*
 *
 * - 催办：POST /cuiban-notification（现成，TicketDetailPage handleUrge 已用）
 * - 评论：GET/POST /{ticket_id}/comments（现成，评论绑定 ticket_id）
 * - 附件：POST /comments/attachments（temp_id 关联，MinIO 存储，发评论时一并入库）
 */
import { createRequest, getToken } from '@/api/client';
import API_CONFIG from '@/config/api';

const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单');

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
