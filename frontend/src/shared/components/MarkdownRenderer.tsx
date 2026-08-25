/**
 * MarkdownRenderer —— AI 流式返回内容的 Markdown 解析 + 媒体渲染组件
 *
 * 功能：
 *  - GitHub Flavored Markdown（表格/任务列表/删除线/链接/代码块）
 *  - 图片 URL 自动识别渲染（支持后端 API 鉴权图片、外部图片直链）
 *  - 视频 URL 自动识别渲染
 *  - XSS 防护（react-markdown 输出 React 元素，天然防 XSS）
 *  - 多层安全兜底（Error Boundary + 简易 Markdown 渲染）
 *
 * 注意：不使用 DOMPurify 清洗 markdown 源码 —— react-markdown 输出的是
 * React 虚拟 DOM（非 raw HTML），不存在 XSS 注入路径。
 */
import { Component, useMemo, useState, useCallback, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { urlTransformAllowDataImage } from '@/shared/utils/markdown';
import { useAuthStore } from '@/stores/auth';
import { ENV_PREFIX } from '@/config/api';
import ImageLightbox from '@/shared/components/ImageLightbox';

// ---------------------------------------------------------------------------
// 媒体 URL 检测正则
// ---------------------------------------------------------------------------

/** 已知图片扩展名 */
const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp|svg|bmp|ico)(\?.*)?$/i;
/** 视频扩展名 */
const VIDEO_EXT_RE = /\.(mp4|webm|ogg|mov|avi|mkv|flv|wmv)(\?.*)?$/i;
/** 已知图片 MIME-type Content-Type 前缀 */
const IMAGE_MIME_RE = /^image\//;
/** 已知视频 MIME-type Content-Type 前缀 */
const VIDEO_MIME_RE = /^video\//;

// ---------------------------------------------------------------------------
// 占位图重写：国内不可达的占位图服务 → 本地 SVG（data URI）
// ---------------------------------------------------------------------------
// AI 回复里的占位图（via.placeholder.com 等）在国内基本打不开，且这类 URL 是
// LLM 即时生成的、不可预测，无法在后端/模板里预先替换。这里在渲染前把已知的
// 占位图域名重写为等价的本地 SVG，无网络依赖、必定可见，并保留尺寸与文字。

/** 已知国内不可达的占位图服务域名 */
const PLACEHOLDER_HOST_RE = /\/\/(?:via\.placeholder\.com|placehold\.(?:it|co)|dummyimage\.com)\//i;

