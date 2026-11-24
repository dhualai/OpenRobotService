import { useEffect, useState } from 'react';
import { Alert, Button, Form, Input, Modal, Select, Space, Spin } from 'antd';
import { Plus, Trash2 } from 'lucide-react';
import { getSimilarSourceTasks, listSourceTasks, type Material, type SourceInput, type SourceSnapshot, type SourceTask } from '@/api/learning';

const STATUS_LABELS = { closed: '已关闭', archiving: '归档中', archived: '已归档' };
const EMPTY_SOURCES: SourceSnapshot[] = [];
const EMPTY_TASK_IDS: string[] = [];
const EMPTY_MATERIALS: Material[] = [];

interface SourceValues { title: string; task_ids: string[]; materials: Material[] }
interface SourcePreview extends Omit<SourceTask, 'status'> { status?: SourceTask['status'] }

export default function SourceModal({ open, initialTitle = '', initialSources = EMPTY_SOURCES, replacing = false, onClose, onSubmit }: {
  open: boolean; initialTitle?: string; initialSources?: SourceSnapshot[]; replacing?: boolean;
  onClose: () => void; onSubmit: (title: string, sources: SourceInput) => Promise<void>;
}) {
  const [form] = Form.useForm<SourceValues>();
  const [tasks, setTasks] = useState<SourceTask[]>([]);
  const [knownTasks, setKnownTasks] = useState<Record<string, SourceTask>>({});
  const [keyword, setKeyword] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [anchorId, setAnchorId] = useState('');
  const [similarTasks, setSimilarTasks] = useState<SourceTask[]>([]);
  const [similarLoading, setSimilarLoading] = useState(false);
  const [similarError, setSimilarError] = useState('');
  const [similarRetry, setSimilarRetry] = useState(0);
  const selected: string[] = Form.useWatch('task_ids', form) || EMPTY_TASK_IDS;
  const materials: Material[] = Form.useWatch('materials', form) || EMPTY_MATERIALS;
  const activeAnchor = selected.includes(anchorId) ? anchorId : '';
  const sourceLimitReached = selected.length + materials.length >= 10;

  useEffect(() => {
    if (!open) return;
    form.resetFields();
    form.setFieldsValue({ title: initialTitle, task_ids: initialSources.filter((source) => source.type === 'work_order').map((source) => String(source.id)),
      materials: initialSources.filter((source) => source.type === 'manual').map((source) => ({ name: source.label, text: source.content, provenance: source.provenance || '' })) });
    setKeyword(''); setPage(1); setError('');
    setTasks([]); setKnownTasks({}); setTotal(0); setAnchorId('');
  }, [open, initialTitle, initialSources, form]);

  useEffect(() => {
    if (open && !selected.includes(anchorId)) setAnchorId(selected[selected.length - 1] || '');
  }, [open, selected, anchorId]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    const timer = window.setTimeout(() => {
      setLoading(true);
      listSourceTasks(keyword, page).then((result) => {
        if (!active) return;
        setTasks(result.items); setTotal(result.total);
        setKnownTasks((current) => ({ ...current, ...Object.fromEntries(result.items.map((task) => [task.id, task])) }));
      }).catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : '工单查询失败，仍可使用技术材料'); })
        .finally(() => { if (active) setLoading(false); });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [open, keyword, page]);

  useEffect(() => {
    let active = true;
    setSimilarTasks([]); setSimilarError(''); setSimilarLoading(false);
    if (!open || !activeAnchor) return;
    setSimilarLoading(true);
    getSimilarSourceTasks(activeAnchor).then((result) => {
      if (!active) return;
      setKnownTasks((current) => ({ ...current, ...Object.fromEntries([result.source, ...result.items].map((task) => [task.id, task])) }));
      if (result.retrieval_status === 'unavailable') {
        setSimilarError('相似工单检索服务暂不可用，请重试');
        return;
      }
      setSimilarTasks(result.items);
    }).catch((cause: unknown) => {
      if (active) setSimilarError(cause instanceof Error ? cause.message : '相似工单查询失败，请重试');
    }).finally(() => { if (active) setSimilarLoading(false); });
    return () => { active = false; };
  }, [open, activeAnchor, similarRetry]);

  const save = async () => {
    try {
      const values = await form.validateFields();
      const materials = values.materials || [];
      if (!values.task_ids?.length && !materials.length) { setError('至少选择一个工单或填写一份技术材料'); return; }
      if ((values.task_ids?.length || 0) + materials.length > 10) { setError('每次最多使用10份材料'); return; }
      setSaving(true); setError('');
      await onSubmit(values.title.trim(), { task_ids: values.task_ids || [], materials });
    } catch (cause) { if (cause instanceof Error) setError(cause.message); }
    finally { setSaving(false); }
  };

  const displayed = new Map<string, SourcePreview>(tasks.map((task) => [task.id, task]));
  selected.forEach((id) => {
    if (!displayed.has(id)) {
      const snapshot = initialSources.find((source) => source.type === 'work_order' && String(source.id) === id);
      displayed.set(id, knownTasks[id] || { id, title: snapshot?.label || `已选工单 ${id}`, summary: snapshot?.content.replace(/\s+/g, ' ').trim().slice(0, 240) || '' });
    }
  });
  const recommendations = similarTasks.filter((task) => !selected.includes(task.id));

  return <Modal open={open} className="learning-source-modal" title={replacing ? '调整知识点来源' : '新建知识点 · 选择来源材料'} width={720} onCancel={() => { if (!saving) onClose(); }}
    onOk={save} okText={replacing ? '保存来源' : '创建草稿并继续'} confirmLoading={saving} forceRender>
    <Alert type="info" showIcon message="可合并多个相似工单，也可仅使用技术材料。原始来源不进入学习内容，AI调用前会再次脱敏。" />
    {error && <Alert type="error" role="alert" message={error} closable onClose={() => setError('')} />}
    <Form name={replacing ? 'learning-replace-source' : 'learning-new-source'} form={form} layout="vertical" initialValues={{ task_ids: [], materials: [] }}>
      <Form.Item name="title" label="知识主题" rules={[{ required: true, whitespace: true, message: '请填写要提炼的知识主题' }]}>
        <Input maxLength={255} placeholder="例如：定位异常排查时如何区分环境与配置因素" />
      </Form.Item>
      <Form.Item name="task_ids" label="来源工单" extra="允许已关闭、归档中、已归档；可跨页多选，无工单也可使用下方技术材料。">
        <Select mode="multiple" showSearch filterOption={false} placeholder="按工单编号或标题搜索" loading={loading}
          onSearch={(value) => { setKeyword(value); setPage(1); }}
          onSelect={(id: string) => setAnchorId(id)}
          options={[...displayed.values()].map((task) => ({ value: task.id, label: `${task.id} · ${task.title}（${task.status ? STATUS_LABELS[task.status] : '待查询'}）`, summary: task.summary }))}
          optionRender={(option) => <div className="learning-source-option"><strong>{option.label}</strong><span>{option.data.summary || '暂无简要描述'}</span></div>}
          notFoundContent={loading ? <Spin size="small" /> : '没有匹配的可用工单'} />
      </Form.Item>
      <div className="learning-source-pager"><span>工单第 {page} 页，共 {total} 条</span><Space>
        <Button size="small" disabled={page <= 1 || loading} onClick={() => setPage(page - 1)}>上一页工单</Button>
        <Button size="small" disabled={page * 20 >= total || loading} onClick={() => setPage(page + 1)}>下一页工单</Button>
      </Space></div>
      {selected.length > 0 && <section className="learning-source-section" aria-label="已选来源工单">
        <h3>已选工单（{selected.length}）</h3>
        <p className="learning-source-hint">下方为脱敏后的简要描述，可切换参照工单查找相似材料。</p>
        {selected.map((id) => {
          const task = displayed.get(id)!;
          return <article className={`learning-source-card${activeAnchor === id ? ' is-anchor' : ''}`} aria-label={`已选工单 ${id}`} key={id}>
            <div className="learning-source-card-heading"><strong>{id} · {task.title}</strong><span>{task.status ? STATUS_LABELS[task.status] : '待查询'}</span></div>
            <p>{task.summary || '暂无简要描述'}</p>
            <Space wrap><Button size="small" type={activeAnchor === id ? 'primary' : 'default'} disabled={activeAnchor === id || saving}
              aria-label={`查找工单 ${id} 的相似工单`} onClick={() => setAnchorId(id)}>{activeAnchor === id ? '当前参照工单' : '查找相似工单'}</Button>
              <Button size="small" type="text" danger disabled={saving} aria-label={`移除来源工单 ${id}`}
                onClick={() => form.setFieldValue('task_ids', selected.filter((selectedId) => selectedId !== id))}>移除来源</Button></Space>
          </article>;
        })}
      </section>}
      {activeAnchor && <section className="learning-source-section" aria-label="相似工单推荐">
        <h3>相似工单推荐</h3>
        <p className="learning-source-hint">以工单 {activeAnchor} 为参照，使用与知识沉淀审核页相同的向量检索，推荐已审核沉淀的相似工单；请判断后手动加入，不代表技术结论相同。</p>
        {similarLoading ? <div role="status" className="learning-source-hint"><Spin size="small" /> 正在查找相似工单…</div> : similarError ?
          <Alert type="warning" showIcon message={similarError} action={<Button size="small" onClick={() => setSimilarRetry((current) => current + 1)}>重试相似工单</Button>} /> :
          recommendations.length ? recommendations.map((task) => <article className="learning-source-card" aria-label={`相似工单 ${task.id}`} key={task.id}>
            <div className="learning-source-card-heading"><strong>{task.id} · {task.title}</strong><span>{STATUS_LABELS[task.status]}</span></div>
            <p>{task.summary || '暂无简要描述'}</p>
            <Button size="small" icon={<Plus size={14} />} aria-label={`加入来源工单 ${task.id}`} disabled={sourceLimitReached || saving}
              onClick={() => {
                if (!selected.includes(task.id) && !sourceLimitReached) form.setFieldValue('task_ids', [...selected, task.id]);
              }}>加入来源</Button>
          </article>) : <p className="learning-source-hint">暂无相似的可用工单，可继续搜索或添加技术材料。</p>}
        {sourceLimitReached && <p className="learning-source-hint">来源已达10份上限，移除工单或技术材料后可继续加入。</p>}
      </section>}
      <Form.List name="materials">{(fields, { add, remove }) => <>
        {fields.map((field, index) => <div className="learning-material" key={field.key}>
          <div className="learning-material-heading"><strong>技术材料 {index + 1}</strong><Button type="text" danger icon={<Trash2 size={15} />} onClick={() => remove(field.name)}>移除</Button></div>
          <Form.Item name={[field.name, 'name']} label="材料名称" rules={[{ required: true, whitespace: true }]}><Input maxLength={255} placeholder="例如：定位维护规范" /></Form.Item>
          <Form.Item name={[field.name, 'provenance']} label="出处及版本" rules={[{ required: true, whitespace: true }]}><Input maxLength={1000} placeholder="说明资料出处、版本和适用范围" /></Form.Item>
          <Form.Item name={[field.name, 'text']} label="材料内容" rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={5} maxLength={16000} showCount placeholder="粘贴有权使用的技术资料，先移除身份、项目及私有配置" /></Form.Item>
        </div>)}
        <Button block type="dashed" icon={<Plus size={15} />} disabled={fields.length >= 10} onClick={() => add({ name: '', provenance: '', text: '' })}>添加技术材料</Button>
      </>}</Form.List>
    </Form>
  </Modal>;
}
