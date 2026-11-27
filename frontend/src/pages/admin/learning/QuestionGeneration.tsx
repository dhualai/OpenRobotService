import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Empty, Input, Pagination, Result, Select, Space, Spin, Tabs, Tag } from 'antd';
import { ArrowLeft, ArrowUpRight, BookOpen, FileText, ListChecks, Plus, RefreshCw, ShieldCheck, Sparkles } from 'lucide-react';
import { createLearningArticle, listLearningArticles, listLearningQuestions, PERM_LEARNING,
  type ArticleSummary, type LearningQuestion } from '@/api/learning';
import { useAuthStore } from '@/stores/auth';
import ArticleEditor from './ArticleEditor';
import LearningPreviewModal from './LearningPreview';
import QuestionEditor from './QuestionEditor';
import QuestionList from './QuestionList';
import SourceModal from './SourceModal';
import LearningTheme from './LearningTheme';
import './learning.css';

const ARTICLE_STATES = [{ value: '', label: '全部文章' }, { value: 'draft', label: '有编辑草稿' }, { value: 'enabled', label: '已启用' }, { value: 'disabled', label: '未启用' }];
const QUESTION_STATES = [{ value: '', label: '全部题目' }, { value: 'draft', label: '草稿' }, { value: 'pending_review', label: '待审核' }, { value: 'approved', label: '已审核' }, { value: 'rejected', label: '已驳回' }, { value: 'enabled', label: '当前可用' }, { value: 'stale', label: '需适配新版文章' }];

