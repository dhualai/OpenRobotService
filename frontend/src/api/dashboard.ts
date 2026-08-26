// 后台管理仪表盘 —— API 请求封装
//
// 契约状态：本文件定义的接口均为「前端先行设计，后端待接入」。
// 调用方式统一走 admin 基础 URL（/api/admin），具体 path 见各函数注释。
// 在后端补齐对应路由之前，请求会走 4xx/5xx 或网络错误分支，页面侧一律
// 优雅降级为「数据为空」展示，不阻塞页面渲染、不弹错误提示刷屏。

import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import type { TicketStatusKey, UrgencyKey } from '@/shared/constants/dashboard';

const adminRequest = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');
const tasksRequest = createRequest(API_CONFIG.TASKS.BASE_URL, 'Tasks');

/**
 * 将 projectIds 拼接为 project_ids 查询参数。
 * - undefined：不附加参数（向后兼容，不过滤）
 * - 空数组：附加 `?project_ids=`（后端识别为「无关联项目」，返回空统计）
 * - 非空数组：附加 `?project_ids=id1,id2`
 */
function buildProjectIdsQuery(projectIds?: string[]): string {
  if (projectIds === undefined) return '';
  return `?project_ids=${encodeURIComponent(projectIds.join(','))}`;
}

/** 将 project_ids 查询参数与既有查询参数（如 ?status=xx）合并 */
function appendProjectIdsQuery(existingQuery: string, projectIds?: string[]): string {
  if (projectIds === undefined) return existingQuery;
  const pidParam = `project_ids=${encodeURIComponent(projectIds.join(','))}`;
  return existingQuery.includes('?')
    ? `${existingQuery}&${pidParam}`
    : `?${pidParam}`;
}

// ============================================================
// 一、工单状态汇总
// ============================================================
export interface TicketSummary {
  total: number;
  pending_count: number;       // 待处理（new + in_progress + paused 之和，后端可直接给出，也可前端累加）
  overdue_count: number;       // 超时工单数（依据 deadline_at < now 计算，见 backend/app/models/task.py deadline_at）
  resolved_rate: number;       // 解决率 0-1，例如 0.82 表示 82%
  by_status: Partial<Record<TicketStatusKey, number>>;
}

const EMPTY_TICKET_SUMMARY: TicketSummary = {
  total: 0, pending_count: 0, overdue_count: 0, resolved_rate: 0,
  by_status: {},
};

/**
 * GET /api/admin/dashboard/tickets/summary?project_ids=id1,id2
 * 响应：{ code: 0, data: TicketSummary }
 * 数据来源：系统任务模块 tasks 表按 TaskStatus 分组统计 + deadline_at 超时计算，
 * 见 backend/app/modules/admin/services/task_dashboard_service.py。
 * 与「工单状态监测」页面（/admin/ticket-monitor，对接 AI 服务 tickets 表）是不同数据源，
 * 本仪表盘统计的是系统任务模块的 Task，不是 AI 模块的 Ticket，需注意区分。
 * projectIds 传入后仅统计这些项目内的工单（按 Task.project_id 过滤）。
 */
export async function fetchTicketSummary(projectIds?: string[]): Promise<TicketSummary> {
  try {
    const query = buildProjectIdsQuery(projectIds);
    const res = await adminRequest<{ code: number; data: TicketSummary }>(`/dashboard/tickets/summary${query}`);
    if (res.code === 0 && res.data) return res.data;
    return EMPTY_TICKET_SUMMARY;
  } catch {
    return EMPTY_TICKET_SUMMARY;
  }
}

export interface TicketListItem {
  id: string; title: string; status: string; priority: string;
  assignee_name?: string; created_at: string;
}

/**
 * GET /api/admin/dashboard/tickets?status={key}&project_ids=id1,id2
 * 点击某个状态标签后展示该状态下的工单列表，响应：{ code: 0, data: { items: TicketListItem[], total: number } }
 * status 支持单一状态 key（in_progress/paused/resolved/closed/cancelled）及仪表盘统计卡下钻的组合 scope：
 *   all     全部工单（监控中的五种状态，不含 new，与 summary.total 同口径）
 *   pending 待处理（处理中 + 暂停/挂起，与 summary.pending_count 同口径）
 *   overdue 超时工单（deadline_at < now 且未完成，与 summary.overdue_count 同口径）
 * projectIds 传入后仅返回这些项目内的工单。
 */
export async function fetchTicketsByStatus(status: string, projectIds?: string[]): Promise<{ items: TicketListItem[]; total: number }> {
  try {
    const baseQuery = `?status=${encodeURIComponent(status)}`;
    const query = appendProjectIdsQuery(baseQuery, projectIds);
    const res = await adminRequest<{ code: number; data: { items: TicketListItem[]; total: number } }>(
      `/dashboard/tickets${query}`,
    );
    if (res.code === 0 && res.data) return res.data;
    return { items: [], total: 0 };
  } catch {
    return { items: [], total: 0 };
  }
}

// ============================================================
// 二、跨项目看板 —— 按月项目数量统计（新 UI 月柱状图，替换原按阶段统计口径）
// ============================================================
export interface ProjectMonthlyItem {
  key: string;   // YYYY-MM
  year: number;
  month: number;
  value: number;          // 已承接项目数（是否承接=是）
  pending_value?: number; // 待定项目数（是否承接=待定），柱子上方浅色段；老后端未返回时按 0 处理
}

export interface ProjectMonthlySummary {
  monthly: ProjectMonthlyItem[];
  years: number[];
}

