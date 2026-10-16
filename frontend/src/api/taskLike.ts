// 工单点赞（活动中心「精品工单榜」的计数来源）REST 通道
//
// GET    /api/tasks/{taskId}/like   我的点赞状态（总数 + 我是否点过）
// POST   /api/tasks/{taskId}/like   点赞（幂等，重复点赞只刷新时间）
// DELETE /api/tasks/{taskId}/like   取消点赞（幂等）
//
// 点赞人由后端按 token 解析，前端无法替他人点赞；调用方负责乐观更新与失败回滚。
// 后端写操作成功后会清空前端 GET 缓存，故点赞后重新打开榜单能拿到最新计数。

import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

export interface TaskLikeState {
  liked: boolean;
  like_count: number;
}

/** 精品工单榜条目（只含已归档工单）。 */
export interface FeaturedTicket {
  id: number;
  title: string;
  status: string;
  like_count: number;
  is_liked: boolean;
  project_name: string | null;
  created_by: string | null;
  created_at: string | null;
  archived_at: string | null;
}

const tasksRequest = () => createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');

function pickState(res: { liked?: boolean; like_count?: number } | null | undefined): TaskLikeState {
  return { liked: !!res?.liked, like_count: Number(res?.like_count ?? 0) };
}

/** 查询点赞状态。失败抛错（详情页首屏需要准确状态，由调用方决定降级）。 */
export async function fetchTaskLike(taskId: string | number): Promise<TaskLikeState> {
  const res = await tasksRequest()<{ liked?: boolean; like_count?: number }>(`/${taskId}/like`, {
    skipCache: true,
  });
  return pickState(res);
}

/** 点赞 / 取消点赞。成功返回最新状态；失败返回 null，由调用方回滚乐观状态。 */
export async function toggleTaskLike(
  taskId: string | number,
  like: boolean,
): Promise<TaskLikeState | null> {
  try {
    const res = await tasksRequest()<{ liked?: boolean; like_count?: number }>(
      `/${taskId}/like`,
      { method: like ? 'POST' : 'DELETE' },
    );
    return pickState(res);
  } catch {
    return null;
  }
}

/** 精品工单榜（只统计已归档工单，按点赞数倒序）。 */
export async function fetchFeaturedTickets(limit = 10): Promise<FeaturedTicket[]> {
  const res = await tasksRequest()<{ total?: number; items?: FeaturedTicket[] }>(
    `/featured?limit=${limit}`,
    { skipCache: true },
  );
  return Array.isArray(res?.items) ? res.items : [];
}
