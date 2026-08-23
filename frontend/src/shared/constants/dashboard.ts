// 后台管理仪表盘 —— 状态/分类的显示映射与统一颜色方案
// 供 Dashboard 首页、以及各下钻明细页共用，避免多处重复定义、颜色不一致。

// ============================================================
// 一、工单状态（系统任务模块 TaskStatus）
// 数据源：GET /api/admin/dashboard/tickets/summary（backend/app/modules/admin/api/dashboard.py）
// 后端 backend/app/models/task.py TaskStatus 枚举：new/in_progress/pending/resolved/canceled/closed。
// 「暂停/挂起」「已取消」在后端分别复用 pending/canceled 语义，映射见
// backend/app/modules/admin/services/task_dashboard_service.py FRONTEND_STATUS_MAP。
// ============================================================
export type TicketStatusKey =
  | 'new' | 'in_progress' | 'paused' | 'resolved' | 'closed' | 'cancelled';

export interface StatusMeta {
  key: TicketStatusKey;
  label: string;
  color: string;
  /** 马卡龙蓝色阶（blue-1 深 → blue-5 浅），工单状态监测环图/图例用 */
  tone: string;
  backendReady: boolean;
}

export const TICKET_STATUS_LIST: StatusMeta[] = [
  // 「新建」不参与工单状态监测统计（后端 get_ticket_summary 亦排除 new），仅监控五种状态；
  // tone 用五状态专用蓝阶 status-1(深) → status-5(浅)，亮度等距拉开、相邻状态对比明显；
  // 图例顺序由 Dashboard 的 STATUS_TONE_ORDER 控制：
  // 处理中 status-1 / 暂停挂起 status-2 / 已关闭 status-3 / 已解决 status-4 / 已取消 status-5。
  { key: 'in_progress', label: '处理中', color: '#2ba471', tone: 'status-1', backendReady: true },
  { key: 'paused', label: '暂停/挂起', color: '#e37318', tone: 'status-2', backendReady: true },
  { key: 'closed', label: '已关闭', color: '#999999', tone: 'status-3', backendReady: true },
  { key: 'resolved', label: '已解决', color: '#00a870', tone: 'status-4', backendReady: true },
  { key: 'cancelled', label: '已取消', color: '#d54941', tone: 'status-5', backendReady: true },
];

export const TICKET_STATUS_MAP: Record<string, StatusMeta> =
  Object.fromEntries(TICKET_STATUS_LIST.map((s) => [s.key, s]));

// ============================================================
// 二、项目调度阶段（ProjectStatus，来自企业微信智能表格，后期接入）
// 数据源：GET /api/admin/dashboard/projects/summary（待后端接入）
// 后端现状：backend/app/modules/admin/schemas_das/request_models.py ProjectStatus
// 枚举目前 13 个值，与产品要求的 15 项存在差异：
//   - 缺「投标阶段」「项目暂停」「项目终止」「项目变更」
//   - 现有「项目中止」与目标的「项目暂停」/「项目终止」语义重叠，需产品与后端对齐后决定是否拆分/改名
//   - 现有「实施运行」不在目标 15 项中，需确认去留
// 本页先按产品要求的 15 项占位设计，接口返回 0 即可。
// ============================================================
export type ProjectStageKey =
  | 'pre_sales' | 'bidding' | 'negotiation' | 'contract_signed' | 'factory_test'
  | 'pending_entry' | 'delayed_entry' | 'in_implementation' | 'implementation_suspended'
  | 'in_trial_operation' | 'acceptance_operation' | 'project_suspended'
  | 'project_terminated' | 'project_changed' | 'project_ended';

export interface StageMeta { key: ProjectStageKey; label: string; color: string; backendReady: boolean; }

export const PROJECT_STAGE_LIST: StageMeta[] = [
  { key: 'pre_sales', label: '售前方案', color: '#8e5fd9', backendReady: true },
  { key: 'bidding', label: '投标阶段', color: '#a166e3', backendReady: false },   // 后端枚举缺失，待补
  { key: 'negotiation', label: '签单洽谈', color: '#0052d9', backendReady: true },
  { key: 'contract_signed', label: '已签合同', color: '#0089ff', backendReady: true },
  { key: 'factory_test', label: '出厂测试', color: '#00a3c4', backendReady: true },
  { key: 'pending_entry', label: '即将进场', color: '#2ba471', backendReady: true },
  { key: 'delayed_entry', label: '延期进场', color: '#e37318', backendReady: true },
  { key: 'in_implementation', label: '正在实施', color: '#00a870', backendReady: true },
  { key: 'implementation_suspended', label: '实施暂停', color: '#ed7b2f', backendReady: true },
  { key: 'in_trial_operation', label: '试运行中', color: '#2f9bed', backendReady: true },
  { key: 'acceptance_operation', label: '验收运营', color: '#2eb872', backendReady: true },
  { key: 'project_suspended', label: '项目暂停', color: '#f0a020', backendReady: false }, // 后端现有「项目中止」需与产品对齐
  { key: 'project_terminated', label: '项目终止', color: '#d54941', backendReady: false }, // 后端枚举缺失，待补
  { key: 'project_changed', label: '项目变更', color: '#b08a2e', backendReady: false },   // 后端枚举缺失，待补
  { key: 'project_ended', label: '项目结束', color: '#666666', backendReady: true },
];

export const PROJECT_STAGE_MAP: Record<string, StageMeta> =
  Object.fromEntries(PROJECT_STAGE_LIST.map((s) => [s.key, s]));

// ============================================================
// 三、项目紧急度四象限（ProjectCategory，后端已支持）
// 数据源：GET /api/admin/dashboard/projects/urgency（待后端接入，可直接从 /projects/ 聚合 category_basis 字段）
// 后端字段：backend/app/modules/admin/schemas_das/request_models.py ProjectCategory
// ============================================================
export type UrgencyKey = 'important_urgent' | 'urgent_not_important' | 'important_not_urgent' | 'not_important_not_urgent';

export interface UrgencyMeta {
  key: UrgencyKey;
  label: string;
  color: string;
  /** 马卡龙蓝色阶（对照 macaron 原型紧急度看板：blue-2 深 → blue-5 浅） */
  tone: string;
}

export const URGENCY_LIST: UrgencyMeta[] = [
  { key: 'important_urgent', label: '重要紧急', color: '#d54941', tone: 'blue-2' },
  { key: 'important_not_urgent', label: '重要不紧急', color: '#e37318', tone: 'blue-3' },
  { key: 'urgent_not_important', label: '紧急不重要', color: '#0052d9', tone: 'blue-4' },
  { key: 'not_important_not_urgent', label: '不重要不紧急', color: '#999999', tone: 'blue-5' },
];

export const URGENCY_MAP: Record<string, UrgencyMeta> =
  Object.fromEntries(URGENCY_LIST.map((u) => [u.key, u]));
