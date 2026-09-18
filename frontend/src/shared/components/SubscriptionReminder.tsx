// 公众号关注提醒弹窗
// 页面挂载时检查用户是否关注服务号，未关注则弹窗提醒。
// 不缓存结果——离开页面再返回或刷新都会重新检查并弹窗。
// 仅对微信账号（username 形如 wechat_xxx）生效：手工/后台账号在 hook 内已直接放行，
// 不发请求、不弹窗。
import { useState } from 'react';
import { Dialog } from 'tdesign-mobile-react';
import { useSubscriptionCheck } from '@/shared/hooks/useSubscriptionCheck';

export default function SubscriptionReminder({ username }: { username?: string | null }) {
  const { subscribed, loading, isWechatUser } = useSubscriptionCheck(username);
  // dismissed 为本地 state：仅在当前页面会话内生效，
  // 离开页面后组件卸载、state 重置，再返回时会重新弹窗。
  const [dismissed, setDismissed] = useState(false);
  // 仅当「确实是微信用户 且 后端确认未关注」时才弹关注提醒；
  // 手工/后台账号（isWechatUser=false）一律不弹
  const visible = !loading && isWechatUser && subscribed === false && !dismissed;

  return (
    <Dialog
      visible={visible}
      title="温馨提示"
      confirmBtn="关闭"
      onConfirm={() => setDismissed(true)}
    >
      <p style={{ textAlign: 'center', fontSize: 15, lineHeight: 1.8, margin: 0 }}>
        您还未关注服务号，请先关注服务号！
      </p>
    </Dialog>
  );
}
