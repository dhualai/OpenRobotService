// 检查当前用户是否关注微信公众号
// 页面挂载时自动调用后端 /wechat/check-subscription 接口，
// 返回 subscribed 状态。不缓存结果——离开页面再返回或刷新都会重新检查。
//
// 微信用户判断：微信登录账号 username 形如 wechat_xxx；手工/后台账号（如管理员）不是
// 微信用户、没有公众号订阅关系，直接放行（subscribed=true）且不发请求，避免无效调用。
import { useState, useEffect, useRef } from 'react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

interface SubscriptionResult {
  subscribed: boolean;
  /** 后端返回：true=微信用户（可查公众号订阅）；false=手工/后台账号（无订阅关系） */
  is_wechat_user?: boolean;
}

interface SubscriptionCheckResult {
  /** 是否已关注：null=检查中/失败(不弹窗), true=已关注, false=未关注 */
  subscribed: boolean | null;
  /** 是否微信用户：false 时界面一律不弹关注提醒 */
  isWechatUser: boolean;
  loading: boolean;
}

/**
 * 检查用户是否关注公众号
 * @param username 当前登录用户名
 */
export function useSubscriptionCheck(username: string | undefined | null): SubscriptionCheckResult {
  const [subscribed, setSubscribed] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  // isWechatUser 默认 true（登录态下用户名通常以 wechat_ 开头）；非微信用户名在 effect 内
  // 同步置 false 并直接放行，保证管理端等手工账号不发请求、不弹窗
  const [isWechatUser, setIsWechatUser] = useState(true);
  const requestIdRef = useRef(0);

  useEffect(() => {
    // 微信登录账号的 username 统一为 wechat_ 前缀（后端 generate_wechat_username）
    const isWechatAccount = !!username && username.startsWith('wechat_');

    if (!username || !isWechatAccount) {
      // 非微信用户：无公众号订阅关系，视为「无需提醒关注」（loading 结束、不弹窗）
      setIsWechatUser(false);
      setSubscribed(true);
      setLoading(false);
      return;
    }

    const currentId = ++requestIdRef.current;
    setLoading(true);
    setIsWechatUser(true);

    (async () => {
      try {
        const request = createRequest(API_CONFIG.WECHAT.BASE_URL, '微信服务');
        const result = await request<SubscriptionResult>(`/check-subscription?username=${encodeURIComponent(username)}`, {
          skipCache: true,
        });
        // 防止竞态：只取最后一次请求的结果
        if (currentId !== requestIdRef.current) return;
        setSubscribed(result.subscribed);
        // 后端复核 is_wechat_user（与前端 wechat_ 前缀判断双保险）：
        // 为 false 视为无需提醒，避免后端判定与前端前缀规则不同步时误弹窗
        setIsWechatUser(result.is_wechat_user !== false);
      } catch {
        if (currentId !== requestIdRef.current) return;
        // 接口失败时不弹窗（subscribed=null），避免非微信用户被误拦
        setSubscribed(null);
      } finally {
        if (currentId === requestIdRef.current) setLoading(false);
      }
    })();
  }, [username]);

  return { subscribed, loading, isWechatUser };
}
