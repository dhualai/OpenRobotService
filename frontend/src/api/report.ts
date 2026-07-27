// 日报/周报分析 API 封装 —— /api/ai/analysis/report/*
//
// 数据来源：ai/agents/AiDataAnalysisPlatform/report_generator.py —— 从 MySQL 实时采集
// 项目/风险/工单/任务数据后调用 LLM 生成结构化报告。sections[].metrics 为真实统计数值，
// sections[].content 与 summary 为 LLM 生成的叙述文本（如实展示大模型原始输出，前端不二次编造）。
import { aiPost, fetchWithAuth } from '@/api/ai';
import API_CONFIG from '@/config/api';

export type ReportPeriod = 'daily' | 'weekly';

export interface ReportSection {
  title: string;
  content: string;
  metrics: Record<string, unknown>;
}

export interface ReportResult {
  period: ReportPeriod;
  date_range: string;
  sections: ReportSection[];
  summary: string;
  raw_response?: string | null;
  generated_at: string;
  project_code?: string | null;
}

export interface GenerateReportParams {
  period: ReportPeriod;
  /** 指定日期 YYYY-MM-DD，默认今天（日报）或本周一（周报） */
  date?: string;
  project_code?: string;
}

/** POST /api/ai/analysis/report/generate （非流式，返回结构化 ReportResult） */
export async function generateReport(params: GenerateReportParams): Promise<ReportResult> {
  const res = await aiPost<{ code: number; data: ReportResult }>('/analysis/report/generate', {
    period: params.period,
    date: params.date,
    project_code: params.project_code,
    stream: false,
  });
  if (res.code !== 0 || !res.data) throw new Error('报告生成失败');
  return res.data;
}

/**
 * POST /api/ai/analysis/report/generate （流式 SSE）
 * 返回原始 Response，调用方通过 readReportStream() 逐 chunk 读取。
 * 后端 SSE 格式：
 *   data: {"content":"<文本>"}\n\n
 *   ...
 *   data: [DONE]\n\n
 */
export async function generateReportStream(params: GenerateReportParams): Promise<Response> {
  const res = await fetchWithAuth(`${API_CONFIG.AI.BASE_URL}/analysis/report/generate`, {
    method: 'POST',
    body: JSON.stringify({
      period: params.period,
      date: params.date,
      project_code: params.project_code,
      stream: true,
    }),
  });
  if (!res.ok) throw new Error(`AI 接口异常: ${res.status}`);
  return res;
}

/**
 * 读取 SSE 流，逐 chunk 回调 onChunk，结束时调用 onDone。
 * 遇到 error 字段时抛出异常。
 */
export async function readReportStream(
  response: Response,
  onChunk: (text: string) => void,
): Promise<string> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let fullText = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const text = decoder.decode(value, { stream: true });
    for (const line of text.split('\n')) {
      if (!line.startsWith('data: ')) continue;
      const payload = line.slice(6).trim();
      if (payload === '[DONE]') return fullText;
      let parsed: { content?: string; error?: string } | null = null;
      try {
        parsed = JSON.parse(payload);
      } catch {
        continue; // 非 JSON 行跳过
      }
      if (parsed?.error) throw new Error(parsed.error);
      if (parsed?.content) {
        fullText += parsed.content;
        onChunk(fullText);
      }
    }
  }
  return fullText;
}
