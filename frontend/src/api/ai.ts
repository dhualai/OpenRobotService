/**
 * AI 模块 API 封装 —— /api/ai/qa/*
 *
 * 诊断 Agent：流式问答（SSE）、工单提交/查询/派单回执、历史工单列表、附件上传。
 * 注：/chat/* 纯对话与 /memory/* 会话记忆两组接口此前未接线，已移除。
 */
import API_CONFIG from '@/config/api';
import { useAuthStore } from '@/stores/auth';
import { kickToLogin } from '@/shared/utils/session';

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
  const res = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers as Record<string, string>),
    },
  });
  // 401：鉴权失效 → 统一提示并跳登录页（AI 模块不走 client.ts，需自行处理）
  if (res.status === 401) {
    kickToLogin('登录已过期，请重新登录');
    throw new Error('UNAUTHORIZED');
  }
  return res;
};

/** 通用 JSON POST */
export const aiPost = async <T = unknown>(path: string, body: object): Promise<T> => {
  const res = await fetchWithAuth(`${BASE}${path}`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`AI 接口异常: ${res.status}`);
  return res.json();
};

/** 通用 JSON GET */
export const aiGet = async <T = unknown>(path: string, params?: Record<string, string>): Promise<T> => {
  const qs = params ? '?' + new URLSearchParams(params).toString() : '';
  const res = await fetchWithAuth(`${BASE}${path}${qs}`, { method: 'GET' });
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

/** 流式问答（SSE）—— 返回 fetch Response，调用方自行读取 ReadableStream */
export const qaAskStream = (body: QAAskRequest): Promise<Response> =>
  fetchWithAuth(`${BASE}/qa/ask/stream`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

/** 提交工单 */
export const qaSubmit = (sessionId: string) =>
  aiPost<{ code: number; [key: string]: unknown }>('/qa/submit', { session_id: sessionId });

// ---------------------------------------------------------------------------
// 转工单二次确认（按钮路径1：prepare 生成草稿 → 弹窗核对/补字段 → confirm 入库）
// 对应后端 ai/api/router.py 的 /qa/ticket/prepare、/confirm、/draft
// ---------------------------------------------------------------------------

/** 工单草稿字段（AI 诊断生成，二次确认时供用户核对/编辑） */
export interface TicketDraft {
  ticket_id?: string;
  session_id?: string;
  type?: string;       // problem|bug|feature|support|other
  title?: string;
  description?: string;
  priority?: string;   // 紧急|高|中|低
  status?: string;
  contact?: string;
  project?: string;    // 项目名称（落 Task.project_name）
  project_id?: string; // 项目编码 project_code（落 Task.project_id，明确绑定关系）
  location?: string;
  robot_type?: string;
  fault_code?: string;
  special_notes?: string;
  steps_to_reproduce?: string;
  expected_result?: string;
  actual_result?: string;
  severity?: string;
  version?: string;
  scenario?: string;
  expected_effect?: string;
  source?: string;
  support_type?: string;
  preferred_response?: string;
  missing_fields?: string[];
  [k: string]: unknown;
}

export interface PrepareTicketResult {
  stage: 'draft_ready' | 'need_fields';
  draft: TicketDraft;
  missing_fields: string[];
  prompt: string;
}

/** 生成工单草稿（按钮转工单：第一次点击） */
export const qaPrepareTicket = (sessionId: string) =>
  aiPost<{ code: number; data?: PrepareTicketResult; message?: string }>(
    '/qa/ticket/prepare', { session_id: sessionId },
  );

/** 确认提交工单（弹窗确认后：overrides 为用户编辑后的字段） */
export const qaConfirmTicket = (sessionId: string, overrides: Partial<TicketDraft>) =>
  aiPost<{
    code: number;
    data?: { ticket: TicketDraft; db_id: number; notice: string };
    message?: string;
    missing_fields?: string[];
  }>('/qa/ticket/confirm', { session_id: sessionId, overrides });

/** 获取待确认草稿（前端轮询兜底，如 SSE 中断后恢复） */
export const qaGetDraft = (sessionId: string) =>
  aiGet<{ code: number; data?: { draft: TicketDraft | null } }>('/qa/ticket/draft', { session_id: sessionId });

/** 获取工单 */
export const qaGetTicket = (sessionId: string) =>
  aiGet<{ code: number; data?: unknown; message?: string }>('/qa/ticket', { session_id: sessionId });

/** 历史工单摘要（/memory/tickets/all 列表项，字段与后端 Ticket 表对齐） */
export interface AiTicketBrief {
  id: number;
  session_id: string;
  ticket_ai_id?: string;
  title: string;
  description?: string;
  type?: string;      // problem|bug|feature|support|other
  priority?: string;  // 紧急|高|中|低
  status?: string;    // pending|dispatched|in_progress|resolved|closed
  contact?: string;
  location?: string;
  robot_type?: string;
  fault_code?: string;
  severity?: string;
  attachments?: unknown;
  diagnosis?: unknown;
  created_at?: string; // ISO
  updated_at?: string; // ISO
}

/** 历史工单列表筛选参数 */
export interface AiTicketListFilters {
  status?: string;   // pending|dispatched|in_progress|resolved|closed
  type?: string;     // problem|bug|feature|support|other
  keyword?: string;  // 模糊搜索标题/描述
  username?: string; // 按创建者用户名过滤
}

/** 历史工单列表（GET /api/ai/memory/tickets/all） */
export const qaListTickets = (skip = 0, limit = 50, filters?: AiTicketListFilters) => {
  const params: Record<string, string> = { skip: String(skip), limit: String(limit) };
  if (filters?.status) params.status = filters.status;
  if (filters?.type) params.type = filters.type;
  if (filters?.keyword) params.keyword = filters.keyword;
  if (filters?.username) params.username = filters.username;
  return aiGet<{
    code: number;
    data?: { items: AiTicketBrief[]; total: number; skip?: number; limit?: number };
    message?: string;
  }>('/memory/tickets/all', params);
};

/** 派单确认回执 */
export const qaTicketAck = (sessionId: string, dispatchId = '', status = 'dispatched') =>
  aiPost<{ code: number; data?: unknown; message?: string }>('/qa/ticket/ack', {
    session_id: sessionId,
    dispatch_id: dispatchId,
    status,
  });

/** 上传接口响应的 data 字段（后端 /api/ai/qa/upload 返回） */
export interface UploadData {
  saved: number;
  files: Array<{ filename: string; size: number; path: string; object_path?: string }>;
  /** 后端确认回执：只传图片=VLM 初步诊断；只传非图片=「暂不支持解析」提示 */
  ack_message?: string;
  /** 仅附带 message 文字非空时有值：完整诊断的 AI 回复（含提单 ticket） */
  ai_response?: { message?: string; action?: string; thinking?: string; ticket?: unknown } | null;
}

/** 上传附件（FormData）。
 * message 为可选的附带文字：非空时后端在上传后顺带跑完整诊断并返回 ai_response；
 * 为空时后端只返回确认回执 ack_message。使用 XMLHttpRequest 以支持上传进度回调
 * （fetch 无法获取 upload 进度）。onProgress 接收 0~100 的整数百分比。
 */
export interface UploadResult {
  ok: boolean;
  status: number;
  data: { code?: number; message?: string; data?: UploadData; [k: string]: unknown };
}
export const qaUpload = (
  sessionId: string,
  files: File[],
  message = '',
  onProgress?: (percent: number) => void,
): Promise<UploadResult> => {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append('session_id', sessionId);
    if (message.trim()) formData.append('message', message.trim());
    files.forEach((f) => formData.append('files', f));
    const token = useAuthStore.getState().token;
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${BASE}/qa/upload`);
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.onprogress = (e: ProgressEvent) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      let data: UploadResult['data'] = {};
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* 非 JSON 响应，忽略解析 */
      }
      resolve({ ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status, data });
    };
    xhr.onerror = () => reject(new Error('网络错误，上传失败'));
    xhr.send(formData);
  });
};