function escapeXml(s: string): string {
  return s.replace(/[<>&'"]/g, (c) =>
    c === '<' ? '&lt;' : c === '>' ? '&gt;' : c === '&' ? '&amp;' : c === "'" ? '&apos;' : '&quot;',
  );
}

/** 生成内联 SVG 占位图的 data URI（base64，规避 markdown destination 对 `)` 的截断） */
function svgPlaceholderDataUri(w: number, h: number, text: string): string {
  const fontSize = Math.max(12, Math.round(Math.min(w, h) / 8));
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">` +
    `<rect width="${w}" height="${h}" fill="#eef1f5"/>` +
    `<rect x="0.5" y="0.5" width="${w - 1}" height="${h - 1}" fill="none" stroke="#c9ced6"/>` +
    `<text x="50%" y="50%" font-family="-apple-system,'PingFang SC','Microsoft YaHei',sans-serif" font-size="${fontSize}" fill="#7a8294" text-anchor="middle" dominant-baseline="middle">${escapeXml(text)}</text>` +
    `</svg>`;
  // TextEncoder → btoa，对中文安全（btoa 直接处理 UTF-8 字符串会抛错）
  const bytes = new TextEncoder().encode(svg);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return `data:image/svg+xml;base64,${btoa(binary)}`;
}

/** 把已知的国内不可达占位图 URL 重写为本地 SVG data URI；其余 URL 原样返回 */
function rewritePlaceholder(src: string): string {
  if (!src || !PLACEHOLDER_HOST_RE.test(src)) return src;
  const sizeMatch = src.match(/\/(\d+)(?:x(\d+))?/);
  const w = sizeMatch ? Math.min(parseInt(sizeMatch[1], 10) || 320, 1600) : 320;
  const h = sizeMatch && sizeMatch[2] ? Math.min(parseInt(sizeMatch[2], 10) || w, 1600) : w;
  const textMatch = src.match(/[?&]text=([^&]+)/i);
  let text = '示意图';
  if (textMatch) {
    try {
      text = decodeURIComponent(textMatch[1]).replace(/\+/g, ' ');
    } catch {
      text = textMatch[1].replace(/\+/g, ' ');
    }
  }
  return svgPlaceholderDataUri(w, h, text);
}

// ---------------------------------------------------------------------------
// 预处理：@# 跨工单引用 → 可点击链接
// ---------------------------------------------------------------------------
// 讨论区/回复里出现的 "@#44123" 是"引用另一个工单"的语法，
// 渲染时转成可点击链接，点击跳转到对应工单详情页（Q4-A 跳转入口）。
// 保护范围：已被 markdown 链接语法包裹的 "@#xxx"（如 [x](...)）不重复转换。
const TICKET_REF_RE = /@#(\d{1,8})/g;

/** 把 "@#编号" 转换为指向工单详情页的 markdown 链接 */
function preprocessTicketRef(text: string): string {
  if (!text) return text;
  return text.replace(TICKET_REF_RE, (_full, id) => `[@#${id}](/tasks/${id})`);
}

// ---------------------------------------------------------------------------
// 预处理：将原始媒体 URL 转为 Markdown 图片/视频语法
// ---------------------------------------------------------------------------

/**
 * 检测文本中的原始 URL（不在 markdown 语法内的），将其转为 markdown 格式：
 *   - 图片 URL → ![image](url)
 *   - 视频 URL → ![▶ Video](url)
 *
 * 保护范围：
 *   - 已有的 ![alt](url) 图片语法
 *   - 已有的 [text](url) 链接语法
 *   - 已有的 HTML <img>/<video> 标签
 */
function preprocessMediaUrls(text: string): string {
  let processed = text;

  // ---- 步骤 1：将原始 HTML <img> / <video> 转为 markdown 语法 ----
  // react-markdown 默认不渲染 raw HTML（安全性），所以提前转换
  processed = processed.replace(
    /<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["'][^>]*\/?>/gi,
    (_full, srcUrl) => {
      if (VIDEO_EXT_RE.test(srcUrl)) return `![▶ Video](${srcUrl})`;
      return `![image](${srcUrl})`;
    }
  );
  processed = processed.replace(
    /<video\b[^>]*?\bsrc\s*=\s*["']([^"']+)["'][^>]*>[\s\S]*?<\/video>/gi,
    (_full, srcUrl) => `![▶ Video](${srcUrl})`
  );

  // ---- 步骤 2：收集受保护的区间（已在 markdown 语法内的 URL） ----
  const protectedRanges: Array<[number, number]> = [];

  // 保护 ![alt](url) 和 [text](url) 语法
  for (const m of processed.matchAll(/(!?)\[([^\]]*)\]\(([^)]+)\)/g)) {
    protectedRanges.push([m.index!, m.index! + m[0].length]);
  }

  const isProtected = (idx: number): boolean =>
    protectedRanges.some(([s, e]) => idx >= s && idx < e);

  // ---- 步骤 3：将裸露的媒体 URL 转为 markdown 语法 ----
  const RAW_URL_RE = /(?<![("'=])(https?:\/\/[^\s<>"')\]]+)/gi;

  const lines = processed.split('\n');
  const result = lines.map((line) => {
    return line.replace(RAW_URL_RE, (url, _groups, offset) => {
      if (isProtected(offset)) return url;

      // 去掉尾部标点和括号
      const cleaned = url.replace(/[,;.!?，。！？；、)\]]+$/, '');

      if (VIDEO_EXT_RE.test(cleaned)) {
        return `![▶ Video](${cleaned})`;
      }
      if (IMAGE_EXT_RE.test(cleaned)) {
        return `![image](${cleaned})`;
      }
      // 无扩展名的 URL 也可能是图片/视频（如 CDN 签名链接），保持原样
      return url;
    });
  });

  return result.join('\n');
}

// ---------------------------------------------------------------------------
// 安全网：剥离后端可能泄漏的 ``` 标记
// ---------------------------------------------------------------------------

function stripCodeFences(text: string): string {
  let t = text.trim();
  t = t.replace(/^```\w*\s*\n?/, '');
  t = t.replace(/\n?```\s*$/, '');
  return t;
}

// ---------------------------------------------------------------------------
// 简易 markdown→HTML（纯字符串替换，不依赖任何库）
// 作为 react-markdown 完全崩溃时的终极兜底
// ---------------------------------------------------------------------------

function simpleMarkdownToHtml(text: string): string {
  let html = text
    // 标题
    .replace(/^#### (.+)$/gm, '<h4>$1</h4>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    // 粗体+斜体
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    // 粗体
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    // 斜体
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // 行内代码
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    // 无序列表项
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/^\* (.+)$/gm, '<li>$1</li>')
    // 有序列表项
    .replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
    // 水平线
    .replace(/^---+$/gm, '<hr>')
    // 引用
    .replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>')
    // 段落：连续非空行 → <p>
    .replace(/\n\n/g, '</p><p>');

  // 包裹连续 li → ul
  html = html.replace(/(<li>.*?<\/li>)\s*(?=<li>|$)/gs, (match) => {
    return `<ul>${match}</ul>`;
  });

  // 包裹首尾
  html = `<p>${html}</p>`;

  // 清理嵌套异常
  html = html.replace(/<p><\/p>/g, '');
  html = html.replace(/<p>(\s*<h[1-4])/g, '$1');
  html = html.replace(/(<\/h[1-4]>)\s*<\/p>/g, '$1');
  html = html.replace(/<p>(\s*<ul)/g, '$1');
  html = html.replace(/(<\/ul>)\s*<\/p>/g, '$1');

  return html;
}

// ---------------------------------------------------------------------------
// 鉴权图片组件 —— 智能处理多种 URL 类型
// ---------------------------------------------------------------------------

interface AuthImageProps {
  src?: string;
  alt?: string;
}

/** AuthImage 的缓存条目：status 为最终/进行中状态。 */
type AuthCacheEntry =
  | { status: 'direct' | 'error' }
  | { status: 'loaded'; blobUrl: string };

/**
 * 模块级鉴权图片缓存（key = fullUrl + token）：
 * react-markdown 用 passKeys(true) 按「标签名 + 兄弟序号」生成 key（如 img-0 / p-0），
 * 会覆盖组件上的 key。流式期间图片前的段落/格式变化会使图片位置 key 变化，导致
 * AuthImage remount → 重置 imgState → 重新 fetch → 图片闪烁。这里把已解析结果缓存，
 * remount 时立即恢复，不重新 fetch，杜绝流式期间的图片闪烁。
 */
const authImgCache = new Map<string, AuthCacheEntry>();
/** 并发去重：同一 URL 只发一次 fetch，其余挂载共享结果。 */
const authImgInflight = new Map<string, Promise<AuthCacheEntry>>();

/** 串上 token 作为缓存 key 的一部分，token 变更/失效后自动重新加载 */
const authCacheKey = (fullUrl: string): string => `${fullUrl}::${useAuthStore.getState().token || ''}`;

/** 鉴权加载单个图片（带缓存 + 并发去重），返回最终缓存条目 */
function loadAuthImage(fullUrl: string): Promise<AuthCacheEntry> {
  const cacheKey = authCacheKey(fullUrl);
  const cached = authImgCache.get(cacheKey);
  if (cached) return Promise.resolve(cached);
  const inflight = authImgInflight.get(cacheKey);
  if (inflight) return inflight;

  const p = (async (): Promise<AuthCacheEntry> => {
    try {
      const token = useAuthStore.getState().token;
      if (!token) {
        const entry: AuthCacheEntry = { status: 'direct' };
        authImgCache.set(cacheKey, entry);
        return entry;
      }

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 15000);

      const res = await fetch(fullUrl, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      });
      clearTimeout(timeoutId);

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const contentType = res.headers.get('content-type') || '';
      if (contentType && !IMAGE_MIME_RE.test(contentType) && !VIDEO_MIME_RE.test(contentType)) {
        const entry: AuthCacheEntry = { status: 'direct' };
        authImgCache.set(cacheKey, entry);
        return entry;
      }

      const blob = await res.blob();
      const blobUrl = URL.createObjectURL(blob);
      const entry: AuthCacheEntry = { status: 'loaded', blobUrl };
      authImgCache.set(cacheKey, entry);
      return entry;
    } catch {
      const entry: AuthCacheEntry = { status: 'direct' };
      authImgCache.set(cacheKey, entry);
      return entry;
    }
  })();

  // 无论成功失败都要清理 inflight，允许后续变更时重新加载
  authImgInflight.set(cacheKey, p.finally(() => authImgInflight.delete(cacheKey)));
  return authImgInflight.get(cacheKey)!;
}

