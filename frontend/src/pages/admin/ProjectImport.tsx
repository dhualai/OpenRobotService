// 项目管理 - 从 BackgroundService tmp 迁移
import { useState, useEffect } from 'react';
import { Button, Toast, Loading, Dialog } from 'tdesign-mobile-react';
import { useNavigate } from 'react-router-dom';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface Project { id: string; name: string; status: string; created_at: string; }

export default function ProjectManage() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const fetchProjects = async () => {
    setLoading(true);
    try {
      const data = await request('/projects/');
      setProjects(normalizeList<Project>(data));
    } catch (err) { Toast({ message: String(err), theme: 'error' }); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetchProjects(); }, []);

  const handleDelete = (id: string) => {
    Dialog.confirm?.({
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
      <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
        <Button theme="primary" block onClick={() => navigate('/admin/project-detail/new')}>
          USP项目
        </Button>
        <Button theme="primary" variant="outline" block onClick={() => navigate('/admin/project-edit')}>
          其他项目
        </Button>
      </div>
      {projects.map((p) => (
        <div
          key={p.id}
          onClick={() => navigate(`/admin/project-detail/${p.id}`)}
          style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)', cursor: 'pointer' }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 500 }}>{p.name}</div>
              <div style={{ fontSize: 12, color: '#999' }}>{p.status}</div>
            </div>
            <div style={{ display: 'flex', gap: 8 }} onClick={(e) => e.stopPropagation()}>
              <Button size="small" theme="danger" variant="outline" onClick={() => handleDelete(p.id)}>删除</Button>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
