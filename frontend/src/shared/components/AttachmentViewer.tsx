import React, { useEffect, useState, useCallback, useRef, lazy, Suspense } from 'react';
import { createPortal } from 'react-dom';
import ImageLightbox from './ImageLightbox';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { setupWechatFilePreview } from '@/shared/utils/wechatJsSdk';
// pdf.js 体积大（主库 + worker 约 1.5MB），懒加载：仅在用户真正点开 PDF 附件时才下载，
// 避免随 AttachmentViewer 被多路由静态引入而进入首屏 bundle。
const PdfViewer = lazy(() => import('./PdfViewer'));

export interface AttachmentViewItem {
  filename: string;
  size?: number;
  /** 内联预览地址（/api/tasks/files/...，Content-Disposition: inline），用于图片 / PDF 展示与 MD 文本拉取 */
  previewUrl: string;
  /** 下载地址（可带鉴权 token），点击「下载」时打开 */
  downloadUrl: string;
}

const IMAGE_EXTS = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg'];
const VIDEO_EXTS = ['mp4', 'webm', 'ogg', 'mov', 'm4v'];
const OFFICE_EXTS = ['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'];

function extOf(name: string): string {
  const i = name.lastIndexOf('.');
  return i >= 0 ? name.slice(i + 1).toLowerCase() : '';
}

type Kind = 'image' | 'video' | 'pdf' | 'office' | 'md' | 'other';

function kindOf(name: string): Kind {
  const ext = extOf(name);
  if (IMAGE_EXTS.includes(ext)) return 'image';
  if (VIDEO_EXTS.includes(ext)) return 'video';
  if (ext === 'pdf') return 'pdf';
  if (OFFICE_EXTS.includes(ext)) return 'office';
  if (ext === 'md' || ext === 'markdown') return 'md';
  return 'other';
}

function formatSize(bytes?: number): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * 给下载 URL 追加 response-content-disposition=attachment 参数，强制浏览器下载而非内联播放。
 * MinIO / S3 兼容的代理端支持该 query 参数；对已带 query 的 URL 用 & 拼接。
 * 对非代理直链（无该参数支持）无害——浏览器忽略未知 query。
 */
