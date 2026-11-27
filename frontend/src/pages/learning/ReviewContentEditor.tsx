import { useEffect, useState } from 'react';
import { Alert, Button, Form, Input, Modal, Select, Space, Tabs } from 'antd';
import { DIFFICULTIES, QUESTION_TYPES, type QuestionContent, type QuestionType } from '@/api/learning';
import { editLearningReview, type LearningReviewDetail, type ReviewArticle } from '@/api/learningReviews';

interface ReviewForm { question: QuestionContent; article: ReviewArticle }

export default function ReviewContentEditor({ detail, initialTab, onClose, onSaved }: {
  detail: LearningReviewDetail; initialTab: 'question' | 'article'; onClose: () => void;
  onSaved: (detail: LearningReviewDetail) => void;
}) {
  const [form] = Form.useForm<ReviewForm>();
  const [modal, modalContext] = Modal.useModal();
  const [tab, setTab] = useState(initialTab);
  const [dirty, setDirty] = useState(false);
  const [changed, setChanged] = useState({ question: false, article: false });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const type: QuestionType = Form.useWatch(['question', 'question_type'], form) || detail.question?.question_type || 'single_choice';
  const options: QuestionContent['options'] = Form.useWatch(['question', 'options'], form) || detail.question?.options || [];
  const points: ReviewArticle['key_points'] = Form.useWatch(['article', 'key_points'], form) || detail.article?.key_points || [];
  useEffect(() => {
    if (detail.question && detail.article) form.setFieldsValue(structuredClone({ question: detail.question, article: detail.article }));
  }, [detail, form]);

  const close = () => {
    if (saving) return;
    if (!dirty) { onClose(); return; }
    modal.confirm({ title: '放弃未保存的审核修改？', okText: '放弃修改', cancelText: '继续编辑', onOk: onClose });
  };
  const save = async () => {
    if (saving) return;
    setError('');
    try {
      const values = await form.validateFields();
      const articleChanged = changed.article;
      const questionChanged = changed.question;
      if (!articleChanged && !questionChanged) { onClose(); return; }
      setSaving(true);
      const result = await editLearningReview(detail.id, {
        revision: detail.revision, article_revision: detail.article_revision,
        ...(articleChanged ? { article: values.article } : {}),
        ...(questionChanged ? { question: values.question } : {}),
      });
      onSaved(result);
    } catch (cause) {
      if (cause instanceof Error) setError(cause.message);
      else if (!(cause && typeof cause === 'object' && 'errorFields' in cause)) setError('保存失败，请重试');
    } finally { setSaving(false); }
  };
  const questionFields = <>
    <div className="learning-form-grid">
      <Form.Item label="题型" name={['question', 'question_type']} rules={[{ required: true }]}>
        <Select options={Object.entries(QUESTION_TYPES).map(([value, label]) => ({ value, label }))}
          onChange={(value: QuestionType) => {
            if (value === 'true_false') form.setFieldsValue({ question: { options: [{ key: 'T', text: '正确' }, { key: 'F', text: '错误' }], correct_answer: [] } });
            else if (type === 'true_false') form.setFieldsValue({ question: { options: [{ key: 'A', text: '' }, { key: 'B', text: '' }], correct_answer: [] } });
          }} />
      </Form.Item>
      <Form.Item label="难度" name={['question', 'difficulty']} rules={[{ required: true }]}>
        <Select options={Object.entries(DIFFICULTIES).map(([value, label]) => ({ value, label }))} />
      </Form.Item>
    </div>
    <Form.Item label="题干" name={['question', 'stem']} rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={3} maxLength={4000} /></Form.Item>
    <Form.List name={['question', 'options']}>{(fields, { add, remove }) => <div className="learning-options-editor">
      <strong>选项</strong>
      {fields.map((field) => <Space key={field.key} className="learning-option-editor" align="start">
        <Form.Item name={[field.name, 'key']} rules={[{ required: true }]}><Input aria-label={`选项${field.name + 1}标识`} maxLength={8} disabled={type === 'true_false'} style={{ width: 64 }} /></Form.Item>
        <Form.Item name={[field.name, 'text']} rules={[{ required: true, whitespace: true }]}><Input aria-label={`选项${field.name + 1}内容`} disabled={type === 'true_false'} maxLength={1000} /></Form.Item>
        {type !== 'true_false' && <Button danger disabled={fields.length <= 2} onClick={() => remove(field.name)}>移除选项{field.name + 1}</Button>}
      </Space>)}
      {type !== 'true_false' && <Button disabled={fields.length >= 6} onClick={() => {
        const keys = new Set(options.map((option) => option.key));
        add({ key: ['A', 'B', 'C', 'D', 'E', 'F'].find((key) => !keys.has(key)) || '', text: '' });
      }}>添加选项</Button>}
    </div>}</Form.List>
    <Form.Item label="正确答案" name={['question', 'correct_answer']} rules={[{ required: true, type: 'array', min: 1 }]}
      getValueProps={(value: string[] | undefined) => ({ value: type === 'multiple_choice' ? value : value?.[0] })}
      normalize={(value: string | string[]) => Array.isArray(value) ? value : value ? [value] : []}>
      <Select mode={type === 'multiple_choice' ? 'multiple' : undefined} options={options.map((option) => ({ value: option.key, label: `${option.key}. ${option.text}` }))} />
    </Form.Item>
    <Form.Item label="答案解析" name={['question', 'explanation']} rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={3} maxLength={4000} /></Form.Item>
    <Form.Item label="关联知识点" name={['question', 'point_ids']} rules={[{ required: true, type: 'array', min: 1 }]}>
      <Select mode="multiple" options={points.map((point) => ({ value: point.id, label: `${point.id} · ${point.text}` }))} />
    </Form.Item>
    <Form.Item label="文章依据摘录" name={['question', 'source_excerpt']} rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={3} maxLength={4000} /></Form.Item>
  </>;
  const articleFields = <>
    <Form.Item label="文章标题" name={['article', 'title']} rules={[{ required: true, whitespace: true }]}><Input maxLength={255} /></Form.Item>
    <Form.Item label="文章分类" name={['article', 'category']} rules={[{ required: true, whitespace: true }]}><Input maxLength={80} /></Form.Item>
    <Form.Item label="标签" name={['article', 'tags']}><Select mode="tags" maxCount={10} /></Form.Item>
    <Form.Item label="知识结论" name={['article', 'summary']} rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={3} maxLength={4000} /></Form.Item>
    <Form.Item label="适用条件" name={['article', 'applicability']} rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={3} maxLength={4000} /></Form.Item>
    <Form.List name={['article', 'key_points']}>{(fields) => fields.map((field) => <div key={field.key}>
      <Form.Item name={[field.name, 'id']} hidden><Input /></Form.Item>
      <Form.Item label={`核心知识点 ${field.name + 1}`} name={[field.name, 'text']} rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={3} maxLength={2000} /></Form.Item>
    </div>)}</Form.List>
    <Form.Item label="操作方法" name={['article', 'method']}><Input.TextArea rows={5} maxLength={6000} /></Form.Item>
    <Form.Item label="注意事项" name={['article', 'cautions']}><Input.TextArea rows={3} maxLength={4000} /></Form.Item>
    <Form.Item label="示例" name={['article', 'example']}><Input.TextArea rows={3} maxLength={4000} /></Form.Item>
  </>;
  return <Modal open title="编辑审核内容" width={820} onCancel={close} onOk={() => void save()}
    okText="保存修改" cancelText="取消" confirmLoading={saving} closable={!saving} maskClosable={!saving} keyboard={!saving}>
    {modalContext}
    <Alert type="info" showIcon message="保存后继续在本工单审核，不会自动通过。文章修改将创建新版本，其他关联题目需重新审核；当前题目的答案依据必须与修改后的文章一致。" />
    {error && <Alert type="error" role="alert" showIcon message={error} />}
    <Form form={form} name="learning-review-content" layout="vertical" disabled={saving} onValuesChange={(values: Partial<ReviewForm>) => {
      setDirty(true); setChanged((current) => ({ question: current.question || !!values.question, article: current.article || !!values.article }));
    }}>
      <Tabs activeKey={tab} onChange={(key) => setTab(key as 'question' | 'article')} items={[
        { key: 'question', label: '题目内容', forceRender: true, children: questionFields },
        { key: 'article', label: '知识点文章', forceRender: true, children: articleFields },
      ]} />
    </Form>
  </Modal>;
}
