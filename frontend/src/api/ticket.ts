/**
 * 工单催办 / 评论 / 附件 API —— /api/tasks/*
 *
 * - 催办：POST /cuiban-notification（现成，TicketDetailPage handleUrge 已用）
 * - 评论：GET/POST /{ticket_id}/comments（现成，评论绑定 ticket_id）
 * - 附件：POST /comments/attachments（temp_id 关联，MinIO 存储，发评论时一并入库）
 */
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { useAuthStore } from '@/stores/auth';

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

/** 一键催办：通知处理人 */
export const urgeTicket = (ticketId: number | string) =>
  request('/cuiban-notification', {
    method: 'POST',
    body: JSON.stringify({ ticket_id: Number(ticketId), notify_type: 1 }),
  });

/** 上报：通知上级/管理员（to_admin=true 区别于催办处理人） */
export const reportTicket = (ticketId: number | string) =>
  request('/cuiban-notification', {
    method: 'POST',
    body: JSON.stringify({ ticket_id: Number(ticketId), notify_type: 1, to_admin: true }),
  });

/** 撤回：将工单状态置为已取消（Canceled） */
export const cancelTicket = (ticketId: number | string) =>
  request(`/${Number(ticketId)}/status?status=canceled`, { method: 'PATCH' });

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
  const token = useAuthStore.getState().token;
  const res = await fetch(`${API_CONFIG.TASKS.BASE_URL}/comments/attachments`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });
  if (!res.ok) throw new Error(`附件上传失败: ${res.status}`);
};
