import { useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Navbar } from 'tdesign-mobile-react';
import UserAvatarMenu from '@/shared/components/UserAvatarMenu';
import { fetchContributorQuestions } from '@/api/knowledge';
import { KnowledgeAvatar, KnowledgeEmpty, KnowledgeState, knowledgeCampaignMonths, useKnowledgeResource } from './knowledgeShared';
import KnowledgeQuestionCard from './KnowledgeQuestionCard';

export default function KnowledgeContributorPage() {
  const { userId = '' } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const month = params.get('month') || '';
  const role = ['questioner', 'answerer'].includes(params.get('role') || '') ? params.get('role')! : 'all';
  const board = params.get('board') === 'answerers' ? 'answerers' : 'questioners';
  const [page, setPage] = useState(1);
  const resource = useKnowledgeResource(`contributor:${userId}:${month}:${role}:${page}`, () => fetchContributorQuestions(userId, month || undefined, role, page));
  const data = resource.data;
  const selectedMonth = month || data?.month || knowledgeCampaignMonths[0];
  const changeFilter = (key: string, value: string) => { setParams((previous) => { const next = new URLSearchParams(previous); next.set('month', selectedMonth); next.set(key, value); return next; }); setPage(1); };
  return <div className="dashboard-page knowledge-page"><Navbar title="人员贡献工单" leftArrow onLeftClick={() => navigate(`/admin/knowledge?tab=${board}&month=${selectedMonth}`)} right={<UserAvatarMenu />} fixed />
    <div className="knowledge-content"><div className="knowledge-toolbar"><h2>TA的贡献工单</h2><select className="knowledge-select" aria-label="贡献月份" value={selectedMonth} onChange={(event) => changeFilter('month', event.target.value)}>{(data?.months || knowledgeCampaignMonths).map((value) => <option key={value} value={value}>{value.replace('-', '年')}月</option>)}</select></div>
      <p className="knowledge-note">按提问时间归属月份，仅展示本期审核通过并公开归档的工单，不公开未归档工单或原工单内部信息。</p>
      <KnowledgeState loading={resource.loading && !data} error={resource.error} retry={resource.reload} />
      {data && <><section className="mac-card mac-card--pad knowledge-person-summary"><KnowledgeAvatar user={data.user} /><div><h3>{data.user.name}</h3><p className="knowledge-muted">提单{data.question_count} · 接单{data.answer_count}</p></div></section>
        <div className="knowledge-subtabs" aria-label="人员工单分类">{[['all', '全部工单'], ['questioner', 'TA的提单'], ['answerer', 'TA的接单']].map(([value, label]) => <button type="button" key={value} aria-pressed={role === value} onClick={() => changeFilter('role', value)}>{label}</button>)}</div>
        {data.items.length === 0 && <KnowledgeEmpty>当前分类暂无公开归档工单。</KnowledgeEmpty>}
        {data.items.map((question) => <KnowledgeQuestionCard key={question.task_id} question={question} onChange={resource.reload} />)}
        {data.total > 20 && <div className="knowledge-pagination"><button className="knowledge-link" disabled={page === 1 || resource.loading} onClick={() => setPage(page - 1)}>上一页</button><span className="knowledge-muted">第{page}页 · 共{data.total}题</span><button className="knowledge-link" disabled={page * 20 >= data.total || resource.loading} onClick={() => setPage(page + 1)}>下一页</button></div>}
      </>}
    </div>
  </div>;
}
