/**
 * ImageLightbox —— 通用图片预览灯箱（双指缩放 / 双击缩放 / 拖拽平移 + 复制 + 下载）
 *
 * 设计目标：
 *  - 点击聊天/文档里的图片 → 全屏预览，移动端 / PC 端均可用
 *  - 交互层复用 tdesign-mobile-react 的 ImageViewer（成熟的移动端方案：
 *    双指缩放、双击缩放、缩放后拖拽平移、单击/遮罩/关闭按钮退出），
 *    不再自研手势逻辑
 *  - 自定义工具条保留「复制」「下载」；复制走 ClipboardItem（需安全上下文），
 *    不支持时降级提示「长按保存」
 *  - 微信内置浏览器「长按 → 保存图片」可用，前提是 src 为真实 http(s)/同域 URL；
 *    blob: / data: URL 微信无法二次下载，会提示保存失败（调用方需保证这一点）
 *  - 通过 Portal 挂载到 body，避免被父级 overflow 裁剪
 *
 * 适用场景：MarkdownRenderer 中 AI 回复图片、ChatPanel 用户气泡图片等。
 */
import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { ImageViewer } from 'tdesign-mobile-react';

interface ImageLightboxProps {
  /** 最终展示地址：真实 http(s)/同域地址（推荐，微信可长按保存）或 blob:/data URI。open 为 true 时必然有值 */
  src?: string;
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
    if (seg && /\.\w{2,5}$/.test(seg)) return decodeURIComponent(seg);
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
      const target = src ?? '';
      const res = await fetch(target, {
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
    a.download = deriveFilename(src ?? '', alt);
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
    <>
      {/* 交互主体：双指缩放 / 双击缩放 / 拖拽平移 / 单击关闭（TDesign ImageViewer） */}
      <ImageViewer
        images={[src ?? '']}
        visible={open}
        maxZoom={3}
        onClose={() => onClose()}
      />
      {/* 自定义工具条：左上角关闭 + 复制 / 下载（关闭按钮自绘，直接调 onClose，避免被工具栏覆盖 / 受控失效） */}
      <div className="img-lightbox__toolbar">
        <button type="button" className="img-lightbox__btn img-lightbox__close" onClick={onClose} aria-label="关闭">
          ✕
        </button>
        <button type="button" className="img-lightbox__btn" onClick={handleCopy}>
          复制
        </button>
        <button type="button" className="img-lightbox__btn" onClick={handleDownload}>
          下载
        </button>
      </div>
      {feedback && <div className="img-lightbox__feedback">{feedback}</div>}
    </>,
    document.body,
  );
}
