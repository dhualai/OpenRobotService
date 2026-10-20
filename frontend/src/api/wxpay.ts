// 微信支付 · 商家转账 —— 用户端「我的历史奖励」接口封装
//
// 业务形态（无卡包 / 无余额账户）：奖励发放只在商户侧记账（reward_record），
// 用户点「立即提现」时才真正向微信发起转账单；因此列表读的是奖励，不是转账单。
// 对应后端 backend/app/modules/wxpay/api/rewards.py（JWT 登录态）。
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

// ⚠️ BASE_URL 是 /api/wxpay，而后端奖励接口在 /api/wxpay/rewards/**、管理端在
//    /api/wxpay/admin/**。两者共用本 request，因此调用方必须各自带上一級子路径，
//    漏写会直接 404（历史上 fetchRewards/fetchRewardStats/withdrawReward 都踩过）。
const request = createRequest(API_CONFIG.WXPAY.BASE_URL, 'Wxpay');

/** 奖励状态（与后端 RewardStatus 对齐，含红包激励机制新增状态） */
export type RewardStatus =
  | 'pending_audit'
  | 'rejected'
  | 'pending'
  | 'withdrawing'
  | 'success'
  | 'expired';

/** 奖励角色 */
export type RewardRole = 'reporter' | 'assignee' | 'reviewer' | 'liker' | 'ranker' | 'manual';

/** 奖励来源类型 */
export type RewardSourceType = 'task' | 'task_like' | 'monthly_rank' | 'admin_test';

export interface RewardItem {
  id: string;
  /** 奖励金额，单位：分 */
  amount: number;
  /** 报备信息①活动名称 */
  scene_name: string;
  /** 报备信息②奖励说明 */
  reward_desc: string;
  status: RewardStatus;
  expire_at?: string | null;
  withdrawn_at?: string | null;
  created_at?: string | null;
  /** 当前有效转账单状态（微信侧 state，用于前端判断按钮文案） */
  bill_state?: string | null;
  out_bill_no?: string | null;
}

export interface RewardListResp {
  total: number;
  page: number;
  size: number;
  items: RewardItem[];
}

export interface RewardStats {
  /** 待提现总额（分） */
  pending_amount: number;
  /** 待提现笔数 */
  pending_count: number;
  /** 累计已到账（分） */
  withdrawn_amount: number;
}

/**
 * 提现结果动作：
 * - confirm         有待确认转账单，用 mch_id/app_id/package 调起 requestMerchantTransfer
 * - processing      转账单处于其它非终态（含刚发起未返回 package），提示稍后查看
 * - need_real_name  收款人姓名未通过微信实名校验，需弹窗让用户填写真实姓名后带 user_name 重试
 * - success         已到账
 * - expired         奖励已失效
 * - rejected        不满足提现条件（金额/状态/微信拒绝），见 message
 */
export type WithdrawAction =
  | 'confirm'
  | 'processing'
  | 'need_real_name'
  | 'success'
  | 'expired'
  | 'rejected';

export interface WithdrawResp {
  action: WithdrawAction;
  message: string;
  mch_id: string;
  app_id: string;
  /** 微信返回的 package_info，仅在 confirm 时有值 */
  package: string;
  out_bill_no: string;
  state: string;
}

/**
 * 我的历史奖励列表。
 *
 * 始终 skipCache：奖励状态由微信回调异步扭转（提现中 → 已到账），
 * 命中 5 分钟 GET 缓存会让用户看到「明明已到账却还显示提现中」。
 */
export function fetchRewards(params?: {
  status?: RewardStatus | '';
  page?: number;
  size?: number;
}): Promise<RewardListResp> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set('status', params.status);
  qs.set('page', String(params?.page ?? 1));
  qs.set('size', String(params?.size ?? 20));
  return request<RewardListResp>(`/rewards/?${qs.toString()}`, { skipCache: true });
}

/** 奖励汇总（待提现 / 累计已到账） */
export function fetchRewardStats(): Promise<RewardStats> {
  return request<RewardStats>('/rewards/stats', { skipCache: true });
}

/**
 * 立即提现 / 去确认。
 *
 * 后端内部走三分支决策：复用 package → 查单提示处理中 → 换新单号建单，
 * 保证同一笔奖励至多有一笔非终态转账单（防重复出资），前端无需自行判重。
 *
 * @param userName 仅在上一次返回 action=need_real_name 时填写：
 *                 微信对单笔 ≥2000 元的转账要求收款人姓名与实名一致，
 *                 受理后后端会固化该姓名，后续支付自动复用。
 */
export function withdrawReward(rewardId: string, userName?: string): Promise<WithdrawResp> {
  return request<WithdrawResp>(`/rewards/${encodeURIComponent(rewardId)}/withdraw`, {
    method: 'POST',
    ...(userName ? { body: JSON.stringify({ user_name: userName }) } : {}),
  });
}

/** 分 → 元（保留两位） */
export function fenToYuan(fen: number): string {
  return ((fen || 0) / 100).toFixed(2);
}

