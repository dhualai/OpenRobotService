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

export interface DashboardQuestion {
  record_id: string;
  reported_at: string;
  problem_description: string;
  solution_description: string;
  vehicle_number: string;
  vehicle_model: string;
  vehicle_info: string;
}

export interface DashboardQuestions {
  report_date: string;
  total: number;
  page: number;
  page_size: number;
  items: DashboardQuestion[];
  last_sync_at: string | null;
}

export async function fetchDashboardQuestions(
  reportDate: string, page = 1, pageSize = 20,
): Promise<DashboardQuestions> {
  const query = new URLSearchParams({
    report_date: reportDate, page: String(page), page_size: String(pageSize),
  });
  const res = await adminRequest<{ code: number; data?: DashboardQuestions }>(
    `/dashboard/questions?${query}`, { skipCache: true },
  );
  if (res.code !== 0 || !res.data) throw new Error('问题列表加载失败');
  return res.data;
}

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
  pending_count: number;       // 待处理（处理中 + 暂停/挂起之和，不含 new；后端给出，前端不做累加）
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

export interface TicketSourceAnalysis {
  by_type: { key: string; count: number }[];   // 工单类型分布（key 为 task_type 枚举值，显示名见 TICKET_TYPE_DISPLAY_MAP）
}

const EMPTY_SOURCE_ANALYSIS: TicketSourceAnalysis = { by_type: [] };

/**
 * GET /api/admin/dashboard/tickets/source-analysis?project_ids=id1,id2
 * 响应：{ code: 0, data: TicketSourceAnalysis }
 * 数据来源：系统任务模块 tasks 表（task_type 分组 + 提单人主角色分组），
 * 与工单状态监测同一数据源；projectIds 传入后仅统计这些项目内的工单。
 */
export async function fetchTicketSourceAnalysis(projectIds?: string[]): Promise<TicketSourceAnalysis> {
  try {
    const query = buildProjectIdsQuery(projectIds);
    const res = await adminRequest<{ code: number; data: TicketSourceAnalysis }>(
      `/dashboard/tickets/source-analysis${query}`,
    );
    if (res.code === 0 && res.data) return res.data;
    return EMPTY_SOURCE_ANALYSIS;
  } catch {
    return EMPTY_SOURCE_ANALYSIS;
  }
}

/** 公司/部门下拉选项与组织分布条目 */
export interface OrgOptionItem {
  id: string;
  name: string;
}

export interface TicketCreatorOrgAnalysis {
  dimension: 'company' | 'department';  // company=按公司统计 / department=选公司后按部门统计
  companies: OrgOptionItem[];           // 公司下拉框选项（全部公司）
  by_org: { label: string; count: number }[]; // 组织维度工单数（含「未分配公司」「未分配部门」「其他」）
}

const EMPTY_CREATOR_ORG: TicketCreatorOrgAnalysis = { dimension: 'company', companies: [], by_org: [] };

/**
 * GET /api/admin/dashboard/tickets/creator-org-analysis?project_ids=id1,id2&company_id=c1
 * 响应：{ code: 0, data: TicketCreatorOrgAnalysis }
 * 数据来源：tasks.created_by → users.company_id / department_id → companies / departments 主数据表
 * （companies.id ↔ departments.company_id），见 task_dashboard_service.get_creator_org_analysis。
 * 不传 companyId 按公司归组；传入后仅统计该公司提单人的工单并按部门归组。
 */
export async function fetchTicketCreatorOrgAnalysis(
  projectIds?: string[],
  companyId?: string,
): Promise<TicketCreatorOrgAnalysis> {
  try {
    const params = new URLSearchParams();
    if (projectIds !== undefined) params.set('project_ids', projectIds.join(','));
    if (companyId) params.set('company_id', companyId);
    const qs = params.toString();
    const res = await adminRequest<{ code: number; data: TicketCreatorOrgAnalysis }>(
      `/dashboard/tickets/creator-org-analysis${qs ? `?${qs}` : ''}`,
    );
    if (res.code === 0 && res.data) return res.data;
    return EMPTY_CREATOR_ORG;
  } catch {
    return EMPTY_CREATOR_ORG;
  }
}

export interface ResponseTimeBucket {
  key: string;    // within_15m / within_1h / within_4h / other
  label: string;  // 15分钟内 / 1h内 / 4h内 / 其他
  count: number;
}

export interface TicketResponseTime {
  total: number;      // 统计范围内工单总数
  responded: number;  // 处理人点开过、有响应时间的工单数（参与分桶）
  by_bucket: ResponseTimeBucket[];
}

