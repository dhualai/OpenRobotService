// 可指派人员列表 API —— 对接 tasks 业务模块 GET /api/tasks/assignable-users
// 该接口仅需登录即可访问，字段最小化（id/username/name/status），不含敏感凭据，
// 与 admin 用户管理接口（/api/admin/users，受权限管控）刻意解耦。
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

export interface UserItem {
  id: string;
  username: string;
  name?: string | null;
  status?: string;
}

// 拉取可指派人员列表（全量，用于前端下拉 + 模糊搜索）
export async function getUsers(skip = 0, limit = 1000): Promise<UserItem[]> {
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '用户服务');
  const data = await request<UserItem[]>(`/assignable-users?skip=${skip}&limit=${limit}`);
  return Array.isArray(data) ? data : [];
}
