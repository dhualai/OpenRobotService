// 微信 JS-SDK 初始化工具（可配置，默认关闭）
//
// 通过 VITE_WECHAT_JSSDK_ENABLED 控制：未启用时 initWechatJsSdk 直接跳过，
// 测试环境无需真实微信环境即可运行。启用时动态加载官方 jweixin 脚本（不占用 npm 依赖），
// 并向后端 /api/wechat/config/js-sdk-config 拉取签名后执行 wx.config。
import API_CONFIG, { RAW_BASE } from '@/config/api';
import { WECHAT_CONFIG } from '@/config/wechat';

const JWEIXIN_CDN = 'https://res.wx.qq.com/open/js/jweixin-1.6.0.js';

interface WxShareData {
  title: string;
  desc?: string;
  link: string;
  imgUrl?: string;
}

interface WxSdk {
  config(options: Record<string, unknown>): void;
  ready(cb: () => void): void;
  error(cb: (err: unknown) => void): void;
  updateAppMessageShareData(data: WxShareData): void;
  updateTimelineShareData(data: { title: string; link: string; imgUrl?: string }): void;
  previewImage(data: { urls: string[]; current?: string }): void;
  previewFile(data: { url: string; name?: string; size?: number }): void;
}

declare global {
  interface Window {
    wx?: WxSdk;
  }
}

export interface WxJsSdkConfig {
  appId: string;
  timestamp: string | number;
  nonceStr: string;
  signature: string;
}

/** 工单详情页 / 附件预览所需 JS 接口（分享卡片 + 微信原生文件预览），初始化时一次性注入 */
export const TASK_JS_API_LIST = ['updateAppMessageShareData', 'updateTimelineShareData', 'previewImage', 'previewFile'];

let sdkLoading: Promise<void> | null = null;

/** 动态加载官方 jweixin 脚本（已加载则复用） */
function loadJweixin(): Promise<void> {
  if (window.wx) return Promise.resolve();
  if (sdkLoading) return sdkLoading;
  sdkLoading = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = JWEIXIN_CDN;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => {
      sdkLoading = null;
      reject(new Error('微信 JS-SDK 脚本加载失败'));
    };
    document.head.appendChild(script);
  });
  return sdkLoading;
}

/** 向后端拉取当前页面的 JS-SDK 签名配置 */
async function fetchJsSdkConfig(pageUrl: string): Promise<WxJsSdkConfig> {
  const api = `${API_CONFIG.WECHAT.BASE_URL}/config/js-sdk-config?url=${encodeURIComponent(pageUrl)}`;
  const resp = await fetch(api);
  if (!resp.ok) throw new Error(`获取 JS-SDK 配置失败: ${resp.status}`);
  const data = await resp.json();
  // 兼容后端返回 { code, data } 包裹或直接返回配置对象
  return (data?.data ?? data) as WxJsSdkConfig;
}

// wx.config 串行队列：同一时刻只允许一个 wx.config 在进行，
// 避免 PC 微信因并行/重复 config 导致 ready/error 回调不触发、Promise 永久 pending（之前卡死的元凶）。
// 最终以链尾（最新 URL）的配置为准。
let configChain: Promise<boolean> = Promise.resolve(true);
let wxConfigured = false;
let lastConfigUrl = '';

/** 取参与签名的页面 URL（去掉 #hash；本项目用 BrowserRouter，无 hash 截断问题） */
function getPageUrl(): string {
  return window.location.href.split('#')[0];
}

/**
 * 带超时的 wx.config：PC 微信偶发 ready/error 都不回调，必须超时兜底，
 * 否则整个串行链会卡死，导致后续分享永远配置不上。
 */
function wxConfigWithTimeout(
  wx: WxSdk,
  cfg: WxJsSdkConfig,
  jsApiList: string[],
  timeoutMs = 8000,
): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      resolve(false);
    }, timeoutMs);
    wx.config({
      debug: false,
      appId: cfg.appId || WECHAT_CONFIG.appId,
      timestamp: cfg.timestamp,
      nonceStr: cfg.nonceStr,
      signature: cfg.signature,
      jsApiList,
    });
    wx.ready(() => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(true);
    });
    wx.error((err: unknown) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(false);
    });
  });
}

