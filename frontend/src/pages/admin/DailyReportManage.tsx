// 日报管理 - 基于接口文档 /api/admin/daily-reports
import { useState, useEffect, useCallback } from 'react';
import { Button, Toast, Loading, Dialog, Input, Textarea, Popup, Form, FormItem } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { formatDateTime } from '@/shared/utils/url';

interface DailyReport {
  id: string; title: string; content: string; project_code?: string;
  report_date?: string; created_at: string; updated_at: string;
}

const pageSize = 20;

export default function DailyReportManage() {
  const [reports, setReports] = useState<DailyReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [keyword, setKeyword] = useState('');

  // 新建/编辑表单
  const [editVisible, setEditVisible] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState({ title: '', content: '', project_code: '', report_date: '' });
  const [formLoading, setFormLoading] = useState(false);

  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const fetchReports = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(page), size: String(pageSize),
      });
      if (keyword) params.set('keyword', keyword);
      const data = await request<{ items: DailyReport[]; total: number }>(`/daily-reports/?${params.toString()}`);
      setReports(data.items || []);
      setTotal(data.total || 0);
    } catch (err) {
      Toast({ message: `加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally { setLoading(false); }
  }, [page, keyword]);

  useEffect(() => { fetchReports(); }, [fetchReports]);

  // 搜索
  const handleSearch = () => { setPage(1); fetchReports(); };

  // 新建
  const openCreate = () => {
    setEditingId(null);
    setForm({ title: '', content: '', project_code: '', report_date: new Date().toISOString().slice(0, 10) });
    setEditVisible(true);
  };

  // 编辑：先拉取详情再打开编辑
  const openEdit = async (report: DailyReport) => {
    try {
      setFormLoading(true);
      const detail = await request<DailyReport>(`/daily-reports/${report.id}`);
      setEditingId(report.id);
      setForm({
        title: detail.title || '',
        content: detail.content || '',
        project_code: detail.project_code || '',
        report_date: detail.report_date || '',
      });
      setEditVisible(true);
    } catch (err) {
      Toast({ message: `获取详情失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally { setFormLoading(false); }
  };

  // 保存
  const handleSave = async () => {
    if (!form.title.trim()) { Toast({ message: '请输入日报标题', theme: 'warning' }); return; }
    setSubmitting(true);
    try {
      if (editingId) {
        await request(`/daily-reports/${editingId}`, { method: 'PUT', body: JSON.stringify(form) });
        Toast({ message: '日报已更新', theme: 'success' });
      } else {
        await request('/daily-reports/', { method: 'POST', body: JSON.stringify(form) });
        Toast({ message: '日报已创建', theme: 'success' });
      }
      setEditVisible(false);
      fetchReports();
    } catch (err) {
      Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally { setSubmitting(false); }
  };

  // 删除
  const handleDelete = (report: DailyReport) => {
    Dialog.confirm?.({
      title: '确认删除',
      content: `确定要删除日报「${report.title}」吗？`,
      onConfirm: async () => {
        try {
          await request(`/daily-reports/${report.id}`, { method: 'DELETE' });
          Toast({ message: '已删除', theme: 'success' });
          fetchReports();
        } catch (err) {
          Toast({ message: `删除失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        }
      },
    });
  };

  return (
    <div style={{ padding: 16 }}>
      {/* 搜索栏 */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <Input
          value={keyword}
          onChange={(v) => setKeyword(String(v))}
          placeholder="搜索日报…"
          clearable
          style={{ flex: 1 }}
        />
        <Button size="small" theme="primary" onClick={handleSearch}>搜索</Button>
        <Button size="small" onClick={openCreate}>+ 新建</Button>
      </div>

      {/* 日报列表 */}
      {loading ? <Loading text="加载中..." /> : reports.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>暂无日报</div>
      ) : (
        <>
          {reports.map((r) => (
            <div key={r.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>{r.title}</div>
                  <div style={{ fontSize: 13, color: '#666', lineHeight: 1.5, maxHeight: 60, overflow: 'hidden' }}>
                    {r.content?.slice(0, 120)}{(r.content?.length || 0) > 120 ? '…' : ''}
                  </div>
                  <div style={{ fontSize: 12, color: '#999', marginTop: 6 }}>
                    {r.report_date && <span>{r.report_date} · </span>}
                    {r.project_code && <span>{r.project_code} · </span>}
                    {formatDateTime(r.created_at).slice(0, 10)}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 8, flexShrink: 0, marginLeft: 12 }}>
                  <Button size="small" variant="outline" onClick={() => openEdit(r)}>编辑</Button>
                  <Button size="small" theme="danger" variant="outline" onClick={() => handleDelete(r)}>删除</Button>
                </div>
              </div>
            </div>
          ))}
          {/* 简易分页 */}
          <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginTop: 12 }}>
            <Button size="small" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>上一页</Button>
            <span style={{ lineHeight: '32px', fontSize: 13, color: '#666' }}>第 {page} 页 / 共 {Math.ceil(total / pageSize) || 1} 页</span>
            <Button size="small" variant="outline" disabled={page * pageSize >= total} onClick={() => setPage((p) => p + 1)}>下一页</Button>
          </div>
        </>
      )}

      {/* 新建/编辑弹窗 */}
      <Popup visible={editVisible} onClose={() => setEditVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '80vh', overflow: 'auto' }}>
          <h4 style={{ marginBottom: 16, fontSize: 16 }}>{editingId ? '编辑日报' : '新建日报'}</h4>
          {formLoading ? <Loading text="加载中..." /> : (
            <Form onSubmit={handleSave}>
              <FormItem label="标题">
                <Input value={form.title} onChange={(v) => setForm((p) => ({ ...p, title: String(v) }))} placeholder="日报标题" clearable />
              </FormItem>
              <FormItem label="内容">
                <Textarea value={form.content} onChange={(v) => setForm((p) => ({ ...p, content: String(v) }))} placeholder="日报内容" autosize={{ minRows: 4, maxRows: 10 }} />
              </FormItem>
              <FormItem label="项目代码">
                <Input value={form.project_code} onChange={(v) => setForm((p) => ({ ...p, project_code: String(v) }))} placeholder="关联项目代码" clearable />
              </FormItem>
              <FormItem label="日期">
                <Input value={form.report_date} onChange={(v) => setForm((p) => ({ ...p, report_date: String(v) }))} placeholder="2024-01-01" clearable />
              </FormItem>
              <FormItem>
                <div style={{ display: 'flex', gap: 8 }}>
                  <Button theme="default" block onClick={() => setEditVisible(false)}>取消</Button>
                  <Button theme="primary" block type="submit" loading={submitting}>保存</Button>
                </div>
              </FormItem>
            </Form>
          )}
        </div>
      </Popup>
    </div>
  );
}
