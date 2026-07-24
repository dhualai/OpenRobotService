// 日报/周报分析 API 封装 —— /api/ai/analysis/report/*
//
// 数据来源：ai/agents/AiDataAnalysisPlatform/report_generator.py —— 从 MySQL 实时采集
// 项目/风险/工单/任务数据后调用 LLM 生成结构化报告。sections[].metrics 为真实统计数值，
// sections[].content 与 summary 为 LLM 生成的叙述文本（如实展示大模型原始输出，前端不二次编造）。
import { aiPost } from '@/api/ai';

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

/** POST /api/ai/analysis/report/generate （stream 固定为 false，走结构化响应） */
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
