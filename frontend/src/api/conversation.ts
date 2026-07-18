/**
 * 会话历史 API —— /api/call/conversations + /messages
 *
 * 用途：把 AI 对话（ChatPanel）的每轮 user/assistant 消息持久化到 DB，
 * 刷新/切页后可按"当前用户 + 场景"恢复最近一条会话。
 *
 * 注意：user_id 由后端按 token 覆盖（前端只持有 username），故创建时传空串。
 */
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

const request = createRequest(API_CONFIG.CALL.BASE_URL, '会话服务');

export type ChatScene = 'call' | 'tasks';

/** 前端场景 → 后端 SceneType 枚举（后端无 task_assist，tasks 映射为 consultation） */
const SCENE_TO_DB: Record<ChatScene, string> = { call: 'chat', tasks: 'consultation' };

export interface ConvMessage {
  id: number;
  role: 'user' | 'assistant' | 'system';
  content: string;
  created_at: string;
}

export interface Conversation {
  id: number;
  title: string;
  user_id: string;
  scene_type: string;
  service_ticket_id: string;
  metadata_: string | null;
  created_at: string;
  updated_at: string;
  messages?: ConvMessage[];
}

/** 读取会话 metadata_ 里的 ai_session_id（恢复 AI 上下文用） */
export function readAiSessionId(conv: { metadata_: string | null }): string {
  if (!conv.metadata_) return '';
  try {
    const obj = JSON.parse(conv.metadata_);
    return obj?.ai_session_id || '';
  } catch {
    return '';
  }
}

/** 创建会话 */
export const createConversation = (params: { title: string; scene: ChatScene; aiSessionId?: string }) =>
  request<Conversation>('/conversations', {
    method: 'POST',
    body: JSON.stringify({
      title: params.title,
      user_id: '', // 后端按 token 覆盖
      service_ticket_id: '', // 纯聊天无关联工单
      scene_type: SCENE_TO_DB[params.scene],
      metadata_: params.aiSessionId ? JSON.stringify({ ai_session_id: params.aiSessionId }) : null,
    }),
  });

/** 当前用户在指定场景下的会话列表（最新在前） */
export const listMyConversations = (scene: ChatScene, limit = 5) =>
  request<Conversation[]>(`/conversations?scene_type=${SCENE_TO_DB[scene]}&limit=${limit}`);

/** 会话详情（含 messages） */
export const getConversation = (id: number) => request<Conversation>(`/conversations/${id}`);

/** 追加一条消息（user/assistant） */
export const appendMessage = (conversationId: number, role: 'user' | 'assistant', content: string) =>
  request<ConvMessage>('/messages', {
    method: 'POST',
    body: JSON.stringify({
      conversation_id: conversationId,
      role,
      content,
      message_type: 'text',
    }),
  });
