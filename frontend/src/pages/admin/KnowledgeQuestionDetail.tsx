import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { Navbar } from 'tdesign-mobile-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import UserAvatarMenu from '@/shared/components/UserAvatarMenu';
import { fetchKnowledgeQuestion } from '@/api/knowledge';
import { useAuthStore } from '@/stores/auth';
import { KnowledgeLikeButton } from './KnowledgeQuestionCard';
import KnowledgeDiscussion from './KnowledgeDiscussion';
import { KnowledgeAvatar, KnowledgeState, knowledgeTime, useKnowledgeResource } from './knowledgeShared';

export function KnowledgeMarkdown({ text }: { text: string }) {
  return <div className="knowledge-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>;
}

export default function KnowledgeQuestionDetail() {
  const { taskId = '' } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const username = useAuthStore((state) => state.username);
  const backTo = location.state?.knowledgeBackTo;
  const returnPath = typeof backTo === 'string' && /^\/admin\/knowledge(?:\?|\/contributors\/)/.test(backTo) ? backTo : '/admin/knowledge?tab=questions';
  const resource = useKnowledgeResource(`question:${taskId}`, () => fetchKnowledgeQuestion(taskId));
  const question = resource.data;
  return <div className="dashboard-page knowledge-page"><Navbar title="归档知识" leftArrow onLeftClick={() => navigate(returnPath)} right={<UserAvatarMenu />} fixed />
    <div className="knowledge-content"><KnowledgeState loading={resource.loading && !question} error={resource.error} retry={resource.reload} />
      {question && <>
        <section className="mac-card mac-card--pad"><div className="knowledge-question-head"><h3>{question.title}</h3><KnowledgeLikeButton question={question} onChange={resource.reload} /></div><div className="knowledge-participants">{[{ user: question.questioner, label: '提单人' }, { user: question.answerer, label: '接单人' }].map(({ user, label }) => <div key={label}><KnowledgeAvatar user={user} /><span><small>{label}</small><strong>{user.name}</strong></span></div>)}</div><p className="knowledge-muted">提问于 {knowledgeTime(question.created_at)}</p><KnowledgeMarkdown text={question.problem} /></section>
        <section className="mac-card mac-card--pad"><h3>问题解答 · 已通过归档报告</h3><KnowledgeMarkdown text={question.content} /></section>
        <section className="mac-card mac-card--pad"><h3>审核意见</h3><p className="knowledge-muted">审核人身份不公开</p><KnowledgeMarkdown text={question.review_comment} /><div className="knowledge-note"><strong>价值点</strong><KnowledgeMarkdown text={question.value_point} /></div></section>
        <KnowledgeDiscussion key={`${username}:${taskId}`} taskId={taskId} />
      </>}
    </div>
  </div>;
}
