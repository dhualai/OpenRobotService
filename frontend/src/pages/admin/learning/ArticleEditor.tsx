import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Collapse, Drawer, Empty, Form, Input, InputNumber, Modal, Select, Space, Spin, Tabs, Tag } from 'antd';
import { Plus, Sparkles, Trash2 } from 'lucide-react';
import { adoptLearningArticle, confirmLearningArticle, DIFFICULTIES, forkLearningArticle, generateLearning,
  getLearningArticle, getLearningGeneration, QUESTION_TYPES, saveLearningArticle, toggleLearningArticle,
  type ArticleContent, type ArticleDetail, type Generation, type LearningQuestion, type QuestionType, type SourceInput } from '@/api/learning';
import { ArticleReading } from './LearningPreview';
import QuestionList from './QuestionList';
import SourceModal from './SourceModal';

const failure = (cause: unknown) => cause instanceof Error ? cause.message : '操作失败，请稍后重试';
const AUDIT_LABELS: Record<string, string> = {
  'article.create': '创建知识点', 'article.save': '保存草稿', 'article.fork': '创建修改草稿',
  'article.confirm': '确认文章版本', 'article.enable': '启用文章', 'article.disable': '停用文章',
  'article.adopt': '采用AI候选内容', 'question.save': '编辑题目', 'question.submit': '提交题目审核',
  'question.approve': '题目审核通过', 'question.reject': '题目驳回', 'question.enable': '启用题目', 'question.disable': '停用题目',
};