const EMPTY_MONTHLY_SUMMARY: ProjectMonthlySummary = { monthly: [], years: [] };

/**
 * GET /api/admin/dashboard/projects/monthly?project_ids=id1,id2
 * 期望响应：{ code: 0, data: { monthly: [{key, year, month, value, pending_value}], years: [...] } }
 * 数据来源：admin 模块 projects 表按业绩核算期 settlement_period 分组统计（手工填写，
 * 常见 YYYYMM 如 202608 = 2026年8月，兼容 YYYY-MM；后端统一输出 YYYY-MM 的 key），
 * 见 backend/app/modules/admin/api/dashboard.py get_project_monthly_summary。
 * 口径与「本月新增」统计卡一致（核算期 = 当前月）；无核算期的项目不参与统计。
 * value = 已承接项目数，pending_value = 待定项目数（柱子浅色段）；待定项目只出现在这张图，
 * 项目总数/项目列表/紧急度看板等其余口径均不含，见 project_service.get_projects 的 include_pending。
 * projectIds 传入后仅统计这些项目。
 */
export async function fetchProjectMonthly(projectIds?: string[]): Promise<ProjectMonthlySummary> {
  try {
    const query = buildProjectIdsQuery(projectIds);
    const res = await adminRequest<{ code: number; data: ProjectMonthlySummary }>(`/dashboard/projects/monthly${query}`);
    if (res.code === 0 && res.data) return res.data;
    return EMPTY_MONTHLY_SUMMARY;
  } catch {
    return EMPTY_MONTHLY_SUMMARY;
  }
}

export interface TaskExecutionStats {
  total_tasks: number;
  finished_tasks: number;
  completion_rate: number | null;
}

export interface ProjectListItem {
  id: string; project_code: string; name: string; status: string; contact_person: string;
  project_manager?: string | null;
  project_contact?: string | null;
  risks?: number;
  task_execution_stats?: TaskExecutionStats | null;
  latest_manual_switch_count?: number | null;
  settlement_period?: string | null;
}

/**
 * GET /api/admin/dashboard/projects?stage={key}&project_ids=id1,id2
 * 点击某个调度阶段标签后展示该阶段下的项目列表，projectIds 传入后仅在这些项目范围内筛选。
 */
export async function fetchProjectsByStage(stage: string, projectIds?: string[]): Promise<{ items: ProjectListItem[]; total: number }> {
  try {
    const baseQuery = `?stage=${encodeURIComponent(stage)}`;
    const query = appendProjectIdsQuery(baseQuery, projectIds);
    const res = await adminRequest<{ code: number; data: { items: ProjectListItem[]; total: number } }>(
      `/dashboard/projects${query}`,
    );
    if (res.code === 0 && res.data) return res.data;
    return { items: [], total: 0 };
  } catch {
    return { items: [], total: 0 };
  }
}

// ============================================================
// 三、跨项目看板 —— 紧急度四象限汇总
// ============================================================
export interface UrgencySummary {
  by_urgency: Partial<Record<UrgencyKey, number>>;
}

const EMPTY_URGENCY_SUMMARY: UrgencySummary = { by_urgency: {} };

/**
 * GET /api/admin/dashboard/projects/urgency?project_ids=id1,id2
 * 期望响应：{ code: 0, data: UrgencySummary }
 * 数据来源：现有 projects 表 category_basis 字段（backend/app/models/delivery.py），
 * 后端字段已存在，仅需新增一个按该字段 group by 的聚合接口，属于本周可落地的低成本项。
 * projectIds 传入后仅统计这些项目的紧急度分布。
 */
export async function fetchUrgencySummary(projectIds?: string[]): Promise<UrgencySummary> {
  try {
    const query = buildProjectIdsQuery(projectIds);
    const res = await adminRequest<{ code: number; data: UrgencySummary }>(`/dashboard/projects/urgency${query}`);
    if (res.code === 0 && res.data) return res.data;
    return EMPTY_URGENCY_SUMMARY;
  } catch {
    return EMPTY_URGENCY_SUMMARY;
  }
}

/**
 * GET /api/admin/dashboard/projects?urgency={key}&project_ids=id1,id2
 * 点击某个紧急度分类后展示该分类下的项目列表，projectIds 传入后仅在这些项目范围内筛选。
 */
export async function fetchProjectsByUrgency(urgency: string, projectIds?: string[]): Promise<{ items: ProjectListItem[]; total: number }> {
  try {
    const baseQuery = `?urgency=${encodeURIComponent(urgency)}`;
    const query = appendProjectIdsQuery(baseQuery, projectIds);
    const res = await adminRequest<{ code: number; data: { items: ProjectListItem[]; total: number } }>(
      `/dashboard/projects${query}`,
    );
    if (res.code === 0 && res.data) return res.data;
    return { items: [], total: 0 };
  } catch {
    return { items: [], total: 0 };
  }
}

export interface SyncResult {
  fetched: number;
  filtered: number;
  created: number;
  updated: number;
  skipped: number;
  errors: string[];
}

/**
 * POST /api/tasks/sources/wecom/projects/sync
 * 同步企业微信项目数据到数据库
 */
export async function syncWecomProjects(): Promise<SyncResult | null> {
  try {
    const res = await tasksRequest<{ code: number; data: SyncResult }>(
      '/sources/wecom/projects/sync',
      { method: 'POST' },
    );
    if (res.code === 200 && res.data) return res.data;
    return null;
  } catch {
    return null;
  }
}
