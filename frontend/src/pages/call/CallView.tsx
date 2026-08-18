// 我要摇人 —— 全屏 AI 对话 + 左侧会话抽屉 + 右上角历史工单入口
import { useState, useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Navbar } from 'tdesign-mobile-react';
import { Menu, CalendarDays } from 'lucide-react';

import ChatPanel from '@/shared/components/ChatPanel';
import HistoryTickets from './HistoryTickets';
import ConversationDrawer from '@/shared/components/ConversationDrawer';
import UserAvatarMenu from '@/shared/components/UserAvatarMenu';
import SubscriptionReminder from '@/shared/components/SubscriptionReminder';
import { useSwipeToClose } from '@/shared/utils/useSwipeToClose';
import { qaListTickets } from '@/api/ai';
import { useWorkbenchStore } from '@/stores/workbench';
import { useAuthStore } from '@/stores/auth';

export default function CallView() {
  const [showHistory, setShowHistory] = useState(false);
  // 历史工单浮层是覆盖层（路由仍是 /call），右滑手势关闭浮层而非回滚路由/退出应用
  const swipeToCloseHistory = useSwipeToClose(() => setShowHistory(false));
  const location = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    if ((location.state as { showHistory?: boolean })?.showHistory) {
      setShowHistory(true);
      // 消费后立即清空 location.state：React Router 的 state 底层是 history.state，
      // 页面刷新后会残留，导致首屏一直停在历史工单列表（看起来像异常跳转）。
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location.state, location.pathname, navigate]);

  // 历史工单浮层打开期间，拦截 iOS 微信屏幕最左边缘的系统级右滑返回：
  // 压一层占位 history（复制 react-router 当前 state 保持兼容），使边缘右滑先 pop 占位层
  // （触发 popstate）而非退出 webview；popstate 时关闭浮层，占位层被自然消费、history 深度恢复。
  // （浮层区域内非边缘的 touch 右滑另由 useSwipeToClose 处理，二者互补。）
  useEffect(() => {
    if (!showHistory) return;
    const seedState = window.history.state ?? null;
    window.history.pushState(seedState, '', window.location.href);
    const onPop = () => setShowHistory(false);
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [showHistory]);

  const [unread, setUnread] = useState(0);
  const { tasksRefreshKey, drawerOpen, setDrawerOpen, conversationTitle } = useWorkbenchStore();
  const username = useAuthStore((s) => s.username);
  const isAdmin = useAuthStore((s) => s.isAdmin);

  useEffect(() => {
    (async () => {
      try {
        // 复用列表接口（limit=1 最小开销）取「除已关闭外」总数，无需额外统计接口
        const filters = !isAdmin && username ? { username } : undefined;
        const res = await qaListTickets(0, 1, filters);
        setUnread(res?.data?.active_total ?? 0);
      } catch { /* ignore */ }
    })();
  }, [tasksRefreshKey, username, isAdmin]);

  return (
    <div className="app-shell">
      <SubscriptionReminder username={username} />
      {/* 内容区（抽屉打开时右挤）。
          ChatPanel 始终 mounted（showHistory 时 display:none 隐藏而非卸载，
          避免切历史后回来消息丢失 */}
      <div className={`app-shell__content ${drawerOpen ? 'is-shifted' : ''}`}>
        {/* 对话区（始终 mounted，showHistory 时隐藏） */}
        <div className="chat-view" style={showHistory ? { display: 'none' } : undefined}>
          <Navbar
            title={<span className="call-navbar-title">{conversationTitle}</span>}
            fixed
            left={
              <button className="navbar-menu-btn" onClick={() => setDrawerOpen(!drawerOpen)} aria-label="会话列表">
                <Menu size={20} strokeWidth={2} />
              </button>
            }
            right={
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <button className="navbar-history-btn" onClick={() => setShowHistory(true)} aria-label="历史工单">
                  <CalendarDays size={20} strokeWidth={2} />
                  {unread > 0 && <span className="navbar-history-badge">{unread > 99 ? '99+' : unread}</span>}
                </button>
                <UserAvatarMenu />
              </div>
            }
          />
          <div className="call-full-chat">
            <ChatPanel scene="call" />
          </div>
        </div>

        {/* 历史工单区（showHistory 时显示，覆盖在对话区上方）。
            用 absolute 相对 .app-shell(内容区，已排除底部 TabBar) 定位，
            而非 fixed 全屏，避免盖住底部模块入口 TabBar（详情页是独立路由天然不盖）。 */}
        {showHistory && (
          <div className="chat-view" style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, zIndex: 50, background: 'var(--gradient-surface)' }} {...swipeToCloseHistory}>
            <Navbar title="历史工单" fixed leftArrow onLeftClick={() => setShowHistory(false)} />
            <div className="page-container" style={{ paddingTop: 16, flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
              <HistoryTickets showHeader={false} />
            </div>
          </div>
        )}
      </div>
      {/* 会话抽屉 + 遮罩 */}
      <ConversationDrawer visible={drawerOpen} onClose={() => setDrawerOpen(false)} />
    </div>
  );
}