/**
 * 鉴权图片组件：
 *   - 外部绝对 URL（非同源）→ 直接 <img> 加载
 *   - 同源直链 URL → 直接 <img> 加载（cookie 自动携带）
 *   - 相对路径 / /api/ 前缀 → fetch + Bearer Token 鉴权后转 blob URL
 *   - 加载失败 → 显示连接 + 点击新窗口打开
 * 加载结果走模块级缓存（authImgCache），流式 remount 复用不闪烁。
 */
function AuthImage({ src, alt = '' }: AuthImageProps) {
  const [imgState, setImgState] = useState<{
    status: 'idle' | 'loading' | 'loaded' | 'direct' | 'error';
    blobUrl?: string;
  }>({ status: 'idle' });
  const [previewOpen, setPreviewOpen] = useState(false);
  const blobUrlRef = useRef<string | null>(null);

  // 同步解析 URL 类型
  const urlInfo = useMemo(() => {
    if (!src) return { type: 'empty' as const, url: '' };
    const isAbsolute = /^https?:\/\//i.test(src);
    const isSameOrigin = isAbsolute && src.startsWith(window.location.origin);
    const isRelative = !isAbsolute;
    const needsAuth = isRelative || isSameOrigin;
    const fullUrl = isAbsolute
      ? src
      : src.startsWith('/api/')
        ? `${ENV_PREFIX}${src}`
        : src.startsWith('/')
          ? src
          : `/${src}`;
    return { type: needsAuth ? 'auth' as const : 'direct' as const, url: src, fullUrl };
  }, [src]);

  // 触发加载：优先从模块级缓存立即恢复（remount 不闪烁），未命中才走鉴权加载
  useEffect(() => {
    setImgState({ status: 'idle' });
    // 不复用共享 blob：blobUrlRef 若指向缓存 blob，此处 revoke 会让仍在展示的同 URL 图片失效闪断，
    // 故只清空引用，由缓存层统一持有。
    blobUrlRef.current = null;

    if (!src || urlInfo.type === 'empty') return;

    // 直接渲染类型（绝对外链/同源/无 token）→ 不用 fetch，缓存为 direct
    if (urlInfo.type === 'direct') {
      const cacheKey = authCacheKey(urlInfo.fullUrl);
      if (authImgCache.get(cacheKey)?.status === 'direct') {
        setImgState({ status: 'direct' });
      } else {
        authImgCache.set(cacheKey, { status: 'direct' });
        setImgState({ status: 'direct' });
      }
      return;
    }

    // 需要鉴权：同步查缓存，命中立即恢复（拿缓存 blobUrl 也即时更新 blobUrlRef）
    const cacheKey = authCacheKey(urlInfo.fullUrl);
    const cached = authImgCache.get(cacheKey);
    if (cached?.status === 'loaded') {
      blobUrlRef.current = cached.blobUrl;
      setImgState({ status: 'loaded', blobUrl: cached.blobUrl });
      return;
    }
    if (cached?.status === 'direct' || cached?.status === 'error') {
      setImgState({ status: cached.status });
      return;
    }

    // 未命中 → 走异步鉴权加载（带并发去重）
    setImgState({ status: 'loading' });
    let cancelled = false;
    loadAuthImage(urlInfo.fullUrl).then((entry) => {
      if (cancelled) return;
      if (entry.status === 'loaded') {
        blobUrlRef.current = entry.blobUrl;
        setImgState({ status: 'loaded', blobUrl: entry.blobUrl });
      } else {
        setImgState({ status: entry.status === 'error' ? 'error' : 'direct' });
      }
    });

    return () => {
      cancelled = true;
    };
  }, [src, urlInfo.type, urlInfo.fullUrl]);

  // 卸载清理：blob URL 由模块级缓存统一持有（跨实例共享），此处在卸载时 revoke 会
  // 让仍在展示的同 URL 图片（缓存复用）失效闪断，故不再 revoke，仅清空本次引用。
  useEffect(() => {
    return () => {
      blobUrlRef.current = null;
    };
  }, []);

  const handleImgError = useCallback(() => {
    setImgState({ status: 'error' });
  }, []);

  // ---- 渲染 ----
  // 预览地址：优先用真实 URL（fullUrl），不用 blob —— 微信内置浏览器长按「保存图片」
  // 会对 URL 二次下载，blob: 无法解析必失败。AI 媒体接口（/api/ai/media/*）是公开
  // 静态资源（StaticFiles 挂载，无鉴权），灯箱内直接以真实 URL 加载即可。
  const previewSrc = urlInfo.fullUrl || imgState.blobUrl || '';
  const openPreview = () => setPreviewOpen(true);

  if (!src || urlInfo.type === 'empty') {
    return null;
  }

  if (imgState.status === 'idle') {
    if (urlInfo.type === 'direct') {
      return (
        <>
          <img
            src={urlInfo.url}
            alt={alt}
            className="md-image md-image--clickable"
            loading="eager"
            onClick={openPreview}
            onError={handleImgError}
            style={{ maxWidth: '100%', borderRadius: 8, margin: '8px 0', display: 'block' }}
          />
          <ImageLightbox src={previewSrc} alt={alt} open={previewOpen} onClose={() => setPreviewOpen(false)} />
        </>
      );
    }
    return (
      <span className="md-media-loading">
        正在加载…
      </span>
    );
  }

  if (imgState.status === 'error') {
    return (
      <span className="md-media-fallback">
        <a href={urlInfo.url} target="_blank" rel="noopener noreferrer">{alt || '查看图片'}</a>
      </span>
    );
  }

  if (imgState.status === 'loading') {
    return (
      <span className="md-media-loading">
        正在加载图片…
      </span>
    );
  }

  const finalSrc = imgState.blobUrl || urlInfo.fullUrl;

  return (
    <>
      <img
        src={finalSrc}
        alt={alt}
        className="md-image md-image--clickable"
        loading="eager"
        onClick={openPreview}
        onError={handleImgError}
        style={{ maxWidth: '100%', borderRadius: 8, margin: '8px 0', display: 'block' }}
      />
      <ImageLightbox src={previewSrc} alt={alt} open={previewOpen} onClose={() => setPreviewOpen(false)} />
    </>
  );
}

