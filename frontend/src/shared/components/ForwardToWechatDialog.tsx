// 讨论区消息 · 转发到微信 弹窗
//
// 仿微信转发面板：底部弹出，展示接收人列表（自己置顶 + 同事），
// 多选接收人 + 选择文本/链接卡片形式，调公众号客服消息推送。
// - 接收人需已绑定微信 open_id（未绑定的灰显不可选）；
// - 当前用户未绑定时，顶部提供"绑定微信"入口（输入 open_id，初版简化，后续可接 OAuth）。
// 初版仅单条文本/链接；合并转发（长图）留待后续。
import { useState, useEffect } from 'react';
import { Button, Toast } from 'tdesign-mobile-react';
import { useAuthStore } from '@/stores/auth';
import {
  getForwardTargets,
  forwardCommentToWechat,
  getMyWechatBind,
  bindMyWechatOpenid,
  type ForwardTarget,
} from '@/api/forward';
import type { DiscussionComment } from './DiscussionPanel';

interface Props {
  visible: boolean;
  taskId?: string | number;
  comment: DiscussionComment | null;
  onClose: () => void;
}

export default function ForwardToWechatDialog({ visible, taskId, comment, onClose }: Props) {
  const { username } = useAuthStore();
  const [targets, setTargets] = useState<ForwardTarget[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [asLink, setAsLink] = useState(false);
  const [sending, setSending] = useState(false);

  // 当前用户绑定状态
  const [myBound, setMyBound] = useState<boolean | null>(null);
  const [showBind, setShowBind] = useState(false);
  const [openIdInput, setOpenIdInput] = useState('');
  const [binding, setBinding] = useState(false);

  useEffect(() => {
    if (!visible || !taskId) return;
    setLoading(true);
    setSelected(new Set());
    setAsLink(false);
    setShowBind(false);
    setOpenIdInput('');
    Promise.all([getForwardTargets(taskId), getMyWechatBind()])
      .then(([t, b]) => {
        const list = Array.isArray(t) ? t : [];
        setTargets(list);
        setMyBound(!!b.bound);
        // 默认选中自己（若已绑定）
        const me = list.find((x) => x.is_self);
        if (me && me.wechat_bound) setSelected(new Set([me.username]));
      })
      .catch((err) => {
        Toast({ message: `加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      })
      .finally(() => setLoading(false));
  }, [visible, taskId]);

  const refreshTargets = async () => {
    if (!taskId) return;
    try {
      const t = await getForwardTargets(taskId);
      setTargets(Array.isArray(t) ? t : []);
    } catch { /* ignore */ }
  };

  const toggle = (u: string, bound: boolean) => {
    if (!bound) return;
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(u)) n.delete(u);
      else n.add(u);
      return n;
    });
  };

  const handleBind = async () => {
    const oid = openIdInput.trim();
    if (!oid) {
      Toast({ message: '请输入 open_id', theme: 'warning' });
      return;
    }
    setBinding(true);
    try {
      await bindMyWechatOpenid(oid);
      setMyBound(true);
      setShowBind(false);
      setOpenIdInput('');
      await refreshTargets();
      // 绑定后默认选中自己
      setSelected((prev) => new Set(prev).add(username || ''));
      Toast({ message: '绑定成功', theme: 'success' });
    } catch (err) {
      Toast({ message: `绑定失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setBinding(false);
    }
  };

  const handleForward = async () => {
    if (!taskId || !comment) return;
    if (selected.size === 0) {
      Toast({ message: '请选择接收人', theme: 'warning' });
      return;
    }
    setSending(true);
    try {
      const res = await forwardCommentToWechat(taskId, comment.id, Array.from(selected), asLink);
      const anyDelivered = res.results?.some((r) => r.status === 'delivered');
      Toast({
        message: res.message || (anyDelivered ? '已发送' : '发送失败'),
        theme: anyDelivered ? 'success' : 'warning',
      });
      onClose();
    } catch (err) {
      Toast({ message: `转发失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSending(false);
    }
  };

  if (!visible) return null;

  const selectableCount = targets.filter((t) => t.wechat_bound).length;

  return (
    <div className="detail-forward-mask" onClick={onClose}>
      <div className="detail-forward-panel" onClick={(e) => e.stopPropagation()}>
        <div className="detail-forward-header">
          <span className="detail-forward-title">转发到微信</span>
          <button type="button" className="detail-forward-close" onClick={onClose} aria-label="关闭">✕</button>
        </div>

        {/* 消息预览 */}
        {comment && (
          <div className="detail-forward-preview">
            <span className="detail-forward-preview__name">{comment.created_by_name || comment.created_by || '用户'}</span>
            <span className="detail-forward-preview__text">{stripText(comment.content)}</span>
          </div>
        )}

        {/* 附件提示：当前仅转发文字，图片/文件本体暂不支持（Phase 2） */}
        {comment?.attachments && comment.attachments.length > 0 && (
          <div className="detail-forward-preview__att-hint">
            含 {comment.attachments.length} 个附件，当前仅转发文字内容，图片/文件暂不支持
          </div>
        )}

        {/* 当前用户未绑定提示 */}
        {myBound === false && !showBind && (
          <div className="detail-forward-bind-tip" onClick={() => setShowBind(true)}>
            您还未绑定微信，点击此处绑定 open_id 后可转发给自己
          </div>
        )}
        {showBind && (
          <div className="detail-forward-bind-box">
            <input
              className="detail-forward-bind-input"
              type="text"
              placeholder="粘贴您的微信 open_id"
              value={openIdInput}
              onChange={(e) => setOpenIdInput(e.target.value)}
            />
            <Button size="small" theme="primary" loading={binding} onClick={handleBind}>绑定</Button>
            <Button size="small" theme="default" variant="outline" onClick={() => setShowBind(false)}>取消</Button>
          </div>
        )}

        {/* 接收人列表 */}
        <div className="detail-forward-list">
          {loading ? (
            <div className="detail-forward-empty">加载中…</div>
          ) : targets.length === 0 ? (
            <div className="detail-forward-empty">暂无可选接收人</div>
          ) : (
            targets.map((t) => {
              const checked = selected.has(t.username);
              const disabled = !t.wechat_bound;
              return (
                <div
                  key={t.id || t.username}
                  className={`detail-forward-item${checked ? ' is-checked' : ''}${disabled ? ' is-disabled' : ''}`}
                  onClick={() => toggle(t.username, t.wechat_bound)}
                >
                  <span className={`detail-forward-check${checked ? ' is-on' : ''}`} aria-hidden>
                    {checked ? '✓' : ''}
                  </span>
                  <span className="detail-forward-item__name">{t.name || t.username}{t.is_self ? '（我）' : ''}</span>
                  <span className="detail-forward-item__badge">
                    {t.wechat_bound ? '已绑定' : '未绑定'}
                  </span>
                </div>
              );
            })
          )}
        </div>

        {/* 形式选择 */}
        <div className="detail-forward-mode">
          <button
            type="button"
            className={`detail-forward-mode__btn${!asLink ? ' is-active' : ''}`}
            onClick={() => setAsLink(false)}
          >
            纯文本
          </button>
          <button
            type="button"
            className={`detail-forward-mode__btn${asLink ? ' is-active' : ''}`}
            onClick={() => setAsLink(true)}
          >
            链接卡片
          </button>
          <span className="detail-forward-mode__hint">
            {selectableCount} 人可收
          </span>
        </div>

        <div className="detail-forward-actions">
          <Button block theme="primary" loading={sending} onClick={handleForward} disabled={selected.size === 0}>
            转发{selected.size > 0 ? `（${selected.size}）` : ''}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** 去除 HTML 标签得到预览纯文本（与后端 _strip_html_for_wechat 一致） */
function stripText(html: string): string {
  if (!html) return '';
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  return (tmp.textContent || tmp.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 80);
}
