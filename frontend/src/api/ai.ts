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
  const doFetch = (tok: string | null) => fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(tok ? { Authorization: `Bearer ${tok}` } : {}),
      ...(init.headers as Record<string, string>),
    },
  });
  let res = await doFetch(useAuthStore.getState().token);
  // 401：token 过期 → 刷新后重试一次（对齐业务后端 createRequest 的刷新能力）。
  // AI 接口（/qa/submit、/qa/ticket/confirm）token 失效时返回 401（而非 200+空 created_by），
  // 此处刷新重试，避免转工单 created_by 为空。
  if (res.status === 401) {
    const ok = await useAuthStore.getState().refreshAuthToken();
    if (ok) {
      res = await doFetch(useAuthStore.getState().token);
    }
    if (res.status === 401) {
      kickToLogin('登录已过期，请重新登录');
      throw new Error('UNAUTHORIZED');
    }
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

/** 提交工单（显式传 username，token 失效时后端兜底绑定真实用户） */
export const qaSubmit = (sessionId: string) =>
  aiPost<{ code: number; [key: string]: unknown }>('/qa/submit', { session_id: sessionId, username: useAuthStore.getState().username });

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
  /** 最晚解决时间（ISO 字符串，转工单弹窗 antd DatePicker 选择 → overrides → confirm_submit 入库 → 落 Task.deadline_at） */
  deadline_at?: string;
  [k: string]: unknown;
}

export interface PrepareTicketResult {
  /** 业务码：0=草稿就绪/需补字段；1=无需重复提交等提示（已建单会话再次转工单等，仅返回 message、无草稿） */
  code?: number;
  stage: 'draft_ready' | 'need_fields' | 'not_ready';
  draft: TicketDraft;
  missing_fields: string[];
  /** 保底必填字段缺失项（stage=not_ready 时返回，面向用户的中文名） */
  missing_info?: string[];
  /** stage=not_ready 时返回的面向用户的引导话术 */
  message?: string;
  prompt: string;
  ticket_ready?: boolean;
}

/** 生成工单草稿（按钮转工单：第一次点击）
 *  保底必填字段不足时返回 code=1 + stage='not_ready' + missing_info（不生成草稿，
 *  用户需回对话补充后再点） */
export const qaPrepareTicket = (sessionId: string) =>
  aiPost<{
    code: number;
    data?: PrepareTicketResult;
    message?: string;
    stage?: string;
    missing_info?: string[];
  }>(
    '/qa/ticket/prepare', { session_id: sessionId },
  );

/** 确认提交工单（弹窗确认后：overrides 为用户编辑后的字段；显式传 username，token 失效时后端兜底绑定真实用户） */
export const qaConfirmTicket = (sessionId: string, overrides: Partial<TicketDraft>) =>
  aiPost<{
    code: number;
    data?: { ticket: TicketDraft; db_id: number; notice: string };
    message?: string;
    missing_fields?: string[];
  }>('/qa/ticket/confirm', { session_id: sessionId, overrides, username: useAuthStore.getState().username });

/** 获取待确认草稿（前端轮询兜底，如 SSE 中断后恢复） */
export const qaGetDraft = (sessionId: string) =>
  aiGet<{ code: number; data?: { draft: TicketDraft | null } }>('/qa/ticket/draft', { session_id: sessionId });

/** 取消确认：清除待确认草稿（用户关闭确认弹窗/放弃提单时调用）。
 * 若不清除，后端 review 幂等分支（pipeline.py existing_draft 已存在）不再发 review 事件，
 * 前端确认弹窗无法再次弹出，提单卡死。清掉后下次对话字段齐全会重新弹窗。 */
export const qaClearDraft = (sessionId: string): Promise<{ code: number; message?: string }> =>
  fetchWithAuth(`${BASE}/qa/ticket/draft?session_id=${encodeURIComponent(sessionId)}`, { method: 'DELETE' }).then(
    (r) => r.json(),
  );

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
  // 提单人 / 接单人（username + 展示名），后端 list_all_tickets 返回
  created_by?: string;
  created_by_name?: string;
  assigned_to?: string;
  assigned_to_name?: string;
  // 项目名称（tasks.project_name，list_all_tickets 返回）
  project?: string;
  // 工单来源（ai 智能派单 / manual 系统任务），用于控制「重新派单」按钮显隐
  source?: string;
}

/** 历史工单列表筛选参数 */
export interface AiTicketListFilters {
  status?: string;          // new|in_progress|pending|resolved|canceled|closed
  type?: string;            // problem|bug|feature|support|other
  keyword?: string;         // 模糊搜索标题/描述
  username?: string;        // 按创建者用户名过滤
  exclude_status?: string;  // 排除的状态，逗号分隔（如 closed）
}

