// 项目成员 API —— 用于讨论区 @ 提及选择
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

export interface ProjectMember {
  id: string;
  username: string;
  name?: string | null;
  role_name?: string | null;
}

/** 获取任务关联项目的成员列表 */
export async function getProjectMembers(taskId: string | number): Promise<ProjectMember[]> {
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
  const data = await request<ProjectMember[]>(`/${taskId}/project-members`);
  return Array.isArray(data) ? data : [];
}
