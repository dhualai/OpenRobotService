import { useState } from 'react';
import { Alert, Button, Checkbox, Empty, Input, Modal, Space, Tag } from 'antd';
import { actLearningQuestion, DIFFICULTIES, QUESTION_TYPES, REVIEW_STATES, type LearningQuestion } from '@/api/learning';

type Action = Parameters<typeof actLearningQuestion>[2];
const ACTION_LABELS: Record<Action, string> = { submit: '提交审核', approve: '审核通过', reject: '驳回', enable: '启用', disable: '停用' };

export default function QuestionList({ questions, onEdit, onChanged, onArticle }: {
  questions: LearningQuestion[]; onEdit: (question: LearningQuestion) => void;
  onChanged: () => void; onArticle?: (id: string) => void;
}) {
  const [selection, setSelection] = useState<{ question: LearningQuestion; action: Action } | null>(null);
  const [reason, setReason] = useState('');
  const [verified, setVerified] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const choose = (question: LearningQuestion, action: Action) => {
    setSelection({ question, action }); setReason(''); setVerified(false); setError('');
  };
  const submit = async () => {
    if (!selection) return;
    setSaving(true); setError('');
    try {
      await actLearningQuestion(selection.question.id, selection.question.revision, selection.action, reason);
      setSelection(null); onChanged();
    } catch (cause) { setError(cause instanceof Error ? cause.message : '操作失败，请刷新后重试'); }
    finally { setSaving(false); }
  };

  return <>
    {!questions.length && <Empty description="暂无题目，先确认知识点文章，再生成相关问题" />}
    <div className="learning-question-list">{questions.map((question) => {
      const outdated = question.current_version !== undefined && question.article_version !== question.current_version;
      return <article key={question.id} className="learning-question-card">
        <div className="learning-card-meta"><Space wrap>
          <Tag color="blue">{QUESTION_TYPES[question.payload.question_type]}</Tag>
          <Tag>{DIFFICULTIES[question.payload.difficulty]}</Tag>
          <Tag color={question.review_status === 'approved' ? 'green' : question.review_status === 'pending_review' ? 'gold' : 'default'}>{REVIEW_STATES[question.review_status]}</Tag>
          <Tag color={question.enabled ? 'cyan' : 'default'}>{question.enabled ? '已启用' : '未启用'}</Tag>
          {outdated && <Tag color="orange">需适配新版文章</Tag>}
        </Space><span>文章 v{question.article_version}</span></div>
        {question.article_title && <Button type="link" className="learning-article-link" onClick={() => onArticle?.(question.article_id)}>{question.article_title}</Button>}
        <h3>{question.payload.stem}</h3>
        <div className="learning-question-options">{question.payload.options.map((option) => <div key={option.key}><b>{option.key}</b>{option.text}</div>)}</div>
        <details><summary>查看答案、解析与依据（仅后台）</summary><p>正确答案：{question.payload.correct_answer.join('、')}</p>
          <p>{question.payload.explanation}</p><blockquote>{question.payload.source_excerpt}</blockquote>
          <p>关联知识点：{question.payload.point_ids.join('、')}</p></details>
        <Space wrap className="learning-card-actions">
          <Button onClick={() => onEdit(question)}>编辑题目</Button>
          {['draft', 'rejected'].includes(question.review_status) && <Button disabled={outdated} onClick={() => choose(question, 'submit')}>提交审核</Button>}
          {question.review_status === 'pending_review' && <>
            <Button type="primary" disabled={outdated} onClick={() => choose(question, 'approve')}>审核通过</Button>
            <Button danger onClick={() => choose(question, 'reject')}>驳回</Button>
          </>}
          {question.review_status === 'approved' && <Button disabled={!question.enabled && (outdated || question.article_enabled === false)} onClick={() => choose(question, question.enabled ? 'disable' : 'enable')}>{question.enabled ? '停用' : '启用'}</Button>}
        </Space>
      </article>;
    })}</div>
    <Modal open={!!selection} title={selection ? `${ACTION_LABELS[selection.action]}题目` : ''} onCancel={() => { if (!saving) setSelection(null); }}
      onOk={submit} confirmLoading={saving} okText="确认" cancelText="取消"
      okButtonProps={{ disabled: selection?.action === 'approve' ? !verified : selection?.action === 'reject' ? !reason.trim() : false }}>
      <p>{selection?.question.payload.stem}</p>
      {selection?.action === 'approve' && <Checkbox checked={verified} onChange={(event) => setVerified(event.target.checked)}>已核对题干、选项、答案、解析和文章依据，且没有敏感信息</Checkbox>}
      {selection?.action === 'reject' && <Input.TextArea aria-label="驳回原因" rows={3} maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="请说明需要修改的问题" />}
      {selection?.action === 'enable' && <Alert type="info" message="启用后纳入可用题目；文章停用或版本更新后将不再可用。" />}
      {error && <Alert type="error" role="alert" message={error} />}
    </Modal>
  </>;
}
