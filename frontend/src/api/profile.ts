// 用户个人资料 API —— GET /api/auth/me、PUT /api/admin/users/{username}、
// POST /api/admin/resource-manager/resources/（头像上传，复用项目文档上传的资源管理中心接口）
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

/** external_credentials 中的 USP 凭据片段 */
export interface UspCredentials {
  username?: string;
  /** 仅用于提交新明文密码；从后端取回时这里是已存储的哈希，前端不应展示 */
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
  company?: string | null;
  department?: string | null;
  external_credentials?: ExternalCredentials | null;
}

/** 个人中心可提交更新的字段（仅发送有变更的字段） */
export interface MyProfileUpdate {
  name?: string;
  avatar_resource_id?: number;
  company?: string;
  department?: string;
  external_credentials?: ExternalCredentials;
}

const authRequest = createRequest(API_CONFIG.AUTH.BASE_URL, '认证服务');
const adminRequest = createRequest(API_CONFIG.ADMIN.BASE_URL, '用户中心');

export async function getMyProfile(): Promise<MyProfile> {
  return authRequest<MyProfile>('/me', { skipCache: true });
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
