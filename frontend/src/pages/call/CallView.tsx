// 我要摇人（需求视角）—— AI 问答助手 + 历史工单入口
import { useState } from 'react';
import { Navbar } from 'tdesign-mobile-react';
import ChatPanel from '@/shared/components/ChatPanel';
import HistoryTickets from './HistoryTickets';

export default function CallView() {
  const [showHistory, setShowHistory] = useState(false);

  if (showHistory) {
    return (
      <div className="chat-view">
        <Navbar 
          title="历史工单" 
          fixed 
          leftArrow 
          onLeftClick={() => setShowHistory(false)} 
        />
        <div className="page-container" style={{ paddingTop: 56, height: 'calc(100vh - 56px)', display: 'flex', flexDirection: 'column' }}>
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
          <button 
            className="navbar-history-btn" 
            onClick={() => setShowHistory(true)}
          >
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
              <line x1="16" y1="2" x2="16" y2="6" />
              <line x1="8" y1="2" x2="8" y2="6" />
              <line x1="3" y1="10" x2="21" y2="10" />
            </svg>
          </button>
        }
      />

      <div className="call-full-chat">
        <ChatPanel scene="call" />
      </div>
    </div>
  );
}