// ═══════════════════════════════════════════════════════════════
// 管理端（后台管理 → 其他 → 奖励管理）
// 对应后端 backend/app/modules/wxpay/api/admin.py，路由 /api/wxpay/admin/*
// ═══════════════════════════════════════════════════════════════

/** 「奖励管理」入口权限码（与后端 admin.py 的 PERM 常量必须一致） */
export const PERM_WXPAY_ADMIN = 'frontend:admin:wxpay-rewards:show';
/** 财务审核权限（通过 / 驳回 / 手动触发自动通过） */
export const PERM_REWARD_AUDIT = 'frontend:admin:reward-audit:write';
/** 规则配置编辑权限 */
export const PERM_REWARD_CONFIG = 'frontend:admin:reward-config:write';

export interface ArchiveFinanceDetail {
  task_id: number;
  report_id: number;
  title: string;
  problem: string;
  content: string;
  review_comment: string;
  value_point: string;
  total_amount: number;
}

export function fetchArchiveFinanceDetail(taskId: number): Promise<ArchiveFinanceDetail> {
  return request<ArchiveFinanceDetail>(`/admin/archive-finance/${taskId}`, { skipCache: true });
}

export interface AdminBillItem {
  id: string;
  reward_id: string;
  out_bill_no: string;
  transfer_bill_no?: string | null;
  appid?: string | null;
  mchid?: string | null;
  openid?: string | null;
  amount?: number | null;
  state?: string | null;
  is_terminal: boolean;
  fail_reason?: string | null;
  wx_create_time?: string | null;
  wx_update_time?: string | null;
  query_count: number;
  created_at?: string | null;
  updated_at?: string | null;
  reward_status?: string | null;
  reward_amount?: number | null;
  user_id?: string | null;
}

export interface AdminBillListResp {
  total: number;
  page: number;
  size: number;
  items: AdminBillItem[];
}

export interface AdminBillStats {
  total: number;
  open_count: number;
  terminal_count: number;
  by_state: Record<string, number>;
}

export interface AdminBillActionResp {
  ok: boolean;
  message: string;
  /** true 表示微信侧查无此单、本地占位记录已清理，前端应刷新列表而非更新行 */
  deleted: boolean;
  bill: AdminBillItem | null;
}

export interface AdminRewardItem {
  id: string;
  user_id: string;
  openid: string;
  amount: number;
  scene_name: string;
  reward_desc: string;
  remark: string;
  source_type: string;
  source_id?: string | null;
  role?: string | null;
  status: string;
  audit_by?: string | null;
  audit_at?: string | null;
  audit_remark?: string | null;
  bill_state?: string | null;
  out_bill_no?: string | null;
  expire_at?: string | null;
  withdrawn_at?: string | null;
  created_at?: string | null;
}

export interface AdminRewardListResp {
  total: number;
  page: number;
  size: number;
  items: AdminRewardItem[];
}

export interface AdminRewardStats {
  pending_audit_count: number;
  /** 待审核总额，单位：分 */
  pending_audit_amount: number;
  rejected_count: number;
  pending_count: number;
  withdrawing_count: number;
  success_count: number;
  expired_count: number;
}

export interface BatchAuditResp {
  ok_count: number;
  skipped_ids: string[];
  message: string;
}

export interface RewardConfigItem {
  config_key: string;
  enabled: boolean;
  config: Record<string, unknown>;
  updated_by?: string | null;
  updated_at?: string | null;
}

export interface RewardConfigUpdateIn {
  enabled?: boolean;
  config?: Record<string, unknown>;
}

export interface GrantRewardResp {
  id: string;
  user_id: string;
  amount: number;
  status: string;
  expire_at?: string | null;
  duplicated: boolean;
}

/** 转账单列表（管理端） */
export function fetchAdminBills(params?: {
  state?: string;
  only_open?: boolean;
  keyword?: string;
  page?: number;
  size?: number;
}): Promise<AdminBillListResp> {
  const qs = new URLSearchParams();
  if (params?.state) qs.set('state', params.state);
  if (params?.only_open !== undefined) qs.set('only_open', String(params.only_open));
  if (params?.keyword) qs.set('keyword', params.keyword);
  qs.set('page', String(params?.page ?? 1));
  qs.set('size', String(params?.size ?? 20));
  return request<AdminBillListResp>(`/admin/bills?${qs.toString()}`, { skipCache: true });
}

/** 转账单状态分布 */
export function fetchAdminBillStats(): Promise<AdminBillStats> {
  return request<AdminBillStats>('/admin/bills/stats', { skipCache: true });
}

/** 查单刷新（从微信侧同步最新状态） */
export function refreshAdminBill(billId: string): Promise<AdminBillActionResp> {
  return request<AdminBillActionResp>(`/admin/bills/${encodeURIComponent(billId)}/refresh`, {
    method: 'POST',
  });
}

/**
 * 撤销转账。
 *
 * 后端返回成功仅代表微信「受理」，必须再查单确认是否到终态；
 * 后端撤销后已自动回查一次，这里把最新状态带回给前端。
 */