const EMPTY_RESPONSE_TIME: TicketResponseTime = { total: 0, responded: 0, by_bucket: [] };

/**
 * GET /api/admin/dashboard/tickets/response-time?project_ids=id1,id2
 * 响应：{ code: 0, data: TicketResponseTime }
 * 数据来源：工单详情页「工单动态」的 VIEW 操作日志（task_operation_logs 表），
 * 响应时间 = 处理人第一次点开工单时间 - 新建工单时间，见
 * backend/app/modules/admin/services/task_dashboard_service.py get_response_time_analysis。
 * 未指派处理人 / 处理人从未点开过的工单不计入 by_bucket（只计入 total）。
 */
export async function fetchTicketResponseTime(projectIds?: string[]): Promise<TicketResponseTime> {
  try {
    const query = buildProjectIdsQuery(projectIds);
    const res = await adminRequest<{ code: number; data: TicketResponseTime }>(
      `/dashboard/tickets/response-time${query}`,
    );
    if (res.code === 0 && res.data) return res.data;
    return EMPTY_RESPONSE_TIME;
  } catch {
    return EMPTY_RESPONSE_TIME;
  }
}

export interface TicketAvgCloseTime {
  by_type: { key: string; count: number; avg_seconds: number }[]; // 各类型已关闭工单平均完单耗时（秒）
}

const EMPTY_AVG_CLOSE_TIME: TicketAvgCloseTime = { by_type: [] };

/**
 * GET /api/admin/dashboard/tickets/avg-close-time?project_ids=id1,id2
 * 响应：{ code: 0, data: TicketAvgCloseTime }
 * 数据来源：系统任务模块 tasks 表，完单耗时 = closed_at - created_at（仅统计已关闭工单），
 * 按 task_type 分组取平均，见 task_dashboard_service.get_avg_close_time_analysis。
 * 与「工单类型分布」同一数据源，供其右侧「平均完单耗时」图表。
 */
export async function fetchTicketAvgCloseTime(projectIds?: string[]): Promise<TicketAvgCloseTime> {
  try {
    const query = buildProjectIdsQuery(projectIds);
    const res = await adminRequest<{ code: number; data: TicketAvgCloseTime }>(
      `/dashboard/tickets/avg-close-time${query}`,
    );
    if (res.code === 0 && res.data) return res.data;
    return EMPTY_AVG_CLOSE_TIME;
  } catch {
    return EMPTY_AVG_CLOSE_TIME;
  }
}

export interface TicketListItem {
  id: string; title: string; status: string; priority: string;
  assignee_name?: string; created_at: string;
  /** 截止时间（后端 naive UTC ISO）。超时工单列表据此展示「已超时 X」；挂起判定见 status */
  deadline_at?: string | null;
}

/**
 * GET /api/admin/dashboard/tickets?status={key}&project_ids=id1,id2&skip=0&limit=20
 * 点击某个状态标签后展示该状态下的工单列表，响应：{ code: 0, data: { items: TicketListItem[], total: number } }
 * status 支持单一状态 key（new/in_progress/paused/resolved/closed/cancelled）及仪表盘统计卡下钻的组合 scope：
 *   all     全部工单（监控中的状态，含 new，与 summary.total 同口径）
 *   pending 待处理（处理中 + 暂停/挂起，与 summary.pending_count 同口径）
 *   overdue 超时工单（deadline_at < now 且未完成，与 summary.overdue_count 同口径）
 *     —— 排序由后端完成：挂起工单始终置顶，其余按超时最久在前（deadline_at 升序），前端不再重排
 * projectIds 传入后仅返回这些项目内的工单；分页默认20条，total始终为该范围的完整工单数。
 */
