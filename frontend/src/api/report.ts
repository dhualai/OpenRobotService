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
  /** 用户ID，用于查询该用户关联的全部项目；与 project_code 同时传时以 project_code 为准 */
  user_id?: string;
}

/** POST /api/ai/analysis/report/generate （非流式，返回结构化 ReportResult） */
export async function generateReport(params: GenerateReportParams): Promise<ReportResult> {
  const res = await aiPost<{ code: number; data: ReportResult }>('/analysis/report/generate', {
    period: params.period,
    date: params.date,
    project_code: params.project_code,
    user_id: params.user_id,
    stream: false,
  });
  if (res.code !== 0 || !res.data) throw new Error('报告生成失败');
  return res.data;
}

/**
 * POST /api/ai/analysis/report/generate （流式 SSE）
 * 返回原始 Response，调用方通过 readReportStream() 逐 chunk 读取。
 * signal：可选 AbortSignal，用于在切换日报/周报、日期、项目或刷新时中断未完成的旧流，
 * 避免两条流交替写同一份状态导致页面闪烁。
 * 后端 SSE 格式：
 *   data: {"content":"<文本>"}\n\n
 *   ...
 *   data: [DONE]\n\n
 */
export async function generateReportStream(params: GenerateReportParams, signal?: AbortSignal): Promise<Response> {
  const res = await fetchWithAuth(`${API_CONFIG.AI.BASE_URL}/analysis/report/generate`, {
    method: 'POST',
    signal,
    body: JSON.stringify({
      period: params.period,
      date: params.date,
      project_code: params.project_code,
      user_id: params.user_id,
      stream: true,
    }),
  });
  if (!res.ok) throw new Error(`AI 接口异常: ${res.status}`);
  return res;
}

/**
 * 读取 SSE 流，逐 chunk 回调 onChunk，结束时返回完整文本。
 * 遇到 error 字段时抛出异常。
 * 带跨 chunk 行缓冲：网络分包可能把一行 SSE 数据截成两个 chunk，
 * 直接 split 会导致半行 JSON.parse 失败被丢弃（内容丢失），这里把不完整的
 * 尾行留在 buffer 中等下个 chunk 拼接后再解析。
 */
export async function readReportStream(
  response: Response,
  onChunk: (text: string) => void,
): Promise<string> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let fullText = '';
  let buffer = '';
  let finished = false;

  const handleLine = (rawLine: string) => {
    const line = rawLine.trimEnd(); // 兼容 CRLF
    if (!line.startsWith('data: ')) return;
    const payload = line.slice(6).trim();
    if (payload === '[DONE]') {
      finished = true;
      return;
    }
    let parsed: { content?: string; error?: string } | null = null;
    try {
      parsed = JSON.parse(payload);
    } catch {
      return; // 非 JSON 行跳过
    }
    if (parsed?.error) throw new Error(parsed.error);
    if (parsed?.content) {
      fullText += parsed.content;
      onChunk(fullText);
    }
  };

  while (!finished) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // 按行切分；pop 保留可能不完整的尾行，等下个 chunk 拼接
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    for (const line of lines) handleLine(line);
  }
  // 流结束后处理 buffer 残留（服务端最后一行未以 \n 结尾时）
  if (!finished && buffer) handleLine(buffer);
  return fullText;
}