// ---------------------------------------------------------------------------
// Error Boundary —— 组件树异常时降级为简易 HTML 渲染
// ---------------------------------------------------------------------------

class MdErrorBoundary extends Component<
  { children: React.ReactNode; fallbackText: string },
  { hasError: boolean }
> {
  constructor(props: { children: React.ReactNode; fallbackText: string }) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: Error) {
    console.error('[MarkdownRenderer Error Boundary]', error.message);
  }

  render() {
    if (this.state.hasError) {
      const fallbackHtml = simpleMarkdownToHtml(stripCodeFences(this.props.fallbackText));
      return (
        <div
          className="markdown-body"
          dangerouslySetInnerHTML={{ __html: fallbackHtml }}
        />
      );
    }
    return this.props.children;
  }
}

// ---------------------------------------------------------------------------
// 消息渲染器组件
// ---------------------------------------------------------------------------

interface MarkdownRendererProps {
  content: string;
  /** 是否处于紧凑模式（系统任务场景），影响字号等 */
  compact?: boolean;
  /**
   * 是否处于流式生成中：true 时「不渲染真实图片/视频」，以稳定占位代替。
   * 原因：流式 content 每帧增长，react-markdown 用 passKeys(true) 按位置生成 key，
   * 图片前的文字/格式变化会让图片节点 remount → 重新加载 → 图片反复闪烁。
   * 流式期间用占位占住排版，定稿（streaming=false）后才一次性精确加载真实媒体，彻底防闪。
   */
  streaming?: boolean;
}

