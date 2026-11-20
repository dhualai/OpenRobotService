// 我要摇人 · 历史工单列表页（独立路由 /call/history）
// 由 CallView 内的覆盖浮层改造为真路由页面：底部 TabBar 可见可点，
// 消除「浮层覆盖时路由仍是 /call、点底部菜单不跳转」的问题。
import { useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Navbar } from 'tdesign-mobile-react';
import { setShareMeta, shareImageUrl } from '@/shared/utils/shareMeta';
import { setupWechatShare } from '@/shared/utils/wechatJsSdk';
import HistoryTickets from './HistoryTickets';

export default function HistoryTicketsPage() {
  const location = useLocation();
  const navigate = useNavigate();

  // 转发卡片（ORS-1086）：历史工单是分享直链的常见落点，预置标题与描述。
  useEffect(() => {
    const title = '历史工单 · 摇人吧';
    const desc = '我在摇人吧提交过的工单，随时回看处理进度与结果。';
    setShareMeta({ title, description: desc });
    void setupWechatShare({ title, desc, link: window.location.href, imgUrl: shareImageUrl() });
  }, []);

  // 返回：有上一条历史（从 /call 进入）→ 原生返回对话页；
  // 刷新/分享直链进入（location.key === 'default'，无可用历史）→ 兜底回 /call。
  const handleBack = () => {
    if (location.key !== 'default') navigate(-1);
    else navigate('/call');
  };

  return (
    <div className="chat-view" style={{ background: 'var(--gradient-surface)' }}>
      <Navbar title="历史工单" fixed leftArrow onLeftClick={handleBack} />
      <div
        className="page-container"
        style={{ paddingTop: 16, flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}
      >
        <HistoryTickets showHeader={false} />
      </div>
    </div>
  );
}