export function cancelAdminBill(billId: string): Promise<AdminBillActionResp> {
  return request<AdminBillActionResp>(`/admin/bills/${encodeURIComponent(billId)}/cancel`, {
    method: 'POST',
  });
}

/** 奖励记录列表（管理端） */
export function fetchAdminRewards(params?: {
  user_id?: string;
  status?: string;
  role?: string;
  source_type?: string;
  keyword?: string;
  page?: number;
  size?: number;
}): Promise<AdminRewardListResp> {
  const qs = new URLSearchParams();
  if (params?.user_id) qs.set('user_id', params.user_id);
  if (params?.status) qs.set('status', params.status);
  if (params?.role) qs.set('role', params.role);
  if (params?.source_type) qs.set('source_type', params.source_type);
  if (params?.keyword) qs.set('keyword', params.keyword);
  qs.set('page', String(params?.page ?? 1));
  qs.set('size', String(params?.size ?? 20));
  return request<AdminRewardListResp>(`/admin/rewards?${qs.toString()}`, { skipCache: true });
}

/** 奖励状态分布概览（Tab 顶部角标） */
export function fetchAdminRewardStats(): Promise<AdminRewardStats> {
  return request<AdminRewardStats>('/admin/rewards/stats', { skipCache: true });
}

/** 批量审核通过（自动发起微信转账） */
export function batchAuditRewards(ids: string[]): Promise<BatchAuditResp> {
  return request<BatchAuditResp>('/admin/rewards/batch-audit', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  });
}

/** 批量驳回（终态，不发钱） */
export function batchRejectRewards(ids: string[], remark?: string): Promise<BatchAuditResp> {
  return request<BatchAuditResp>('/admin/rewards/batch-reject', {
    method: 'POST',
    body: JSON.stringify({ ids, audit_remark: remark || '' }),
  });
}

/** 手动触发自动通过（对所有 pending_audit 重跑决策引擎） */
export function runAutoPassRewards(): Promise<BatchAuditResp> {
  return request<BatchAuditResp>('/admin/rewards/run-auto-pass', { method: 'POST' });
}

/** 规则配置列表 */
export function fetchRewardConfig(): Promise<RewardConfigItem[]> {
  return request<RewardConfigItem[]>('/admin/reward-config', { skipCache: true });
}

/** 更新单条规则配置 */
export function updateRewardConfig(
  configKey: string,
  payload: RewardConfigUpdateIn,
): Promise<RewardConfigItem> {
  return request<RewardConfigItem>(`/admin/reward-config/${encodeURIComponent(configKey)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

/** 转账测试「收款用户」下拉候选：读 users 表，并给出可收款 openid */
export interface AdminUserOption {
  id: string;
  username: string;
  name?: string | null;
  wechat_openid?: string | null;
  /** 可收款 openid：绑定列优先，微信注册用户回退 users.id；都没有则空串 */
  openid: string;
  /** bound=绑定列 / self=微信注册用户 id 即 openid / 空=解析不到 */
  openid_source: string;
}

export interface AdminUserOptionListResp {
  total: number;
  /** 命中数超过本次返回条数 → 需用 keyword 进一步缩小范围 */
  truncated: boolean;
  items: AdminUserOption[];
}

/** 收款用户候选（转账测试下拉数据源） */
export function fetchAdminUserOptions(params?: {
  keyword?: string;
  only_receivable?: boolean;
  limit?: number;
}): Promise<AdminUserOptionListResp> {
  const qs = new URLSearchParams();
  if (params?.keyword) qs.set('keyword', params.keyword);
  if (params?.only_receivable !== undefined) {
    qs.set('only_receivable', String(params.only_receivable));
  }
  qs.set('limit', String(params?.limit ?? 200));
  return request<AdminUserOptionListResp>(`/admin/user-options?${qs.toString()}`, {
    skipCache: true,
  });
}

/** 发放奖励（仅记账，不建转账单） */
export function grantAdminReward(payload: {
  user_id: string;
  openid?: string;
  amount: number;
  scene_name: string;
  reward_desc: string;
  remark?: string;
  source_type?: string;
  source_id?: string;
  expire_days?: number;
}): Promise<GrantRewardResp> {
  return request<GrantRewardResp>('/admin/rewards/grant', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

/**
 * 对指定奖励发起转账（提现）。
 *
 * 与用户自提现走同一套三分支决策，因此后台重复点击不会造成重复出资。
 * 返回 action=confirm 时可用 mch_id/app_id/package 调起 requestMerchantTransfer，
 * 但管理端通常在普通浏览器操作（无 WeixinJSBridge），此时以查单/回调结果为准。
 */
export function withdrawAdminReward(rewardId: string, userName?: string): Promise<WithdrawResp> {
  return request<WithdrawResp>(`/admin/rewards/${encodeURIComponent(rewardId)}/withdraw`, {
    method: 'POST',
    ...(userName ? { body: JSON.stringify({ user_name: userName }) } : {}),
  });
}