/** 判断 alt 文本是否标记为视频 */
function isVideoAlt(alt: string): boolean {
  return /^▶\s*(Video|视频)/i.test(alt);
}

/**
 * 流式期间对「未闭合的半截媒体语法」进行中和，避免它们以原始文本闪现：
 *   - 只处理「成形的图片 markdown 保留给 img component 走占位块」，
 *   - 这里仅中和「半截」的 `![`（如 `![架构图](htt`，URL 还没敲完 → react-markdown
 *     不识别为 img，会当普通文本把 `![架构图](htt...` 原样显示，很丑），替换为占位文本。
 * 成形的 `![alt](url)` 不影响（进入 ReactMarkdown → img → streaming 占位块）。
 */
const MASK_MEDIA_PLACEHOLDER = '⏳ 图片加载中…';
function maskMediaForStreaming(text: string): string {
  if (!text) return text;
  // 未闭合的半截 ![（有 `![` 但行尾仍无闭合 ））：中和为占位，避免原样丑字闪现
  return text.replace(/!\[[^\]]*\]\([^)\n]*$/g, MASK_MEDIA_PLACEHOLDER);
}

/**
 * MarkdownRenderer
 *
 * 处理管线：
 *   1. stripCodeFences —— 剥离可能泄漏的 ``` 包裹
 *   2. preprocessMediaUrls —— 原始媒体 URL 转 markdown 图片/视频语法（streaming 时跳过，避免流式渲染真实媒体）
 *   3. maskMediaForStreaming —— streaming 时把图片 markdown 替换为稳定占位
 *   4. react-markdown —— 将 markdown 解析为 React 虚拟 DOM
 *   5. 自定义 components —— img/a/pre/code/table/p 使用定制渲染
 *   6. Error Boundary —— 异常时降级为简易 HTML 渲染，不白屏
 *
 * 不做 DOMPurify 预清洗：
 *   react-markdown 输出 React.createElement() 调用，不是 innerHTML，
 *   天然不存在 XSS 注入路径，DOMPurify 反而会破坏 markdown 结构。
 */
