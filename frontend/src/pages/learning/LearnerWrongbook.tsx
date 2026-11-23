import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Navbar, Loading, Toast } from 'tdesign-mobile-react';
import { ArrowLeft, RefreshCw, BookX, CheckCircle, Circle, ChevronRight, Award } from 'lucide-react';
import { listWrongbook, setWrongbookStatus, type WrongbookItem, type WrongbookStatus } from '@/api/learner';
import './learner.css';

const STATUS_FILTERS: { key: WrongbookStatus | ''; label: string }[] = [
  { key: '', label: '全部' },
  { key: 'wrong', label: '未掌握' },
  { key: 'mastered', label: '已掌握' },
  { key: 'skipped', label: '跳过' },
];

export default function LearnerWrongbook() {
  const navigate = useNavigate();
  const [items, setItems] = useState<WrongbookItem[]>([]);
  const [summary, setSummary] = useState({ total: 0, wrong: 0, mastered: 0, skipped: 0 });
  const [status, setStatus] = useState<WrongbookStatus | ''>('');
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    listWrongbook(status).then((result) => {
      if (!active) return;
      setItems(result.items);
      setSummary(result.summary);
    }).catch((err) => {
      if (active) Toast({ message: `加载失败：${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [status, refreshKey]);

  const handleMark = async (id: string, newStatus: WrongbookStatus) => {
    try {
      const result = await setWrongbookStatus(id, newStatus);
      Toast({
        message: result.status === 'mastered' ? '已标记为掌握' : result.status === 'skipped' ? '已跳过' : '已恢复',
        theme: 'success',
      });
      setRefreshKey((k) => k + 1);
    } catch (err) {
      Toast({ message: `操作失败：${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  return (
    <div className="learner-home">
      <Navbar title="错题本" fixed />

      {/* 概览卡片 */}
      <section className="learner-wrongbook-hero">
        <div className="learner-wrongbook-stat">
          <BookX size={18} />
          <div>
            <div className="learner-wrongbook-stat__num">{summary.wrong}</div>
            <div className="learner-wrongbook-stat__label">未掌握</div>
          </div>
        </div>
        <div className="learner-wrongbook-stat">
          <CheckCircle size={18} />
          <div>
            <div className="learner-wrongbook-stat__num">{summary.mastered}</div>
            <div className="learner-wrongbook-stat__label">已掌握</div>
          </div>
        </div>
        <div className="learner-wrongbook-stat">
          <Circle size={18} />
          <div>
            <div className="learner-wrongbook-stat__num">{summary.skipped}</div>
            <div className="learner-wrongbook-stat__label">已跳过</div>
          </div>
        </div>
        <div className="learner-wrongbook-stat">
          <Award size={18} />
          <div>
            <div className="learner-wrongbook-stat__num">{summary.total}</div>
            <div className="learner-wrongbook-stat__label">全部错题</div>
          </div>
        </div>
      </section>

      {/* 状态筛选 */}
      <div className="learner-wrongbook-filters">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.key || 'all'}
            className={`learner-wrongbook-filter ${status === f.key ? 'is-active' : ''}`}
            onClick={() => setStatus(f.key)}
          >
            {f.label}
          </button>
        ))}
        <button className="learner-home__refresh" onClick={refresh} aria-label="刷新">
          <RefreshCw size={14} />
        </button>
      </div>

      {loading ? (
        <div className="learner-home__loading">
          <Loading size="24px" text="加载中…" />
        </div>
      ) : items.length === 0 ? (
        <div className="learner-home__empty">
          <BookX size={48} strokeWidth={1.5} />
          <p>{status ? `当前筛选下没有错题` : '还没有错题，保持住！'}</p>
        </div>
      ) : (
        <div className="learner-home__list">
          {items.map((item) => {
            const question = item.question;
            return (
              <div key={item.id} className={`learner-wrongbook-card learner-wrongbook-card--${item.status}`}>
                <div className="learner-wrongbook-card__top">
                  <span className="learner-wrongbook-card__chapter">
                    {item.article_category} · {item.article_title}
                  </span>
                  <span className={`learner-wrongbook-card__status learner-wrongbook-card__status--${item.status}`}>
                    {item.status === 'wrong' ? `未掌握 · 错${item.wrong_count}次` : item.status === 'mastered' ? '已掌握' : '已跳过'}
                  </span>
                </div>

                <div className="learner-wrongbook-card__type">
                  {question.question_type === 'single_choice' ? '单选题' :
                   question.question_type === 'multiple_choice' ? '多选题' : '判断题'}
                </div>
                <div className="learner-wrongbook-card__stem">{question.stem}</div>

                {question.options?.length > 0 && (
                  <ul className="learner-wrongbook-card__options">
                    {question.options.map((opt) => (
                      <li key={opt.key}>
                        <span className="learner-wrongbook-card__opt-key">{opt.key}</span>
                        {opt.text}
                      </li>
                    ))}
                  </ul>
                )}

                <div className="learner-wrongbook-card__footer">
                  <button
                    className="learner-wrongbook-card__back"
                    onClick={() => navigate(`/tasks/learning/${item.article_id}`)}
                  >
                    <ChevronRight size={16} /> 回到课程重做
                  </button>
                  {item.status === 'wrong' ? (
                    <div className="learner-wrongbook-card__actions">
                      <button onClick={() => handleMark(item.id, 'mastered')} className="is-master">
                        标记已掌握
                      </button>
                      <button onClick={() => handleMark(item.id, 'skipped')} className="is-skip">
                        跳过
                      </button>
                    </div>
                  ) : item.status === 'mastered' ? (
                    <button onClick={() => handleMark(item.id, 'wrong')} className="learner-wrongbook-card__actions is-reset">
                      恢复为未掌握
                    </button>
                  ) : (
                    <button onClick={() => handleMark(item.id, 'wrong')} className="learner-wrongbook-card__actions is-reset">
                      取消跳过
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
