import { useEffect, useState } from 'react';
import { Alert, Button, Checkbox, Form, Input, Modal, Select, Space, Spin } from 'antd';
import { Plus, Trash2 } from 'lucide-react';
import { listSourceTasks, type Material, type SourceInput, type SourceSnapshot, type SourceTask } from '@/api/learning';

const STATUS_LABELS = { closed: '已关闭', archiving: '归档中', archived: '已归档' };
const EMPTY_SOURCES: SourceSnapshot[] = [];

interface SourceValues { title: string; task_ids: string[]; materials: Material[]; verified: boolean }

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
  const selected = Form.useWatch('task_ids', form) || [];

  useEffect(() => {
    if (!open) return;
    form.resetFields();
    form.setFieldsValue({ title: initialTitle, task_ids: initialSources.filter((source) => source.type === 'work_order').map((source) => String(source.id)),
      materials: initialSources.filter((source) => source.type === 'manual').map((source) => ({ name: source.label, text: source.content, provenance: source.provenance || '' })), verified: false });
    setKeyword(''); setPage(1); setError('');
  }, [open, initialTitle, initialSources, form]);

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

  const save = async () => {
    try {
      const values = await form.validateFields();
      const materials = values.materials || [];
      if (!values.task_ids?.length && !materials.length) { setError('至少选择一个工单或填写一份技术材料'); return; }
      if ((values.task_ids?.length || 0) + materials.length > 10) { setError('每次最多使用10份材料'); return; }
      setSaving(true); setError('');
      await onSubmit(values.title.trim(), { task_ids: values.task_ids || [], materials, verified: values.verified });
    } catch (cause) { if (cause instanceof Error) setError(cause.message); }
    finally { setSaving(false); }
  };

  const displayed = new Map(tasks.map((task) => [task.id, task]));
  selected.forEach((id: string) => {
    if (!displayed.has(id)) displayed.set(id, knownTasks[id] || { id, title: `已选工单 ${id}`, status: 'closed' });
  });

  return <Modal open={open} title={replacing ? '调整知识点来源' : '新建知识点 · 选择来源材料'} width={720} onCancel={() => { if (!saving) onClose(); }}
    onOk={save} okText={replacing ? '保存来源' : '创建草稿并继续'} confirmLoading={saving} forceRender>
    <Alert type="info" showIcon message="可合并多个相似工单，也可仅使用技术材料。原始来源不进入学习内容，AI调用前会再次脱敏。" />
    {error && <Alert type="error" role="alert" message={error} closable onClose={() => setError('')} />}
    <Form name={replacing ? 'learning-replace-source' : 'learning-new-source'} form={form} layout="vertical" initialValues={{ task_ids: [], materials: [], verified: false }}>
      <Form.Item name="title" label="知识主题" rules={[{ required: true, whitespace: true, message: '请填写要提炼的知识主题' }]}>
        <Input maxLength={255} placeholder="例如：定位异常排查时如何区分环境与配置因素" />
      </Form.Item>
      <Form.Item name="task_ids" label="来源工单" extra="允许已关闭、归档中、已归档；可跨页多选，无工单也可使用下方技术材料。">
        <Select mode="multiple" showSearch filterOption={false} placeholder="按工单编号或标题搜索" loading={loading}
          onSearch={(value) => { setKeyword(value); setPage(1); }}
          options={[...displayed.values()].map((task) => ({ value: task.id, label: `${task.id} · ${task.title}（${STATUS_LABELS[task.status]}）` }))}
          notFoundContent={loading ? <Spin size="small" /> : '没有匹配的可用工单'} />
      </Form.Item>
      <div className="learning-source-pager"><span>工单第 {page} 页，共 {total} 条</span><Space>
        <Button size="small" disabled={page <= 1 || loading} onClick={() => setPage(page - 1)}>上一页工单</Button>
        <Button size="small" disabled={page * 20 >= total || loading} onClick={() => setPage(page + 1)}>下一页工单</Button>
      </Space></div>
      <Form.List name="materials">{(fields, { add, remove }) => <>
        {fields.map((field, index) => <div className="learning-material" key={field.key}>
          <div className="learning-material-heading"><strong>技术材料 {index + 1}</strong><Button type="text" danger icon={<Trash2 size={15} />} onClick={() => remove(field.name)}>移除</Button></div>
          <Form.Item name={[field.name, 'name']} label="材料名称" rules={[{ required: true, whitespace: true }]}><Input maxLength={255} placeholder="例如：定位维护规范" /></Form.Item>
          <Form.Item name={[field.name, 'provenance']} label="出处及版本" rules={[{ required: true, whitespace: true }]}><Input maxLength={1000} placeholder="说明资料出处、版本和适用范围" /></Form.Item>
          <Form.Item name={[field.name, 'text']} label="材料内容" rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={5} maxLength={16000} showCount placeholder="粘贴有权使用的技术资料，先移除身份、项目及私有配置" /></Form.Item>
        </div>)}
        <Button block type="dashed" icon={<Plus size={15} />} disabled={fields.length >= 10} onClick={() => add({ name: '', provenance: '', text: '' })}>添加技术材料</Button>
      </>}</Form.List>
      <Form.Item name="verified" valuePropName="checked" className="learning-source-verified"
        rules={[{ validator: (_, value) => value ? Promise.resolve() : Promise.reject(new Error('请先确认材料授权和敏感信息已核对')) }]}>
        <Checkbox>已核对材料使用授权和敏感信息；生成结果仍须人工确认</Checkbox>
      </Form.Item>
    </Form>
  </Modal>;
}