/** 历史工单列表（GET /api/ai/memory/tickets/all） */
export const qaListTickets = (skip = 0, limit = 50, filters?: AiTicketListFilters) => {
  const params: Record<string, string> = { skip: String(skip), limit: String(limit) };
  if (filters?.status) params.status = filters.status;
  if (filters?.type) params.type = filters.type;
  if (filters?.keyword) params.keyword = filters.keyword;
  if (filters?.username) params.username = filters.username;
  if (filters?.exclude_status) params.exclude_status = filters.exclude_status;
  return aiGet<{
    code: number;
    data?: {
      items: AiTicketBrief[];
      total: number;
      skip?: number;
      limit?: number;
      by_status?: Record<string, number>; // 各状态数量（口径：source+username，复用列表接口返回）
      active_total?: number;               // 除已关闭外总数
    };
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


// ---------------------------------------------------------------------------
// 流式上传（SSE）—— /qa/upload（带 Accept: text/event-stream 触发流式）
// ---------------------------------------------------------------------------

export interface UploadStreamCallbacks {
  /** 文件已保存到后端（saved 列表 + filenames） */
  onFileSaved?: (data: { saved: Array<{ filename: string; size: number; path: string; object_path?: string }>; filenames: string }) => void;
  /** 流式 token：VLM 图片描述 +（附带文字时的）诊断文字都会触发，按顺序拼接 */
  onToken?: (token: string) => void;
  /** VLM 图片分析完成（desc 为完整描述） */
  onVisionDone?: (desc: string) => void;
  /** 最终结果（不含附带文字时 = 确认回执；含文字时 = 诊断结果 {action, thinking, ticket}） */
  onResult?: (data: Record<string, unknown>) => void;
  /** 流结束 */
  onDone?: (data: { total_ms?: number }) => void;
  /** 工具调用轮过渡语撤销：后端确认本轮调工具，通知前端清空已流式上屏的口头预告 */
  onTransitionRollback?: () => void;
  /** 错误（HTTP / SSE event:error） */
  onError?: (msg: string) => void;
}

/**
 * 流式上传附件（FormData + SSE，带 Accept: text/event-stream 触发 /qa/upload 流式分支）。
 * 文件保存、VLM 图片分析、附带文字的完整诊断均通过 SSE 逐步推送，前端可实时渲染。
 */
export const qaUploadStream = async (
  sessionId: string,
  files: File[],
  message: string,
  cb: UploadStreamCallbacks,
): Promise<void> => {
  // 安全包装：回调在 SSE 读流循环内被调用，任何回调抛错都会中断整个流，
  // 导致"后端成功却前台显示失败"。这里统一吞掉回调异常，让流正常读完。
  const safe = <A extends unknown[]>(fn?: (...args: A) => void | Promise<void>) =>
    (...args: A) => {
      try {
        const r = fn?.(...args);
        if (r && typeof (r as Promise<void>).catch === 'function') {
          (r as Promise<void>).catch((e) => console.warn('[upload-stream] 回调异常已忽略:', e));
        }
      } catch (e) {
        console.warn('[upload-stream] 回调异常已忽略:', e);
      }
    };

  const doStream = async (tok: string | null): Promise<boolean> => {
    const formData = new FormData();
    formData.append('session_id', sessionId);
    if (message.trim()) formData.append('message', message.trim());
    files.forEach((f) => formData.append('files', f));

    const controller = new AbortController();
    const resp = await fetch(`${BASE}/qa/upload`, {
      method: 'POST',
      body: formData,
      signal: controller.signal,
      headers: {
        Accept: 'text/event-stream',
        ...(tok ? { Authorization: `Bearer ${tok}` } : {}),
      },
    });
    if (resp.status === 401) return false;

    if (!resp.ok) {
      safe(cb.onError)(`上传失败: HTTP ${resp.status}`);
      return true;
    }
    if (!resp.body) {
      safe(cb.onError)('流式响应为空');
      return true;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let currentEvent = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // 按行切分；pop 保留可能不完整的一行
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const rawLine of lines) {
          const line = rawLine.trimEnd();
          if (!line) continue;
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7);
            continue;
          }
          if (!line.startsWith('data: ')) continue;
          let data: Record<string, unknown>;
          try {
            data = JSON.parse(line.slice(6));
          } catch {
            continue;
          }
          if (currentEvent === 'file_saved') {
            safe(cb.onFileSaved)(data as unknown as Parameters<NonNullable<UploadStreamCallbacks['onFileSaved']>>[0]);
          } else if (data.token) {
            safe(cb.onToken)(String(data.token));
          } else if (currentEvent === 'vision_done' && typeof data.desc === 'string') {
            safe(cb.onVisionDone)(data.desc);
          } else if (currentEvent === 'result') {
            safe(cb.onResult)(data);
          } else if (currentEvent === 'done') {
            safe(cb.onDone)(data as { total_ms?: number });
          } else if (currentEvent === 'transition_rollback') {
            safe(cb.onTransitionRollback)();
          } else if (currentEvent === 'error' && data.error) {
            safe(cb.onError)(String(data.error));
          }
        }
      }
    } finally {
      controller.abort();
    }
    return true;
  };

  let ok = await doStream(useAuthStore.getState().token);
  if (!ok) {
    const refreshed = await useAuthStore.getState().refreshAuthToken();
    if (refreshed) {
      ok = await doStream(useAuthStore.getState().token);
    }
    if (!ok) {
      kickToLogin('登录已过期，请重新登录');
      safe(cb.onError)('登录已过期，请重新登录');
    }
  }
};
