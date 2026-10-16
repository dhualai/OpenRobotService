// 活动中心抽屉（摇人页公告条点开）
//
// 全屏右侧滑出，内容两段：
//   1) 公告：已发布公告全文（标题 + 正文，正文保留换行）
//   2) 精品工单 TOP 榜：只含已归档工单，按点赞数倒序；行内可点赞/取消（乐观更新 + 失败回滚）
//
// 打开时才拉数据（visible 变化触发），关闭保留上次内容避免闪烁；点工单跳「系统任务」详情页。
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Toast } from 'tdesign-mobile-react';
import { X, ThumbsUp, Trophy } from 'lucide-react';

import { fetchAnnouncements, type Announcement } from '@/api/announcement';
import { fetchFeaturedTickets, toggleTaskLike, type FeaturedTicket } from '@/api/taskLike';
import { formatDateTimeShort } from '@/shared/utils/url';

interface Props {
  visible: boolean;
  onClose: () => void;
}

/** 正在提交点赞的工单 id 集合：请求期间按钮禁用，防连点 */
type PendingSet = Set<number>;

export default function ActivityCenterDrawer({ visible, onClose }: Props) {
  const navigate = useNavigate();
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [tickets, setTickets] = useState<FeaturedTicket[]>([]);
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState<PendingSet>(new Set());

  // 打开抽屉时拉一次数据：公告与榜单互不依赖，并行请求；
  // 任一段失败都不清空另一段（Promise.allSettled 语义，各自 catch 后保持原样）
  useEffect(() => {
    if (!visible) return;
    let alive = true;
    setLoading(true);
    (async () => {
      const [annRes, tickRes] = await Promise.allSettled([
        fetchAnnouncements(0, 20),
        fetchFeaturedTickets(10),
      ]);
      if (!alive) return;
      if (annRes.status === 'fulfilled') setAnnouncements(annRes.value.items);
      if (tickRes.status === 'fulfilled') setTickets(tickRes.value);
      if (annRes.status === 'rejected' && tickRes.status === 'rejected') {
        Toast({ message: '活动内容加载失败，请稍后重试', theme: 'error' });
      }
      setLoading(false);
    })();
    return () => { alive = false; };
  }, [visible]);

  /** 点赞 / 取消：先改本地（乐观），失败回滚并提示 */
  const handleToggleLike = useCallback(async (ticket: FeaturedTicket) => {
    if (pending.has(ticket.id)) return;
    const nextLiked = !ticket.is_liked;

    setPending((prev) => new Set(prev).add(ticket.id));
    setTickets((prev) => prev.map((t) => (
      t.id === ticket.id
        ? { ...t, is_liked: nextLiked, like_count: Math.max(0, t.like_count + (nextLiked ? 1 : -1)) }
        : t
    )));

    const res = await toggleTaskLike(ticket.id, nextLiked);
    if (res) {
      setTickets((prev) => prev.map((t) => (
        t.id === ticket.id ? { ...t, is_liked: res.liked, like_count: res.like_count } : t
      )));
    } else {
      setTickets((prev) => prev.map((t) => (t.id === ticket.id ? ticket : t)));
      Toast({ message: nextLiked ? '点赞失败，请稍后重试' : '取消点赞失败，请稍后重试', theme: 'error' });
    }
    setPending((prev) => {
      const next = new Set(prev);
      next.delete(ticket.id);
      return next;
    });
  }, [pending]);

  const handleOpenTicket = (id: number) => {
    onClose();
    navigate(`/tasks/${id}`);
  };

  return (
    <>
      <div className={`drawer-overlay ${visible ? 'visible' : ''}`} onClick={onClose} />
      <aside className={`activity-drawer ${visible ? 'open' : ''}`} aria-hidden={!visible}>
        <header className="activity-drawer__header">
          <span className="activity-drawer__title">
            <Trophy size={16} strokeWidth={2} />
            活动中心
          </span>
          <button type="button" className="conv-drawer__close" onClick={onClose} aria-label="关闭">
            <X size={16} strokeWidth={2} />
          </button>
        </header>

        <div className="activity-drawer__body">
          {/* ── 公告 ── */}
          <section className="activity-section">
            <h3 className="activity-section__title">公告</h3>
            {announcements.length === 0 ? (
              <div className="activity-empty">{loading ? '加载中…' : '暂无公告'}</div>
            ) : announcements.map((item) => (
              <article key={item.id} className="activity-announcement">
                <div className="activity-announcement__head">
                  <span className="activity-announcement__title">{item.title}</span>
                  {item.is_pinned && <span className="activity-announcement__pin">置顶</span>}
                </div>
                {item.published_at && (
                  <div className="activity-announcement__time">{formatDateTimeShort(item.published_at)}</div>
                )}
                <p className="activity-announcement__content">{item.content}</p>
              </article>
            ))}
          </section>

          {/* ── 精品工单 TOP 榜 ── */}
          <section className="activity-section">
            <h3 className="activity-section__title">精品工单 TOP</h3>
            {tickets.length === 0 ? (
              <div className="activity-empty">{loading ? '加载中…' : '暂无上榜工单'}</div>
            ) : (
              <ol className="featured-list">
                {tickets.map((ticket, index) => (
                  <li key={ticket.id} className="featured-item">
                    <button
                      type="button"
                      className="featured-item__main"
                      onClick={() => handleOpenTicket(ticket.id)}
                    >
                      <span
                        className={`featured-item__rank ${index < 3 ? 'is-top' : ''}`}
                        aria-label={`第 ${index + 1} 名`}
                      >
                        {index + 1}
                      </span>
                      <span className="featured-item__info">
                        <span className="featured-item__title">{ticket.title}</span>
                        {ticket.project_name && (
                          <span className="featured-item__project">{ticket.project_name}</span>
                        )}
                      </span>
                    </button>
                    <button
                      type="button"
                      className={`featured-item__like ${ticket.is_liked ? 'is-liked' : ''}`}
                      disabled={pending.has(ticket.id)}
                      onClick={() => handleToggleLike(ticket)}
                      aria-pressed={ticket.is_liked}
                      aria-label={ticket.is_liked ? '取消点赞' : '点赞'}
                    >
                      <ThumbsUp size={14} strokeWidth={2} fill={ticket.is_liked ? 'currentColor' : 'none'} />
                      {/* 计数值单独成段，测试用 testid 定位（不依赖类名） */}
                      <span data-testid={`like-count-${ticket.id}`}>{ticket.like_count}</span>
                    </button>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      </aside>
    </>
  );
}
