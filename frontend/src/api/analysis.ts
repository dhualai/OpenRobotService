// 后台数据助手 API 封装 —— POST /api/ai/analysis/chat
// 对应后端 ai/agents/AiDataAnalysisPlatform/router.py quick_chat（快速对话，非流式 JSON）：
//   请求 QuickChatRequest { question, context? } → 响应 ChatResponse { answer, model?, usage? }
import { fetchWithAuth } from '@/api/ai';
import API_CONFIG from '@/config/api';

export interface AnalysisChatParams {
  question: string;
  /** 补充上下文（可选，当前未传） */
  context?: string;
}

export interface AnalysisChatResult {
  answer: string;
  model?: string | null;
  usage?: Record<string, unknown> | null;
}

/** POST /api/ai/analysis/chat —— 返回完整回答文本（非流式） */
export async function analysisChat(
  params: AnalysisChatParams,
  signal?: AbortSignal,
): Promise<string> {
  const res = await fetchWithAuth(`${API_CONFIG.AI.BASE_URL}/analysis/chat`, {
    method: 'POST',
    body: JSON.stringify({
      question: params.question,
      context: params.context,
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
  return data.answer;
}
