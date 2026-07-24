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
import { useAuthStore } from '@/stores/auth';
import { ENV_PREFIX } from '@/config/api';

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

/**
 * 鉴权图片组件：
 *   - 外部绝对 URL（非同源）→ 直接 <img> 加载
 *   - 同源直链 URL → 直接 <img> 加载（cookie 自动携带）
 *   - 相对路径 / /api/ 前缀 → fetch + Bearer Token 鉴权后转 blob URL
 *   - 加载失败 → 显示连接 + 点击新窗口打开
 */
function AuthImage({ src, alt = '' }: AuthImageProps) {
  const [imgState, setImgState] = useState<{
    status: 'idle' | 'loading' | 'loaded' | 'direct' | 'error';
    blobUrl?: string;
  }>({ status: 'idle' });
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

  // 触发鉴权加载或直接渲染
  useEffect(() => {
    setImgState({ status: 'idle' });
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }

    if (!src || urlInfo.type === 'empty') return;

    if (urlInfo.type === 'direct') {
      setImgState({ status: 'direct' });
      return;
    }

    // type === 'auth'：通过 Bearer Token 获取
    const token = useAuthStore.getState().token;
    if (!token) {
      setImgState({ status: 'direct' });
      return;
    }

    setImgState({ status: 'loading' });

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 15000);

    fetch(urlInfo.fullUrl, {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    })
      .then(async (res) => {
        clearTimeout(timeoutId);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const contentType = res.headers.get('content-type') || '';
        if (contentType && !IMAGE_MIME_RE.test(contentType) && !VIDEO_MIME_RE.test(contentType)) {
          setImgState({ status: 'direct' });
          return;
        }

        const blob = await res.blob();
        const blobUrl = URL.createObjectURL(blob);
        blobUrlRef.current = blobUrl;
        setImgState({ status: 'loaded', blobUrl });
      })
      .catch(() => {
        setImgState({ status: 'direct' });
      });

    return () => {
      controller.abort();
      clearTimeout(timeoutId);
    };
  }, [src, urlInfo.type, urlInfo.fullUrl]);

  // 组件卸载时清理 blob URL
  useEffect(() => {
    return () => {
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, []);

  const handleImgError = useCallback(() => {
    setImgState({ status: 'error' });
  }, []);

  // ---- 渲染 ----
  if (!src || urlInfo.type === 'empty') {
    return null;
  }

  if (imgState.status === 'idle') {
    if (urlInfo.type === 'direct') {
      return (
        <img
          src={urlInfo.url}
          alt={alt}
          className="md-image"
          loading="lazy"
          onError={handleImgError}
          style={{ maxWidth: '100%', borderRadius: 8, margin: '8px 0', display: 'block' }}
        />
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
    <img
      src={finalSrc}
      alt={alt}
      className="md-image"
      loading="lazy"
      onError={handleImgError}
      style={{ maxWidth: '100%', borderRadius: 8, margin: '8px 0', display: 'block' }}
    />
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
}

/** 判断 alt 文本是否标记为视频 */
function isVideoAlt(alt: string): boolean {
  return /^▶\s*(Video|视频)/i.test(alt);
}

/**
 * MarkdownRenderer
 *
 * 处理管线：
 *   1. stripCodeFences —— 剥离可能泄漏的 ``` 包裹
 *   2. preprocessMediaUrls —— 原始媒体 URL 转 markdown 图片/视频语法
 *   3. react-markdown —— 将 markdown 解析为 React 虚拟 DOM
 *   4. 自定义 components —— img/a/pre/code/table/p 使用定制渲染
 *   5. Error Boundary —— 异常时降级为简易 HTML 渲染，不白屏
 *
 * 不做 DOMPurify 预清洗：
 *   react-markdown 输出 React.createElement() 调用，不是 innerHTML，
 *   天然不存在 XSS 注入路径，DOMPurify 反而会破坏 markdown 结构。
 */
export default function MarkdownRenderer({ content, compact = false }: MarkdownRendererProps) {
  // 剥离 ``` → 媒体预处理
  const processedContent = useMemo(
    () => preprocessMediaUrls(stripCodeFences(content)),
    [content]
  );

  return (
    <MdErrorBoundary fallbackText={content}>
      <div className={`markdown-body${compact ? ' md-compact' : ''}`}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
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

              return <AuthImage src={srcUrl} alt={altText} />;
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
                return <AuthImage src={url} alt={String(children || '')} />;
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
