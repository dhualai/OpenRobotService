// 企业微信表格数据源（后台「其他」→ 企微表格）。
// 后端把每张企微智能表格当一条可配置的数据源：填 docid + sheet_id 就能接进来，
// 同步结果落在统一的 external_record 镜像表里，不需要为每张表建物理表。
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

/** 后台「企微表格」入口与接口共用的权限码（常量放这里，避免入口页为拿常量加载整页） */
export const PERM_WECOM_SHEETS = 'frontend:admin:wecom-sheets:show';

/** 后端统一包 {code, message, data}，这里只取 data */
function unwrap<T>(raw: unknown): T {
  if (raw && typeof raw === 'object' && 'data' in raw) {
    return (raw as { data: T }).data;
  }
  return raw as T;
}

export interface WecomSheetSource {
  id: number;
  key: string;
  display_name: string;
  docid: string;
  sheet_id: string;
  enabled: boolean;
  target_mode: 'mirror' | 'handler' | string;
  handler_key?: string | null;
  sync_interval_min: number;
  notes?: string | null;
  record_count: number;
  last_sync_at?: string | null;
  last_stats?: SyncStats | null;
  last_error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SyncStats {
  fetched?: number;
  created?: number;
  updated?: number;
  unchanged?: number;
  missing?: number;
  skipped?: number;
  total?: number;
  /** 正在同步中 / 已停用时后端返回 skipped */
  skipped_reason?: string;
  reason?: string;
}

export interface SheetPreview {
  total: number;
  columns: string[];
  records: Array<{ record_id: string; values: Record<string, unknown> }>;
}

export interface MirrorRecord {
  record_id: string;
  values: Record<string, unknown>;
  pulled_at?: string | null;
  changed_at?: string | null;
}

export interface WecomDocSheet {
  sheet_id: string;
  title?: string;
}

/** 新建企微智能表格的返回。docid 企微只返回这一次，必须落库 */
export interface CreatedWecomDoc {
  docid: string;
  url: string;
  doc_name: string;
  sheets: WecomDocSheet[];
}

export interface CreateDocPayload {
  doc_name: string;
  spaceid?: string;
  fatherid?: string;
  admin_users?: string[];
}

export interface SheetSourcePayload {
  key?: string;
  display_name?: string;
  docid?: string;
  sheet_id?: string;
  enabled?: boolean;
  target_mode?: string;
  handler_key?: string | null;
  sync_interval_min?: number;
  notes?: string | null;
}

export async function fetchSheetSources(): Promise<WecomSheetSource[]> {
  const raw = await request('/wecom-sheets', { skipCache: true });
  const data = unwrap<WecomSheetSource[]>(raw);
  return Array.isArray(data) ? data : [];
}

export async function createSheetSource(payload: SheetSourcePayload): Promise<WecomSheetSource> {
  return unwrap<WecomSheetSource>(
    await request('/wecom-sheets', { method: 'POST', body: JSON.stringify(payload) }),
  );
}

export async function updateSheetSource(id: number, payload: SheetSourcePayload): Promise<WecomSheetSource> {
  return unwrap<WecomSheetSource>(
    await request(`/wecom-sheets/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  );
}

export async function deleteSheetSource(id: number): Promise<void> {
  await request(`/wecom-sheets/${id}`, { method: 'DELETE' });
}

/** 测试连接：不落库，只回真实列名 + 少量样例行 */
export async function previewSheet(docid: string, sheetId: string, limit = 3): Promise<SheetPreview> {
  return unwrap<SheetPreview>(
    await request('/wecom-sheets/preview', {
      method: 'POST',
      body: JSON.stringify({ docid, sheet_id: sheetId, limit }),
      timeout: 60000,
    }),
  );
}

/**
 * 在企微里新建一张智能表格，拿回 docid。
 * 这是唯一能拿到 API docid 的方式：手工在企微里建的表格，
 * 浏览器链接里的 s3_xxx 是 URL ID 不是 docid，同步时会报 301085。
 */
export async function createWecomDoc(payload: CreateDocPayload): Promise<CreatedWecomDoc> {
  return unwrap<CreatedWecomDoc>(
    await request('/wecom-sheets/create-doc', {
      method: 'POST',
      body: JSON.stringify(payload),
      timeout: 60000,
    }),
  );
}

/** 查询某文档下的子表，用来选 sheet_id（不用去浏览器 URL 抠 tab 参数） */
export async function fetchWecomDocSheets(docid: string): Promise<WecomDocSheet[]> {
  const raw = await request(`/wecom-sheets/doc-sheets?docid=${encodeURIComponent(docid)}`, {
    skipCache: true,
    timeout: 30000,
  });
  const data = unwrap<{ sheets?: WecomDocSheet[] }>(raw);
  return Array.isArray(data?.sheets) ? data.sheets : [];
}

export async function syncSheet(id: number): Promise<SyncStats> {
  return unwrap<SyncStats>(await request(`/wecom-sheets/${id}/sync`, { method: 'POST', timeout: 180000 }));
}

export async function fetchSheetRecords(
  id: number,
  params: { skip?: number; limit?: number; q?: string } = {},
): Promise<{ total: number; items: MirrorRecord[] }> {
  const qs = new URLSearchParams();
  if (params.skip) qs.set('skip', String(params.skip));
  if (params.limit) qs.set('limit', String(params.limit));
  if (params.q) qs.set('q', params.q);
  const query = qs.toString();
  const raw = await request(`/wecom-sheets/${id}/records${query ? `?${query}` : ''}`, { skipCache: true });
  const data = unwrap<{ total: number; items: MirrorRecord[] }>(raw);
  return { total: data?.total ?? 0, items: data?.items ?? [] };
}
