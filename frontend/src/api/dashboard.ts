// 后台管理仪表盘 —— API 请求封装
//
// 契约状态：本文件定义的接口均为「前端先行设计，后端待接入」。
// 调用方式统一走 admin 基础 URL（/api/admin），具体 path 见各函数注释。
// 在后端补齐对应路由之前，请求会走 4xx/5xx 或网络错误分支，页面侧一律
// 优雅降级为「数据为空」展示，不阻塞页面渲染、不弹错误提示刷屏。

import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import type { TicketStatusKey, ProjectStageKey, UrgencyKey } from '@/shared/constants/dashboard';

const adminRequest = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

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
 * 待接入：GET /api/admin/dashboard/tickets/summary
 * 期望响应：{ code: 0, data: TicketSummary }
 * 数据来源建议：系统任务模块 tasks 表按 TaskStatus 分组统计 + deadline_at 超时计算。
 * 与「工单状态监测」页面（/admin/ticket-monitor，对接 AI 服务 tickets 表）是不同数据源，
 * 本仪表盘统计的是系统任务模块的 Task，不是 AI 模块的 Ticket，需注意区分。
 */
export async function fetchTicketSummary(): Promise<TicketSummary> {
  try {
    const res = await adminRequest<{ code: number; data: TicketSummary }>('/dashboard/tickets/summary');
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
 * 待接入：GET /api/admin/dashboard/tickets?status={key}
 * 点击某个状态标签后展示该状态下的工单列表，期望响应：{ code: 0, data: { items: TicketListItem[], total: number } }
 */
export async function fetchTicketsByStatus(status: string): Promise<{ items: TicketListItem[]; total: number }> {
  try {
    const res = await adminRequest<{ code: number; data: { items: TicketListItem[]; total: number } }>(
      `/dashboard/tickets?status=${encodeURIComponent(status)}`,
    );
    if (res.code === 0 && res.data) return res.data;
    return { items: [], total: 0 };
  } catch {
    return { items: [], total: 0 };
  }
}

// ============================================================
// 二、跨项目看板 —— 调度阶段汇总
// ============================================================
export interface ProjectStageSummary {
  total: number;
  by_stage: Partial<Record<ProjectStageKey, number>>;
}

const EMPTY_STAGE_SUMMARY: ProjectStageSummary = { total: 0, by_stage: {} };

/**
 * 待接入：GET /api/admin/dashboard/projects/summary
 * 期望响应：{ code: 0, data: ProjectStageSummary }
 * 数据来源（后期）：企业微信智能表格同步的调度项目数据，见 PRD 描述「后期接入企业微信的智能表格内容」。
 * 过渡期可由后端从现有 projects 表的 status 字段聚合返回（需先完成枚举扩充，见
 * shared/constants/dashboard.ts 顶部注释里列出的 4 个缺失阶段）。
 */
export async function fetchProjectStageSummary(): Promise<ProjectStageSummary> {
  try {
    const res = await adminRequest<{ code: number; data: ProjectStageSummary }>('/dashboard/projects/summary');
    if (res.code === 0 && res.data) return res.data;
    return EMPTY_STAGE_SUMMARY;
  } catch {
    return EMPTY_STAGE_SUMMARY;
  }
}

export interface ProjectListItem {
  id: string; project_code: string; name: string; status: string; contact_person: string;
}

/**
 * 待接入：GET /api/admin/dashboard/projects?stage={key}
 * 点击某个调度阶段标签后展示该阶段下的项目列表
 */
export async function fetchProjectsByStage(stage: string): Promise<{ items: ProjectListItem[]; total: number }> {
  try {
    const res = await adminRequest<{ code: number; data: { items: ProjectListItem[]; total: number } }>(
      `/dashboard/projects?stage=${encodeURIComponent(stage)}`,
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
 * 待接入：GET /api/admin/dashboard/projects/urgency
 * 期望响应：{ code: 0, data: UrgencySummary }
 * 数据来源：现有 projects 表 category_basis 字段（backend/app/models/delivery.py），
 * 后端字段已存在，仅需新增一个按该字段 group by 的聚合接口，属于本周可落地的低成本项。
 */
export async function fetchUrgencySummary(): Promise<UrgencySummary> {
  try {
    const res = await adminRequest<{ code: number; data: UrgencySummary }>('/dashboard/projects/urgency');
    if (res.code === 0 && res.data) return res.data;
    return EMPTY_URGENCY_SUMMARY;
  } catch {
    return EMPTY_URGENCY_SUMMARY;
  }
}

/**
 * 待接入：GET /api/admin/dashboard/projects?urgency={key}
 * 点击某个紧急度分类后展示该分类下的项目列表
 */
export async function fetchProjectsByUrgency(urgency: string): Promise<{ items: ProjectListItem[]; total: number }> {
  try {
    const res = await adminRequest<{ code: number; data: { items: ProjectListItem[]; total: number } }>(
      `/dashboard/projects?urgency=${encodeURIComponent(urgency)}`,
    );
    if (res.code === 0 && res.data) return res.data;
    return { items: [], total: 0 };
  } catch {
    return { items: [], total: 0 };
  }
}
