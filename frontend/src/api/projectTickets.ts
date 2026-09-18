// 项目工单卡 API —— 对接 admin 模块 /api/admin/project-tickets/*
// 后端实现：backend/app/modules/admin/api/project_tickets.py（数据源为系统任务 tasks 表，
// Task.project_id = 项目ID/代码；与「工单状态监测」页的 AI tickets 表是两个数据源）。
// 契约要点：
//   - GET  overview：顶部汇总（总工单数/正在处理/已完成）、近 8 周
//     每周新建工单数（周一为起点）、核心阻滞工单（AI 配置或默认规则）；
//   - POST blocking-config：仅管理员/超级管理员，把提示词交给大模型判定核心阻滞工单，
//     结果落库后返回更新后的阻断板块（结构同 overview.blocking）。
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

/** 阻滞/工单一览条目（后端已把人员名、描述摘要拼好） */
export interface ProjectTicketItem {
  id: number;
  title: string;
  /** 原始 TaskStatus 枚举值：new/in_progress/pending/resolved/canceled/closed */
  status: string;
  /** 原始 TaskPriority 枚举值：low/medium/high/urgent */
  priority: string;
  ticket_type: string;
  created_by?: string | null;
  creator_name?: string | null;
  assigned_to?: string | null;
  assignee_name?: string | null;
  created_at?: string | null;
  deadline_at?: string | null;
  /** 服务端判定的超期标记（截止已过且仍未完成；前端不再自行比较时间） */
  overdue?: boolean;
  description?: string | null;
}

export interface ProjectTicketsBlocking {
  /** ai = 管理员配置过阻滞权重；default = 按优先级/超期的默认排序 */
  mode: 'ai' | 'default';
  tickets: ProjectTicketItem[];
  /** 已配置的判定提示词（default 模式下若有旧配置也会带回，供管理员修改） */
  prompt?: string | null;
  summary?: string | null;
  /** AI 给每个工单的阻滞理由，键为工单ID 字符串 */
  reasons?: Record<string, string> | null;
  updated_by_name?: string | null;
  updated_at?: string | null;
}

export interface ProjectTicketsOverview {
  total: number;
  /** 前端状态 key 口径（new/in_progress/paused/resolved/closed/cancelled）→ 数量 */
  by_status: Partial<Record<string, number>>;
  pending_count: number;
  overdue_count: number;
  resolved_rate: number;
  /** 近 8 周每周新建工单数（week_start 为周一，'YYYY-MM-DD'） */
  weekly: Array<{ week_start: string; count: number }>;
  blocking: ProjectTicketsBlocking;
}

// 与其他 api 模块一致：调用时再建 requester，不在模块顶层求值（便于测试 mock @/api/client）
const request = () => createRequest(API_CONFIG.ADMIN.BASE_URL, '项目工单服务');

/** 工单概览（卡片三部分一次取回） */
export async function fetchProjectTicketsOverviewApi(projectId: string): Promise<ProjectTicketsOverview> {
  const res = await request()<{ code: number; data: ProjectTicketsOverview }>(
    `/project-tickets/projects/${encodeURIComponent(projectId)}/overview`,
  );
  if (res.code !== 0 || !res.data) throw new Error('项目工单数据返回异常');
  return res.data;
}

/** 配置阻滞权重（仅管理员）：服务端调大模型判定后返回更新后的阻滞板块 */
export async function configureBlockingWeightsApi(
  projectId: string,
  prompt: string,
): Promise<ProjectTicketsBlocking> {
  const res = await request()<{ code: number; data: ProjectTicketsBlocking }>(
    `/project-tickets/projects/${encodeURIComponent(projectId)}/blocking-config`,
    { method: 'POST', body: JSON.stringify({ prompt }) },
  );
  if (res.code !== 0 || !res.data) throw new Error('阻滞权重配置返回异常');
  return res.data;
}
