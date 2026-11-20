// 工单关注（卡片星标）REST 通道
//
// POST   /api/tasks/{taskId}/follow   关注（幂等，重复关注只刷新时间）
// DELETE /api/tasks/{taskId}/follow   取消关注（幂等）
//
// 两个接口后端都按 token 解析归属人，前端无法替他人关注；调用方负责乐观更新与失败回滚。

import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

/** 关注 / 取消关注。成功返回 true，失败静默返回 false（不打断 UI，由调用方回滚乐观状态）。 */
export async function toggleTaskFollow(
  taskId: string | number,
  follow: boolean,
): Promise<boolean> {
  try {
    const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
    await request<{ ok?: boolean; followed?: boolean }>(`/${taskId}/follow`, {
      method: follow ? 'POST' : 'DELETE',
    });
    return true;
  } catch {
    return false;
  }
}
