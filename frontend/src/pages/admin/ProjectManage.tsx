// 项目管理 - 从 BackgroundService tmp 迁移
import { useState, useEffect } from 'react';
import { Button, Toast, Loading, Dialog } from 'tdesign-mobile-react';
import { useNavigate } from 'react-router-dom';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

interface Project { id: string; name: string; status: string; created_at: string; }

export default function ProjectManage() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const request = createRequest(API_CONFIG.PROJECT.BASE_URL, 'Project');

  const fetchProjects = async () => {
    setLoading(true);
    try {
      const data = await request<Project[]>('/projects/');
      setProjects(data || []);
    } catch (err) { Toast({ message: String(err), theme: 'error' }); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetchProjects(); }, []);

  const handleDelete = (id: string) => {
    Dialog.confirm({
      title: '确认删除', content: '确定要删除此项目吗？',
      onConfirm: async () => {
        await request(`/projects/${id}`, { method: 'DELETE' });
        Toast({ message: '已删除', theme: 'success' });
        fetchProjects();
      },
    });
  };

  if (loading) return <Loading text="加载中..." />;

  return (
    <div style={{ padding: 16 }}>
      <Button theme="primary" block style={{ marginBottom: 16 }} onClick={() => navigate('/admin/project-edit')}>
        新建项目
      </Button>
      {projects.map((p) => (
        <div key={p.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 500 }}>{p.name}</div>
              <div style={{ fontSize: 12, color: '#999' }}>{p.status}</div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button size="small" variant="outline" onClick={() => navigate(`/admin/project-edit/${p.id}`)}>编辑</Button>
              <Button size="small" theme="danger" variant="outline" onClick={() => handleDelete(p.id)}>删除</Button>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