/** 真正执行一次 wx.config（签指定 pageUrl），返回是否成功 */
async function doConfigOnce(pageUrl: string, jsApiList: string[]): Promise<boolean> {
  try {
    await loadJweixin();
    const wx = window.wx;
    if (!wx) {
      return false;
    }
    const cfg = await fetchJsSdkConfig(pageUrl);
    const ok = await wxConfigWithTimeout(wx, cfg, jsApiList);
    if (ok) {
      wxConfigured = true;
      lastConfigUrl = pageUrl;
    } else {
      wxConfigured = false;
      lastConfigUrl = '';
    }
    return ok;
  } catch (e) {
    console.warn('[wechatJsSdk] doConfigOnce 异常:', e);
    wxConfigured = false;
    lastConfigUrl = '';
    return false;
  }
}

/**
 * 初始化微信 JS-SDK（串行、按 URL 重签）。
 * 默认关闭（VITE_WECHAT_JSSDK_ENABLED !== 'true' 时直接返回 false），便于测试环境跳过。
 * 行为：同一 URL 仅 config 一次；URL 变化（SPA 切工单）自动用新 URL 重新签名。
 * 调用方在「打开/切换详情页」时直接调用即可，会按当前页 URL 重新注册分享链接。
 * @param jsApiList 需要使用的 JS 接口列表，如 ['chooseImage', 'scanQRCode']
 * @returns 是否初始化成功
 */
export async function initWechatJsSdk(jsApiList: string[] = []): Promise<boolean> {
  if (!WECHAT_CONFIG.jsSdkEnabled) {
    return false;
  }
  const pageUrl = getPageUrl();

  // 串行：把本次配置追加到链尾，保证最终以「最新 URL」收尾，且不并行 wx.config
  configChain = configChain.then(async () => {
    // 同 URL 已成功配置过则跳过（避免 PC 重复 config 失败），直接复用
    if (wxConfigured && lastConfigUrl === pageUrl) {
      return true;
    }
    return doConfigOnce(pageUrl, jsApiList);
  });
  return configChain;
}

/** 当前是否启用 JS-SDK（供 UI 决定是否展示依赖 JS-SDK 的功能） */
export const isWechatJsSdkEnabled = (): boolean => WECHAT_CONFIG.jsSdkEnabled;

export interface WechatShareData {
  /** 分享卡片标题 */
  title: string;
  /**
   * 分享卡片描述（转发给好友/群时展示）。
   * 注意：微信对 desc 无官方硬字数上限，但客户端会显示截断——
   * 会话列表预览约显示前 30 字、点开卡片详情约显示前 54 字，超出以「…」收尾；
   * 朋友圈（updateTimelineShareData）不展示 desc，仅看 title。
   * 因此建议把关键信息前置，并传入约 120 字以内的文本即可（由调用方 slice 控制）。
   */
  desc: string;
  /** 点击卡片跳转地址（需在公众号 JS 接口安全域名内） */
  link: string;
  /** 缩略图 URL（公网可访问） */
  imgUrl?: string;
}

/**
 * 配置微信自定义分享（转发到好友/群/朋友圈）。
 * 复用 initWechatJsSdk 完成签名，再调用 updateAppMessageShareData / updateTimelineShareData。
 * 注意：JS-SDK 分享只是「配置卡片元信息」，用户需在微信内点右上角「…」实际转发，本函数仅完成预置。
 * @returns 是否配置成功（未启用/非微信环境/签名失败均返回 false）
 */
/**
 * 调用序号：用于消除「快速切换工单」时的异步竞态。
 * 每次调用自增，只有序号仍为「最新」的那次才允许真正写入 wx，
 * 避免较慢的旧异步链在晚到时用上一张工单的 link 覆盖当前分享卡片
 * （PC 微信端尤其明显，会表现为转发出上一单的链接）。
 */
