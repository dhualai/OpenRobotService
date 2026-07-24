// 我要摇人（需求视角）—— AI 问答助手 + 历史工单入口（右上角 ≡ + 红点计数）
import { useState, useEffect } from 'react';
import { Navbar } from 'tdesign-mobile-react';
import ChatPanel from '@/shared/components/ChatPanel';
import HistoryTickets from './HistoryTickets';
import { qaListTickets } from '@/api/ai';
import { useWorkbenchStore } from '@/stores/workbench';

// 红点计数：新建 + 进行中 + 待处理（pending）
const ACTIVE_STATUSES = ['new', 'in_progress', 'pending'];

export default function CallView() {
  const [showHistory, setShowHistory] = useState(false);
  const [unread, setUnread] = useState(0);
  const tasksRefreshKey = useWorkbenchStore((s) => s.tasksRefreshKey);

  useEffect(() => {
    (async () => {
      try {
        const res = await qaListTickets(0, 200);
        const items = res?.data?.items || [];
        setUnread(items.filter((t) => ACTIVE_STATUSES.includes(t.status || '')).length);
      } catch { /* ignore */ }
    })();
  }, [tasksRefreshKey]);

  if (showHistory) {
    return (
      <div className="chat-view">
        <Navbar title="历史工单" fixed leftArrow onLeftClick={() => setShowHistory(false)} />
        <div className="page-container" style={{ paddingTop: 16, height: 'calc(100vh - 16px)', display: 'flex', flexDirection: 'column' }}>
          <HistoryTickets showHeader={false} />
        </div>
      </div>
    );
  }

  return (
    <div className="chat-view">
      <Navbar
        title="我要摇人"
        fixed
        right={
          <button className="navbar-history-btn" onClick={() => setShowHistory(true)} aria-label="历史工单">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
              <line x1="16" y1="2" x2="16" y2="6" />
              <line x1="8" y1="2" x2="8" y2="6" />
              <line x1="3" y1="10" x2="21" y2="10" />
            </svg>
            {unread > 0 && <span className="navbar-history-badge">{unread > 99 ? '99+' : unread}</span>}
          </button>
        }
      />
      <div className="call-full-chat">
        <ChatPanel scene="call" />
      </div>
    </div>
  );
}
