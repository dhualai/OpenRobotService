// 知识库浏览（ORS-716）—— 后台「数据资源 → 知识库」。
// 读的是服务器上的 kb 目录，接口见 backend/app/modules/admin/api/kb_browse.py。

import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

const adminRequest = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

export interface KbDir {
  name: string;
  /** 中文名（目录是英文的，后端映射；车型/品牌名保持原样） */
  label: string;
  path: string;
  md_count: number;
}

export interface KbFile {
  name: string;
  label: string;
  path: string;
  size: number;
}

export interface KbTree {
  path: string;
  /** 中文面包屑（后端映射），首项是「知识库」根 */
  crumbs: { label: string; path: string }[];
  dirs: KbDir[];
  files: KbFile[];
}

export interface KbDoc {
  path: string;
  name: string;
  label: string;
  size: number;
  content: string;
}

export async function fetchKbTree(path = ''): Promise<KbTree | null> {
  try {
    const res = await adminRequest<{ code: number; data: KbTree }>(
      `/kb/tree?path=${encodeURIComponent(path)}`,
    );
    return res.code === 0 ? res.data : null;
  } catch {
    return null;
  }
}

export async function fetchKbFile(path: string): Promise<KbDoc | null> {
  try {
    const res = await adminRequest<{ code: number; data: KbDoc }>(
      `/kb/file?path=${encodeURIComponent(path)}`,
    );
    return res.code === 0 ? res.data : null;
  } catch {
    return null;
  }
}

/** md 里的相对图片路径 → kb 内绝对相对路径（./media/x.png 基于 md 所在目录解析）。 */
export function resolveKbMedia(mdPath: string, src: string): string {
  if (!src) return src;
  if (/^(https?:)?\/\//i.test(src) || src.startsWith('data:')) return src;
  const dir = mdPath.includes('/') ? mdPath.slice(0, mdPath.lastIndexOf('/')) : '';
  const parts = (dir ? dir.split('/') : []).concat(src.split('/'));
  const out: string[] = [];
  for (const p of parts) {
    if (!p || p === '.') continue;
    if (p === '..') out.pop();
    else out.push(p);
  }
  return out.join('/');
}

/** 图片走后台同域接口，手机端预览时才带得上登录态。 */
export function kbMediaUrl(path: string): string {
  return `${API_CONFIG.ADMIN.BASE_URL}/kb/media?path=${encodeURIComponent(path)}`;
}
