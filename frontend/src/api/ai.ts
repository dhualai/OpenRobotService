/**
 * AI 模块 API 封装 —— /api/ai/*
 *
 * 三大路由组：
 *   /api/ai/qa/*    诊断 Agent（流式/非流式问答、工单提交、附件上传）
 *   /api/ai/chat/*  纯 LLM 对话（流式/非流式）
 *   /api/ai/memory/* 会话记忆（历史、待派单、清除）
 */
import API_CONFIG from '@/config/api';
import { useAuthStore } from '@/stores/auth';

const BASE = API_CONFIG.AI.BASE_URL; // '/api/ai'

// ---------------------------------------------------------------------------
// 通用请求 helpers
// ---------------------------------------------------------------------------

/** 生成 session_id（前端用时间戳+随机数，保证唯一性） */
export const generateSessionId = (): string =>
  `sess_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;

/** 将 session_id 持久化到 localStorage，方便会话列表获取 */
export const trackSession = (sessionId: string): void => {
  try {
    const ids: string[] = JSON.parse(localStorage.getItem('ai_sessions') || '[]');
    if (!ids.includes(sessionId)) {
      ids.unshift(sessionId); // 最新排最前
      // 最多保留 50 个
      localStorage.setItem('ai_sessions', JSON.stringify(ids.slice(0, 50)));
    }
  } catch { /* ignore */ }
};

/** 带 token 的 fetch 封装（用于 SSE 流式请求） */
export const fetchWithAuth = async (url: string, init: RequestInit = {}) => {
  const token = useAuthStore.getState().token;
  return fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers as Record<string, string>),
    },
  });
};

/** 通用 JSON POST */
export const aiPost = async <T = any>(path: string, body: Record<string, any>): Promise<T> => {
  const res = await fetchWithAuth(`${BASE}${path}`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`AI 接口异常: ${res.status}`);
  return res.json();
};

/** 通用 JSON GET */
export const aiGet = async <T = any>(path: string, params?: Record<string, string>): Promise<T> => {
  const qs = params ? '?' + new URLSearchParams(params).toString() : '';
  const res = await fetchWithAuth(`${BASE}${path}${qs}`, { method: 'GET' });
  if (!res.ok) throw new Error(`AI 接口异常: ${res.status}`);
  return res.json();
};

/** 通用 JSON DELETE */
export const aiDelete = async <T = any>(path: string, params?: Record<string, string>): Promise<T> => {
  const qs = params ? '?' + new URLSearchParams(params).toString() : '';
  const res = await fetchWithAuth(`${BASE}${path}${qs}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`AI 接口异常: ${res.status}`);
  return res.json();
};

// ---------------------------------------------------------------------------
// QA 诊断接口 (/api/ai/qa)
// ---------------------------------------------------------------------------

export interface QAAskRequest {
  session_id: string;
  query: string;
  skip_retrieval?: boolean;
}

export interface QAAskResponse {
  code: number;
  message?: string;
  [key: string]: any;
}

/** 非流式问答 */
export const qaAsk = (body: QAAskRequest) => aiPost<QAAskResponse>('/qa/ask', body);

/** 流式问答（SSE）—— 返回 fetch Response，调用方自行读取 ReadableStream */
export const qaAskStream = (body: QAAskRequest): Promise<Response> =>
  fetchWithAuth(`${BASE}/qa/ask/stream`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

/** 提交工单 */
export const qaSubmit = (sessionId: string) =>
  aiPost<{ code: number; [key: string]: any }>('/qa/submit', { session_id: sessionId });

/** 获取工单 */
export const qaGetTicket = (sessionId: string) =>
  aiGet<{ code: number; data?: any; message?: string }>('/qa/ticket', { session_id: sessionId });

/** 派单确认回执 */
export const qaTicketAck = (sessionId: string, dispatchId = '', status = 'dispatched') =>
  aiPost<{ code: number; data?: any; message?: string }>('/qa/ticket/ack', {
    session_id: sessionId,
    dispatch_id: dispatchId,
    status,
  });

/** 上传附件（FormData） */
export const qaUpload = async (sessionId: string, files: File[]): Promise<Response> => {
  const formData = new FormData();
  formData.append('session_id', sessionId);
  files.forEach((f) => formData.append('files', f));
  const token = useAuthStore.getState().token;
  return fetch(`${BASE}/qa/upload`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });
};

/** AI 模块健康检查 */
export const qaHealth = () => aiGet<{ code: number; data?: any }>('/qa/health');

// ---------------------------------------------------------------------------
// 纯 LLM 对话接口 (/api/ai/chat)
// ---------------------------------------------------------------------------

export interface ChatRequest {
  session_id?: string;
  query: string;
  max_tokens?: number;
  temperature?: number;
  system_prompt?: string;
}

export interface ChatResponse {
  code: number;
  data?: { answer: string; total_ms: number };
}

/** 非流式 LLM 对话 */
export const chatSend = (body: ChatRequest) => aiPost<ChatResponse>('/chat', body);

/** 流式 LLM 对话（SSE） */
export const chatSendStream = (body: ChatRequest): Promise<Response> =>
  fetchWithAuth(`${BASE}/chat/stream`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

// ---------------------------------------------------------------------------
// 会话记忆接口 (/api/ai/memory)
// ---------------------------------------------------------------------------

export interface HistoryResponse {
  code: number;
  data?: {
    session_id: string;
    turns: Array<{ role: string; content: string; timestamp?: string }>;
    count: number;
  };
}

export interface TicketsResponse {
  code: number;
  data?: { pending: string[]; count: number };
}

/** 获取会话对话历史 */
export const memoryHistory = (sessionId: string) =>
  aiGet<HistoryResponse>('/memory/history', { session_id: sessionId });

/** 获取待派单列表 */
export const memoryTickets = () => aiGet<TicketsResponse>('/memory/tickets');

/** 清除单个会话 */
export const memoryClear = (sessionId: string) =>
  aiDelete<{ code: number; data?: any }>('/memory/clear', { session_id: sessionId });

/** 清除所有会话 */
export const memoryClearAll = () =>
  aiDelete<{ code: number; data?: any }>('/memory/clear-all');
