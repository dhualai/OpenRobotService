/**
 * 转发卡片元信息（`<title>` / `description` / `og:*` / `twitter:*`）——「按链接生成卡片」的统一入口。
 *
 * 背景：**企业微信没有 JS-SDK 分享接口**，在企微里点右上角「…」转发出去的卡片，
 * 内容只能来自页面 head（`<title>` + `og:image` + `og:description`）；微信内则由
 * `setupWechatShare` 精确控制 title/desc/imgUrl/link。两条链路都要有，**同一个链接在
 * 微信和企微里才会长成同一张卡片**，而不是一个只剩空白标题的壳。
 *
 * 本模块只负责 head 这条链路（纯 DOM 写入，无副作用、可在测试里断言）：
 * - `index.html` 里另有一份**静态**兜底（企微若不执行 JS 也能拿到标题与图）；
 * - 页面在渲染时调用 `setShareMeta` 覆盖成当前内容（问题标题、奖金数目等）。
 */

/** 站点名（写入 og:site_name） */
export const SHARE_SITE_NAME = '摇人吧';

/** 默认 logo 文件名（放在 public/ 下，随构建产物部署到站点根） */
const DEFAULT_LOGO_FILE = 'share-thumb.png';

/** 默认 logo 的边长（px）：微信分享卡片建议 ≥300×300，改图时需同步改这里 */
const DEFAULT_LOGO_SIZE = 300;

/** 卡片字段（纯文本，不接受 HTML） */
export interface ShareMetaInput {
  /** 卡片标题：同时写入 `document.title` */
  title: string;
  /**
   * 卡片描述：会话列表/详情页展示。
   * 微信客户端会截断（会话列表约前 30 字、详情约前 54 字），关键信息请前置。
   */
  description?: string;
  /** 缩略图绝对地址；不传则用站点默认 logo */
  imageUrl?: string;
  /** 卡片链接；默认取当前地址（去掉 hash） */
  url?: string;
}

/** 构建基路径：生产 `/p/app/`、测试 `/t/app/`、本地 `/`（Vite 的 BASE_URL） */
function currentBase(): string {
  const base = (import.meta.env?.BASE_URL as string | undefined) || '/';
  return base.endsWith('/') ? base : `${base}/`;
}

/** 当前页面地址（去掉 hash —— 微信按 URL 匹配，hash 不参与） */
export function currentShareUrl(): string {
  if (typeof window === 'undefined' || !window.location) return '';
  const { origin, pathname, search } = window.location;
  return `${origin}${pathname}${search}`;
}

/**
 * 默认缩略图（logo）绝对地址。
 *
 * 优先用环境变量 `VITE_WECHAT_SHARE_IMG_URL`（生产已配成公网绝对地址）；
 * 未配置时按「当前站点 origin + 构建基路径 + 默认 logo」推导，
 * 这样生产 `/p/app/`、测试 `/t/app/`、本地开发各自动适配，无需为每个环境改代码。
 */
export function shareImageUrl(): string {
  const configured = (import.meta.env?.VITE_WECHAT_SHARE_IMG_URL as string | undefined) || '';
  if (configured) return configured;
  const path = `${currentBase()}${DEFAULT_LOGO_FILE}`;
  if (typeof window === 'undefined' || !window.location?.origin) return path;
  return `${window.location.origin}${path}`;
}

function upsertMeta(key: string, content: string, attr: 'name' | 'property'): void {
  if (typeof document === 'undefined') return;
  let el = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`);
  if (!el) {
    el = document.createElement('meta');
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  el.setAttribute('content', content);
}

/** 空值时移除旧标签，避免上一个页面的描述/图残留在卡片里 */
function removeMeta(key: string, attr: 'name' | 'property'): void {
  if (typeof document === 'undefined') return;
  document.head.querySelector(`meta[${attr}="${key}"]`)?.remove();
}

function upsertCanonical(url: string): void {
  if (typeof document === 'undefined' || !url) return;
  let el = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
  if (!el) {
    el = document.createElement('link');
    el.setAttribute('rel', 'canonical');
    document.head.appendChild(el);
  }
  el.setAttribute('href', url);
}

/**
 * 写入当前页面的转发卡片元信息。
 *
 * 幂等：同 key 反复调用只更新 content，不会堆出重复标签。
 * 空 `title` 直接忽略（不覆盖已有标题，避免路由切换瞬间把卡片标题清空）。
 */
export function setShareMeta(input: ShareMetaInput): void {
  if (typeof document === 'undefined') return;
  const title = (input.title || '').trim();
  if (!title) return;
  const description = (input.description || '').trim();
  const image = (input.imageUrl || shareImageUrl()).trim();
  const url = (input.url || currentShareUrl()).trim();

  document.title = title;

  upsertMeta('description', description, 'name');
  upsertMeta('og:site_name', SHARE_SITE_NAME, 'property');
  upsertMeta('og:type', 'website', 'property');
  upsertMeta('og:title', title, 'property');
  upsertMeta('twitter:card', 'summary_large_image', 'name');
  upsertMeta('twitter:title', title, 'name');

  if (description) {
    upsertMeta('og:description', description, 'property');
    upsertMeta('twitter:description', description, 'name');
  } else {
    removeMeta('og:description', 'property');
    removeMeta('twitter:description', 'name');
  }

  if (image) {
    upsertMeta('og:image', image, 'property');
    upsertMeta('twitter:image', image, 'name');
    // 只有默认 logo 的尺寸是确定的（微信建议 ≥300×300）；页面自定义配图不写死宽高，
    // 否则会给抓取方错误信息，反而导致抓图失败。
    if (image === shareImageUrl()) {
      upsertMeta('og:image:width', String(DEFAULT_LOGO_SIZE), 'property');
      upsertMeta('og:image:height', String(DEFAULT_LOGO_SIZE), 'property');
      upsertMeta('og:image:type', 'image/png', 'property');
    }
  }
  if (url) {
    upsertMeta('og:url', url, 'property');
    upsertCanonical(url);
  }
}