function withAttachmentDisposition(url: string): string {
  if (!url) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}response-content-disposition=attachment%3B%20filename%3D${encodeURIComponent('download')}`;
}

function isWeChat(): boolean {
  return typeof navigator !== 'undefined' && /MicroMessenger/i.test(navigator.userAgent);
}

/**
 * 统一的附件预览组件：覆盖图片（灯箱）、PDF（内联 iframe）、Markdown（渲染）及其它格式。
 * 同时提供「下载」入口，并在微信环境下给出操作提示。
 */
export default function AttachmentViewer({ item, onClose }: { item: AttachmentViewItem | null; onClose: () => void }) {
  const [mdText, setMdText] = useState('');
  const [mdLoading, setMdLoading] = useState(false);
  const [mdError, setMdError] = useState('');

  const kind: Kind = item ? kindOf(item.filename) : 'other';

  const loadMd = useCallback(async (url: string) => {
    setMdLoading(true);
    setMdError('');
    setMdText('');
    try {
      const res = await fetch(url, { credentials: 'include' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setMdText(await res.text());
    } catch (e) {
      setMdError(e instanceof Error ? e.message : String(e));
    } finally {
      setMdLoading(false);
    }
  }, []);

  useEffect(() => {
    if (item && kind === 'md') loadMd(item.previewUrl);
    else {
      setMdText('');
      setMdError('');
      setMdLoading(false);
    }
  }, [item, kind, loadMd]);

  // 微信内：图片/Office 改走 JS-SDK 原生预览（wx.previewImage / wx.previewFile）；
  // PDF 不走原生——微信内置文档查看器（wx.previewFile）兼容性差，改由前端 pdf.js 在 H5 内
  // 渲染（canvas 在微信 WebView 支持良好），保证微信端也能直接预览。
  // 调起成功则由微信接管并关闭本 H5 弹窗，失败回退下方 H5 预览（已内置「在浏览器打开」提示）。
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);

  const [wxHandled, setWxHandled] = useState(false);
  // 微信原生预览调起期间（异步）标记：此时若直接渲染 H5 灯箱会先闪现「带复制下载的灯箱」，
  // 待微信接管后灯箱才消失——表现为「灯箱闪一下再显示原生预览」。故该期间不渲染 H5 弹窗，
  // 调起成功后由微信接管（wxHandled），失败才回退 H5 灯箱。
  const [wxPending, setWxPending] = useState(false);
  const wxPreviewable = kind === 'image' || kind === 'office';

  useEffect(() => {
    setWxHandled(false);
    setWxPending(false);
    if (item && wxPreviewable && isWeChat() && window.wx) {
      let cancelled = false;
      setWxPending(true);
      const absUrl = item.previewUrl.startsWith('http')
        ? item.previewUrl
        : `${window.location.origin}${item.previewUrl}`;
      setupWechatFilePreview({
        kind: kind as 'image' | 'pdf' | 'office',
        url: absUrl,
        name: item.filename,
        size: item.size,
      }).then((ok) => {
        if (cancelled) return;
        setWxPending(false);
        if (ok) {
          setWxHandled(true);
          onCloseRef.current();
        }
      });
      return () => { cancelled = true; };
    }
  }, [item, kind, wxPreviewable]);

  if (!item) return null;
  // 微信原生预览已接管，无需再渲染 H5 弹窗
  if (wxHandled) return null;
  // 微信原生预览调起中：暂不渲染 H5 灯箱，避免「灯箱闪现后切原生预览」的闪烁
  if (wxPending) return null;

  // 图片：使用全屏灯箱（自带缩放 / 长按照片保存 / 下载）
  if (kind === 'image') {
    return <ImageLightbox src={item.previewUrl} alt={item.filename} open onClose={onClose} />;
  }

  // 视频：在 H5 弹窗内内联播放（带 controls + 关闭按钮），避免浏览器原生播放页无关闭入口（#389）
  if (kind === 'video') {
    return createPortal(
      <div className="attachment-viewer" onClick={onClose}>
        <div className="attachment-viewer__panel attachment-viewer__panel--video" onClick={(e) => e.stopPropagation()}>
          <div className="attachment-viewer__bar">
            <span className="attachment-viewer__name" title={item.filename}>
              {item.filename}
            </span>
            <div className="attachment-viewer__actions">
              <button
                type="button"
                className="attachment-viewer__dl"
                onClick={() => {
                  // 视频下载：downloadUrl 追加 attachment 头参数，强制浏览器下载而非播放
                  const dl = withAttachmentDisposition(item.downloadUrl);
                  if (wechat) {
                    window.location.href = dl;
                  } else {
                    window.open(dl, '_blank', 'noopener,noreferrer');
                  }
                }}
              >
                下载
              </button>
              <button type="button" className="attachment-viewer__close" onClick={onClose} aria-label="关闭">
                ✕
              </button>
            </div>
          </div>
          <div className="attachment-viewer__body">
            <video
              src={item.previewUrl}
              controls
              playsInline
              preload="metadata"
              className="attachment-viewer__video"
            />
          </div>
        </div>
      </div>,
      document.body,
    );
  }

  const wechat = isWeChat();

  return createPortal(
    <div className="attachment-viewer" onClick={onClose}>
      <div className="attachment-viewer__panel" onClick={(e) => e.stopPropagation()}>
        <div className="attachment-viewer__bar">
          <span className="attachment-viewer__name" title={item.filename}>
            {item.filename}
          </span>
          <div className="attachment-viewer__actions">
            <button
              type="button"
              className="attachment-viewer__dl"
              onClick={() => {
                // 强制下载（视频尤其需要，否则浏览器直接内联播放）
                const dl = withAttachmentDisposition(item.downloadUrl);
                if (wechat) {
                  // 微信内置 WebView 无法直接下载文件：把当前页跳到绝对下载地址，
                  // 微信会弹出「在浏览器打开」横幅，用户在系统浏览器中即可直接下载
                  // （downloadUrl 已携带 token，浏览器打开不会落到 SPA 404 → 微信 OAuth 重定向）。
                  window.location.href = dl;
                } else {
                  window.open(dl, '_blank', 'noopener,noreferrer');
                }
              }}
            >
              下载
            </button>
            <button type="button" className="attachment-viewer__close" onClick={onClose} aria-label="关闭">
              ✕
            </button>
          </div>
        </div>
        <div className="attachment-viewer__body">
          {kind === 'pdf' && (
            <Suspense fallback={<div className="attachment-viewer__hint">PDF 预览加载中…</div>}>
              <PdfViewer url={item.previewUrl} name={item.filename} />
            </Suspense>
          )}
          {kind === 'md' &&
            (mdLoading ? (
              <div className="attachment-viewer__hint">加载中…</div>
            ) : mdError ? (
              <div className="attachment-viewer__hint attachment-viewer__hint--error">预览失败：{mdError}</div>
            ) : (
              <div className="markdown-body md-content attachment-viewer__md">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{mdText}</ReactMarkdown>
              </div>
            ))}
          {(kind === 'other' || kind === 'office') && (
            <div className="attachment-viewer__hint">
              该文件格式暂不支持在线预览。
              <br />
              {wechat
                ? '请点击右上角「···」→「在浏览器中打开」，在浏览器中点击「下载」即可保存文件。'
                : '请点击「下载」后在本地打开。'}
            </div>
          )}
          {kind !== 'other' && kind !== 'office' && wechat && (
            <div className="attachment-viewer__wechat">
              微信内如无法预览，请点击右上角「···」选择「在浏览器打开」
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
