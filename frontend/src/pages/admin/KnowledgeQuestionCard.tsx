import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Toast } from 'tdesign-mobile-react';
import { ThumbsUp } from 'lucide-react';
import { toggleKnowledgeLike, type KnowledgeQuestion } from '@/api/knowledge';
import { knowledgeTime } from './knowledgeShared';

export function KnowledgeLikeButton({ question, onChange }: { question: KnowledgeQuestion; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const toggle = async () => {
    if (busy) return;
    setBusy(true);
    try { await toggleKnowledgeLike(question); onChange(); }
    catch (error) { Toast({ message: error instanceof Error ? error.message : '点赞失败，请重试', theme: 'error' }); }
    finally { setBusy(false); }
  };
  return <button type="button" className="knowledge-like" aria-label={`${question.liked_by_me ? '取消点赞' : '点赞'}：${question.title}`} aria-pressed={question.liked_by_me} disabled={busy || !question.can_like} onClick={(event) => { event.stopPropagation(); void toggle(); }}>
    <ThumbsUp size={14} />{question.valid_like_count}
  </button>;
}

export default function KnowledgeQuestionCard({ question, onChange }: { question: KnowledgeQuestion; onChange: () => void }) {
  const navigate = useNavigate();
  const location = useLocation();
  const open = () => navigate(`/admin/knowledge/questions/${question.task_id}`, { state: { knowledgeBackTo: `${location.pathname}${location.search}` } });
  return <section className="mac-card mac-card--pad knowledge-question" aria-label={question.title}>
    <div className="knowledge-question-head"><button type="button" className="knowledge-card-heading" onClick={open}><h3>{question.title}</h3></button><KnowledgeLikeButton question={question} onChange={onChange} /></div>
    <p className="knowledge-excerpt">{question.problem}</p>
    {question.value_point && <div className="knowledge-note">价值点：{question.value_point}</div>}
    <div className="knowledge-question-footer"><span>提单：{question.questioner.name} · 接单：{question.answerer.name}<br />提问于 {knowledgeTime(question.created_at)}</span></div>
    <button type="button" className="knowledge-link" onClick={open}>查看问题、解答与审核意见 →</button>
  </section>;
}