export async function fetchTicketsByStatus(
  status: string,
  projectIds?: string[],
  pagination: { skip: number; limit: number } = { skip: 0, limit: 20 },
): Promise<{ items: TicketListItem[]; total: number }> {
  const baseQuery = `?status=${encodeURIComponent(status)}&skip=${pagination.skip}&limit=${pagination.limit}`;
  const query = appendProjectIdsQuery(baseQuery, projectIds);
  const res = await adminRequest<{ code: number; data: { items: TicketListItem[]; total: number } }>(
    `/dashboard/tickets${query}`,
    { skipCache: true },
  );
  if (res.code === 0 && res.data) return res.data;
  throw new Error('工单列表加载失败');
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
  // 项目没有任何采集数据时后端返回 null（页面按「-」展示），0 才是真实统计值
  total_tasks: number | null;
  finished_tasks: number | null;
  completion_rate: number | null;
  data_date?: string | null; // 这组统计对应的数据日期（已导入的最新一天）
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

// ============================================================
// 四、仪表盘聚合接口 —— 一次返回 6 类看板数据，替代首屏并发请求
// ============================================================

/** summary-all 附带的轻量项目列表：仅首屏统计卡所需字段，risks 为未关闭风险数 */
export interface ProjectBriefItem {
  project_code: string;
  name: string;
  contact_person: string;
  settlement_period?: string | null;
  risks: number;
}

export interface DashboardSummaryAll {
  tickets: TicketSummary;
  source: TicketSourceAnalysis;
  response_time: TicketResponseTime;
  avg_close_time: TicketAvgCloseTime;
  monthly: ProjectMonthlySummary;
  urgency: UrgencySummary;
  projects_brief?: ProjectBriefItem[];
}

const EMPTY_SUMMARY_ALL: DashboardSummaryAll = {
  tickets: EMPTY_TICKET_SUMMARY,
  source: EMPTY_SOURCE_ANALYSIS,
  response_time: EMPTY_RESPONSE_TIME,
  avg_close_time: EMPTY_AVG_CLOSE_TIME,
  monthly: EMPTY_MONTHLY_SUMMARY,
  urgency: EMPTY_URGENCY_SUMMARY,
  projects_brief: [],
};

/**
 * GET /api/admin/dashboard/summary-all?project_ids=id1,id2
 * 聚合接口：一次返回工单汇总/来源/响应时间/项目月统计/紧急度/轻量项目列表，
 * 替代前端首屏 6 个并发请求（fetchTicketSummary / fetchTicketSourceAnalysis /
 * fetchTicketResponseTime / fetchProjectMonthly / fetchUrgencySummary /
 * /projects?include_analysis=true）。
 * 项目列表只带统计卡所需轻量字段（projects_brief）；需要完整分析字段的重列表
 * （project_summary/任务指标等）仍走 /dashboard/projects 下钻端点。
 */
export async function fetchDashboardSummaryAll(projectIds?: string[]): Promise<DashboardSummaryAll> {
  try {
    const query = buildProjectIdsQuery(projectIds);
    const res = await adminRequest<{ code: number; data: DashboardSummaryAll }>(`/dashboard/summary-all${query}`);
    if (res.code === 0 && res.data) return res.data;
    return EMPTY_SUMMARY_ALL;
  } catch {
    return EMPTY_SUMMARY_ALL;
  }
}

// ==================== AI 直答率周报（ORS-#981） ====================
// 数据链：本地 dar_weekly SOP 生成周报 → report 步自动 POST 到后端
// （dar_weekly 读 DAR_PUSH_BASE/USER/PASS）→ 本接口给「工单数据看板」页
// AI 直答率区块展示。摘要不含 fails 明细（含用户对话，不上服务号）。

export interface DarReportSummary {
  week_date: string;
  env: string;
  /** 三口径文本（L1/L2/L3），dar_weekly 原样 */
  dar_rates: Record<string, string>;
  /** 环比（同分母）：{ "L2_人工": { prev, curr, delta }, ... } */
  wow: Record<string, { prev: number; curr: number; delta: number }>;
  kb_gap: string | null;
  manual_progress: string | null;
  this_week: { from?: string; to?: string; n_convs?: number; n_segs?: number; n_ticketed?: number; L1_rate?: string };
  /**
   * 工作台展示值快照（推送时 dar_studio.metrics 原样取）——区块按这份渲染，
   * 与工作台首页那张卡同数。老周报没有这个字段，退回上面的周报口径字段。
   */
  workbench?: {
    hero?: DarWorkbenchCard[];
    small?: DarWorkbenchCard[];
    manual_progress?: string | null;
    export_at?: string | null;
    date?: string | null;
  } | null;
  updated_at: string | null;
}

/** 工作台指标卡（label/value/sub 三元组，与 dar_studio hero/small 同构） */
export interface DarWorkbenchCard {
  label: string;
  value: string;
  sub?: string;
}

export async function fetchDarLatest(env = 'prod'): Promise<DarReportSummary | null> {
  try {
    const res = await adminRequest<{ code: number; data: DarReportSummary | null }>(
      `/dashboard/dar-reports/latest?env=${env}`,
    );
    return res.code === 0 ? res.data ?? null : null;
  } catch {
    return null;
  }
}

export async function fetchDarList(env = 'prod', limit = 12): Promise<DarReportSummary[]> {
  try {
    const res = await adminRequest<{ code: number; data: DarReportSummary[] }>(
      `/dashboard/dar-reports/list?env=${env}&limit=${limit}`,
    );
    return res.code === 0 ? res.data ?? [] : [];
  } catch {
    return [];
  }
}