export default function ArticleEditor({ articleId, refreshKey, onClose, onChanged, onQuestionEdit, onPreview }: {
  articleId: string | null; refreshKey: number; onClose: () => void; onChanged: () => void;
  onQuestionEdit: (question: LearningQuestion) => void; onPreview: (id: string) => void;
}) {
  const [form] = Form.useForm<ArticleContent>();
  const [data, setData] = useState<ArticleDetail | null>(null);
  const [formContent, setFormContent] = useState<ArticleContent | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [dirty, setDirty] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [verified, setVerified] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [candidate, setCandidate] = useState<Generation | null>(null);
  const [count, setCount] = useState(5);
  const [types, setTypes] = useState<QuestionType[]>(['single_choice', 'multiple_choice', 'true_false']);
  const [difficulty, setDifficulty] = useState<keyof typeof DIFFICULTIES>('medium');
  const activeId = useRef(articleId);
  activeId.current = articleId;

  const accept = useCallback((next: ArticleDetail, reset = true) => {
    setData(next);
    if (reset) { setFormContent(next.draft); setDirty(false); }
  }, []);

  useEffect(() => {
    if (!formContent) return;
    form.resetFields(); form.setFieldsValue(structuredClone(formContent));
  }, [formContent, form]);

  useEffect(() => {
    if (!articleId) return;
    let active = true;
    setLoading(true); setData(null); setError(''); setCandidate(null); setPending(null); setDirty(false);
    getLearningArticle(articleId).then((next) => {
      if (!active) return;
      accept(next); setPending(next.generations.find((job) => job.state === 'running')?.id || null);
    }).catch((cause: unknown) => { if (active) setError(failure(cause)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [articleId, accept]);

  useEffect(() => {
    if (!articleId || !refreshKey) return;
    let active = true;
    getLearningArticle(articleId).then((next) => { if (active) setData(next); })
      .catch((cause: unknown) => { if (active) setError(failure(cause)); });
    return () => { active = false; };
  }, [articleId, refreshKey]);

  useEffect(() => {
    if (!articleId || !pending) return;
    let active = true;
    const timer = window.setInterval(() => {
      getLearningGeneration(articleId, pending).then(async (job) => {
        if (!active || job.state === 'running') return;
        const next = await getLearningArticle(articleId);
        if (!active) return;
        setPending(null); setData(next); onChanged();
        if (job.state === 'failed') setError(job.error || '生成失败，已保留当前草稿');
      }).catch((cause: unknown) => { if (active) setError(`查询生成状态失败：${failure(cause)}，可关闭后重新打开查询`); });
    }, 4000);
    return () => { active = false; window.clearInterval(timer); };
  }, [articleId, pending, onChanged]);

  useEffect(() => {
    if (!dirty && !pending) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener('beforeunload', prevent);
    return () => window.removeEventListener('beforeunload', prevent);
  }, [dirty, pending]);

  const close = () => {
    if (busy) return;
    if (!dirty) { onClose(); return; }
    Modal.confirm({ title: '放弃未保存的文章修改？', content: '已保存的草稿和后台生成记录会保留。', okText: '放弃修改', cancelText: '继续编辑', onOk: onClose });
  };
  const mutate = async (operation: () => Promise<ArticleDetail>) => {
    setBusy(true); setError('');
    try { const next = await operation(); accept(next); onChanged(); return next; }
    catch (cause) { setError(failure(cause)); return null; }
    finally { setBusy(false); }
  };
  const save = async (sources?: SourceInput, title?: string) => {
    if (!data) return null;
    try {
      const content = await form.validateFields();
      if (title) content.title = title;
      return await mutate(() => saveLearningArticle(data.id, data.revision, content, sources));
    } catch { setError('请先补全文章中标记的字段'); return null; }
  };
  const generate = async (kind: 'article' | 'questions') => {
    if (!data) return;
    const current = dirty ? await save() : data;
    if (!current) return;
    const requestId = typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID() : `generation-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
    setBusy(true); setPending(requestId); setError('');
    try {
      const job = await generateLearning(current.id, { request_id: requestId, revision: current.revision, kind,
        count, question_types: types, difficulty });
      if (activeId.current !== current.id) return;
      if (job.state !== 'running') setPending(null);
      if (job.state === 'failed') setError(job.error || '生成失败，已保留当前草稿');
      setData(await getLearningArticle(current.id)); onChanged();
    } catch (cause) {
      if (activeId.current !== current.id) return;
      setError(`生成请求未完成：${failure(cause)}。正在检查后台记录，请勿重复发起。`);
      try {
        const job = await getLearningGeneration(current.id, requestId);
        if (job.state !== 'running') { setPending(null); setData(await getLearningArticle(current.id)); onChanged(); }
      } catch { setPending(null); }
    } finally { setBusy(false); }
  };
  const confirm = async () => {
    if (!data) return;
    const current = dirty ? await save() : data;
    if (!current) return;
    const next = await mutate(() => confirmLearningArticle(current.id, current.revision));
    if (next) { setConfirmOpen(false); setVerified(false); }
  };
  const current = data?.versions.find((version) => version.version === data.active_version);
  const disabled = busy || !!pending;
  const jobs = data?.generations || [];

  return <>
    <Drawer open={!!articleId} title="知识点与题目维护" width="min(100vw, 1040px)" onClose={close} destroyOnHidden
      footer={data && <div className="learning-editor-footer"><span>{dirty ? '有未保存修改' : '内容已保存'} · 修订 {data.revision}</span><Space wrap>
        <Button disabled={busy} onClick={close}>关闭</Button>
        {data.draft && <><Button disabled={disabled} loading={busy} onClick={() => save()}>保存草稿</Button>
          <Button type="primary" disabled={disabled} onClick={() => { setVerified(false); setConfirmOpen(true); }}>确认文章版本</Button></>}
      </Space></div>}>
      {error && <Alert type="error" showIcon message={error} role="alert" closable onClose={() => setError('')} />}
      {loading && <div className="learning-loading"><Spin /></div>}
      {data && <>
        <div className="learning-editor-title"><div><Space wrap><Tag color="blue">{data.active_version ? `已确认 v${data.active_version}` : '待确认'}</Tag>
          <Tag color={data.enabled ? 'green' : 'default'}>{data.enabled ? '文章已启用' : '文章未启用'}</Tag>{data.draft && <Tag color="gold">有编辑草稿</Tag>}</Space><h2>{data.title}</h2></div>
          <Space wrap><Button disabled={!data.active_version} onClick={() => onPreview(data.id)}>开卷预览</Button>
            {!!data.active_version && <Button disabled={disabled || dirty} onClick={() => mutate(() => toggleLearningArticle(data.id, data.revision, !data.enabled))}>{data.enabled ? '停用文章' : '启用文章'}</Button>}</Space></div>
        {pending && <Alert type="info" showIcon message="AI正在生成，结果会自动更新。关闭页面后，可重新进入这里查询记录。" />}
        <Tabs defaultActiveKey="article" items={[
          { key: 'article', label: '知识点文章', children: <>
            <Alert type="info" showIcon message="只提炼短小、可复用的知识点，不替代项目沉淀文章。来源仅限授权后台追溯，不进入开卷内容。" />
            <Collapse className="learning-sources" items={[{ key: 'sources', label: `来源材料 · ${data.sources.length} 份（仅后台）`, children: <>
              {data.sources.map((source) => <details key={source.marker}><summary><Tag>{source.marker}</Tag>{source.type === 'work_order' ? '工单' : '技术材料'} · {source.label}</summary><pre>{source.content}</pre></details>)}
              {data.draft && <Button disabled={disabled} onClick={() => setSourceOpen(true)}>调整来源材料</Button>}
            </> }]} />
            {data.draft ? <>
              <div className="learning-section-heading"><h3>编辑知识点</h3><Button icon={<Sparkles size={15} />} disabled={disabled} loading={busy} onClick={() => generate('article')}>AI提炼文章</Button></div>
              <Form name="learning-article" form={form} layout="vertical" disabled={disabled} onValuesChange={() => setDirty(true)}>
                <div className="learning-form-grid"><Form.Item name="title" label="文章标题" rules={[{ required: true, whitespace: true }]}><Input maxLength={255} /></Form.Item>
                  <Form.Item name="category" label="知识分类" rules={[{ required: true, whitespace: true }]}><Input maxLength={80} /></Form.Item></div>
                <Form.Item name="tags" label="标签"><Select mode="tags" placeholder="输入标签后按回车" /></Form.Item>
                <Form.Item name="summary" label="知识概述"><Input.TextArea rows={3} maxLength={4000} /></Form.Item>
                <Form.Item name="applicability" label="适用条件与边界"><Input.TextArea rows={2} maxLength={4000} /></Form.Item>
                <Form.List name="key_points">{(fields, { add, remove }) => <div className="learning-point-editor"><h3>核心知识点与证据</h3>
                  {fields.map((field) => <div className="learning-point-card" key={field.key}>
                    <div className="learning-form-grid"><Form.Item name={[field.name, 'id']} label="知识点标识" rules={[{ required: true }]}><Input placeholder="KP1" maxLength={12} /></Form.Item>
                      <Form.Item name={[field.name, 'source_markers']} label="来源编号" rules={[{ required: true, type: 'array', min: 1 }]}><Select mode="multiple" options={data.sources.map((source) => ({ value: source.marker, label: source.marker }))} /></Form.Item></div>
                    <Form.Item name={[field.name, 'text']} label="知识点内容" rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={2} maxLength={2000} /></Form.Item>
                    <Form.Item name={[field.name, 'source_excerpt']} label="来源依据摘录（仅后台）" rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={2} maxLength={2000} placeholder="逐字摘录脱敏来源中的支持内容" /></Form.Item>
                    <Button danger type="text" disabled={disabled} icon={<Trash2 size={15} />} onClick={() => remove(field.name)}>移除知识点</Button>
                  </div>)}
                  <Button type="dashed" disabled={disabled || fields.length >= 5} icon={<Plus size={15} />} onClick={() => {
                    const existing = new Set((form.getFieldValue('key_points') || []).map((point: { id: string }) => point.id));
                    const id = Array.from({ length: 99 }, (_, index) => `KP${index + 1}`).find((value) => !existing.has(value));
                    add({ id, text: '', source_markers: [], source_excerpt: '' }); setDirty(true);
                  }}>添加知识点</Button>
                </div>}</Form.List>
                <Form.Item name="method" label="通用判断方法"><Input.TextArea rows={3} maxLength={6000} /></Form.Item>
                <Form.Item name="cautions" label="注意事项"><Input.TextArea rows={2} maxLength={4000} /></Form.Item>
                <Form.Item name="example" label="简短示例"><Input.TextArea rows={2} maxLength={4000} /></Form.Item>
                <Form.Item name="warnings" label="待核实事项" extra="存在待核实事项时不能确认文章。请先核实并修正内容，再移除对应事项。"><Select mode="tags" placeholder="无待核实事项时留空" /></Form.Item>
              </Form>
            </> : <><div className="learning-section-heading"><h3>当前生效文章 · v{data.active_version}</h3><Button disabled={disabled} onClick={() => mutate(() => forkLearningArticle(data.id, data.revision))}>创建修改草稿</Button></div>
              {current && <ArticleReading article={current.content} />}</>}
            {!!jobs.length && <section className="learning-generation-records"><h3>最近生成记录</h3>{jobs.map((job) => <div key={job.id} className="learning-generation-record"><div><Tag color={job.state === 'failed' ? 'red' : job.state === 'succeeded' ? 'green' : 'blue'}>{job.state === 'running' ? '生成中' : job.state === 'succeeded' ? '已完成' : '失败'}</Tag>
              {job.kind === 'article' ? '知识点文章' : `题目生成${job.result?.saved_count !== undefined ? ` · 已保存 ${job.result.saved_count} 题` : ''}`}<small>{new Date(`${job.created_at}Z`).toLocaleString()}</small>
              {job.error && <p className="learning-error-text">{job.error}</p>}{!!job.result?.errors?.length && <p className="learning-error-text">{job.result.errors.join('；')}</p>}
              {job.result?.reason && <p className="learning-muted">生成说明：{job.result.reason}</p>}
            </div>{job.kind === 'article' && job.result?.content && <Button size="small" onClick={() => setCandidate(job)}>查看候选内容</Button>}</div>)}</section>}
          </> },
          { key: 'questions', label: `关联题目（${data.questions.length}）`, children: <>
            <Alert type="info" showIcon message="题目仅依据已确认文章生成。新题默认草稿且停用；文章确认新版本后，旧题必须适配并重新审核。" />
            <div className="learning-generation-controls"><div><label>题目数量<InputNumber aria-label="题目数量" min={1} max={10} value={count} onChange={(value) => setCount(value || 1)} /></label>
              <label>题型<Select aria-label="生成题型" mode="multiple" value={types} onChange={setTypes} options={Object.entries(QUESTION_TYPES).map(([value, label]) => ({ value, label }))} /></label>
              <label>难度<Select aria-label="生成难度" value={difficulty} onChange={setDifficulty} options={Object.entries(DIFFICULTIES).map(([value, label]) => ({ value, label }))} /></label></div>
              <Button type="primary" icon={<Sparkles size={15} />} disabled={disabled || !data.active_version || !data.enabled || !types.length || dirty} loading={busy} onClick={() => generate('questions')}>AI生成题目</Button>
              {(!data.active_version || !data.enabled) && <span className="learning-muted">请先确认并启用文章。</span>}
              {data.draft && !!data.active_version && <span className="learning-muted">依据已确认 v{data.active_version} 出题，不使用未确认草稿。</span>}
            </div>
            <QuestionList questions={data.questions.map((question) => ({ ...question, current_version: data.active_version, article_enabled: data.enabled }))}
              onEdit={onQuestionEdit} onChanged={onChanged} />
          </> },
          { key: 'history', label: '版本与操作记录', children: <>
            {!data.versions.length && <Empty description="还没有已确认版本" />}
            <Collapse items={data.versions.map((version) => ({ key: version.version, label: `文章 v${version.version}${version.version === data.active_version ? ' · 当前版本' : ' · 历史快照'}`, children: <ArticleReading article={version.content} /> }))} />
            <h3>最近操作</h3><div className="learning-audits">{data.audits.map((record, index) => <div key={index}><div><b>{AUDIT_LABELS[record.action] || record.action}</b>
              {typeof record.snapshot.reason === 'string' && record.snapshot.reason && <p>原因：{record.snapshot.reason}</p>}</div><span>{new Date(`${record.created_at}Z`).toLocaleString()} · 操作人 {record.operator_id}</span></div>)}</div>
          </> },
        ]} />
      </>}
    </Drawer>
    <SourceModal open={sourceOpen} replacing initialTitle={data?.draft?.title || data?.title} initialSources={data?.sources} onClose={() => setSourceOpen(false)}
      onSubmit={async (title, sources) => { const next = await save(sources, title); if (next) setSourceOpen(false); else throw new Error('保存失败，请查看文章编辑区的提示'); }} />
    <Modal open={confirmOpen} title="确认知识点文章版本" onCancel={() => { if (!busy) setConfirmOpen(false); }} onOk={confirm} confirmLoading={busy} okText="确认并生成不可变版本" okButtonProps={{ disabled: !verified }}>
      <Alert type="warning" showIcon message="确认后锁定该版本。再次确认新版本会让已有题目退回草稿并停用，需要重新适配审核。" />
      <Checkbox checked={verified} onChange={(event) => setVerified(event.target.checked)}>已核验知识准确性、来源证据、适用边界及完整脱敏结果</Checkbox>
      {error && <Alert type="error" message={error} />}
    </Modal>
    <Modal open={!!candidate} title="AI候选文章 · 采用前请核对" width={780} onCancel={() => setCandidate(null)} okText="采用到编辑草稿" cancelText="暂不采用"
      confirmLoading={busy} okButtonProps={{ disabled: disabled || dirty || !data?.draft || candidate?.input_revision !== data?.revision }} onOk={async () => {
        if (!data || !candidate) return;
        const next = await mutate(() => adoptLearningArticle(data.id, candidate.id, data.revision));
        if (next) setCandidate(null);
      }}>
      <Alert type="info" message="采用只替换编辑草稿，不自动确认文章、不影响当前已确认版本。" />
      {(dirty || candidate?.input_revision !== data?.revision) && <Alert type="warning" message="草稿已变更，此候选不能直接覆盖。请核对后手动整合，或保存草稿后重新生成。" />}
      {!!candidate?.result?.content?.warnings.length && <Alert type="warning" message={candidate.result.content.warnings.join('；')} />}
      {candidate?.result?.content && <ArticleReading article={candidate.result.content} />}
    </Modal>
  </>;
}
