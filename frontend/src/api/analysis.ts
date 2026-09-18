// 后台数据助手 API 封装 —— POST /api/ai/analysis/chat
// 对应后端 ai/agents/AiDataAnalysisPlatform/router.py quick_chat（快速对话，非流式 JSON）：
//   请求 QuickChatRequest { question, context?, user_id?, context_meta?, conversation_id? }
//   → 响应 ChatResponse { answer, mode, model?, usage?, plan?, suggestions?, conversation_id? }
import { fetchWithAuth } from '@/api/ai';
import API_CONFIG from '@/config/api';

// ── 类型定义（与后端 schemas.py 对齐）──────────────────────

/** 页面上下文元信息；问题未提及具体项目/用户时作为分析范围的兜底 */
export interface ChatContextMeta {
  /** 当前页面场景标识，如 admin（后台管理） */
  scene?: string;
  /** 页面上下文中的项目代码；问题未提及具体项目时作为兜底 */
  project_code?: string;
}

/** 时间范围说明 */
export interface AnalysisTimeRange {
  type: string;          // today / yesterday / recent_days / this_week / last_week / this_month / last_month / custom
  days?: number;
  start?: string | null;
  end?: string | null;
  label: string;         // 人类可读标签，如 "近7天"
  explicit: boolean;     // 用户是否明确提到了时间范围
}

/** 项目范围说明 */
export interface AnalysisScope {
  type: string;          // global / single_project / user_projects
  project_code?: string | null;
  project_name?: string | null;
  user_id?: string | null;
}

/** 解析出的分析计划（口径回显） */
export interface AnalysisPlan {
  metric_keys: string[];
  time_range: AnalysisTimeRange;
  scope: AnalysisScope;
  action: string;        // summary / trend / distribution / compare / top
  confidence: number;
  missing_fields: string[];
  original_question: string;
}

export interface AnalysisChatParams {
  question: string;
  /** 补充上下文（可选） */
  context?: string;
  /** 当前用户ID（users.id）：分析意图时后端按用户关联项目自动查库 */
  user_id?: string;
  /** 前端页面上下文，用于补全分析范围（问题未提及项目时兜底） */
  context_meta?: ChatContextMeta;
  /** 对话会话ID；澄清多轮时原样回传以关联上下文 */
  conversation_id?: string;
}

export interface AnalysisChatResult {
  answer: string;
  mode: 'chat' | 'analysis' | 'clarify';
  model?: string | null;
  usage?: Record<string, unknown> | null;
  analysis?: Record<string, unknown> | null;
  plan?: AnalysisPlan | null;
  suggestions?: string[];
  conversation_id?: string | null;
  charts?: AnalysisChart[] | null;
  cards?: AnalysisCard[] | null;
}

/** 图表规格（后端采集数据生成，LLM 不参与）；option 为完整 ECharts option */
export interface AnalysisChart {
  chart_type: 'pie' | 'bar' | 'line';
  title: string;
  option: Record<string, unknown>;
}

/** 单值指标卡片（后端采集数据生成） */
export interface AnalysisCard {
  label: string;
  value: string;
  unit?: string | null;
  /** metric（百分比类，大号强调色） / count（计数类） */
  kind?: string;
}

/** POST /api/ai/analysis/chat —— 返回完整 ChatResponse（含 mode / plan / suggestions / conversation_id） */
export async function analysisChat(
  params: AnalysisChatParams,
  signal?: AbortSignal,
): Promise<AnalysisChatResult> {
  const res = await fetchWithAuth(`${API_CONFIG.AI.BASE_URL}/analysis/chat`, {
    method: 'POST',
    body: JSON.stringify({
      question: params.question,
      context: params.context,
      user_id: params.user_id,
      context_meta: params.context_meta,
      conversation_id: params.conversation_id,
    }),
    signal,
  });
  if (!res.ok) {
    // 后端 FastAPI HTTPException 的 detail 可能是字符串或数组，尽力提取
    let detail = '';
    try {
      const body = await res.json();
      detail = typeof body?.detail === 'string' ? body.detail : '';
    } catch { /* 非 JSON 错误体（网关等）忽略 */ }
    throw new Error(detail || `服务异常（HTTP ${res.status}）`);
  }
  return (await res.json()) as AnalysisChatResult;
}

/** 流式 meta 事件（回答前的结构化元信息：模式/口径/图表/卡片/候选问题） */
export interface AnalysisChatStreamMeta {
  mode: 'chat' | 'analysis' | 'clarify';
  plan?: AnalysisPlan | null;
  charts?: AnalysisChart[] | null;
  cards?: AnalysisCard[] | null;
  suggestions?: string[] | null;
  conversation_id?: string | null;
}

/** 流式回调集合：meta 先行（图表/卡片/口径），delta 逐块追加回答文本 */
export interface AnalysisChatStreamHandlers {
  onMeta: (meta: AnalysisChatStreamMeta) => void;
  onDelta: (content: string) => void;
  onDone: (payload: { conversation_id?: string | null; mode?: string }) => void;
}

/**
 * POST /api/ai/analysis/chat/stream —— 流式问答（SSE）。
 *
 * 事件协议（与后端 router.quick_chat_stream 对齐）：
 *   meta（mode/plan/charts/cards/suggestions）→ delta*（回答文本块）→ done
 *   error 事件抛异常；HTTP 非 2xx 同样抛异常（401 由 fetchWithAuth 统一处理）。
 */
export async function analysisChatStream(
  params: AnalysisChatParams,
  handlers: AnalysisChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetchWithAuth(`${API_CONFIG.AI.BASE_URL}/analysis/chat/stream`, {
    method: 'POST',
    body: JSON.stringify({
      question: params.question,
      context: params.context,
      user_id: params.user_id,
      context_meta: params.context_meta,
      conversation_id: params.conversation_id,
    }),
    signal,
  });
  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = typeof body?.detail === 'string' ? body.detail : '';
    } catch { /* 非 JSON 错误体忽略 */ }
    throw new Error(detail || `服务异常（HTTP ${res.status}）`);
  }
  if (!res.body) throw new Error('流式响应无 body');

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE 事件以空行分隔；单个网络包可能含多个事件或半个事件，需缓存拼装
      let sep = buffer.indexOf('\n\n');
      while (sep !== -1) {
        const rawEvent = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        sep = buffer.indexOf('\n\n');
        const dataLine = rawEvent.split('\n').find((l) => l.startsWith('data:'));
        if (!dataLine) continue;
        const dataStr = dataLine.slice('data:'.length).trim();
        if (!dataStr || dataStr === '[DONE]') continue;
        let obj: Record<string, unknown>;
        try { obj = JSON.parse(dataStr) as Record<string, unknown>; } catch { continue; }
        if (obj.type === 'meta') {
          handlers.onMeta(obj as unknown as AnalysisChatStreamMeta);
        } else if (obj.type === 'delta') {
          handlers.onDelta(String(obj.content ?? ''));
        } else if (obj.type === 'done') {
          handlers.onDone(obj as { conversation_id?: string | null; mode?: string });
        } else if (obj.type === 'error') {
          throw new Error(String(obj.error || '服务异常'));
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
