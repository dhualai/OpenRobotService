// 我要摇人 —— 全屏 AI 对话 + 左侧会话抽屉 + 右上角历史工单入口
import { useState, useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { Navbar } from 'tdesign-mobile-react';

import ChatPanel from '@/shared/components/ChatPanel';
import HistoryTickets from './HistoryTickets';
import ConversationDrawer from '@/shared/components/ConversationDrawer';
import UserAvatarMenu from '@/shared/components/UserAvatarMenu';
import { qaListTickets } from '@/api/ai';
import { useWorkbenchStore } from '@/stores/workbench';
import { useAuthStore } from '@/stores/auth';

const ACTIVE_STATUSES = ['new', 'in_progress', 'pending'];

export default function CallView() {
  const [showHistory, setShowHistory] = useState(false);
  const location = useLocation();
  useEffect(() => {
    if ((location.state as { showHistory?: boolean })?.showHistory) setShowHistory(true);
  }, [location.state]);
  const [unread, setUnread] = useState(0);
  const { tasksRefreshKey, drawerOpen, setDrawerOpen, conversationTitle } = useWorkbenchStore();
  const username = useAuthStore((s) => s.username);
  const isAdmin = useAuthStore((s) => s.isAdmin);

  useEffect(() => {
    (async () => {
      try {
        const filters = !isAdmin && username ? { username } : undefined;
        const res = await qaListTickets(0, 200, filters);
        const items = res?.data?.items || [];
        setUnread(items.filter((t) => ACTIVE_STATUSES.includes(t.status || '')).length);
      } catch { /* ignore */ }
    })();
  }, [tasksRefreshKey, username, isAdmin]);

  return (
    <div className="app-shell">
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
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <line x1="3" y1="6" x2="21" y2="6" />
                  <line x1="3" y1="12" x2="21" y2="12" />
                  <line x1="3" y1="18" x2="21" y2="18" />
                </svg>
              </button>
            }
            right={
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <button className="navbar-history-btn" onClick={() => setShowHistory(true)} aria-label="历史工单">
                  <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
                    <line x1="16" y1="2" x2="16" y2="6" />
                    <line x1="8" y1="2" x2="8" y2="6" />
                    <line x1="3" y1="10" x2="21" y2="10" />
                  </svg>
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

        {/* 历史工单区（showHistory 时显示，覆盖在对话区上方） */}
        {showHistory && (
          <div className="chat-view" style={{ position: 'fixed', top: 0, left: 0, width: '100%', height: '100%', zIndex: 50, background: '#f5f5f5' }}>
            <Navbar title="历史工单" fixed leftArrow onLeftClick={() => setShowHistory(false)} />
            <div className="page-container" style={{ paddingTop: 16, height: 'calc(100vh - 16px)', display: 'flex', flexDirection: 'column' }}>
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
