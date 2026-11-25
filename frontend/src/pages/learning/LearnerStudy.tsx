import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Loading, Toast } from 'tdesign-mobile-react';
import { ArrowLeft, Check, X, BookOpen, Lightbulb, AlertTriangle, ChevronLeft, ChevronRight } from 'lucide-react';
import { getLearnerArticle, submitLearnerAnswer, type LearnerArticleDetail } from '@/api/learner';
import './learner.css';

export default function LearnerStudy() {
  const navigate = useNavigate();
  const { articleId } = useParams<{ articleId: string }>();
  const [detail, setDetail] = useState<LearnerArticleDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [showResult, setShowResult] = useState(false);
  const [lastResult, setLastResult] = useState<{ is_correct: boolean; correct_answer: string[]; explanation: string; source_excerpt: string } | null>(null);
  const [answeredMap, setAnsweredMap] = useState<Record<string, { is_correct: boolean }>>({});

  // 加载文章详情
  useEffect(() => {
    if (!articleId) return;
    let active = true;
    setLoading(true);
    getLearnerArticle(articleId).then((result) => {
      if (!active) return;
      setDetail(result);
      const map: Record<string, { is_correct: boolean }> = {};
      result.answers.forEach((a) => { map[a.question_id] = { is_correct: a.is_correct }; });
      setAnsweredMap(map);
      // 找到第一题未答的题目
      const firstUnanswered = result.questions.findIndex((q) => !map[q.id]);
      setCurrentIndex(firstUnanswered >= 0 ? firstUnanswered : 0);
      setSelected([]);
      setShowResult(false);
      setLastResult(null);
    }).catch((err) => {
      if (!active) return;
      Toast({ message: `加载失败：${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [articleId]);

  const question = detail?.questions[currentIndex];
  const answeredIds = useMemo(() => new Set(detail?.answers.map((a) => a.question_id) ?? []), [detail]);

  // 当前题目是否已答过
  const isAnswered = question ? answeredIds.has(question.id) : false;
  const prevAnswered = question ? answeredMap[question.id] : undefined;

  // 当切换题目时，重置选择状态
  useEffect(() => {
    setSelected([]);
    setShowResult(false);
    setLastResult(null);
  }, [currentIndex]);

  const toggleOption = useCallback((key: string) => {
    if (!question || showResult || isAnswered) return;
    const isMultiple = question.question_type === 'multiple_choice';
    if (isMultiple) {
      setSelected((prev) => prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]);
    } else {
      setSelected([key]);
    }
  }, [question, showResult, isAnswered]);

  const handleSubmit = useCallback(async () => {
    if (!question || selected.length === 0 || submitting) return;
    setSubmitting(true);
    try {
      const result = await submitLearnerAnswer(articleId!, question.id, selected);
      setLastResult(result);
      setShowResult(true);
      setAnsweredMap((prev) => ({ ...prev, [question.id]: { is_correct: result.is_correct } }));
      // 本地 answers 列表也更新，这样 isAnswered 状态刷新
      setDetail((prev) => prev ? {
        ...prev,
        answers: [...prev.answers.filter((a) => a.question_id !== question.id), {
          question_id: question.id, answers: selected, is_correct: result.is_correct, created_at: new Date().toISOString(),
        }],
      } : prev);
      Toast({ message: result.is_correct ? '回答正确！🎉' : '答错了，看看解析吧', theme: result.is_correct ? 'success' : 'warning' });
    } catch (err) {
      Toast({ message: `提交失败：${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmitting(false);
    }
  }, [articleId, question, selected, submitting]);

  // 智能返回：优先历史栈，直接打开 URL 时 fallback 到学习中心
  const handleBack = useCallback(() => {
    if (window.history.length > 1) {
      navigate(-1);
    } else {
      navigate('/tasks/learning', { replace: true });
    }
  }, [navigate]);

  const goPrev = useCallback(() => {
    if (currentIndex > 0) setCurrentIndex((i) => i - 1);
  }, [currentIndex]);

  const goNext = useCallback(() => {
    if (detail && currentIndex < detail.questions.length - 1) {
      setCurrentIndex((i) => i + 1);
    } else if (detail) {
      // 全部答完 — 跳回列表
      Toast({ message: '🎉 恭喜完成所有题目！', theme: 'success' });
      navigate('/tasks/learning');
    }
  }, [detail, currentIndex, navigate]);

  if (loading || !detail) {
    return (
      <div className="learner-study">
        <div className="learner-study__loading">
          <Loading size="24px" text="加载中…" />
        </div>
      </div>
    );
  }

  const content = detail.article.content;
  const total = detail.questions.length;
  const answeredCount = detail.answers.length;

  return (
    <div className="learner-study">
      {/* 顶栏 */}
      <header className="learner-study__header">
        <button className="learner-study__back" onClick={handleBack} aria-label="返回">
          <ArrowLeft size={20} />
        </button>
        <div className="learner-study__title-area">
          <span className="learner-study__category">{detail.article.category}</span>
          <span className="learner-study__title" title={detail.article.title}>{detail.article.title}</span>
        </div>
      </header>

      {/* 左右分栏主体 */}
      <div className="learner-study__body">
        {/* 左栏：文章阅读 */}
        <section className="learner-study__reading">
          {content.summary && (
            <div className="learner-section">
              <div className="learner-section__label">结论摘要</div>
              <p>{content.summary}</p>
            </div>
          )}
          {content.applicability && (
            <div className="learner-section">
              <div className="learner-section__label">适用条件</div>
              <p>{content.applicability}</p>
            </div>
          )}
          {content.key_points?.length > 0 && (
            <div className="learner-section">
              <div className="learner-section__label">
                <Lightbulb size={14} /> 核心知识点
              </div>
              <ul className="learner-points">
                {content.key_points.map((kp, idx) => (
                  <li key={idx}>
                    <span className="learner-points__id">KP{idx + 1}</span>
                    <span>{kp.text}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {content.method && (
            <div className="learner-section">
              <div className="learner-section__label">处理方法</div>
              <p style={{ whiteSpace: 'pre-wrap' }}>{content.method}</p>
            </div>
          )}
          {content.cautions && (
            <div className="learner-section">
              <div className="learner-section__label">注意事项</div>
              <p>{content.cautions}</p>
            </div>
          )}
          {content.example && (
            <div className="learner-section">
              <div className="learner-section__label">示例</div>
              <p style={{ whiteSpace: 'pre-wrap' }}>{content.example}</p>
            </div>
          )}
          {content.tags?.length > 0 && (
            <div className="learner-section learner-tags">
              {content.tags.map((tag) => (
                <span key={tag} className="learner-tags__item">#{tag}</span>
              ))}
            </div>
          )}
        </section>

        {/* 右栏：答题区 */}
        <section className="learner-study__quiz">
          <div className="learner-quiz__header">
            <span className="learner-quiz__progress">
              <BookOpen size={14} /> Quest {currentIndex + 1} / {total}
            </span>
            {question && (
              <span className="learner-quiz__type">
                {question.question_type === 'single_choice' ? '单选题' : question.question_type === 'multiple_choice' ? '多选题' : '判断题'}
              </span>
            )}
          </div>

          {question ? (
            <>
              <div className="learner-quiz__stem">{question.stem}</div>

              <div className="learner-quiz__options">
                {question.options.map((opt) => {
                  const isSelected = selected.includes(opt.key);
                  const correctKey = showResult && lastResult?.correct_answer.includes(opt.key);
                  const wrongKey = showResult && isSelected && !lastResult?.correct_answer.includes(opt.key);
                  return (
                    <button
                      key={opt.key}
                      className={`learner-option ${isSelected ? 'is-selected' : ''} ${correctKey ? 'is-correct' : ''} ${wrongKey ? 'is-wrong' : ''}`}
                      onClick={() => toggleOption(opt.key)}
                    >
                      <span className="learner-option__key">{opt.key}</span>
                      <span className="learner-option__text">{opt.text}</span>
                      {correctKey && <Check size={16} className="learner-option__icon is-correct" />}
                      {wrongKey && <X size={16} className="learner-option__icon is-wrong" />}
                    </button>
                  );
                })}
              </div>

              {/* 提交按钮：每次都可以重做，后端取最后一次结果 */}
              <button
                className={`learner-quiz__submit ${selected.length === 0 || submitting ? 'is-disabled' : ''}`}
                onClick={handleSubmit}
                disabled={selected.length === 0 || submitting}
              >
                {submitting ? '提交中…' : (isAnswered ? '重新提交' : '提交答案')}
              </button>
              {isAnswered && prevAnswered && (
                <div className={`learner-quiz__feedback ${prevAnswered.is_correct ? 'is-done-correct' : 'is-done-wrong'}`}>
                  {prevAnswered.is_correct
                    ? <><Check size={16} /> 上次答对了，再答一次也可以</>
                    : <><X size={16} /> 上次答错了，试试能不能改对</>}
                </div>
              )}

              {/* 解析区 */}
              {showResult && lastResult && (
                <div className="learner-quiz__explanation">
                  <div className="learner-quiz__explanation-title">
                    {lastResult.is_correct ? '🎉 回答正确！' : '答案不对，看看解析'}
                  </div>
                  <div className="learner-quiz__ans">
                    <span className="learner-quiz__ans-label">正确答案：</span>
                    {lastResult.correct_answer.join(' / ')}
                  </div>
                  <div className="learner-quiz__ans">
                    <span className="learner-quiz__ans-label">解析：</span>
                    {lastResult.explanation}
                  </div>
                  {lastResult.source_excerpt && (
                    <div className="learner-quiz__source">
                      <AlertTriangle size={14} />
                      依据：{lastResult.source_excerpt}
                    </div>
                  )}
                </div>
              )}
            </>
          ) : (
            <div className="learner-quiz__empty">暂无题目</div>
          )}

          {/* 下一题按钮：每次答完/跳过都可以 */}
          <button className="learner-quiz__next" onClick={goNext}>
            {currentIndex < total - 1 ? <>下一题 <ChevronRight size={16} /></> : '完成所有题目'}
          </button>
        </section>
      </div>

      {/* 底栏：分段进度指示 */}
      <footer className="learner-study__footer">
        <button
          className={`learner-footer__btn ${currentIndex === 0 ? 'is-disabled' : ''}`}
          onClick={goPrev}
          disabled={currentIndex === 0}
        >
          <ChevronLeft size={16} /> 上一题
        </button>

        <div className="learner-progress-dots">
          {detail.questions.map((q, idx) => {
            const answered = answeredIds.has(q.id);
            const correct = answeredMap[q.id]?.is_correct;
            const isCurrent = idx === currentIndex;
            return (
              <button
                key={q.id}
                className={`learner-progress-dot ${isCurrent ? 'is-current' : ''} ${answered ? (correct ? 'is-correct' : 'is-wrong') : ''}`}
                onClick={() => setCurrentIndex(idx)}
                aria-label={`Quest ${idx + 1}${answered ? (correct ? '· 已答对' : '· 已答错') : ''}`}
              />
            );
          })}
        </div>

        <span className="learner-footer__count">{answeredCount} / {total}</span>
      </footer>
    </div>
  );
}