export default function MarkdownRenderer({ content, compact = false, streaming = false }: MarkdownRendererProps) {
  // 剥离 ``` → @# 工单引用链接 → 媒体预处理
  const processedContent = useMemo(() => {
    const stripped = stripCodeFences(content);
    // 流式期间不识别/不渲染真实媒体；定稿后才走完整媒体管线
    if (streaming) {
      return maskMediaForStreaming(preprocessTicketRef(stripped));
    }
    return preprocessMediaUrls(preprocessTicketRef(stripped));
  }, [content, streaming]);

  return (
    <MdErrorBoundary fallbackText={content}>
      <div className={`markdown-body${compact ? ' md-compact' : ''}`}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          urlTransform={urlTransformAllowDataImage}
          components={{
            // ---- 图片 / 视频渲染 ----
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            img({ src, alt, ...props }: any) {
              const altText: string = alt || '';
              const srcUrl: string = src || '';

              if (isVideoAlt(altText)) {
                return (
                  <span className="md-media-wrap" style={{ display: 'block', margin: '8px 0' }}>
                    <video
                      src={srcUrl}
                      controls
                      preload="metadata"
                      style={{ maxWidth: '100%', borderRadius: 8, background: '#000' }}
                    >
                      您的浏览器不支持视频播放。
                    </video>
                  </span>
                );
              }

              // 流式期间：不渲染真实图片，用稳定的块级占位占住图片位置（不混进行内文字流），
              // 回答定稿（streaming=false）后才渲染真实 <img>。占位块无异步副作用，
              // 即使流式 remount 也不闪烁。
              if (streaming) {
                return <span className="md-media-loading">⏳ 图片加载中…</span>;
              }

              // key 按 src 稳定：流式期间整段 markdown 每帧重解析重建，若无稳定 key，
              // 图片前内容变化（如未闭合加粗/新增段落）会导致图片节点位置/父节点变化，
              // React 判定为新节点 → AuthImage remount → 重置 imgState 重新 fetch → 图片闪烁。
              // 以 src 作 key 让同 URL 图片跨帧复用同实例，保留加载状态不重置。
              return <AuthImage key={srcUrl} src={srcUrl} alt={altText} />;
            },

            // ---- 链接：外部链接新窗口打开 + 媒体链接自动渲染 ----
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            a({ href, children, ...props }: any) {
              const url: string = href || '';
              const isExternal = /^https?:\/\//i.test(url);
              const isMediaUrl = IMAGE_EXT_RE.test(url) || VIDEO_EXT_RE.test(url);

              if (isMediaUrl) {
                if (VIDEO_EXT_RE.test(url)) {
                  return (
                    <span className="md-media-wrap" style={{ display: 'block', margin: '8px 0' }}>
                      <video
                        src={url}
                        controls
                        preload="metadata"
                        style={{ maxWidth: '100%', borderRadius: 8, background: '#000' }}
                      >
                        您的浏览器不支持视频播放。
                      </video>
                    </span>
                  );
                }
                if (streaming) {
                  return <span className="md-media-loading">⏳ 图片加载中…</span>;
                }
                return <AuthImage key={url} src={url} alt={String(children || '')} />;
              }

              return (
                <a
                  href={url}
                  target={isExternal ? '_blank' : undefined}
                  rel={isExternal ? 'noopener noreferrer' : undefined}
                  {...props}
                >
                  {children}
                </a>
              );
            },

            // ---- 代码块 ----
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            pre({ children, ...props }: any) {
              return (
                <pre className="md-code-block" {...props}>
                  {children}
                </pre>
              );
            },

            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            code({ className, children, ...props }: any) {
              const isInline = !className;
              return (
                <code
                  className={`${className || ''}${isInline ? ' md-inline-code' : ''}`}
                  {...props}
                >
                  {children}
                </code>
              );
            },

            // ---- 表格 ----
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            table({ children, ...props }: any) {
              return (
                <div className="md-table-wrap" style={{ overflowX: 'auto' }}>
                  <table className="md-table" {...props}>
                    {children}
                  </table>
                </div>
              );
            },

            // ---- 段落 ----
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            p({ children, ...props }: any) {
              return (
                <p className="md-paragraph" {...props}>
                  {children}
                </p>
              );
            },
          }}
        >
          {processedContent}
        </ReactMarkdown>
      </div>
    </MdErrorBoundary>
  );
}
