// 项目授权管理 - 从 BackgroundService tmp-project 迁移
import { useState, useEffect } from 'react';
import { Button, Toast, Loading, Dialog } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface AuthItem { id: string; project: string; user: string; role: string; granted_at: string; }

export default function ProjectAuth() {
  const [items, setItems] = useState<AuthItem[]>([]);
  const [loading, setLoading] = useState(true);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const fetchData = async () => {
    setLoading(true);
    try {
      const data = await request('/projects/licenses/all');
      // TODO: 原接口 /project/auth/list，新接口改为 /projects/licenses/{project_code}?type=all
      setItems(normalizeList<AuthItem>(data));
    } catch (err) {
      Toast({ message: `加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally { setLoading(false); }
  };

  useEffect(() => { fetchData(); }, []);

  const handleRevoke = async (id: string) => {
    Dialog.confirm?.({
      title: '确认撤销',
      content: '确定要撤销此授权吗？',
      onConfirm: async () => {
        try {
          // TODO: 撤销授权接口需确认新后端对应端点
          await request(`/project/auth/${id}`, { method: 'DELETE' });
          Toast({ message: '授权已撤销', theme: 'success' });
          fetchData();
        } catch (err) {
          Toast({ message: `撤销失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        }
      },
    });
  };

  if (loading) return <Loading text="加载中..." />;

  return (
    <div style={{ padding: 16 }}>
      {items.map((item) => (
        <div key={item.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 500 }}>{item.project}</div>
              <div style={{ fontSize: 13, color: '#666' }}>{item.user} - {item.role}</div>
            </div>
            <Button size="small" theme="danger" variant="outline" onClick={() => handleRevoke(item.id)}>撤销</Button>
          </div>
        </div>
      ))}
      {items.length === 0 && <div style={{ textAlign: 'center', padding: 60, color: '#999' }}>暂无授权记录</div>}
    </div>
  );
}
