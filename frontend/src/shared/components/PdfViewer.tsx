import React, { useEffect, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

// 全局只需配置一次 worker；使用 ?url 让 Vite 在构建期产出同源 worker，避免跨域/路径问题。
pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

/**
 * PDF 前端预览（pdf.js）：把 PDF 每页渲染到 canvas，全端（含微信 WebView）可用。
 * 用 canvas 渲染绕开浏览器/微信原生 PDF 查看器的兼容性差异——微信内置 WebView
 * 不支持内嵌 iframe 渲染 PDF，但 canvas 支持良好，因此 H5 内即可预览。
 */
export default function PdfViewer({ url, name }: { url: string; name: string }) {
  const [numPages, setNumPages] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [scale, setScale] = useState(1.3);
  const [pdf, setPdf] = useState<pdfjsLib.PDFDocumentProxy | null>(null);
  const canvasRefs = useRef<(HTMLCanvasElement | null)[]>([]);

  // 1) 加载 PDF 文档
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    setNumPages(0);
    setPdf(null);
    const task = pdfjsLib.getDocument(url);
    task.promise
      .then((doc) => {
        if (cancelled) {
          doc.destroy();
          return;
        }
        setPdf(doc);
        setNumPages(doc.numPages);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        setLoading(false);
      });
    return () => {
      cancelled = true;
      task.destroy();
    };
  }, [url]);

  // 2) 渲染全部页到 canvas（按 DPR 高清 + 适配宽度）
  useEffect(() => {
    if (!pdf) return;
    let cancelled = false;
    const outputScale = Math.min(window.devicePixelRatio || 1, 2);
    canvasRefs.current = [];
    (async () => {
      for (let i = 1; i <= pdf.numPages; i++) {
        if (cancelled) return;
        const canvas = canvasRefs.current[i - 1];
        if (!canvas) continue;
        const ctx = canvas.getContext('2d');
        if (!ctx) continue;
        try {
          const page = await pdf.getPage(i);
          const viewport = page.getViewport({ scale: scale * outputScale });
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.style.width = `${Math.floor(viewport.width / outputScale)}px`;
          canvas.style.height = `${Math.floor(viewport.height / outputScale)}px`;
          await page.render({ canvas, canvasContext: ctx, viewport }).promise;
        } catch (e) {
          if (!cancelled) console.warn('[PdfViewer] 渲染第', i, '页失败:', e);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pdf, scale]);

  return (
    <div className="pdf-viewer">
      <div className="pdf-viewer__toolbar">
        <span className="pdf-viewer__title" title={name}>
          {name}
        </span>
        <span className="pdf-viewer__meta">{loading ? '加载中…' : `共 ${numPages} 页`}</span>
        <div className="pdf-viewer__zoom">
          <button
            type="button"
            onClick={() => setScale((s) => Math.max(0.5, +(s - 0.2).toFixed(2)))}
            aria-label="缩小"
          >
            －
          </button>
          <span>{Math.round(scale * 100)}%</span>
          <button
            type="button"
            onClick={() => setScale((s) => Math.min(3, +(s + 0.2).toFixed(2)))}
            aria-label="放大"
          >
            ＋
          </button>
        </div>
      </div>
      <div className="pdf-viewer__scroll">
        {error ? (
          <div className="attachment-viewer__hint attachment-viewer__hint--error">
            PDF 加载失败：{error}
          </div>
        ) : (
          Array.from({ length: numPages }, (_, i) => (
            <canvas key={i} ref={(el) => { canvasRefs.current[i] = el; }} className="pdf-viewer__page" />
          ))
        )}
        {loading && !error && <div className="attachment-viewer__hint">PDF 渲染中…</div>}
      </div>
    </div>
  );
}
