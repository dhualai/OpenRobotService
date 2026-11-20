// 商家转账确认页桥接（WeixinJSBridge.invoke('requestMerchantTransfer')）单测
//
// 覆盖：环境不支持（无桥 / 超时未就绪）、ok / cancel / fail 三态映射、
// 回调缺失时的超时兜底，以及 WeixinJSBridgeReady 事件等待。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { isWeixinBrowser, requestMerchantTransfer } from '../merchantTransfer';

type InvokeCb = (res?: { err_msg?: string }) => void;

function installBridge(cbCapture: (cb: InvokeCb) => void) {
  const bridge = {
    invoke: vi.fn((_name: string, _params: unknown, cb?: InvokeCb) => {
      if (cb) cbCapture(cb);
    }),
  };
  (window as unknown as { WeixinJSBridge?: unknown }).WeixinJSBridge = bridge;
  return bridge;
}

const PARAMS = { mchId: '1230000109', appId: 'wx1234567890', package: 'mch_id=123&app_id=wx===' };

describe('requestMerchantTransfer', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    delete (window as unknown as { WeixinJSBridge?: unknown }).WeixinJSBridge;
  });

  afterEach(() => {
    vi.useRealTimers();
    delete (window as unknown as { WeixinJSBridge?: unknown }).WeixinJSBridge;
  });

  it('无 WeixinJSBridge（非微信环境）→ unsupported，且不会抛异常', async () => {
    const p = requestMerchantTransfer(PARAMS);
    await vi.advanceTimersByTimeAsync(3100);
    await expect(p).resolves.toEqual({ result: 'unsupported', raw: '' });
  });

  it('回调 :ok → ok（注意：ok 仅代表已确认/已受理，不等于到账）', async () => {
    let cb: InvokeCb | undefined;
    const bridge = installBridge((c) => {
      cb = c;
    });

    const p = requestMerchantTransfer(PARAMS);
    await Promise.resolve(); // 桥已就绪：等微任务把 invoke 发出去
    expect(bridge.invoke).toHaveBeenCalledWith(
      'requestMerchantTransfer',
      { mchId: PARAMS.mchId, appId: PARAMS.appId, package: PARAMS.package },
      expect.any(Function),
    );
    cb?.({ err_msg: 'requestMerchantTransfer:ok' });
    await expect(p).resolves.toEqual({ result: 'ok', raw: 'requestMerchantTransfer:ok' });
  });

  it('回调 :cancel → cancel（转账单仍在微信侧挂起，可再次提现复用）', async () => {
    let cb: InvokeCb | undefined;
    installBridge((c) => {
      cb = c;
    });
    const p = requestMerchantTransfer(PARAMS);
    await Promise.resolve();
    cb?.({ err_msg: 'requestMerchantTransfer:cancel' });
    await expect(p).resolves.toMatchObject({ result: 'cancel' });
  });

  it('回调 :fail → fail 并带可读提示', async () => {
    let cb: InvokeCb | undefined;
    installBridge((c) => {
      cb = c;
    });
    const p = requestMerchantTransfer(PARAMS);
    await Promise.resolve();
    cb?.({ err_msg: 'requestMerchantTransfer:fail' });
    await expect(p).resolves.toMatchObject({
      result: 'fail',
      message: '调起失败（requestMerchantTransfer:fail）',
    });
  });

  it('微信不回调时超时兜底为 fail，避免按钮永久 loading', async () => {
    installBridge(() => {
      /* 故意不回调 */
    });
    const p = requestMerchantTransfer(PARAMS);
    await vi.advanceTimersByTimeAsync(10100);
    await expect(p).resolves.toMatchObject({ result: 'fail', message: '调起超时，请重新提现' });
  });

  it('桥未就绪时等待 WeixinJSBridgeReady 事件再调起', async () => {
    let cb: InvokeCb | undefined;
    installBridge((c) => {
      cb = c;
    });
    // 先移除：模拟页面早期桥尚未注入
    delete (window as unknown as { WeixinJSBridge?: unknown }).WeixinJSBridge;

    const p = requestMerchantTransfer(PARAMS);
    installBridge((c) => {
      cb = c;
    });
    window.dispatchEvent(new Event('WeixinJSBridgeReady'));
    await Promise.resolve();
    cb?.({ err_msg: 'requestMerchantTransfer:ok' });
    await expect(p).resolves.toMatchObject({ result: 'ok' });
  });
});

describe('isWeixinBrowser', () => {
  const original = navigator.userAgent;
  afterEach(() => {
    Object.defineProperty(navigator, 'userAgent', { value: original, configurable: true });
  });

  it('识别微信 UA', () => {
    Object.defineProperty(navigator, 'userAgent', {
      value: 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0) MicroMessenger/8.0.40',
      configurable: true,
    });
    expect(isWeixinBrowser()).toBe(true);
  });

  it('非微信 UA 返回 false', () => {
    Object.defineProperty(navigator, 'userAgent', { value: 'Mozilla/5.0 Chrome/120', configurable: true });
    expect(isWeixinBrowser()).toBe(false);
  });
});
