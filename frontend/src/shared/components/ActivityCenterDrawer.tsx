import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Toast } from 'tdesign-mobile-react';
import { X, ThumbsUp, Trophy } from 'lucide-react';

import { fetchAnnouncements, type Announcement } from '@/api/announcement';
import {
  fetchKnowledgeOverview, fetchKnowledgeQuestions, toggleKnowledgeLike, type KnowledgeQuestion,
} from '@/api/knowledge';
import { formatDateTimeShort } from '@/shared/utils/url';
import { fetchChallenges, type Challenge } from '@/api/knowledgeChallenges';

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
  const [annExpanded, setAnnExpanded] = useState(false);
  const [annOverflow, setAnnOverflow] = useState(false);
  const announcementViewport = useRef<HTMLDivElement>(null);
  const announcementContent = useRef<HTMLDivElement>(null);
  const announcementId = useId();
  const [challenges, setChallenges] = useState<Challenge[]>([]);
  const [challengeTotal, setChallengeTotal] = useState(0);
  const [challengePage, setChallengePage] = useState(1);
  const [challengeReload, setChallengeReload] = useState(0);
  const [challengeLoading, setChallengeLoading] = useState(false);
  const [challengeError, setChallengeError] = useState(false);

  useEffect(() => {
    if (visible) setAnnExpanded(false);
  }, [visible]);

  useEffect(() => {
    if (!visible || annExpanded) return;
    const viewport = announcementViewport.current;
    const content = announcementContent.current;
    if (!viewport || !content) return;
    const measure = () => setAnnOverflow(viewport.scrollHeight > viewport.clientHeight + 1);
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(measure);
    observer?.observe(viewport);
    observer?.observe(content);
    window.addEventListener('resize', measure);
    window.visualViewport?.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
      window.visualViewport?.removeEventListener('resize', measure);
    };
  }, [visible, annExpanded, announcements]);

  useEffect(() => {
    if (!visible) return;
    let alive = true;
    setChallengeLoading(true);
    setChallengeError(false);
    void fetchChallenges('active', challengePage).then((result) => {
      if (!alive) return;
      setChallenges(result.items);
      setChallengeTotal(result.total);
    }).catch(() => {
      if (alive) { setChallenges([]); setChallengeError(true); }
    }).finally(() => { if (alive) setChallengeLoading(false); });
    return () => { alive = false; };
  }, [visible, challengePage, challengeReload]);

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
          <section className={`activity-section activity-announcements${annExpanded ? ' is-expanded' : ''}`}>
            <h3 className="activity-section__title">公告</h3>
            <div className="activity-announcements__viewport" ref={announcementViewport} id={announcementId}>
              <div ref={announcementContent}>
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
              </div>
            </div>
            {annOverflow && <button type="button" className="activity-announcements__toggle" aria-expanded={annExpanded} aria-controls={announcementId} onClick={() => setAnnExpanded(!annExpanded)}>{annExpanded ? '收起内容' : '查看完整内容'}</button>}
          </section>

          {/* ── 优质问题 TOP 榜（与后台知识贡献榜问题榜同源） ── */}
          <section className="activity-section">
            <div className="activity-section__head">
              <h3 className="activity-section__title">优质问题 TOP</h3>
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

          <section className="activity-section" aria-label="高价值问题">
            <div className="activity-section__head">
              <h3 className="activity-section__title">高价值问题</h3>
              <button type="button" className="activity-section__link" onClick={() => { onClose(); navigate('/admin/knowledge/challenges'); }}>查看全部</button>
            </div>
            {challengeError ? <div className="activity-empty">高价值问题加载失败 <button type="button" className="activity-section__link" onClick={() => setChallengeReload((previous) => previous + 1)}>重试高价值问题</button></div>
              : challengeLoading ? <div className="activity-empty">加载中…</div>
                : challenges.length === 0 ? <div className="activity-empty">暂无高价值问题</div>
                  : <ol className="featured-list">{challenges.map((challenge) => <li key={challenge.id} className="featured-item">
                    <button type="button" className="featured-item__main" onClick={() => { onClose(); navigate(`/admin/knowledge/challenges/${challenge.id}`); }}>
                      <span className="featured-item__info">
                        <span className="featured-item__title">{challenge.title}</span>
                        <span className="featured-item__project">悬赏 ¥{(challenge.bounty_fen / 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 })} · {challenge.state === 'paused' ? '已暂停' : '征集中'} · {formatDateTimeShort(challenge.ends_at)}截止</span>
                      </span>
                    </button>
                  </li>)}</ol>}
            {challengeTotal > 20 && <div className="activity-challenge-pages" aria-label="高价值问题分页">
              <button type="button" className="activity-section__link" disabled={challengeLoading || challengePage === 1} onClick={() => setChallengePage((previous) => previous - 1)}>上一页</button>
              <span>{challengePage} / {Math.ceil(challengeTotal / 20)}</span>
              <button type="button" className="activity-section__link" disabled={challengeLoading || challengePage * 20 >= challengeTotal} onClick={() => setChallengePage((previous) => previous + 1)}>下一页</button>
            </div>}
          </section>
        </div>
      </aside>
    </>
  );
}
