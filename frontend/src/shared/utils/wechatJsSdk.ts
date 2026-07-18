// 微信 JS-SDK 初始化工具（可配置，默认关闭）
//
// 通过 VITE_WECHAT_JSSDK_ENABLED 控制：未启用时 initWechatJsSdk 直接跳过，
// 测试环境无需真实微信环境即可运行。启用时动态加载官方 jweixin 脚本（不占用 npm 依赖），
// 并向后端 /api/wechat/config/js-sdk-config 拉取签名后执行 wx.config。
import API_CONFIG from '@/config/api';
import { WECHAT_CONFIG } from '@/config/wechat';

const JWEIXIN_CDN = 'https://res.wx.qq.com/open/js/jweixin-1.6.0.js';

interface WxSdk {
  config(options: Record<string, unknown>): void;
  ready(cb: () => void): void;
  error(cb: (err: unknown) => void): void;
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
