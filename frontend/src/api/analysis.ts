// 后台数据助手 API 封装 —— POST /api/ai/analysis/chat
// 对应后端 ai/agents/AiDataAnalysisPlatform/router.py quick_chat（快速对话，非流式 JSON）：
//   请求 QuickChatRequest { question, context? } → 响应 ChatResponse { answer, model?, usage? }
import { fetchWithAuth } from '@/api/ai';
import API_CONFIG from '@/config/api';

export interface ChatContextMeta {
  /** 当前页面场景标识，如 admin（后台管理） */
  scene?: string;
  /** 页面上下文中的项目代码；问题未提及具体项目时作为兜底 */
  project_code?: string;
}

export interface AnalysisChatParams {
  question: string;
  /** 补充上下文（可选，当前未传） */
  context?: string;
  /** 当前用户ID（users.id）：分析意图时后端按用户关联项目自动查库；不传则分析意图会 400 */
  user_id?: string;
  /** 前端页面上下文，用于补全分析范围（问题未提及项目时兜底） */
  context_meta?: ChatContextMeta;
}

export interface AnalysisChatResult {
  answer: string;
  model?: string | null;
  usage?: Record<string, unknown> | null;
  /** 响应模式：chat（纯闲聊）或 analysis（指标分析）。
   *  对应后端 DataAnalysisAgent._classify_question_intent() 的意图识别结果。 */
  mode?: string;
}

/** POST /api/ai/analysis/chat —— 返回完整回答文本 + 意图模式（非流式） */
export async function analysisChat(
  params: AnalysisChatParams,
  signal?: AbortSignal,
): Promise<{ answer: string; mode: string }> {
  const res = await fetchWithAuth(`${API_CONFIG.AI.BASE_URL}/analysis/chat`, {
    method: 'POST',
    body: JSON.stringify({
      question: params.question,
      context: params.context,
      user_id: params.user_id,
      context_meta: params.context_meta,
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
  const data = (await res.json()) as AnalysisChatResult;
  return { answer: data.answer, mode: data.mode || 'chat' };
}
