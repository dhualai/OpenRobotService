/**
 * 工单「问题文档」API —— /api/tasks/{id}/spec-doc
 *
 * - 读取：GET  /{task_id}/spec-doc
 * - 保存：PUT  /{task_id}/spec-doc（乐观锁 revision，冲突 409）
 * - 解析：POST /spec-doc/parse（上传 .md/.docx/.doc → markdown，并保留原文件）
 */
import { createRequest, getToken } from '@/api/client';
import API_CONFIG from '@/config/api';
import { readStored } from '@/stores/authStorage';

const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');

/** 原始上传文件引用 */
export interface SpecDocSourceFile {
  object_path: string;
  filename?: string;
  size?: number;
}

/** 问题文档读取响应 */
export interface SpecDoc {
  exists: boolean;
  task_id: number;
  content: string;
  content_type: string;
  source?: string | null;
  source_files: SpecDocSourceFile[];
  revision: number;
  created_by?: string | null;
  updated_by?: string | null;
  updated_by_name?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** 保存入参 */
export interface SpecDocSaveParams {
  content: string;
  /** 客户端当前修订号（乐观锁）；不传=强制覆盖 */
  revision?: number;
  source?: string;
  source_files?: SpecDocSourceFile[];
}

/** 上传解析结果 */
export interface SpecDocParseResult {
  content: string;
  filename: string;
  size: number;
  object_path: string;
}

/** 读取工单问题文档（无文档时 exists=false） */
export const getSpecDoc = (taskId: number | string) =>
  request<SpecDoc>(`/${Number(taskId)}/spec-doc`, { skipCache: true });

/** 保存问题文档正文（乐观锁：revision 不一致后端返回 409） */
export const saveSpecDoc = (taskId: number | string, params: SpecDocSaveParams) =>
  request<SpecDoc>(`/${Number(taskId)}/spec-doc`, {
    method: 'PUT',
    body: JSON.stringify({
      content: params.content,
      revision: params.revision ?? null,
      source: params.source ?? null,
      source_files: params.source_files ?? null,
    }),
  });

/** 上传文档解析为 markdown（FormData + Bearer；原文件同时落 MinIO） */
export const parseSpecDocFile = async (file: File): Promise<SpecDocParseResult> => {
  const formData = new FormData();
  formData.append('file', file);
  const token = getToken() || readStored('AUTH_TOKEN') || '';
  const res = await fetch(`${API_CONFIG.TASKS.BASE_URL}/spec-doc/parse`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });
  if (!res.ok) {
    let detail = `文档解析失败: ${res.status}`;
    try {
      const err = await res.json();
      if (err?.detail) detail = typeof err.detail === 'string' ? err.detail : detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return (await res.json()) as SpecDocParseResult;
};

/** 上传单张图片（编辑器插入图片/粘贴用），返回代理 URL（/api/tasks/files/...） */
export const uploadSpecDocImage = async (file: File): Promise<string> => {
  const formData = new FormData();
  formData.append('file', file);
  const token = getToken() || readStored('AUTH_TOKEN') || '';
  const res = await fetch(`${API_CONFIG.TASKS.BASE_URL}/spec-doc/image`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });
  if (!res.ok) {
    let detail = `图片上传失败: ${res.status}`;
    try {
      const err = await res.json();
      if (err?.detail) detail = typeof err.detail === 'string' ? err.detail : detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  const data = (await res.json()) as { url?: string };
  if (!data.url) throw new Error('图片上传失败');
  return data.url;
};
