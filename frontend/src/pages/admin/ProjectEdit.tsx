// 项目编辑 - 从 BackgroundService tmp-project 迁移
import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Form, FormItem, Input, Textarea, Button, Toast, Loading } from 'tdesign-mobile-react';
import ClearableInput from '@/shared/components/ClearableInput';
import { createRequest, clearCache } from '@/api/client';
import API_CONFIG from '@/config/api';

export default function ProjectEdit() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState({ project_code: '', name: '', description: '', status: '售前方案' });
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  useEffect(() => {
    if (id) {
      setLoading(true);
      request<{ project_code: string; name: string; description: string; status: string }>(`/projects/${encodeURIComponent(id)}`)
        .then((data) => setForm({ project_code: data.project_code, name: data.name, description: data.description, status: data.status }))
        .catch((err) => Toast({ message: `加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }))
        .finally(() => setLoading(false));
    }
  }, [id]);

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      // 防御：strip 掉 project_code / name 两端空白，避免尾随空格导致
      // GET /projects/{id}/members 查询永远匹配不上 INSERT 进去的行
      const trimmed = {
        ...form,
        project_code: (form.project_code || '').trim(),
        name: (form.name || '').trim(),
        description: (form.description || '').trim(),
      };
      if (!trimmed.name) {
        Toast({ message: '项目名称不能为空', theme: 'warning' });
        setSubmitting(false);
        return;
      }
      if (id) {
        await request(`/projects/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(trimmed) });
      } else {
        await request('/projects/', { method: 'POST', body: JSON.stringify(trimmed) });
      }
      Toast({ message: '保存成功', theme: 'success' });
      clearCache(); // 清除请求缓存，确保返回项目管理页时能拉取到最新数据
      navigate(-1);
    } catch (err) {
      Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally { setSubmitting(false); }
  };

  if (loading) return <Loading text="加载中..." />;

  return (
    <div style={{ padding: 16 }}>
      <h4 style={{ marginBottom: 16 }}>{id ? '编辑项目' : '新建项目'}</h4>
      <Form onSubmit={handleSubmit}>
        <FormItem label="项目编码" name="project_code">
          <ClearableInput value={form.project_code} onChange={(v) => setForm((p) => ({ ...p, project_code: String(v) }))} placeholder="选填，留空自动生成" />
        </FormItem>
        <FormItem label="项目名称" name="name">
          <ClearableInput value={form.name} onChange={(v) => setForm((p) => ({ ...p, name: String(v) }))} placeholder="输入项目名称" />
        </FormItem>
        <FormItem label="描述" name="description">
          <Textarea value={form.description} onChange={(v) => setForm((p) => ({ ...p, description: String(v) }))} placeholder="项目描述" autosize={{ minRows: 3 }} />
        </FormItem>
        <FormItem label="状态" name="status">
          <Input value={form.status} onChange={(v) => setForm((p) => ({ ...p, status: String(v) }))} placeholder="active" />
        </FormItem>
        <FormItem><Button theme="primary" block type="submit" loading={submitting}>保存</Button></FormItem>
      </Form>
    </div>
  );
}
