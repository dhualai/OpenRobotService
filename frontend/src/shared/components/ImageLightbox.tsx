/**
 * ImageLightbox —— 通用图片预览灯箱（点击放大查看 + 复制 + 下载）
 *
 * 设计目标：
 *  - 点击聊天/文档里的图片 → 全屏遮罩放大预览，移动端 / PC 端均可用
 *  - 提供「复制」「下载」按钮；复制走 ClipboardItem（需安全上下文），
 *    不支持时降级提示「长按保存」
 *  - 依赖零外部 UI 库，通过 Portal 挂载到 body，避免被父级 overflow 裁剪
 *
 * 适用场景：MarkdownRenderer 中 AI 回复图片、ChatPanel 用户气泡图片等。
 */
import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';

interface ImageLightboxProps {
  /** 最终展示地址：blob:（已鉴权）或外部绝对地址 / 同域相对地址 / data URI */
  src: string;
  alt?: string;
  open: boolean;
  onClose: () => void;
}

/** 从地址推导下载文件名（兜底用 alt 或 image） */
function deriveFilename(src: string, alt?: string): string {
  try {
    if (src.startsWith('data:')) return `${alt || 'image'}.svg`;
    const url = new URL(src, window.location.href);
    const seg = url.pathname.split('/').filter(Boolean).pop();
    if (seg && /\.\w{2,5}$/.test(seg)) return seg;
  } catch {
    /* 解析失败忽略 */
  }
  return `${alt || 'image'}.png`;
}

export default function ImageLightbox({ src, alt, open, onClose }: ImageLightboxProps) {
  const [feedback, setFeedback] = useState('');
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  // ESC 关闭 + 锁定背景滚动
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  // 关闭时清理临时 blob 与提示
  useEffect(() => {
    if (!open) {
      setFeedback('');
      setBlobUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
    }
  }, [open]);

  const flash = useCallback((msg: string) => {
    setFeedback(msg);
    window.setTimeout(() => setFeedback(''), 2000);
  }, []);

  /** 取回 Blob：blob: 直接 fetch 回原 blob；其余带凭据请求（同域鉴权图） */
  const getBlob = useCallback(async (): Promise<Blob | null> => {
    try {
      const res = await fetch(src.startsWith('blob:') ? src : src, {
        credentials: 'include',
      });
      if (!res.ok) return null;
      return await res.blob();
    } catch {
      return null;
    }
  }, [src]);

  const handleDownload = useCallback(async () => {
    let url = blobUrl;
    if (!url) {
      const blob = await getBlob();
      if (!blob) {
        flash('下载失败：无法获取图片');
        return;
      }
      url = URL.createObjectURL(blob);
      setBlobUrl(url);
    }
    const a = document.createElement('a');
    a.href = url;
    a.download = deriveFilename(src, alt);
    document.body.appendChild(a);
    a.click();
    a.remove();
    flash('已开始下载');
  }, [blobUrl, getBlob, src, alt, flash]);

  const handleCopy = useCallback(async () => {
    // 复制需 ClipboardItem + 安全上下文（https / localhost）；手机经 IP(http) 不可用
    const ClipboardItemCtor = (window as unknown as { ClipboardItem?: typeof ClipboardItem }).ClipboardItem;
    if (!navigator.clipboard || !ClipboardItemCtor || !window.isSecureContext) {
      flash('当前环境不支持复制，请长按图片保存');
      return;
    }
    let blob: Blob | null = blobUrl ? await (await fetch(blobUrl)).blob() : null;
    if (!blob) {
      blob = await getBlob();
      if (blob && !blobUrl) setBlobUrl(URL.createObjectURL(blob));
    }
    if (!blob) {
      flash('复制失败：无法获取图片');
      return;
    }
    try {
      await navigator.clipboard.write([
        new ClipboardItemCtor({ [blob.type || 'image/png']: blob }),
      ]);
      flash('图片已复制');
    } catch {
      flash('复制失败：浏览器限制或跨域');
    }
  }, [blobUrl, getBlob, flash]);

  if (!open) return null;

  return createPortal(
    <div className="img-lightbox" onClick={onClose} role="dialog" aria-modal="true">
      <div className="img-lightbox__toolbar" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="img-lightbox__btn" onClick={handleCopy}>
          复制
        </button>
        <button type="button" className="img-lightbox__btn" onClick={handleDownload}>
          下载
        </button>
        <button
          type="button"
          className="img-lightbox__btn img-lightbox__btn--close"
          onClick={onClose}
          aria-label="关闭"
        >
          ✕
        </button>
      </div>
      <img
        className="img-lightbox__img"
        src={src}
        alt={alt || '图片'}
        onClick={(e) => e.stopPropagation()}
      />
      {feedback && <div className="img-lightbox__feedback">{feedback}</div>}
    </div>,
    document.body,
  );
}
