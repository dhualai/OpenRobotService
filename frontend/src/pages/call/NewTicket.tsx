// 一键报障 / 新建工单页面 - 从 HelpDesk NewTicketForm 迁移
import { useState } from 'react';
import { Navbar, Form, FormItem, Input, Textarea, Button, Toast, Upload } from 'tdesign-mobile-react';
import { useNavigate } from 'react-router-dom';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

export default function NewTicket() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [formData, setFormData] = useState({
    title: '',
    description: '',
    priority: 'medium',
    ticket_type: 'support',
    project_id: '',
    assignee_id: '',
  });

  const request = createRequest(API_CONFIG.FQA.BASE_URL, 'FQA');

  const handleSubmit = async () => {
    if (!formData.title.trim()) {
      Toast({ message: '请输入工单标题', theme: 'warning' });
      return;
    }
    setLoading(true);
    try {
      await request('/tickets/', {
        method: 'POST',
        body: JSON.stringify(formData),
      });
      Toast({ message: '工单创建成功', theme: 'success' });
      navigate('/tasks', { replace: true });
    } catch (err) {
      Toast({ message: `创建失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page-container" style={{ paddingTop: 56 }}>
      <Navbar title="一键报障" fixed onLeftClick={() => navigate(-1)} />
      <Form onSubmit={handleSubmit}>
        <FormItem label="问题标题" name="title">
          <Input
            placeholder="简要描述你遇到的问题"
            value={formData.title}
            onChange={(v) => setFormData((p) => ({ ...p, title: String(v) }))}
            clearable
          />
        </FormItem>
        <FormItem label="详细描述" name="description">
          <Textarea
            placeholder="请详细描述问题的现象、复现步骤等"
            value={formData.description}
            onChange={(v) => setFormData((p) => ({ ...p, description: String(v) }))}
            autosize={{ minRows: 4, maxRows: 10 }}
          />
        </FormItem>
        <FormItem label="优先级" name="priority">
          <Input
            placeholder="中"
            value={formData.priority === 'medium' ? '中' : formData.priority}
            onChange={(v) => setFormData((p) => ({ ...p, priority: String(v) }))}
          />
        </FormItem>
        <FormItem label="类型" name="ticket_type">
          <Input
            placeholder="支持"
            value={formData.ticket_type === 'support' ? '支持' : formData.ticket_type}
            onChange={(v) => setFormData((p) => ({ ...p, ticket_type: String(v) }))}
          />
        </FormItem>
        <FormItem label="附件" name="attachments">
          <Upload accept="image/*" max={5} />
        </FormItem>
        <FormItem>
          <Button theme="primary" type="submit" block loading={loading}>
            提交工单
          </Button>
        </FormItem>
      </Form>
    </div>
  );
}
