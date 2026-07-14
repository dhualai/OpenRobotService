// 人员分配看板 - 从 BackgroundService tmp-project 迁移
import { useState, useEffect } from 'react';
import { Loading } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface Allocation { id: string; person: string; project: string; role: string; workload: number; }

export default function PersonnelBoard() {
  const [items, setItems] = useState<Allocation[]>([]);
  const [loading, setLoading] = useState(true);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');
  // TODO: 新后端暂无 /personnel/board 接口，需后端补充

  useEffect(() => {
    request('/personnel/board').then((d) => setItems(normalizeList<Allocation>(d))).catch(console.error).finally(() => setLoading(false));
  }, []);

  if (loading) return <Loading text="加载人员分配..." />;

  return (
    <div style={{ padding: 16 }}>
      <h4 style={{ marginBottom: 16 }}>人员分配看板</h4>
      {items.map((item) => (
        <div key={item.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 500 }}>{item.person}</div>
              <div style={{ fontSize: 13, color: '#666', marginTop: 2 }}>{item.project} - {item.role}</div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: 20, fontWeight: 700, color: item.workload > 80 ? '#d54941' : '#0052d9' }}>{item.workload}%</div>
              <div style={{ fontSize: 12, color: '#999' }}>工作负载</div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
