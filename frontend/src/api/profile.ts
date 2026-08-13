// 用户个人资料 API —— GET /api/auth/me、PUT /api/admin/users/{username}、
// POST /api/admin/resource-manager/resources/（头像上传，复用项目文档上传的资源管理中心接口）
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

/** external_credentials 中的 USP 凭据片段 */
export interface UspCredentials {
  username?: string;
  /** 提交时传新明文密码；从后端取回时为 "-"（表示已设置密码，前端据此判断是否必填）或空 */
  password?: string;
}

export interface ExternalCredentials {
  usp?: UspCredentials;
  [provider: string]: unknown;
}

export interface MyProfile {
  id: string;
  username: string;
  name?: string | null;
  status: string;
  avatar_resource_id?: number | null;
  company_id?: string | null;
  department_id?: string | null;
  company?: string | null;
  department?: string | null;
  external_credentials?: ExternalCredentials | null;
}

/** 个人中心可提交更新的字段（仅发送有变更的字段） */
export interface MyProfileUpdate {
  name?: string;
  avatar_resource_id?: number;
  company_id?: string;
  department_id?: string;
  external_credentials?: ExternalCredentials;
}

const authRequest = createRequest(API_CONFIG.AUTH.BASE_URL, '认证服务');
const adminRequest = createRequest(API_CONFIG.ADMIN.BASE_URL, '用户中心');

export async function getMyProfile(): Promise<MyProfile> {
  return authRequest<MyProfile>('/me', { skipCache: true });
}

/** 公司/部门下拉可选项（来自主数据表，含审核状态） */
export interface OrgOption {
  id: string;
  name: string;
  status: string;
}

export interface ProfileFieldOptions {
  companies: OrgOption[];
  departments_by_company: Record<string, OrgOption[]>;
  my_pending: {
    companies: { id: string; name: string }[];
    departments: { id: string; name: string; company_name: string }[];
  };
}

export async function getProfileOptions(): Promise<ProfileFieldOptions> {
  return adminRequest<ProfileFieldOptions>('/users/options', { skipCache: true });
}

/** 提交新公司（创建 pending 记录 + 审核工单） */
export async function submitNewCompany(name: string): Promise<{ company: { id: string; name: string; status: string }; ticket_id: number | null }> {
  return adminRequest('/users/options/company', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

/** 提交新部门（创建 pending 记录 + 审核工单） */
export async function submitNewDepartment(name: string, companyId: string): Promise<{ department: { id: string; name: string; status: string }; ticket_id: number | null }> {
  return adminRequest('/users/options/department', {
    method: 'POST',
    body: JSON.stringify({ name, company_id: companyId }),
  });
}

/** 根据中文姓名生成去重的 USP 账户名 */
export async function generateUspUsername(name: string): Promise<string> {
  const query = new URLSearchParams({ name }).toString();
  const res = await adminRequest<{ usp_username: string }>(`/users/usp-username?${query}`, {
    skipCache: true,
  });
  return res.usp_username;
}

export async function updateMyProfile(
  username: string,
  data: MyProfileUpdate,
): Promise<MyProfile> {
  return adminRequest<MyProfile>(`/users/${username}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function uploadAvatar(file: File, ownerId: string): Promise<{ id: number }> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('owner_id', ownerId);
  formData.append('resource_type', 'image');
  formData.append('category', '用户头像');
  return adminRequest<{ id: number }>('/resource-manager/resources/', {
    method: 'POST',
    body: formData,
  });
}

export function avatarUrl(resourceId: number): string {
  return `${API_CONFIG.ADMIN.BASE_URL}/resource-manager/resources/${resourceId}/download`;
}
