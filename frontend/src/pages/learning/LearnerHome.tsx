import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Navbar, Loading, Toast } from 'tdesign-mobile-react';
import { BookOpen, RefreshCw, CheckCircle, ChevronRight, Trophy, Target, TrendingUp, Sparkles, BookX } from 'lucide-react';
import { listLearnerArticles, getLearnerProgress, type LearnerArticleSummary, type LearnerProgress } from '@/api/learner';
import './learner.css';

/** 智能返回：优先历史栈，无历史则回到 tasks（上一级来源页） */
function smartBack(navigate: ReturnType<typeof useNavigate>, fallback: string) {
  if (window.history.length > 1) {
    navigate(-1);
  } else {
    navigate(fallback, { replace: true });
  }
}

export default function LearnerHome() {
  const navigate = useNavigate();
  const handleBack = useCallback(() => smartBack(navigate, '/tasks'), [navigate]);
  const [articles, setArticles] = useState<LearnerArticleSummary[]>([]);
  const [progress, setProgress] = useState<LearnerProgress | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([
      listLearnerArticles(),
      getLearnerProgress(),
    ]).then(([articlesResult, progressResult]) => {
      if (!active) return;
      setArticles(articlesResult.items);
      setProgress(progressResult);
    }).catch((err) => {
      if (active) Toast({ message: `加载失败：${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refreshKey]);

  const completed = progress?.completed_articles ?? 0;
  const total = progress?.total_articles ?? 0;
  const correct = progress?.correct_questions ?? 0;
  const answered = progress?.answered_questions ?? 0;
  const completionRate = total > 0 ? Math.round((completed / total) * 100) : 0;
  const accuracyRate = answered > 0 ? Math.round((correct / answered) * 100) : 0;

  return (
    <div className="learner-home">
      <Navbar title="学习中心" fixed leftArrow onLeftClick={handleBack} />

      {/* Hero 渐变卡片 —— 借鉴 QuestionGeneration 的 hero 设计 */}
      <section className="learner-hero">
        <div className="learner-hero__bg" aria-hidden="true" />
        <div className="learner-hero__main">
          <div className="learner-eyebrow">
            <span />
            <BookOpen size={14} />
            <span>LEARNING CENTER</span>
          </div>
          <h1>每天学一点，持续进步</h1>
          <p>从经验文章中学习规范流程，答对题目加深记忆。</p>
        </div>

        {/* 进度统计卡片组 */}
        <div className="learner-hero__stats">
          <div className="learner-stat">
            <div className="learner-stat__icon learner-stat__icon--mint"><Target size={16} /></div>
            <div className="learner-stat__body">
              <div className="learner-stat__num">{completed}<span className="learner-stat__sep">/</span>{total}</div>
              <div className="learner-stat__label">完成课程</div>
            </div>
            <div className="learner-stat__bar">
              <div className="learner-stat__fill" style={{ width: `${completionRate}%` }} />
            </div>
          </div>
          <div className="learner-stat">
            <div className="learner-stat__icon learner-stat__icon--sky"><TrendingUp size={16} /></div>
            <div className="learner-stat__body">
              <div className="learner-stat__num">{accuracyRate}<span className="learner-stat__unit">%</span></div>
              <div className="learner-stat__label">答对率</div>
            </div>
            <div className="learner-stat__bar">
              <div className="learner-stat__fill learner-stat__fill--sky" style={{ width: `${accuracyRate}%` }} />
            </div>
          </div>
          <div className="learner-stat">
            <div className="learner-stat__icon learner-stat__icon--butter"><Trophy size={16} /></div>
            <div className="learner-stat__body">
              <div className="learner-stat__num">{correct}</div>
              <div className="learner-stat__label">累计答对 {answered} 题</div>
            </div>
          </div>
        </div>

        {/* 错题本快捷入口 */}
        <div className="learner-wrongbook-entry" onClick={() => navigate('/tasks/learning/wrongbook')}>
          <div className="learner-wrongbook-entry__icon"><BookX size={22} /></div>
          <div className="learner-wrongbook-entry__text">
            <strong>错题本</strong>
            <span>曾经答错的题目，可以重新做并标记掌握</span>
          </div>
          <ChevronRight size={18} className="learner-wrongbook-entry__arrow" />
        </div>
      </section>

      {/* 标题栏 + 刷新 */}
      <div className="learner-section-header">
        <div className="learner-section-title">
          <Sparkles size={16} />
          <h2>可用课程</h2>
        </div>
        <button className="learner-refresh" onClick={refresh} aria-label="刷新课程列表">
          <RefreshCw size={14} />
          <span>刷新</span>
        </button>
      </div>

      {loading ? (
        <div className="learner-loading">
          <Loading size="24px" text="加载中…" />
        </div>
      ) : articles.length === 0 ? (
        <div className="learner-empty">
          <div className="learner-empty__icon"><BookOpen size={40} strokeWidth={1.5} /></div>
          <p>暂无可用课程</p>
          <span>请等待管理员发布知识点</span>
        </div>
      ) : (
        <div className="learner-list">
          {articles.map((article) => {
            const percent = article.total_count > 0 ? Math.round((article.answered_count / article.total_count) * 100) : 0;
            return (
              <article
                key={article.id}
                className={`learner-card ${article.completed ? 'is-completed' : ''}`}
                onClick={() => navigate(`/tasks/learning/${article.id}`)}
              >
                <div className="learner-card__glow" aria-hidden="true" />
                <div className="learner-card__top">
                  <span className="learner-card__category">{article.category}</span>
                  {article.completed ? (
                    <span className="learner-card__status is-done">
                      <CheckCircle size={13} /> 已完成
                    </span>
                  ) : article.answered_count > 0 ? (
                    <span className="learner-card__status is-in-progress">
                      继续学习
                    </span>
                  ) : (
                    <span className="learner-card__status is-new">开始学习</span>
                  )}
                </div>

                <h3 className="learner-card__title">{article.title}</h3>

                {article.summary && (
                  <p className="learner-card__summary">{article.summary.slice(0, 80)}{article.summary.length > 80 ? '…' : ''}</p>
                )}

                {article.total_count > 0 && (
                  <div className="learner-card__progress">
                    <div className="learner-card__progress-bar">
                      <div className="learner-card__progress-fill" style={{ width: `${percent}%` }} />
                    </div>
                    <span className="learner-card__progress-text">
                      {article.answered_count} / {article.total_count} 题 · {percent}%
                    </span>
                  </div>
                )}

                <div className="learner-card__footer">
                  <span className="learner-card__version">v{article.active_version}</span>
                  <ChevronRight size={16} />
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
