/**
 * MarkdownRenderer — AI 流式返回内容的 Markdown 解析 + 媒体渲染组件
 *
 * 功能：
 *  - GitHub Flavored Markdown（表格/任务列表/删除线/链接/代码块）
 *  - 图片 URL 自动识别渲染（支持后端 API 鉴权图片、外部图片直链）
 *  - 视频 URL 自动识别渲染
 *  - XSS 防护（react-markdown 输出 React 元素，天然防 XSS）
 *
 * 注意：不使用 DOMPurify 清洗 markdown 源码 —— react-markdown 输出的是
 * React 虚拟 DOM（非 raw HTML），不存在 XSS 注入路径。
 */
import { useMemo, useState, useCallback, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useAuthStore } from '@/stores/auth';

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
    // 需要鉴权：相对路径 或 同源路径（可能是后端 API）
    const needsAuth = isRelative || isSameOrigin;
    // 构建完整 URL
    const fullUrl = isAbsolute
      ? src
      : src.startsWith('/')
        ? src
        : `/${src}`;
    return { type: needsAuth ? 'auth' as const : 'direct' as const, url: src, fullUrl };
  }, [src]);

  // 触发鉴权加载或直接渲染
  useEffect(() => {
    // 重置状态（src 变化时）
    setImgState({ status: 'idle' });
    // 清理旧 blob URL
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
      setImgState({ status: 'direct' }); // 无 token 降级为直接加载
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

        // 检查 Content-Type：非图片/视频降级为直接链接
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
        // fetch 失败，降级为直接加载（浏览器自行处理跨域/404）
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

  // 兜底：直接 img 加载失败时显示连接
  const handleImgError = useCallback(() => {
    setImgState({ status: 'error' });
  }, []);

  // ---- 渲染 ----
  if (!src || urlInfo.type === 'empty') {
    return null;
  }

  // idle 状态：先不渲染 <img>，等 useEffect 决定走 direct 还是 auth 路径
  // 这避免了 auth URL 在 mount 瞬间以无鉴权方式直接加载导致 404 → onError 的竞态
  if (imgState.status === 'idle') {
    if (urlInfo.type === 'direct') {
      // 外部直链：可以安全地立即渲染
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
    // 相对/同源路径：先挂起，等鉴权 fetch 或降级决策
    return (
      <span className="md-media-loading">
        ⏳ 正在加载…
      </span>
    );
  }

  if (imgState.status === 'error') {
    return (
      <span className="md-media-fallback">
        📷 <a href={urlInfo.url} target="_blank" rel="noopener noreferrer">{alt || '查看图片'}</a>
      </span>
    );
  }

  if (imgState.status === 'loading') {
    return (
      <span className="md-media-loading">
        ⏳ 正在加载图片…
      </span>
    );
  }

  // status === 'direct' 或 'loaded'
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
 *   1. preprocessMediaUrls —— 原始媒体 URL 转 markdown 图片/视频语法
 *   2. react-markdown —— 将 markdown 解析为 React 虚拟 DOM
 *   3. 自定义 components —— img/a/pre/code/table/p 使用定制渲染
 *
 * 不做 DOMPurify 预清洗：
 *   react-markdown 输出 React.createElement() 调用，不是 innerHTML，
 *   天然不存在 XSS 注入路径，DOMPurify 反而会破坏 markdown 结构。
 */
export default function MarkdownRenderer({ content, compact = false }: MarkdownRendererProps) {
  // 媒体 URL 预处理：将原始图片/视频 URL 转为 markdown 语法
  const processedContent = useMemo(() => preprocessMediaUrls(content), [content]);

  return (
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

          // ---- 链接：外部链接新窗口打开 ----
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
  );
}
