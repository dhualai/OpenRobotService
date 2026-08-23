// 微信 JS-SDK 初始化工具（可配置，默认关闭）
//
// 通过 VITE_WECHAT_JSSDK_ENABLED 控制：未启用时 initWechatJsSdk 直接跳过，
// 测试环境无需真实微信环境即可运行。启用时动态加载官方 jweixin 脚本（不占用 npm 依赖），
// 并向后端 /api/wechat/config/js-sdk-config 拉取签名后执行 wx.config。
import API_CONFIG from '@/config/api';
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

/**
 * 初始化微信 JS-SDK。
 * 默认关闭（VITE_WECHAT_JSSDK_ENABLED !== 'true' 时直接返回 false），便于测试环境跳过。
 * @param jsApiList 需要使用的 JS 接口列表，如 ['chooseImage', 'scanQRCode']
 * @returns 是否初始化成功
 */
export async function initWechatJsSdk(jsApiList: string[] = []): Promise<boolean> {
  if (!WECHAT_CONFIG.jsSdkEnabled) return false; // 未启用，直接跳过
  try {
    await loadJweixin();
    const wx = window.wx;
    if (!wx) return false;
    const pageUrl = window.location.href.split('#')[0];
    const cfg = await fetchJsSdkConfig(pageUrl);
    return await new Promise<boolean>((resolve) => {
      wx.config({
        debug: false,
        appId: cfg.appId || WECHAT_CONFIG.appId,
        timestamp: cfg.timestamp,
        nonceStr: cfg.nonceStr,
        signature: cfg.signature,
        jsApiList,
      });
      wx.ready(() => resolve(true));
      wx.error(() => resolve(false));
    });
  } catch (e) {
    console.warn('[wechatJsSdk] 初始化失败:', e);
    return false;
  }
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
export async function setupWechatShare(data: WechatShareData): Promise<boolean> {
  const ok = await initWechatJsSdk(TASK_JS_API_LIST);
  if (!ok || !window.wx) return false;
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
