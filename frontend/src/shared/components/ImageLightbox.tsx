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
  // 图片预加载就绪标记：ImageViewer 的 mask 是半透明（rgba 0,0,0,0.6），挂载时若 <img> 未
  // 就绪会先透出底层页面再跳出图片 → 闪烁。这里先用 new Image() + decode() 预加载，解码
  // 完成后再挂载 ImageViewer（缓存图片 decode 近乎瞬时，挂载后 <img> 同步从缓存渲染，无闪烁）。
  // 预载期间不渲染任何层（点击即显，无 loading 遮罩）；非缓存图片加超时兜底避免久等。
  const [imgReady, setImgReady] = useState(false);

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
      setImgReady(false);
      setBlobUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
    }
  }, [open]);

  // 预加载图片：open 且 src 有值时触发，解码就绪后置 imgReady=true。
  // 缓存图片 decode 近乎瞬时（loading 一闪即过）；非缓存图片则持续显示 loading 直至就绪，
  // 加载失败（onerror）也放行，由 ImageViewer 显示裂图。移除强制超时：避免未就绪就挂载造成闪烁。
  useEffect(() => {
    if (!open || !src) {
      setImgReady(false);
      return;
    }
    setImgReady(false);
    let cancelled = false;
    const img = new Image();
    const done = () => { if (!cancelled) setImgReady(true); };
    img.onload = done;
    img.onerror = done; // 加载失败也放行，由 ImageViewer 显示裂图
    img.src = src;
    // decode 确保解码完成（缓存图片近乎瞬时），进一步降低挂载后渲染延迟
    if (img.decode) {
      img.decode().then(done).catch(done);
    }
    return () => { cancelled = true; };
  }, [open, src]);

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
      {/* 图片未加载/未解码完成时显示 loading 遮罩（纯黑 + 转圈），就绪后切换到 ImageViewer。
          缓存图片 decode 近乎瞬时（loading 几乎不可见）；非缓存图片显示 loading 直到就绪。 */}
      {!imgReady && (
        <div className="img-lightbox__loading" role="status" aria-label="图片加载中" onClick={onClose}>
          <span className="img-lightbox__loading-spinner" />
        </div>
      )}
      {/* 图片预加载就绪后才挂载 ImageViewer + 工具栏：避免半透明 mask 先透出底层、图片后跳出导致的闪烁 */}
      {imgReady && (
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
        </>
      )}
      {feedback && <div className="img-lightbox__feedback">{feedback}</div>}
    </>,
    document.body,
  );
}
