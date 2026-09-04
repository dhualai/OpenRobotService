// 讨论区已读回执 REST 通道（WS 不可用时的兜底）
//
// 正常路径走 WebSocket（实时、低开销）；但 WS 尚未建连 / 断线重连中会丢帧，
// 此时前端改用本模块的 REST 接口上报，保证「已读名单」不因传输通道时序而漏报。
// 两个接口后端都是幂等的：重复上报只会刷新 read_at，不会产生脏数据。

import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

export interface ReadRecordItem {
  username: string;
  name?: string | null;
  avatar_resource_id?: number | null;
  read_at?: string | null;
}

interface ReportReadResponse {
  ok?: boolean;
  comment_ids?: number[];
  last_read_comment_id?: number | null;
  records?: ReadRecordItem[];
}

/** 上报已读（REST 兜底）。成功返回 true，失败静默返回 false（不打断 UI）。 */
export async function reportCommentRead(
  taskId: string | number,
  commentIds: number[],
  lastReadCommentId: number | null,
): Promise<boolean> {
  if (!commentIds.length) return false;
  try {
    const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
    const res = await request<ReportReadResponse>(`/${taskId}/comments/read`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        comment_ids: commentIds,
        last_read_comment_id: lastReadCommentId,
      }),
    });
    return res?.ok !== false;
  } catch {
    return false;
  }
}

/** 按需拉取单条评论的已读名单（已读弹层打开时刷新，兜底 welcome 快照截断）。 */
export async function fetchCommentReadList(
  taskId: string | number,
  commentId: string | number,
): Promise<ReadRecordItem[]> {
  try {
    const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
    const res = await request<ReadRecordItem[] | { detail?: string }>(
      `/${taskId}/comments/${commentId}/read`,
    );
    return Array.isArray(res) ? res : [];
  } catch {
    return [];
  }
}
