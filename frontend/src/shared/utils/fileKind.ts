/**
 * 问题文档相关文件类型口径（编辑器 / 提单字段共用）。
 * 与后端 spec_doc_parser 白名单、spec_doc /spec-doc/image 图片白名单保持一致。
 */

/** 文档解析支持的扩展名（input accept 语法） */
export const SPEC_DOC_ACCEPT = '.md,.markdown,.txt,.doc,.docx';

/** 文档大小上限（MB），与后端 MAX_DOC_SIZE 一致 */
export const SPEC_DOC_FILE_MAX_MB = 5;

/** 图片扩展名白名单（与后端 _IMAGE_EXTS 一致） */
const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp']);

/** 编辑器插入图片的 input accept */
export const SPEC_DOC_IMAGE_ACCEPT = 'image/png,image/jpeg,image/gif,image/webp,image/bmp';

/** 按扩展名（或 MIME）判断是否白名单图片 */
export function isImageFile(file: File): boolean {
  const name = (file.name || '').toLowerCase();
  const dot = name.lastIndexOf('.');
  if (dot > 0 && IMAGE_EXTS.has(name.slice(dot + 1))) return true;
  return (file.type || '').startsWith('image/');
}
