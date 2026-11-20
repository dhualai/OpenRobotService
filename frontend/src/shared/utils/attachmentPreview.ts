/**
 * 附件在线预览类型：讨论区 / 工单附件共用。
 * txt、md、clipboard(N行).txt 必须走文本预览，不能落到「暂不支持」。
 */

export type AttachmentPreviewKind =
  | 'image'
  | 'video'
  | 'pdf'
  | 'office'
  | 'md'
  | 'text'
  | 'other';

const IMAGE_EXTS = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg'];
const VIDEO_EXTS = ['mp4', 'webm', 'ogg', 'mov', 'm4v'];
const OFFICE_EXTS = ['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'];
const TEXT_EXTS = ['txt', 'log', 'csv', 'json', 'xml', 'yaml', 'yml', 'ini', 'conf', 'text'];
const MD_EXTS = ['md', 'markdown'];

export function decodePathSegment(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

export function basenameOf(path: string): string {
  const noQuery = (path || '').split('?')[0];
  const base = noQuery.split('/').pop() || noQuery;
  return decodePathSegment(base);
}

export function extOf(name: string): string {
  const base = basenameOf(name);
  const i = base.lastIndexOf('.');
  if (i <= 0) return '';
  return base.slice(i + 1).toLowerCase();
}

/** 文件名或代理 URL → 预览类型。clipboard 引用无论有无扩展名都按正文打开。 */
export function attachmentPreviewKind(filename: string, url = ''): AttachmentPreviewKind {
  const names = [filename, basenameOf(filename), basenameOf(url)].filter(Boolean);
  for (const n of names) {
    const ext = extOf(n);
    if (IMAGE_EXTS.includes(ext)) return 'image';
    if (VIDEO_EXTS.includes(ext)) return 'video';
    if (ext === 'pdf') return 'pdf';
    if (OFFICE_EXTS.includes(ext)) return 'office';
    if (MD_EXTS.includes(ext)) return 'md';
    if (TEXT_EXTS.includes(ext)) return 'text';
    if (/clipboard/i.test(n)) return 'text';
  }
  return 'other';
}

export function encodePathSegment(s: string): string {
  // encodeURIComponent 不编码括号；markdown 链接和部分代理会在 ) 处截断路径。
  return encodeURIComponent(s).replace(/\(/g, '%28').replace(/\)/g, '%29');
}

/** 评论附件代理 URL：按段编码，避免中文和括号截断路径。 */
export function commentFileProxyUrl(baseUrl: string, objectPath: string): string {
  const encoded = objectPath.split('/').filter(Boolean).map(encodePathSegment).join('/');
  return `${baseUrl}/files/${encoded}`;
}
