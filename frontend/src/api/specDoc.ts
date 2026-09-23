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

// ---------------------------------------------------------------------------
// 内嵌 base64 图片外置 / 折叠
// ---------------------------------------------------------------------------
// 背景：md 正文里内嵌 base64 图片（Word 导入降级、历史数据）会把正文撑到百 KB 级
// 并形成超长单行。编辑器（@uiw/react-md-editor）挂载时用 Prism/refractor 做语法
// 高亮，其 setext 标题正则在「超长单行 + == 结尾」形态下是 O(n²) 灾难性回溯，
// 可阻塞主线程数十秒（工单 836 实测 81s，页面完全无法交互）。
// 治理：打开编辑器前把大图上传为在线图片（正文降到 KB 级）；
// 上传失败时折叠超大 data URI 兜底，保证编辑器永不因高亮卡死。

/** 正文里的 base64 内联图片（data:image/xxx;base64,<data>） */
const INLINE_IMAGE_RE = /data:(image\/[a-z0-9.+-]+);base64,([A-Za-z0-9+/=]+)/gi;

/** 外置阈值：小于该字节数的内联图保持原样（图标类小图不值得多开请求） */
export const INLINE_IMAGE_EXTERNALIZE_MIN = 8 * 1024;

/** 折叠阈值：外置失败且超过该体积的 data URI 会被折叠（避免高亮回溯卡死页面） */
export const INLINE_IMAGE_FOLD_LIMIT = 64 * 1024;

export interface ExternalizeInlineResult {
  content: string;
  /** 成功外置（替换为在线 URL）的数量 */
  replaced: number;
  /** 上传失败、保持内联的数量 */
  failed: number;
}

/** base64 → File（供上传内联图片用） */
function base64ToImageFile(b64: string, mime: string): File {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
  const ext = (mime.split('/')[1] || 'png').replace(/[^a-z0-9]/gi, '') || 'png';
  return new File([bytes], `inline.${ext}`, { type: mime });
}

/**
 * 把正文里体积超阈值的 base64 内联图片上传为在线图片（POST /spec-doc/image），
 * 并原地替换为代理 URL。上传失败的单张保持内联（由调用方决定是否折叠兜底）。
 */
export async function externalizeInlineImages(
  md: string,
  minBytes: number = INLINE_IMAGE_EXTERNALIZE_MIN,
): Promise<ExternalizeInlineResult> {
  if (!md || !md.includes('data:image')) return { content: md, replaced: 0, failed: 0 };

  const targets = Array.from(md.matchAll(INLINE_IMAGE_RE)).filter(
    (m) => m[2].length > minBytes,
  );
  if (!targets.length) return { content: md, replaced: 0, failed: 0 };

  let content = md;
  let replaced = 0;
  let failed = 0;
  for (const m of targets) {
    const raw = m[0];
    if (!content.includes(raw)) continue;
    try {
      const url = await uploadSpecDocImage(base64ToImageFile(m[2], m[1]));
      content = content.replace(raw, url);
      replaced += 1;
    } catch {
      failed += 1;
    }
  }
  return { content, replaced, failed };
}

export interface FoldInlineResult {
  content: string;
  folded: number;
}

/**
 * 折叠兜底：把仍超过 limit 的 data URI 替换为占位图片标记。
 * 仅在「外置失败」时使用——宁可让用户重新插入图片，也不能让页面卡死。
 */
export function foldHugeInlineImages(
  md: string,
  limit: number = INLINE_IMAGE_FOLD_LIMIT,
): FoldInlineResult {
  if (!md || !md.includes('data:image')) return { content: md, folded: 0 };
  let folded = 0;
  const content = md.replace(INLINE_IMAGE_RE, (raw) => {
    if (raw.length <= limit) return raw;
    folded += 1;
    return 'data:image/png;base64,iVBORw0KGgo='; // 1x1 透明 PNG 占位（保持 markdown 图片语法可用）
  });
  return { content, folded };
}
