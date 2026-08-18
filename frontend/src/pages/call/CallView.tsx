// 我要摇人 —— 全屏 AI 对话 + 左侧会话抽屉 + 右上角历史工单入口
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Navbar } from 'tdesign-mobile-react';
import { Menu, CalendarDays } from 'lucide-react';

import ChatPanel from '@/shared/components/ChatPanel';
import ConversationDrawer from '@/shared/components/ConversationDrawer';
import UserAvatarMenu from '@/shared/components/UserAvatarMenu';
import SubscriptionReminder from '@/shared/components/SubscriptionReminder';
import { qaListTickets } from '@/api/ai';
import { useWorkbenchStore } from '@/stores/workbench';
import { useAuthStore } from '@/stores/auth';

export default function CallView() {
  const navigate = useNavigate();
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
        <div className="chat-view">
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
                <button className="navbar-history-btn" onClick={() => navigate('/call/history')} aria-label="历史工单">
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
      </div>
      {/* 会话抽屉 + 遮罩 */}
      <ConversationDrawer visible={drawerOpen} onClose={() => setDrawerOpen(false)} />
    </div>
  );
}
