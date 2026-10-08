// 二维码管理相关 API
// 对齐 API_CONFIG.ADMIN.BASE_URL（= /api/admin 或 /t/api/admin /p/api/admin）
// endpoint 写相对路径 /qrcodes、/qrcodes/batch，不要重复 base
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

export interface QrcodeItem {
  id: number;
  scene_str: string;
  name: string;
  description?: string;
  ticket?: string;
  url?: string;
  qrcode_image_url?: string;
  type: 'temporary' | 'permanent';
  expire_seconds?: number;
  status: string;
  batch_id?: string;
  /** 项目名（录入信息行自带；普通码行为 null）。项目id 就是行 id（str(id)），不单独下发 */
  project_name?: string | null;
  /** 项目编号（录入信息行；普通码行为 null） */
  project_code?: string | null;
  /** 项目地点（录入信息行） */
  project_location?: string | null;
  /** 客户名称（录入信息行） */
  customer_name?: string | null;
  /** 车型（录入信息行） */
  vehicle_model?: string | null;
  redirect_url?: string;
  created_by?: string;
  published_by?: string;
  deprecated_by?: string;
  ticket_created_at?: string;
  created_at?: string;
  updated_at?: string;
}

export interface QrcodeListResult {
  total: number;
  items: QrcodeItem[];
}

export interface QrcodeStats {
  status: Record<string, number>;
  type: Record<string, number>;
  total: number;
  permanent_quota_remaining: number;
}

export type QrcodeStatus = 'init' | 'entering' | 'published' | 'deprecated';
export type QrcodeType = 'temporary' | 'permanent';

export const QRCODE_STATUS_LABELS: Record<QrcodeStatus, { label: string; color: string }> = {
  init:       { label: '初始化', color: '#888d8f' },
  entering:   { label: '录入中', color: '#5aa9cd' },
  published:  { label: '已发布', color: '#2d9d5c' },
  deprecated: { label: '已弃用', color: '#c94a4a' },
};

export async function fetchQrcodes(params: {
  status?: string;
  qrcode_type?: string;
  keyword?: string;
  batch_id?: string;
  skip?: number;
  limit?: number;
}): Promise<QrcodeListResult> {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.append(k, String(v));
  });
  const query = qs.toString();
  // endpoint 相对路径，不带 base（BASE_URL 已拼好 /api/admin）
  return request(query ? `/qrcodes?${query}` : '/qrcodes');
}

export async function fetchQrcode(id: number): Promise<QrcodeItem> {
  return request(`/qrcodes/${id}`);
}

export async function fetchQrcodeStats(): Promise<QrcodeStats> {
  return request('/qrcodes/stats/summary');
}

/**
 * 按场景值精确查一条二维码（摇人页「扫码进入」链路，登录即可访问）。
 *
 * 扫码跳转链接形如 `/app/call?scene=xxx`，scene 即 str(id)（2026-09-30 口径）。
 * 查无此码（404 / 400）或网络异常一律返回 null，调用方据此静默降级、不打扰用户。
 *
 * skipCache 必开：码状态（是否已发布=出厂）要求每次进入都拿最新，
 * 走 client.ts 的 5 分钟 GET 内存缓存会拿到陈旧状态。
 */
export async function fetchQrcodeByScene(scene: string): Promise<QrcodeItem | null> {
  try {
    return await request<QrcodeItem>(`/qrcodes/by-scene/${encodeURIComponent(scene)}`, { skipCache: true });
  } catch {
    // 静默：查无此码 / 参数非法 / 网络异常都归为「拿不到」，由调用方决定不打扰用户。
    // 刻意不打日志：scene 是业务标识，按脱敏口径不落明文。
    return null;
  }
}

export async function createQrcode(data: {
  name?: string;
  description?: string;
  qrcode_type?: QrcodeType;
  redirect_url?: string;
}): Promise<QrcodeItem> {
  return request('/qrcodes', { method: 'POST', body: JSON.stringify(data) });
}

export async function batchCreateQrcodes(data: {
  count: number;
  name_prefix?: string;
  qrcode_type?: QrcodeType;
  redirect_url?: string;
}): Promise<{ batch_id: string; created: number[]; created_count: number; skipped_count: number }> {
  return request('/qrcodes/batch', { method: 'POST', body: JSON.stringify(data) });
}

export async function generateQrcodeTicket(id: number): Promise<QrcodeItem> {
  return request(`/qrcodes/${id}/generate`, { method: 'POST' });
}

export async function batchGenerateTickets(data: {
  batch_id?: string;
  qid_list?: number[];
  only_init?: boolean;
}): Promise<{ total: number; success: Array<{ id: number; scene_str: string }>; failed: Array<{ id: number; scene_str: string; reason: string }> }> {
  return request('/qrcodes/batch-generate', { method: 'POST', body: JSON.stringify(data) });
}

export async function updateQrcode(id: number, data: {
  name?: string;
  description?: string;
  redirect_url?: string;
}): Promise<QrcodeItem> {
  return request(`/qrcodes/${id}`, { method: 'PUT', body: JSON.stringify(data) });
}

/** 状态流转动作。confirm 直接发布到 published，见 InfoEntry.tsx。 */
export async function qrcodeTransition(id: number, action: 'confirm' | 'publish' | 'deprecate'): Promise<QrcodeItem> {
  return request(`/qrcodes/${id}/${action}`, { method: 'POST' });
}

export async function deleteQrcode(id: number): Promise<{ ok: boolean }> {
  return request(`/qrcodes/${id}`, { method: 'DELETE' });
}

// ── 录入信息（项目信息登记）──
// 五个字段 = wechat_qrcodes 一行（和行 id 同行存），见 pages/admin/InfoEntry.tsx。
// 项目id 就是行 id（str(id)，保存后自动生成，不随表单提交）；项目编号唯一可改；
// 重复由后端 400 拦下。

export interface ProjectInfoPayload {
  /** 项目编号（唯一，可改） */
  project_code: string;
  /** 项目名 */
  project_name: string;
  project_location?: string;
  customer_name?: string;
  vehicle_model?: string;
}

/** 登记一条项目信息（落成 wechat_qrcodes 新行，init 状态） */
export async function createProjectInfo(data: ProjectInfoPayload): Promise<QrcodeItem> {
  return request('/qrcodes/project-info', { method: 'POST', body: JSON.stringify(data) });
}

/** 更新一条项目信息（字段传了才改） */
export async function updateProjectInfo(id: number, data: Partial<ProjectInfoPayload>): Promise<QrcodeItem> {
  return request(`/qrcodes/${id}/project-info`, { method: 'PUT', body: JSON.stringify(data) });
}
