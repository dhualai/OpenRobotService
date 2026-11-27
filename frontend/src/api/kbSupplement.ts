// 知识补充（ORS-1159）—— 后台「知识库 → 待补充」。
// 数据链：工作台把 AI 归纳的知识点推过来 → 部门多人补答案 → AI 汇总 → 审核 → 入库。

import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

const adminRequest = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

export type SupplementStatus =
  | 'open'
  | 'drafting'
  | 'merged'
  | 'reviewing'
  | 'approved'
  | 'rejected';

export interface SupplementQuestion {
  id: string;
  week_date: string;
  env: string;
  title: string;
  description: string;
  qtype: string;
  hit_count: number;
  status: SupplementStatus;
  pushed_at?: string | null;
  updated_at?: string | null;
  draft_count?: number;
}

export interface SupplementDraft {
  id: number;
  question_id: string;
  author_id: string;
  author_name: string;
  content_md: string;
  media?: string;
  is_merged: boolean;
  updated_at?: string | null;
}

export interface SupplementMe {
  can_write: boolean;
  can_review: boolean;
  user_id: string;
  name: string;
}

export async function fetchSupplementList(params: {
  status?: string;
  qtype?: string;
  q?: string;
} = {}): Promise<{ data: SupplementQuestion[]; summary: Record<string, number> } | null> {
  try {
    const qs = new URLSearchParams();
    if (params.status) qs.set('status', params.status);
    if (params.qtype) qs.set('qtype', params.qtype);
    if (params.q) qs.set('q', params.q);
    const res = await adminRequest<{
      code: number;
      data: SupplementQuestion[];
      summary: Record<string, number>;
    }>(`/kb/supplement-questions${qs.toString() ? `?${qs}` : ''}`);
    return res.code === 0 ? { data: res.data, summary: res.summary } : null;
  } catch {
    return null;
  }
}

export async function fetchSupplementDetail(
  id: string,
): Promise<{ data: SupplementQuestion; drafts: SupplementDraft[]; me: SupplementMe } | null> {
  try {
    const res = await adminRequest<{
      code: number;
      data: SupplementQuestion;
      drafts: SupplementDraft[];
      me: SupplementMe;
    }>(`/kb/supplement-questions/${encodeURIComponent(id)}`);
    return res.code === 0 ? { data: res.data, drafts: res.drafts, me: res.me } : null;
  } catch {
    return null;
  }
}

export async function saveSupplementDraft(id: string, contentMd: string): Promise<void> {
  await adminRequest(`/kb/supplement-questions/${encodeURIComponent(id)}/drafts`, {
    method: 'POST',
    body: JSON.stringify({ content_md: contentMd }),
  });
}

export async function mergeSupplementDrafts(id: string): Promise<string> {
  const res = await adminRequest<{ code: number; content_md: string }>(
    `/kb/supplement-questions/${encodeURIComponent(id)}/merge`,
    { method: 'POST' },
  );
  return res.content_md || '';
}

export async function submitSupplementReview(id: string): Promise<void> {
  await adminRequest(`/kb/supplement-questions/${encodeURIComponent(id)}/submit-review`, {
    method: 'POST',
  });
}

export async function approveSupplement(id: string): Promise<{ file?: string }> {
  return adminRequest(`/kb/supplement-questions/${encodeURIComponent(id)}/approve`, {
    method: 'POST',
  });
}

export async function rejectSupplement(id: string, reason: string): Promise<void> {
  await adminRequest(`/kb/supplement-questions/${encodeURIComponent(id)}/reject`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
}

export const SUPPLEMENT_STATUS_LABEL: Record<SupplementStatus, string> = {
  open: '待补充',
  drafting: '补充中',
  merged: '已汇总',
  reviewing: '待审核',
  approved: '已入库',
  rejected: '已驳回',
};

export const SUPPLEMENT_STATUS_TONE: Record<SupplementStatus, string> = {
  open: '#64748b',
  drafting: '#2563eb',
  merged: '#7c3aed',
  reviewing: '#d97706',
  approved: '#16a34a',
  rejected: '#dc2626',
};
