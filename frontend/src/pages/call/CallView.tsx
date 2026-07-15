// 我要摇人（需求视角）—— 上：AI 问答助手 / 下：历史工单（6:4 分栏，与系统任务一致）
import { Navbar } from 'tdesign-mobile-react';
import ChatPanel from '@/shared/components/ChatPanel';
import HistoryTickets from './HistoryTickets';

export default function CallView() {
  return (
    <div className="chat-view call-split">
      <Navbar title="我要摇人" fixed />

      {/* 上：AI 问答助手（60%） */}
      <div className="call-top-chat">
        <ChatPanel scene="call" />
      </div>

      {/* 下：历史工单（40%） */}
      <div className="call-bottom-tickets">
        <HistoryTickets />
      </div>
    </div>
  );
}
