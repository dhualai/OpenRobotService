// 活动中心抽屉（摇人页公告条点开）
//
// 全屏右侧滑出，内容两段：
//   1) 公告：已发布公告全文（标题 + 正文，正文保留换行）
//   2) 精品工单 TOP 榜：与后台「知识贡献榜」同源（GET /api/admin/knowledge/questions?sort=likes），
//      按有效点赞数倒序，可按月切换；行内点赞走知识点赞接口（受 can_like 与每日 5 个限制）。
//
// 打开时才拉数据（visible 变化触发），关闭保留上次内容避免闪烁；点工单跳「系统任务」详情页。
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Toast } from 'tdesign-mobile-react';
import { X, ThumbsUp, Trophy } from 'lucide-react';

import { fetchAnnouncements, type Announcement } from '@/api/announcement';
import {
  fetchKnowledgeOverview, fetchKnowledgeQuestions, toggleKnowledgeLike, type KnowledgeQuestion,
} from '@/api/knowledge';
import { formatDateTimeShort } from '@/shared/utils/url';

interface Props {
  visible: boolean;
  onClose: () => void;
}

/** 榜单条数（抽屉里只展示前 N 名，完整榜单去后台知识贡献榜看） */
const FEATURED_LIMIT = 10;

/** 正在提交点赞的问题 id 集合：请求期间按钮禁用，防连点 */
type PendingSet = Set<number>;

/** 接口未返回月份时的兜底当月（北京时间），避免榜单整块空白 */
function fallbackMonth(): string {
  return new Date().toLocaleString('en-CA', { timeZone: 'Asia/Shanghai' }).slice(0, 7);
}

export default function ActivityCenterDrawer({ visible, onClose }: Props) {
  const navigate = useNavigate();
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [questions, setQuestions] = useState<KnowledgeQuestion[]>([]);
  const [months, setMonths] = useState<string[]>([]);
  const [month, setMonth] = useState('');
  const [annLoading, setAnnLoading] = useState(false);
  const [boardLoading, setBoardLoading] = useState(false);
  const [pending, setPending] = useState<PendingSet>(new Set());

  // 公告 + 月份信息：两者互不依赖，并行请求；任一失败都不清空另一段
  useEffect(() => {
    if (!visible) return;
    let alive = true;
    setAnnLoading(true);
    (async () => {
      const [annRes, ovRes] = await Promise.allSettled([
        fetchAnnouncements(0, 20),
        fetchKnowledgeOverview(),
      ]);
      if (!alive) return;
      setAnnLoading(false);
      if (annRes.status === 'fulfilled') setAnnouncements(annRes.value.items);
      if (ovRes.status === 'fulfilled') {
        setMonths(ovRes.value.months);
        setMonth((previous) => previous || ovRes.value.month);
      } else {
        setMonth((previous) => previous || fallbackMonth());
      }
      if (annRes.status === 'rejected' && ovRes.status === 'rejected') {
        Toast({ message: '活动内容加载失败，请稍后重试', theme: 'error' });
      }
    })();
    return () => { alive = false; };
  }, [visible]);

  // 榜单：月份确定后（含切换月份）拉一次，与后台知识榜同一排序口径（sort=likes）
  useEffect(() => {
    if (!visible || !month) return;
    let alive = true;
    setBoardLoading(true);
    fetchKnowledgeQuestions(month, 'likes', 1, FEATURED_LIMIT)
      .then((result) => { if (alive) setQuestions(Array.isArray(result?.items) ? result.items : []); })
      .catch(() => { if (alive) setQuestions([]); })
      .finally(() => { if (alive) setBoardLoading(false); });
    return () => { alive = false; };
  }, [visible, month]);

  /** 点赞 / 取消：先改本地（乐观），失败回滚并提示；限流、非有效问题等错误用后端文案 */
  const handleToggleLike = useCallback(async (question: KnowledgeQuestion) => {
    if (pending.has(question.task_id)) return;
    const nextLiked = !question.liked_by_me;

    setPending((previous) => new Set(previous).add(question.task_id));
    setQuestions((previous) => previous.map((item) => (
      item.task_id === question.task_id
        ? {
          ...item,
          liked_by_me: nextLiked,
          valid_like_count: Math.max(0, item.valid_like_count + (nextLiked ? 1 : -1)),
        }
        : item
    )));

    try {
      const updated = await toggleKnowledgeLike(question);
      setQuestions((previous) => previous.map((item) => (
        item.task_id === updated.task_id ? updated : item
      )));
    } catch (error) {
      setQuestions((previous) => previous.map((item) => (
        item.task_id === question.task_id ? question : item
      )));
      const fallback = nextLiked ? '点赞失败，请稍后重试' : '取消点赞失败，请稍后重试';
      Toast({
        message: error instanceof Error && error.message ? error.message : fallback,
        theme: 'error',
      });
    } finally {
      setPending((previous) => {
        const next = new Set(previous);
        next.delete(question.task_id);
        return next;
      });
    }
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
              <div className="activity-empty">{annLoading ? '加载中…' : '暂无公告'}</div>
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

          {/* ── 精品工单 TOP 榜（与后台知识贡献榜问题榜同源） ── */}
          <section className="activity-section">
            <div className="activity-section__head">
              <h3 className="activity-section__title">精品工单 TOP</h3>
              {months.length > 0 && (
                <select
                  className="activity-month-select"
                  aria-label="榜单月份"
                  value={month}
                  onChange={(event) => setMonth(event.target.value)}
                >
                  {months.map((value) => (
                    <option key={value} value={value}>{value.replace('-', '年')}月</option>
                  ))}
                </select>
              )}
            </div>
            {questions.length === 0 ? (
              <div className="activity-empty">{boardLoading ? '加载中…' : '本月暂无上榜问题'}</div>
            ) : (
              <ol className="featured-list">
                {questions.map((question, index) => (
                  <li key={question.task_id} className="featured-item">
                    <button
                      type="button"
                      className="featured-item__main"
                      onClick={() => handleOpenTicket(question.task_id)}
                    >
                      <span
                        className={`featured-item__rank ${index < 3 ? 'is-top' : ''}`}
                        aria-label={`第 ${index + 1} 名`}
                      >
                        {index + 1}
                      </span>
                      <span className="featured-item__info">
                        <span className="featured-item__title">{question.title}</span>
                        <span className="featured-item__project">
                          提单：{question.questioner.name} · 接单：{question.answerer.name}
                        </span>
                      </span>
                    </button>
                    <button
                      type="button"
                      className={`featured-item__like ${question.liked_by_me ? 'is-liked' : ''}`}
                      disabled={!question.can_like || pending.has(question.task_id)}
                      onClick={() => handleToggleLike(question)}
                      aria-pressed={question.liked_by_me}
                      aria-label={!question.can_like
                        ? '当前不可点赞'
                        : question.liked_by_me ? '取消点赞' : '点赞'}
                    >
                      <ThumbsUp size={14} strokeWidth={2} fill={question.liked_by_me ? 'currentColor' : 'none'} />
                      {/* 计数值单独成段，测试用 testid 定位（不依赖类名） */}
                      <span data-testid={`like-count-${question.task_id}`}>{question.valid_like_count}</span>
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
