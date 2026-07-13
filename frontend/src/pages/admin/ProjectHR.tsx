// 人力资源管理 - 从 BackgroundService tmp-project 迁移
import { useState, useEffect } from 'react';
import { Loading, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

interface HRItem { id: string; name: string; role: string; projects: string[]; availability: number; }

export default function ProjectHR() {
  const [items, setItems] = useState<HRItem[]>([]);
  const [loading, setLoading] = useState(true);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');
  // TODO: 新后端暂无 /hr/overview 接口，需后端补充

  useEffect(() => {
    request<HRItem[]>('/hr/overview').then(setItems).catch((e) => Toast({ message: String(e), theme: 'error' })).finally(() => setLoading(false));
  }, []);

  if (loading) return <Loading text="加载人力资源..." />;

  return (
    <div style={{ padding: 16 }}>
      {items.map((item) => (
        <div key={item.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 500 }}>{item.name}</div>
              <div style={{ fontSize: 13, color: '#666' }}>{item.role} · {item.projects?.join(', ') || '未分配'}</div>
            </div>
            <div style={{ fontSize: 13, padding: '4px 10px', borderRadius: 12, background: item.availability > 50 ? '#e8f5e9' : '#fce4ec', color: item.availability > 50 ? '#2e7d32' : '#c62828' }}>
              {item.availability > 50 ? '可用' : '忙碌'} ({item.availability}%)
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
