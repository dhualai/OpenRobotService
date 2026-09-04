// 检查当前用户是否关注微信公众号
// 页面挂载时自动调用后端 /wechat/check-subscription 接口，
// 返回 subscribed 状态。不缓存结果——离开页面再返回或刷新都会重新检查。
import { useState, useEffect, useRef } from 'react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

interface SubscriptionResult {
  subscribed: boolean;
}

/**
 * 检查用户是否关注公众号
 * @param username 当前登录用户名
 * @returns { subscribed: boolean | null, loading: boolean }
 *   - subscribed: null=检查中/失败(不弹窗), true=已关注, false=未关注
 */
export function useSubscriptionCheck(username: string | undefined | null) {
  const [subscribed, setSubscribed] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const requestIdRef = useRef(0);

  useEffect(() => {
    if (!username) {
      setLoading(false);
      return;
    }

    const currentId = ++requestIdRef.current;
    setLoading(true);

    (async () => {
      try {
        const request = createRequest(API_CONFIG.WECHAT.BASE_URL, '微信服务');
        const result = await request<SubscriptionResult>(`/check-subscription?username=${encodeURIComponent(username)}`, {
          skipCache: true,
        });
        // 防止竞态：只取最后一次请求的结果
        if (currentId !== requestIdRef.current) return;
        setSubscribed(result.subscribed);
      } catch {
        if (currentId !== requestIdRef.current) return;
        // 接口失败时不弹窗（subscribed=null），避免非微信用户被误拦
        setSubscribed(null);
      } finally {
        if (currentId === requestIdRef.current) setLoading(false);
      }
    })();
  }, [username]);

  return { subscribed, loading };
}
