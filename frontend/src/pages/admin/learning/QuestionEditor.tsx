import { useEffect, useState } from 'react';
import { Alert, Button, Collapse, Form, Input, Modal, Select, Space, Spin } from 'antd';
import { Plus, Trash2 } from 'lucide-react';
import { DIFFICULTIES, getLearningArticle, QUESTION_TYPES, saveLearningQuestion,
  type ArticleDetail, type LearningQuestion, type QuestionContent, type QuestionType } from '@/api/learning';
import { ArticleReading } from './LearningPreview';

export default function QuestionEditor({ question, onClose, onSaved }: {
  question: LearningQuestion | null; onClose: () => void; onSaved: () => void;
}) {
  const [form] = Form.useForm<QuestionContent>();
  const [modal, modalContext] = Modal.useModal();
  const [article, setArticle] = useState<ArticleDetail | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [dirty, setDirty] = useState(false);
  const type: QuestionType = Form.useWatch('question_type', form) || question?.payload.question_type || 'single_choice';
  const options = Form.useWatch('options', form) as QuestionContent['options'] | undefined;

  useEffect(() => {
    if (!question) return;
    let active = true;
    form.setFieldsValue(structuredClone(question.payload)); setArticle(null); setError(''); setDirty(false);
    getLearningArticle(question.article_id).then((result) => { if (active) setArticle(result); })
      .catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : '文章加载失败'); });
    return () => { active = false; };
  }, [question, form]);

  const close = () => {
    if (saving) return;
    if (!dirty) { onClose(); return; }
    modal.confirm({ title: '放弃未保存的题目修改？', content: '已保存的草稿不受影响。', okText: '放弃修改', cancelText: '继续编辑', onOk: onClose });
  };

  const save = async () => {
    if (!question) return;
    try {
      const payload = await form.validateFields();
      setSaving(true); setError('');
      await saveLearningQuestion(question.id, question.revision, payload);
      setDirty(false); onSaved(); onClose();
    } catch (cause) { if (cause instanceof Error) setError(cause.message); }
    finally { setSaving(false); }
  };

  const current = article?.versions.find((version) => version.version === article.active_version);
  const rejection = article?.audits.find((record) => record.question_id === question?.id && record.action === 'question.reject');
  return <Modal open={!!question} title="编辑题目" width={820} onCancel={close} onOk={save} okText="保存草稿，重新审核"
    confirmLoading={saving} okButtonProps={{ disabled: !article }} forceRender>
    {modalContext}
    <Alert type="info" showIcon message="修改题目后会退回草稿并停用，必须重新审核。答案应能从关联文章中明确找到。" />
    {error && <Alert type="error" role="alert" message={error} />}
    {question?.review_status === 'rejected' && typeof rejection?.snapshot.reason === 'string' && <Alert type="warning" message={`驳回原因：${rejection.snapshot.reason}`} />}
    {!article && !error && <Spin />}
    {article && question && article.active_version !== question.article_version && <Alert type="warning" message={`文章已更新至 v${article.active_version}。请核对并更新知识点及依据，保存后重新绑定当前版本。`} />}
    {current && <Collapse items={[{ key: 'article', label: `查阅当前知识点文章 · v${current.version}`, children: <ArticleReading article={current.content} /> }]} />}
    <Form name="learning-question" form={form} layout="vertical" onValuesChange={() => setDirty(true)}>
      <div className="learning-form-grid">
        <Form.Item label="题型" name="question_type" rules={[{ required: true }]}><Select options={Object.entries(QUESTION_TYPES).map(([value, label]) => ({ value, label }))}
          onChange={(value: QuestionType) => {
            if (value === 'true_false') form.setFieldsValue({ options: [{ key: 'T', text: '正确' }, { key: 'F', text: '错误' }], correct_answer: [] });
            else if (type === 'true_false') form.setFieldsValue({ options: [{ key: 'A', text: '' }, { key: 'B', text: '' }], correct_answer: [] });
          }} /></Form.Item>
        <Form.Item label="难度" name="difficulty" rules={[{ required: true }]}><Select options={Object.entries(DIFFICULTIES).map(([value, label]) => ({ value, label }))} /></Form.Item>
      </div>
      <Form.Item label="题干" name="stem" rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={3} maxLength={4000} /></Form.Item>
      <Form.List name="options">{(fields, { add, remove }) => <div className="learning-options-editor">
        <strong>选项</strong>
        {fields.map((field) => <Space key={field.key} className="learning-option-editor" align="start">
          <Form.Item name={[field.name, 'key']} rules={[{ required: true }]}><Input aria-label={`选项${field.name + 1}标识`} maxLength={8} disabled={type === 'true_false'} style={{ width: 64 }} /></Form.Item>
          <Form.Item name={[field.name, 'text']} rules={[{ required: true, whitespace: true }]}><Input aria-label={`选项${field.name + 1}内容`} disabled={type === 'true_false'} maxLength={1000} /></Form.Item>
          {type !== 'true_false' && <Button aria-label={`移除选项${field.name + 1}`} danger type="text" disabled={fields.length <= 2} icon={<Trash2 size={16} />} onClick={() => remove(field.name)} />}
        </Space>)}
        {type !== 'true_false' && <Button type="dashed" disabled={fields.length >= 6} icon={<Plus size={15} />} onClick={() => {
          const keys = new Set((options || []).map((option) => option.key));
          const key = ['A', 'B', 'C', 'D', 'E', 'F'].find((candidate) => !keys.has(candidate)) || '';
          add({ key, text: '' });
        }}>添加选项</Button>}
      </div>}</Form.List>
      <Form.Item label="正确答案" name="correct_answer" rules={[{ required: true, type: 'array', min: 1 }]}
        getValueProps={(value: string[] | undefined) => ({ value: type === 'multiple_choice' ? value : value?.[0] })}
        normalize={(value: string | string[]) => Array.isArray(value) ? value : value ? [value] : []}>
        <Select mode={type === 'multiple_choice' ? 'multiple' : undefined} options={(options || []).map((option) => ({ value: option.key, label: `${option.key}. ${option.text}` }))} />
      </Form.Item>
      <Form.Item label="答案解析" name="explanation" rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={3} maxLength={4000} /></Form.Item>
      <Form.Item label="关联知识点" name="point_ids" rules={[{ required: true, type: 'array', min: 1 }]}><Select mode="multiple" options={(current?.content.key_points || []).map((point) => ({ value: point.id, label: `${point.id} · ${point.text}` }))} /></Form.Item>
      <Form.Item label="文章依据摘录" name="source_excerpt" extra="逐字摘录当前文章中的依据，不要填写原始工单内容。" rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={3} maxLength={4000} /></Form.Item>
    </Form>
  </Modal>;
}
