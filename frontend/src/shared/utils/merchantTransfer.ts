// 微信内调起「商家转账 · 收款确认页」——WeixinJSBridge.invoke('requestMerchantTransfer')
//
// 触发时机（与后端三分支决策配套）：后端 POST /rewards/{id}/withdraw 返回
// action=confirm + package 时，前端用这里的 requestMerchantTransfer 拉起微信原生确认页。
//
// ⚠️ 语义要点（勿在 UI 上撒谎）：
//   ok  ≠ 已到账，仅表示「用户已确认/已受理」，最终以微信转账单回调（SUCCESS）为准；
//         前端只提示「已提交，等待到账」，具体状态由后端回调扭转后列表刷新体现。
//   cancel = 用户在确认页放弃，转账单仍在微信侧挂起（24h 内可再次用同一 package 拉起）。
//   fail   = 调起失败（参数错误 / 环境不支持 / package 过期），需要重新走提现接口取新 package。
//
// 依赖：微信 JS-SDK 之外的原生桥（WeixinJSBridge），微信 WebView 内置，无需加载脚本；
// 但桥在页面加载早期可能尚未就绪，需监听 WeixinJSBridgeReady 事件。

/** 桥就绪等待上限：非微信环境（如浏览器调试）最多等这么久就判定 unsupported */
const BRIDGE_READY_TIMEOUT_MS = 3000;
/** 单次 invoke 回调超时兜底：微信偶发既不回调成功也不回调失败，不能让按钮永久 loading */
const INVOKE_TIMEOUT_MS = 10000;

export interface MerchantTransferParams {
  /** 商户号 mchid（后端返回，勿前端硬编码） */
  mchId: string;
  /** 公众号/小程序 appid（后端返回） */
  appId: string;
  /** 微信返回的 package_info（24h 有效，过期需重新提现取新的） */
  package: string;
}

export type MerchantTransferResult =
  | { result: 'ok'; raw: string }
  | { result: 'cancel'; raw: string }
  | { result: 'fail'; raw: string; message: string }
  | { result: 'unsupported'; raw: '' };

type InvokeCallback = (res?: { err_msg?: string } & Record<string, unknown>) => void;

interface WeixinJSBridgeLike {
  invoke?: (name: string, params: Record<string, unknown>, cb?: InvokeCallback) => void;
}

declare global {
  interface Window {
    WeixinJSBridge?: WeixinJSBridgeLike;
  }
}

/** 是否在微信内置浏览器中（用于给出可操作的提示文案） */
export function isWeixinBrowser(): boolean {
  if (typeof navigator === 'undefined') return false;
  return /micromessenger/i.test(navigator.userAgent || '');
}

function currentBridge(): WeixinJSBridgeLike | undefined {
  if (typeof window === 'undefined') return undefined;
  return window.WeixinJSBridge;
}

/**
 * 等待 WeixinJSBridge 就绪。
 * 已就绪 → 立即返回；否则监听 WeixinJSBridgeReady，超时返回 null（不再无限等待）。
 */
function whenBridgeReady(timeoutMs: number): Promise<WeixinJSBridgeLike | null> {
  const ready = currentBridge();
  if (ready?.invoke) return Promise.resolve(ready);
  if (typeof window === 'undefined') return Promise.resolve(null);

  return new Promise((resolve) => {
    let settled = false;
    const finish = (bridge: WeixinJSBridgeLike | null) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      window.removeEventListener('WeixinJSBridgeReady', onReady);
      resolve(bridge);
    };
    const onReady = () => {
      const bridge = currentBridge();
      finish(bridge?.invoke ? bridge : null);
    };
    const timer = window.setTimeout(() => finish(currentBridge()?.invoke ? currentBridge()! : null), timeoutMs);
    window.addEventListener('WeixinJSBridgeReady', onReady, { once: true });
  });
}

/**
 * 拉起微信「商家转账」确认收款页。
 *
 * 返回结构化结果，绝不抛异常（调用方只需按 result 分支提示），
 * 异常/超时统一收敛为 fail，避免未捕获 Promise 打断页面的 loading 清理。
 */
export async function requestMerchantTransfer(
  params: MerchantTransferParams,
): Promise<MerchantTransferResult> {
  const bridge = await whenBridgeReady(BRIDGE_READY_TIMEOUT_MS);
  if (!bridge?.invoke) return { result: 'unsupported', raw: '' };

  return new Promise<MerchantTransferResult>((resolve) => {
    let settled = false;
    const settle = (res: MerchantTransferResult) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      resolve(res);
    };
    const timer = window.setTimeout(
      () => settle({ result: 'fail', raw: '', message: '调起超时，请重新提现' }),
      INVOKE_TIMEOUT_MS,
    );

    try {
      bridge.invoke!(
        'requestMerchantTransfer',
        {
          mchId: params.mchId,
          appId: params.appId,
          package: params.package,
        },
        (res) => {
          const raw = String(res?.err_msg || '');
          // 微信回调形如 requestMerchantTransfer:ok / :cancel / :fail
          if (raw.endsWith(':ok')) settle({ result: 'ok', raw });
          else if (raw.endsWith(':cancel')) settle({ result: 'cancel', raw });
          else settle({ result: 'fail', raw, message: raw ? `调起失败（${raw}）` : '调起失败，请重新提现' });
        },
      );
    } catch (err) {
      settle({
        result: 'fail',
        raw: '',
        message: err instanceof Error ? err.message : '调起异常，请重新提现',
      });
    }
  });
}
