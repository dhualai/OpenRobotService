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
