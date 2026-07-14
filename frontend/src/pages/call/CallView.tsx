// 我要摇人（需求视角）—— 上：AI 在线咨询 / 下：历史工单列表
import { Navbar } from 'tdesign-mobile-react';
import ChatPanel from '@/shared/components/ChatPanel';
import HistoryTickets from './HistoryTickets';

export default function CallView() {
  return (
    <div className="chat-view call-split">
      <Navbar title="我要摇人" fixed />
      <div className="call-top-chat">
        <ChatPanel scene="call" />
      </div>
      <div className="call-bottom-tickets">
        <HistoryTickets />
      </div>
    </div>
  );
}
