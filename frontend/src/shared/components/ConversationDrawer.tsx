// 会话抽屉：左侧滑出（80%宽，max 340px）+ 遮罩 + 标题栏 + 切换/重命名/删除 + 底部新建会话
// 视觉按设计稿 01b-chat-sessions：选中 blue-soft、行内 Pencil/Trash2、底部 secondary 新建按钮
import { useState, useEffect } from 'react';
import { Popup, Button, Toast } from 'tdesign-mobile-react';
import { Pencil, Trash2, MessageSquarePlus, X } from 'lucide-react';
import { useWorkbenchStore } from '@/stores/workbench';
import { formatDateTime } from '@/shared/utils/url';
import type { Conversation } from '@/api/conversation';

interface Props {
  visible: boolean;
  onClose: () => void;
}

export default function ConversationDrawer({ visible, onClose }: Props) {
  const { conversations, conversationId, refreshConversations, deleteConversation, renameConversation, setConversationId, requestNewConversation } = useWorkbenchStore();
  const [renaming, setRenaming] = useState<Conversation | null>(null);
  const [renameText, setRenameText] = useState('');
  const [deleting, setDeleting] = useState<Conversation | null>(null);

  useEffect(() => { if (visible) refreshConversations(); }, [visible, refreshConversations]);

  // 切换会话
  const handleSwitch = (id: number) => {
    setConversationId(id);
    onClose();
  };

  // 编辑标题
  const openRename = (conv: Conversation) => {
    setRenaming(conv);
    setRenameText(conv.title || '');
  };
  const handleRenameSubmit = async () => {
    if (renaming && renameText.trim()) {
      await renameConversation(renaming.id, renameText.trim());
    }
    setRenaming(null);
    setRenameText('');
  };

  // 删除确认
  const handleDeleteConfirm = async () => {
    if (deleting) {
      const ok = await deleteConversation(deleting.id);
      Toast({ message: ok ? '已删除' : '删除失败', theme: ok ? 'success' : 'error' });
    }
    setDeleting(null);
  };

  return (
    <>
      {/* 遮罩 */}
      <div
        className={`drawer-overlay ${visible ? 'visible' : ''}`}
        onClick={onClose}
      />
      {/* 抽屉 */}
      <aside className={`conv-drawer ${visible ? 'open' : ''}`}>
        {/* 标题栏（设计稿：居中标题 + 右侧圆形关闭钮） */}
        <header className="conv-drawer__header">
          <span className="conv-drawer__title">历史会话</span>
          <button type="button" className="conv-drawer__close" onClick={onClose} aria-label="关闭">
            <X size={16} strokeWidth={2} />
          </button>
        </header>
        {/* 会话列表 */}
        <div className="conv-list">
          {conversations.length === 0 ? (
            <div className="conv-list__empty">暂无历史会话</div>
          ) : conversations.map((conv) => (
            <div
              key={conv.id}
              className={`conv-item ${conversationId === conv.id ? 'active' : ''}`}
              onClick={() => handleSwitch(conv.id)}
            >
              <div className="conv-item__main">
                <div className="conv-item__title">{conv.title || '未命名会话'}</div>
                <div className="conv-item__time">{formatDateTime(conv.created_at).slice(0, 10)}</div>
              </div>
              <div className="conv-item__actions">
                <button type="button" onClick={(e) => { e.stopPropagation(); openRename(conv); }} aria-label="重命名">
                  <Pencil size={14} strokeWidth={2} />
                </button>
                <button type="button" onClick={(e) => { e.stopPropagation(); setDeleting(conv); }} aria-label="删除">
                  <Trash2 size={14} strokeWidth={2} />
                </button>
              </div>
            </div>
          ))}
        </div>
        {/* 底部固定操作区（设计稿：secondary 全宽新建会话按钮，hover blue-soft） */}
        <footer className="conv-drawer__footer">
          <button
            type="button"
            className="conv-drawer__new-btn"
            onClick={() => { requestNewConversation(); onClose(); }}
          >
            <MessageSquarePlus size={16} strokeWidth={2} />
            新建会话
          </button>
        </footer>
      </aside>

      {/* 编辑标题弹窗（底部） */}
      <Popup visible={!!renaming} onClose={() => setRenaming(null)} placement="bottom" showOverlay>
        <div className="conv-dialog">
          <h4 className="conv-dialog__title">修改标题</h4>
          <input
            className="conv-dialog__input"
            value={renameText}
            onChange={(e) => setRenameText(e.target.value)}
            placeholder="输入会话标题"
            autoFocus
          />
          <div className="conv-dialog__btns">
            <Button block theme="default" onClick={() => setRenaming(null)}>取消</Button>
            <Button block theme="primary" onClick={handleRenameSubmit}>确定</Button>
          </div>
        </div>
      </Popup>

      {/* 删除确认弹窗（底部） */}
      <Popup visible={!!deleting} onClose={() => setDeleting(null)} placement="bottom" showOverlay>
        <div className="conv-dialog">
          <p className="conv-dialog__msg">确定删除会话「{deleting?.title || '未命名'}」吗？删除后不可恢复。</p>
          <div className="conv-dialog__btns">
            <Button block theme="default" onClick={() => setDeleting(null)}>取消</Button>
            <Button block className="conv-delete-confirm-btn" onClick={handleDeleteConfirm}>删除</Button>
          </div>
        </div>
      </Popup>
    </>
  );
}
