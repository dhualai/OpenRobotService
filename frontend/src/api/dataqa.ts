/**
 * AI 数据助手会话 API —— /api/dataqa/conversations + /messages
 *
 * 用途：把数据助手问答的每轮 user/assistant 消息持久化到独立表
 * dataqa_conversations / dataqa_messages（与摇人对话的 conversations/messages 完全隔离），
 * 刷新/重进后可恢复历史会话记录，支持新建与删除。
 *
 * 注意：user_id 由后端按 token 覆盖（前端只持有 username），故创建时传空串。
 */
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

const request = createRequest(API_CONFIG.DATAQA.BASE_URL, '数据助手会话服务');

export interface DataqaMessage {
  id: number;
  role: 'user' | 'assistant' | 'system';
  content: string;
  created_at: string;
  message_type?: string;
  file_urls?: string | null;
  /** 消息元数据 JSON（DB messages.metadata_ 直出）；assistant 消息可能含 { mode: 'chat' | 'analysis' } */
  metadata_?: string | null;
}

export interface DataqaConversation {
  id: number;
  title: string;
  user_id: string;
  metadata_: string | null;
  created_at: string;
  updated_at: string;
  messages?: DataqaMessage[];
}

/** 创建会话（首问自动建，标题=首问截断） */
export const createConversation = (params: { title: string }) =>
  request<DataqaConversation>('/conversations', {
    method: 'POST',
    body: JSON.stringify({ title: params.title, user_id: '', metadata_: null }),
  });

/** 当前用户的数据助手会话列表（最新在前）；skipCache 确保删除/新建后列表是最新的 */
export const listMyConversations = (limit = 50) =>
  request<DataqaConversation[]>(`/conversations?limit=${limit}`, { skipCache: true });

/** 删除会话（连同消息） */
export const deleteConversation = (id: number) =>
  request(`/conversations/${id}`, { method: 'DELETE' });

/** 会话详情（含 messages） */
export const getConversation = (id: number) => request<DataqaConversation>(`/conversations/${id}`);

/** 追加一条消息（user/assistant），可附带 metadata（JSON 字符串）。
 *  metadata 会直接写入 DB messages.metadata_，恢复历史时可用 mode 区分普通聊天/指标分析。 */
export const appendMessage = (
  conversationId: number,
  role: 'user' | 'assistant',
  content: string,
  metadata?: string,
) =>
  request<DataqaMessage>('/messages', {
    method: 'POST',
    body: JSON.stringify({
      conversation_id: conversationId,
      role,
      content,
      metadata_: metadata ?? null,
    }),
  });
