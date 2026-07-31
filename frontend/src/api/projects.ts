// 项目列表 API —— 对接 admin 模块 GET /api/admin/projects/
// 该接口仅需登录 token 即可访问（security 验证，不查角色）；
// 传 include_analysis=false 跳过风险计算，只返回项目基础字段，供工单绑定项目下拉使用。
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

export interface ProjectItem {
  id: string;
  project_code: string; // 项目业务编码（作为工单 project_id 绑定）
  name: string;
  status?: string;
}

// 模糊搜索项目（keyword 为空时返回全量，默认 200 条）
export async function getProjects(keyword = '', skip = 0, limit = 200): Promise<ProjectItem[]> {
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, '项目服务');
  const params = new URLSearchParams({
    skip: String(skip),
    limit: String(limit),
    include_analysis: 'false',
  });
  if (keyword.trim()) params.set('keyword', keyword.trim());
  const data = await request<ProjectItem[]>(`/projects/?${params.toString()}`);
  return Array.isArray(data) ? data : [];
}

// ── 项目成员（用于讨论区 @ 提及）──────────────────────────────

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
