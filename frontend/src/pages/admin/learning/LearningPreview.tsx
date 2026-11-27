import { useEffect, useState } from 'react';
import { Alert, Button, Checkbox, Empty, Modal, Spin, Tag } from 'antd';
import { answerLearningPreview, getLearningPreview, QUESTION_TYPES, type LearningPreview, type PreviewResult, type ReadingArticle } from '@/api/learning';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';

export function ArticleReading({ article }: { article: ReadingArticle }) {
  const sections = [
    ['知识结论', article.summary], ['适用条件', article.applicability],
    ['关键方法', article.method], ['注意事项', article.cautions], ['简短示例', article.example],
  ];
  return <section className="learning-reading" aria-label="知识点文章">
    <Tag color="processing">{article.category}</Tag>
    <h2>{article.title}</h2>
    {sections.slice(0, 2).map(([label, value]) => value && <div key={label}><h3>{label}</h3><MarkdownRenderer content={value} /></div>)}
    {!!article.key_points.length && <div><h3>核心知识点</h3><ol>{article.key_points.map((point, index) => <li key={index}><MarkdownRenderer content={point.text} /></li>)}</ol></div>}
    {sections.slice(2).map(([label, value]) => value && <div key={label}><h3>{label}</h3><MarkdownRenderer content={value} /></div>)}
  </section>;
}

export default function LearningPreviewModal({ articleId, onClose }: { articleId: string | null; onClose: () => void }) {
  const [data, setData] = useState<LearningPreview | null>(null);
  const [availableOnly, setAvailableOnly] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [answers, setAnswers] = useState<Record<string, string[]>>({});
  const [results, setResults] = useState<Record<string, PreviewResult>>({});
  const [submitting, setSubmitting] = useState<string | null>(null);

  useEffect(() => {
    if (!articleId) return;
    let active = true;
    setLoading(true); setError(''); setData(null); setAnswers({}); setResults({});
    getLearningPreview(articleId, availableOnly).then((result) => {
      if (active) setData(result);
    }).catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : '预览加载失败');
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [articleId, availableOnly]);

  const submit = async (questionId: string) => {
    if (!articleId) return;
    setSubmitting(questionId); setError('');
    try {
      const result = await answerLearningPreview(articleId, questionId, answers[questionId] || []);
      setResults((current) => ({ ...current, [questionId]: result }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : '模拟提交失败'); }
    finally { setSubmitting(null); }
  };

  return <Modal open={!!articleId} title="开卷学习预览" onCancel={onClose} footer={null} width={820} destroyOnHidden>
    <Alert type="info" showIcon message="先阅读知识点，再回答相关问题；答题时可继续查阅文章。后台模拟不产生学习记录。" />
    <Checkbox checked={availableOnly} onChange={(event) => setAvailableOnly(event.target.checked)} className="learning-preview-filter">仅预览已审核且启用的题目</Checkbox>
    {error && <Alert type="error" showIcon message={error} role="alert" />}
    {loading && <div className="learning-loading"><Spin /></div>}
    {data && <>
      <ArticleReading article={data.article} />
      <div className="learning-preview-heading"><span>阅读完成，试着回答</span><Tag>文章 v{data.version}</Tag></div>
      {!data.questions.length && <Empty description="当前范围还没有题目，请先生成或审核题目" />}
      {data.questions.map((question, index) => <fieldset className="learning-preview-question" key={question.id}>
        <legend><Tag color="processing">{QUESTION_TYPES[question.question_type]}</Tag>{index + 1}. {question.stem}</legend>
        {question.options.map((option) => <label className="learning-answer-option" key={option.key}>
          <input type={question.question_type === 'multiple_choice' ? 'checkbox' : 'radio'} name={question.id}
            checked={(answers[question.id] || []).includes(option.key)} onChange={(event) => {
              const selected = question.question_type === 'multiple_choice'
                ? event.target.checked ? [...(answers[question.id] || []), option.key] : (answers[question.id] || []).filter((key) => key !== option.key)
                : [option.key];
              setAnswers((current) => ({ ...current, [question.id]: selected }));
              setResults((current) => { const next = { ...current }; delete next[question.id]; return next; });
            }} />
          <span>{option.key}. {option.text}</span>
        </label>)}
        <Button type="primary" loading={submitting === question.id} disabled={!answers[question.id]?.length || (!!submitting && submitting !== question.id)} onClick={() => submit(question.id)}>模拟提交</Button>
        {results[question.id] && <div className="learning-answer-result" role="status">
          <Tag color={results[question.id].is_correct ? 'success' : 'warning'}>{results[question.id].is_correct ? '回答正确' : '回答错误，可以查阅后重试'}</Tag>
          <p>正确答案：{results[question.id].correct_answer.join('、')}</p>
          <p>{results[question.id].explanation}</p><blockquote>{results[question.id].source_excerpt}</blockquote>
        </div>}
      </fieldset>)}
    </>}
  </Modal>;
}