let shareCallSeq = 0;

export async function setupWechatShare(data: WechatShareData): Promise<boolean> {
  const mySeq = ++shareCallSeq;

  // 首次校验：若已被更新的调用取代，直接放弃（连 wx.config 旧 URL 签名都不发）
  if (mySeq !== shareCallSeq) {
    return false;
  }

  const ok = await initWechatJsSdk(TASK_JS_API_LIST);

  // 二次校验：await 期间可能又来了更新的工单，此时旧调用作废，避免写入过期 link
  if (mySeq !== shareCallSeq) {
    return false;
  }
  if (!ok || !window.wx) {
    return false;
  }
  try {
    window.wx.updateAppMessageShareData({
      title: data.title,
      desc: data.desc,
      link: data.link,
      imgUrl: data.imgUrl || '',
    });
    window.wx.updateTimelineShareData({
      title: data.title,
      link: data.link,
      imgUrl: data.imgUrl || '',
    });
    return true;
  } catch (e) {
    console.warn('[wechatJsSdk] 配置分享失败:', e);
    return false;
  }
}

export interface WechatFilePreviewOptions {
  /** image → wx.previewImage；pdf / office → wx.previewFile */
  kind: 'image' | 'pdf' | 'office';
  /** 公网可访问的附件 URL（建议用后端 /api/tasks/files/{path} 代理地址，Absolute） */
  url: string;
  name?: string;
  size?: number;
}

/**
 * 微信内调起原生文件预览：图片用 wx.previewImage（全屏 / 滑动 / 长按保存），
 * pdf/office 用 wx.previewFile（微信内置文档查看器，可下载/转发）。
 * 失败（未启用 / 非微信 / 域名未配 / API 缺失）返回 false，调用方应回退 H5 预览。
 */
export async function setupWechatFilePreview(opts: WechatFilePreviewOptions): Promise<boolean> {
  if (!WECHAT_CONFIG.jsSdkEnabled) return false;
  if (typeof navigator === 'undefined' || !/MicroMessenger/i.test(navigator.userAgent)) return false;
  try {
    const ok = await initWechatJsSdk(TASK_JS_API_LIST);
    if (!ok || !window.wx) return false;
    const wx = window.wx;
    if (opts.kind === 'image') {
      wx.previewImage({ urls: [opts.url], current: opts.url });
    } else {
      wx.previewFile({ url: opts.url, name: opts.name || '', size: opts.size || 0 });
    }
    return true;
  } catch (e) {
    console.warn('[wechatJsSdk] 文件预览调起失败，回退 H5:', e);
    return false;
  }
}

/**
 * 是否 PC 端微信（Windows / Mac 客户端）。
 * PC 微信分享链接取「页面加载时」的 URL，且不认 JS-SDK 设的 link（手机端会认），
 * 因此 SPA 跳转进入工单后分享会停留在首张工单——需改用整页刷新跳转来修正。
 */
export const isPcWechat = (): boolean => /WindowsWechat|MacWechat/i.test(navigator.userAgent);

/**
 * 在 PC 微信内跳转：用整页跳转（window.location.href）替代 SPA，
 * 避免「先 SPA 渲染详情页、再整页刷新」的白屏闪烁（一次加载即可）。
 * 普通浏览器 / 手机端仍走 SPA，体验无损。
 * @param navigate react-router 的 navigate 函数
 * @param path 目标路由（不含 base 前缀，如 /tasks/123）
 */
export function navigateInWechat(navigate: (path: string) => void, path: string): void {
  if (isPcWechat()) {
    // base 必须用路由 basename（RAW_BASE），不能用 pathname 正则硬截，否则列表页
    // /app/tasks（末尾无 '/tasks/'）截不出正确前缀，会拼成 /app/tasks/tasks/123 导致跳转错位。
    const base = RAW_BASE === '/' ? '' : RAW_BASE.replace(/\/+$/, '');
    const target = `${window.location.origin}${base}${path}`;
    window.location.href = target;
  } else {
    navigate(path);
  }
}
