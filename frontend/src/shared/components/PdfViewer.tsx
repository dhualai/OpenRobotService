import React, { useEffect, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
// 用 Vite 的 ?worker 导入：Vite 会把 worker 及其内部 import 依赖一起打包，
// 并以 module worker 正确实例化。这比 ?url 更稳——?url 只复制单个文件，
// 而 pdf.worker.min.mjs 内部有 import，复制后依赖 404 会导致 worker 起不来（白屏）。
import PdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?worker';

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
  // 每次加载都新建一个独立 worker 并设置到 GlobalWorkerOptions.workerPort，
  // 用完（task 彻底销毁后）再 terminate。这样可彻底避免「共享单个 worker +
  // 异步 destroy 期间 _pendingDestroy 竞态」导致的 "worker is being destroyed" 错误
  //（React 严格模式双挂载、错误边界重建、快速重开都会触发该竞态）。
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    setNumPages(0);
    setPdf(null);

    const worker = new PdfWorker();
    pdfjsLib.GlobalWorkerOptions.workerPort = worker;
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
      // task.destroy() 内部异步：先置 _pendingDestroy，再 await 传输层销毁。
      // 必须等它彻底结束后再 terminate 本 worker，否则会中断仍在进行中的消息。
      task
        .destroy()
        .catch(() => {})
        .finally(() => {
          try {
            worker.terminate();
          } catch {
            /* noop */
          }
        });
    };
  }, [url]);

  // 2) 渲染全部页到 canvas（按 DPR 高清 + 适配宽度）；任一一页失败则给出可见提示
  useEffect(() => {
    if (!pdf) return;
    let cancelled = false;
    const outputScale = Math.min(window.devicePixelRatio || 1, 2);
    // 注意：不要重置 canvasRefs，ref 回调在 commit 阶段已填好，清空会导致读取为 undefined 而整篇白屏
    let done = 0;
    let failed = 0;
    const checkFinish = () => {
      if (done + failed < pdf.numPages) return;
      if (!cancelled) {
        if (done === 0 && pdf.numPages > 0) {
          setError('PDF 渲染失败（worker 未就绪或文件损坏），请在浏览器打开下载。');
        }
        setLoading(false);
      }
    };
    (async () => {
      for (let i = 1; i <= pdf.numPages; i++) {
        if (cancelled) return;
        const canvas = canvasRefs.current[i - 1];
        if (!canvas) {
          failed += 1;
          checkFinish();
          continue;
        }
        const ctx = canvas.getContext('2d');
        if (!ctx) {
          failed += 1;
          checkFinish();
          continue;
        }
        try {
          const page = await pdf.getPage(i);
          const viewport = page.getViewport({ scale: scale * outputScale });
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.style.width = `${Math.floor(viewport.width / outputScale)}px`;
          canvas.style.height = `${Math.floor(viewport.height / outputScale)}px`;
          await page.render({ canvas, canvasContext: ctx, viewport }).promise;
          done += 1;
        } catch (e) {
          console.warn('[PdfViewer] 渲染第', i, '页失败:', e);
          failed += 1;
        }
        checkFinish();
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
            {error}
          </div>
        ) : (
          Array.from({ length: numPages }, (_, i) => (
            <canvas
              key={i}
              ref={(el) => {
                canvasRefs.current[i] = el;
              }}
              className="pdf-viewer__page"
            />
          ))
        )}
        {loading && !error && <div className="attachment-viewer__hint">PDF 渲染中…</div>}
      </div>
    </div>
  );
}
