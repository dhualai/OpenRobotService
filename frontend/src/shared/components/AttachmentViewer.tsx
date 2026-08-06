import React, { useEffect, useState, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import ImageLightbox from './ImageLightbox';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { setupWechatFilePreview } from '@/shared/utils/wechatJsSdk';

export interface AttachmentViewItem {
  filename: string;
  size?: number;
  /** 内联预览地址（/api/tasks/files/...，Content-Disposition: inline），用于图片 / PDF 展示与 MD 文本拉取 */
  previewUrl: string;
  /** 下载地址（可带鉴权 token），点击「下载」时打开 */
  downloadUrl: string;
}

const IMAGE_EXTS = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg'];
const OFFICE_EXTS = ['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'];

function extOf(name: string): string {
  const i = name.lastIndexOf('.');
  return i >= 0 ? name.slice(i + 1).toLowerCase() : '';
}

type Kind = 'image' | 'pdf' | 'office' | 'md' | 'other';

function kindOf(name: string): Kind {
  const ext = extOf(name);
  if (IMAGE_EXTS.includes(ext)) return 'image';
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

  // 微信内：图片/PDF/Office 改走 JS-SDK 原生预览（wx.previewImage / wx.previewFile）；
  // 调起成功则由微信接管并关闭本 H5 弹窗，失败回退下方 H5 预览（已内置「在浏览器打开」提示）。
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);

  const [wxHandled, setWxHandled] = useState(false);
  const wxPreviewable = kind === 'image' || kind === 'pdf' || kind === 'office';

  useEffect(() => {
    setWxHandled(false);
    if (item && wxPreviewable && isWeChat() && window.wx) {
      let cancelled = false;
      const absUrl = item.previewUrl.startsWith('http')
        ? item.previewUrl
        : `${window.location.origin}${item.previewUrl}`;
      setupWechatFilePreview({
        kind: kind as 'image' | 'pdf' | 'office',
        url: absUrl,
        name: item.filename,
        size: item.size,
      }).then((ok) => {
        if (ok && !cancelled) {
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

  // 图片：使用全屏灯箱（自带缩放 / 长按照片保存 / 下载）
  if (kind === 'image') {
    return <ImageLightbox src={item.previewUrl} alt={item.filename} open onClose={onClose} />;
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
              onClick={() => window.open(item.downloadUrl, '_blank', 'noopener,noreferrer')}
            >
              下载
            </button>
            <button type="button" className="attachment-viewer__close" onClick={onClose} aria-label="关闭">
              ✕
            </button>
          </div>
        </div>
        <div className="attachment-viewer__body">
          {kind === 'pdf' && <iframe className="attachment-viewer__frame" src={item.previewUrl} title={item.filename} />}
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
              请点击右上角「下载」后在本地打开。
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