function QuestionGenerationContent() {
  const navigate = useNavigate();
  const allowed = useAuthStore((state) => state.hasPermission(PERM_LEARNING));
  const [tab, setTab] = useState('articles');
  const [keyword, setKeyword] = useState('');
  const [state, setState] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [articles, setArticles] = useState<ArticleSummary[]>([]);
  const [questions, setQuestions] = useState<LearningQuestion[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const [creating, setCreating] = useState(false);
  const [articleId, setArticleId] = useState<string | null>(null);
  const [question, setQuestion] = useState<LearningQuestion | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const refresh = useCallback(() => setRefreshKey((current) => current + 1), []);

  useEffect(() => {
    if (!allowed) return;
    let active = true;
    setLoading(true); setError('');
    const timer = window.setTimeout(async () => {
      try {
        if (tab === 'articles') {
          const result = await listLearningArticles(keyword, state, page);
          if (active) { setArticles(result.items); setTotal(result.total); }
        } else {
          const result = await listLearningQuestions(keyword, state, page);
          if (active) { setQuestions(result.items); setTotal(result.total); }
        }
      } catch (cause) { if (active) setError(cause instanceof Error ? cause.message : '加载失败，请重试'); }
      finally { if (active) setLoading(false); }
    }, keyword ? 250 : 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [allowed, tab, keyword, state, page, refreshKey]);

  if (!allowed) return <main className="learning-page"><Result status="403" title="暂无题目生成与维护权限" subTitle="请联系管理员分配权限后重试。" extra={<Button onClick={() => navigate('/admin/entries/operations')}>返回运营与数据</Button>} /></main>;

  return <main className="learning-page">
    <div className="learning-page-top"><Button type="text" icon={<ArrowLeft size={18} />} onClick={() => navigate('/admin/entries/operations')}>返回运营与数据</Button><span>后台管理 / 其他 / 运营与数据</span></div>
    <section className="learning-hero">
      <div className="learning-hero-main"><div className="learning-eyebrow"><span /> KNOWLEDGE STUDIO <Tag>内容管理</Tag></div>
        <h1>题目生成</h1><p>把经验提炼成知识，让每道题都有据可查。</p>
        <span className="learning-hero-description">从工单与技术材料中提炼精炼知识点，人工确认后生成相关问题，在这里完成日常维护与审核。</span>
        <Button type="primary" size="large" icon={<Plus size={18} />} onClick={() => setCreating(true)}>新建知识点</Button>
      </div>
      <div className="learning-hero-note"><BookOpen size={26} /><strong>先阅读，再答题</strong><p>答题时可持续查阅知识点文章，<br />不要求记住现场细节。</p><span><ShieldCheck size={14} /> 脱敏内容 · 人工审核 · 版本可追溯</span></div>
    </section>
    <section className="learning-flow" aria-label="内容生成流程">
      {[{ icon: FileText, title: '01 选择来源', text: '多个相似工单 / 技术材料' }, { icon: Sparkles, title: '02 提炼知识', text: 'AI辅助 · 人工确认版本' }, { icon: ListChecks, title: '03 生成题目', text: '依据文章 · 审核后启用' }].map((step) => <div key={step.title}><step.icon size={20} /><div><strong>{step.title}</strong><span>{step.text}</span></div></div>)}
    </section>
    <section className="learning-workspace">
      <Tabs activeKey={tab} onChange={(value) => { setTab(value); setState(''); setPage(1); setKeyword(''); setTotal(0); }}
        items={[{ key: 'articles', label: <span><BookOpen size={16} />知识点管理</span> }, { key: 'questions', label: <span><ListChecks size={16} />题库维护</span> }]} />
      <div className="learning-toolbar"><Input.Search aria-label={tab === 'articles' ? '搜索知识点文章' : '搜索题目'} allowClear placeholder={tab === 'articles' ? '搜索知识点标题' : '搜索题干或文章标题'} value={keyword} onChange={(event) => { setKeyword(event.target.value); setPage(1); }} />
        <Select aria-label="状态筛选" value={state} onChange={(value) => { setState(value); setPage(1); }} options={tab === 'articles' ? ARTICLE_STATES : QUESTION_STATES} />
        <Button aria-label="刷新列表" icon={<RefreshCw size={15} />} onClick={refresh}>刷新</Button><span className="learning-list-total">共 {total} {tab === 'articles' ? '篇文章' : '道题目'}</span></div>
      {error && <Alert type="error" role="alert" showIcon message={error} action={<Button size="small" onClick={refresh}>重试</Button>} />}
      {loading ? <div className="learning-loading" role="status" aria-label="正在加载"><Spin /></div> : !error && <>
        {tab === 'articles' ? articles.length ? <div className="learning-article-grid">{articles.map((article) => <article className="learning-article-card" key={article.id}>
          <div className="learning-card-meta"><Tag>{article.category}</Tag><Space size={0}><Tag color={article.enabled ? 'success' : 'default'}>{article.enabled ? '已启用' : '未启用'}</Tag>{article.has_draft && <Tag color="warning">有草稿</Tag>}</Space></div>
          <h2>{article.title}</h2><div className="learning-article-stats"><span><FileText size={14} />{article.active_version ? `已确认 v${article.active_version}` : '等待确认文章'}</span><span><ListChecks size={14} />{article.question_count} 道关联题目</span></div>
          <div className="learning-article-card-footer"><span>更新于 {new Date(`${article.updated_at}Z`).toLocaleDateString()}</span><Space><Button type="text" disabled={!article.active_version} onClick={() => setPreviewId(article.id)}>开卷预览</Button><Button type="primary" onClick={() => setArticleId(article.id)}>维护内容<ArrowUpRight size={14} /></Button></Space></div>
        </article>)}</div> : <div className="learning-empty"><Empty description={keyword || state ? '没有匹配的知识点文章，试试调整筛选条件' : '还没有知识点，从一组相似工单或技术材料开始'} />{!keyword && !state && <Button type="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>新建知识点</Button>}</div>
          : <QuestionList questions={questions} onEdit={setQuestion} onChanged={refresh} onArticle={setArticleId} />}
        {!!total && <Pagination className="learning-pagination" current={page} total={total} pageSize={20} showSizeChanger={false} onChange={setPage} />}
      </>}
    </section>
    <p className="learning-scope-note"><ShieldCheck size={14} />本页仅管理知识点与题目，不发布系统任务；学习预览不展示原始工单。</p>
    <SourceModal open={creating} onClose={() => setCreating(false)} onSubmit={async (title, sources) => {
      const article = await createLearningArticle(title, sources);
      setCreating(false); setArticleId(article.id); refresh();
    }} />
    <ArticleEditor articleId={articleId} refreshKey={refreshKey} onClose={() => setArticleId(null)} onChanged={refresh} onPreview={setPreviewId} />
    {question && <QuestionEditor question={question} onClose={() => setQuestion(null)} onSaved={refresh} />}
    <LearningPreviewModal articleId={previewId} onClose={() => setPreviewId(null)} />
  </main>;
}

export default function QuestionGeneration() {
  return <LearningTheme><QuestionGenerationContent /></LearningTheme>;
}
