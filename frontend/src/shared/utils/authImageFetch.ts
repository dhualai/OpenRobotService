/**
 * authImageFetch.ts —— 带 Bearer 鉴权的图片加载 + 缓存层
 *
 * 用途：MarkdownRenderer 的 AuthImage 和 ArchiveReportRichEditor 的 ReportImage NodeView
 * 都要拉 `/api/tasks/attachments/download` 等需要鉴权的图片接口。
 * <img src="..." /> 不会自动带 Authorization header，必须 fetch 拿 blob 再渲染。
 *
 * 设计要点：
 *   - 模块级缓存（cache key = url + token）：同一 URL 不重复 fetch
 *   - 并发去重：多个组件同时请求同一 URL 只发一次 fetch
 *   - 并发限流：最多 8 路，避免一次性图片请求占满连接
 *   - token 变更自动失效（token 拼在 cache key 里）
 */

import { useAuthStore } from '@/stores/auth';

type AuthCacheEntry =
  | { status: 'direct' | 'error' }
  | { status: 'loaded'; blobUrl: string };

const IMAGE_MIME_RE = /^image\//;
const VIDEO_MIME_RE = /^video\//;

/** 模块级缓存（key = fullUrl + token） */
const _authImgCache = new Map<string, AuthCacheEntry>();
/** 并发去重：同一 URL 只发一次 fetch */
const _authImgInflight = new Map<string, Promise<AuthCacheEntry>>();

const authCacheKey = (fullUrl: string): string =>
  `${fullUrl}::${useAuthStore.getState().token || ''}`;

// 并发限制
const MAX_CONCURRENT_IMG = 8;
let _imgActive = 0;
const _imgWaiters: Array<() => void> = [];
function _acquireImgSlot(): Promise<void> {
  if (_imgActive < MAX_CONCURRENT_IMG) {
    _imgActive += 1;
    return Promise.resolve();
  }
  return new Promise<void>((resolve) => _imgWaiters.push(resolve));
}
function _releaseImgSlot(): void {
  _imgActive = Math.max(0, _imgActive - 1);
  _imgWaiters.shift()?.();
}

/** 查询缓存（不发请求）。remount 时快速恢复，不再 fetch。 */
export function getAuthImgCache(fullUrl: string): AuthCacheEntry | undefined {
  return _authImgCache.get(authCacheKey(fullUrl));
}

/** 写入缓存（direct / error 兜底场景），供 MarkdownRenderer / ArchiveReportRichEditor 统一使用 */
export function setAuthImgCache(fullUrl: string, entry: AuthCacheEntry): void {
  _authImgCache.set(authCacheKey(fullUrl), entry);
}

/** 鉴权加载单个图片（带缓存 + 并发去重 + 限流），返回最终缓存条目 */
export function loadAuthImage(fullUrl: string): Promise<AuthCacheEntry> {
  const cacheKey = authCacheKey(fullUrl);
  const cached = _authImgCache.get(cacheKey);
  if (cached) return Promise.resolve(cached);
  const inflight = _authImgInflight.get(cacheKey);
  if (inflight) return inflight;

  const p = (async (): Promise<AuthCacheEntry> => {
    await _acquireImgSlot();
    try {
      const token = useAuthStore.getState().token;
      if (!token) {
        const entry: AuthCacheEntry = { status: 'direct' };
        _authImgCache.set(cacheKey, entry);
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
        _authImgCache.set(cacheKey, entry);
        return entry;
      }

      const blob = await res.blob();
      const blobUrl = URL.createObjectURL(blob);
      const entry: AuthCacheEntry = { status: 'loaded', blobUrl };
      _authImgCache.set(cacheKey, entry);
      return entry;
    } catch {
      const entry: AuthCacheEntry = { status: 'direct' };
      _authImgCache.set(cacheKey, entry);
      return entry;
    } finally {
      _releaseImgSlot();
    }
  })();

  _authImgInflight.set(cacheKey, p.finally(() => _authImgInflight.delete(cacheKey)));
  return p;
}
