// 风险编辑 - 从 BackgroundService tmp-project 迁移
import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Form, FormItem, Input, Textarea, Button, Toast, Loading } from 'tdesign-mobile-react';
import ClearableInput from '@/shared/components/ClearableInput';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

export default function RiskEdit() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState({ title: '', description: '', level: 'medium', project: '', mitigation: '' });
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  useEffect(() => {
    if (id) {
      setLoading(true);
      request<typeof form>(`/projects/risks/${id}`).then(setForm).catch((e) => Toast({ message: String(e), theme: 'error' })).finally(() => setLoading(false));
    }
  }, [id]);

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      if (id) await request(`/projects/risks/${id}`, { method: 'PUT', body: JSON.stringify(form) });
      else await request('/projects/risks/', { method: 'POST', body: JSON.stringify(form) });
      Toast({ message: '保存成功', theme: 'success' });
      navigate(-1);
    } catch (err) { Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }); }
    finally { setSubmitting(false); }
  };

  if (loading) return <Loading text="加载中..." />;

  return (
    <div style={{ padding: 16 }}>
      <Form onSubmit={handleSubmit}>
        <FormItem label="风险标题"><ClearableInput value={form.title} onChange={(v) => setForm((p) => ({ ...p, title: String(v) }))} placeholder="输入风险标题" /></FormItem>
        <FormItem label="描述"><Textarea value={form.description} onChange={(v) => setForm((p) => ({ ...p, description: String(v) }))} placeholder="风险描述" autosize={{ minRows: 3 }} /></FormItem>
        <FormItem label="级别"><Input value={form.level} onChange={(v) => setForm((p) => ({ ...p, level: String(v) }))} placeholder="low/medium/high" /></FormItem>
        <FormItem label="关联项目"><Input value={form.project} onChange={(v) => setForm((p) => ({ ...p, project: String(v) }))} placeholder="项目名称" /></FormItem>
        <FormItem label="缓解措施"><Textarea value={form.mitigation} onChange={(v) => setForm((p) => ({ ...p, mitigation: String(v) }))} placeholder="风险缓解措施" autosize={{ minRows: 2 }} /></FormItem>
        <FormItem><Button theme="primary" block type="submit" loading={submitting}>保存</Button></FormItem>
      </Form>
    </div>
  );
}
