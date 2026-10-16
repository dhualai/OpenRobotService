// 公告（活动中心）REST 通道
//
// 前台（登录即可）
//   GET /api/announcements/active   首页固定公告条：当前生效的一条
//   GET /api/announcements          抽屉内已发布公告列表
// 后台（需 frontend:admin:announcement:manage）
//   GET/POST   /api/admin/announcements
//   PUT/DELETE /api/admin/announcements/{id}
//   POST       /api/admin/announcements/{id}/publish | /offline
//
// 首页公告条只常驻一条（后端排序：已发布 → 置顶优先 → 发布时间倒序），不轮播。

import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

/** 后台「公告管理」权限码（与后端 admin/api/announcement.py 的 PERM_CODE 一致）。
 *  放在 api 模块，避免入口页（AdminEntries）为拿一个字符串把整页拖进首包。 */
export const PERM_ANNOUNCEMENT = 'frontend:admin:announcement:manage';

export type AnnouncementStatus = 'draft' | 'published' | 'offline';

/** 状态中文标签（后台列表与筛选共用） */
export const ANNOUNCEMENT_STATUS_LABELS: Record<AnnouncementStatus, string> = {
  draft: '草稿',
  published: '已发布',
  offline: '已下线',
};

export interface Announcement {
  id: number;
  title: string;
  content: string;
  is_pinned: boolean;
  status: AnnouncementStatus;
  published_at: string | null;
  created_by: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AnnouncementListResult {
  total: number;
  items: Announcement[];
}

/** 后台新建/编辑入参（编辑时只传要改的字段）。 */
export interface AnnouncementInput {
  title?: string;
  content?: string;
  is_pinned?: boolean;
  status?: AnnouncementStatus;
}

const frontendRequest = () => createRequest(API_CONFIG.ANNOUNCEMENT.BASE_URL, '公告服务');
const adminRequest = () => createRequest(API_CONFIG.ADMIN.BASE_URL, '后台管理');

// ---------------------------------------------------------------------------
// 前台
// ---------------------------------------------------------------------------

/** 首页公告条：当前生效的一条公告；没有已发布公告时返回 null（公告条不渲染）。 */
export async function fetchActiveAnnouncement(): Promise<Announcement | null> {
  const request = frontendRequest();
  const res = await request<{ item: Announcement | null }>('/active', { skipCache: true });
  return res?.item ?? null;
}

/** 抽屉内公告列表（只含已发布）。 */
export async function fetchAnnouncements(skip = 0, limit = 20): Promise<AnnouncementListResult> {
  const request = frontendRequest();
  const res = await request<AnnouncementListResult>(`?skip=${skip}&limit=${limit}`, {
    skipCache: true,
  });
  return {
    total: Number(res?.total ?? 0),
    items: Array.isArray(res?.items) ? res.items : [],
  };
}

// ---------------------------------------------------------------------------
// 后台
// ---------------------------------------------------------------------------

export interface AdminAnnouncementQuery {
  status?: AnnouncementStatus | '';
  keyword?: string;
  skip?: number;
  limit?: number;
}

export async function adminListAnnouncements(
  query: AdminAnnouncementQuery = {},
): Promise<AnnouncementListResult> {
  const request = adminRequest();
  const params = new URLSearchParams();
  if (query.status) params.set('status', query.status);
  if (query.keyword) params.set('keyword', query.keyword);
  params.set('skip', String(query.skip ?? 0));
  params.set('limit', String(query.limit ?? 20));

  const res = await request<AnnouncementListResult>(`/announcements?${params.toString()}`, {
    skipCache: true,
  });
  return {
    total: Number(res?.total ?? 0),
    items: Array.isArray(res?.items) ? res.items : [],
  };
}

export async function adminCreateAnnouncement(
  payload: AnnouncementInput & { title: string; content: string },
): Promise<Announcement | null> {
  const request = adminRequest();
  const res = await request<{ item?: Announcement }>('/announcements', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return res?.item ?? null;
}

export async function adminUpdateAnnouncement(
  id: number,
  payload: AnnouncementInput,
): Promise<Announcement | null> {
  const request = adminRequest();
  const res = await request<{ item?: Announcement }>(`/announcements/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
  return res?.item ?? null;
}

export async function adminPublishAnnouncement(id: number): Promise<Announcement | null> {
  const request = adminRequest();
  const res = await request<{ item?: Announcement }>(`/announcements/${id}/publish`, {
    method: 'POST',
  });
  return res?.item ?? null;
}

export async function adminOfflineAnnouncement(id: number): Promise<Announcement | null> {
  const request = adminRequest();
  const res = await request<{ item?: Announcement }>(`/announcements/${id}/offline`, {
    method: 'POST',
  });
  return res?.item ?? null;
}

export async function adminDeleteAnnouncement(id: number): Promise<boolean> {
  const request = adminRequest();
  await request(`/announcements/${id}`, { method: 'DELETE' });
  return true;
}
