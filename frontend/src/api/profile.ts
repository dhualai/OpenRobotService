// 用户个人资料 API —— GET /api/auth/me、PUT /api/admin/users/{username}、
// POST /api/admin/resource-manager/resources/（头像上传，复用项目文档上传的资源管理中心接口）
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

export interface MyProfile {
  id: string;
  username: string;
  name?: string | null;
  status: string;
  avatar_resource_id?: number | null;
}

const authRequest = createRequest(API_CONFIG.AUTH.BASE_URL, '认证服务');
const adminRequest = createRequest(API_CONFIG.ADMIN.BASE_URL, '用户中心');

export async function getMyProfile(): Promise<MyProfile> {
  return authRequest<MyProfile>('/me', { skipCache: true });
}

export async function updateMyProfile(
  username: string,
  data: { name?: string; avatar_resource_id?: number },
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
