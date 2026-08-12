// 会话抽屉：左侧滑出（80%宽）+ 遮罩 + 新建/切换/编辑标题/删除
// 仿千问/deepseek 移动端会话管理布局
import { useState, useEffect } from 'react';
import { Popup, Button, Toast } from 'tdesign-mobile-react';
import { AddIcon, Edit1Icon, DeleteIcon } from 'tdesign-icons-react';
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

  // 新建会话：清空当前 → ChatPanel 新建（延迟创建，首条消息时入库）。
  // 用 requestNewConversation 标记「显式新建」，使进入页逻辑不会把空白新会话覆盖成最近会话。
  const handleNew = () => {
    requestNewConversation();
    onClose();
  };

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
        {/* 新建会话按钮 */}
        <button type="button" className="conv-new-btn" onClick={handleNew}>
          <AddIcon size="20px" />
          <span>新建会话</span>
        </button>

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
                <button type="button" onClick={(e) => { e.stopPropagation(); openRename(conv); }} aria-label="编辑标题">
                  <Edit1Icon size="16px" />
                </button>
                <button type="button" onClick={(e) => { e.stopPropagation(); setDeleting(conv); }} aria-label="删除">
                  <DeleteIcon size="16px" />
                </button>
              </div>
            </div>
          ))}
        </div>
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
