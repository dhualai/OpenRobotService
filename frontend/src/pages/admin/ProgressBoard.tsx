// 进度看板 - 从 BackgroundService tmp-project 迁移
import { useState, useEffect } from 'react';
import { Loading } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import ReactECharts from '@/shared/components/ReactECharts';

interface ProgressItem { name: string; progress: number; status: string; }

export default function ProgressBoard() {
  const [items, setItems] = useState<ProgressItem[]>([]);
  const [loading, setLoading] = useState(true);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');
  // TODO: 新后端暂无 /progress/board 接口，需后端补充

  useEffect(() => {
    request('/progress/board').then((d) => setItems(normalizeList<ProgressItem>(d))).catch(console.error).finally(() => setLoading(false));
  }, []);

  if (loading) return <Loading text="加载进度看板..." />;

  const chartOption = {
    tooltip: { trigger: 'axis' as const },
    xAxis: { type: 'value' as const, max: 100 },
    yAxis: { type: 'category' as const, data: items.map((i) => i.name) },
    series: [{ type: 'bar', data: items.map((i) => i.progress), label: { show: true, formatter: '{c}%' }, color: '#0052d9' }],
  };

  return (
    <div style={{ padding: 16 }}>
      <h4 style={{ marginBottom: 16 }}>项目进度总览</h4>
      <div style={{ background: '#fff', borderRadius: 8, padding: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.08)' }}>
        <ReactECharts option={chartOption} style={{ height: 400 }} />
      </div>
      {items.map((item) => (
        <div key={item.name} style={{ background: '#fff', borderRadius: 8, padding: 14, marginTop: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
            <strong>{item.name}</strong>
            <span style={{ color: '#0052d9', fontWeight: 600 }}>{item.progress}%</span>
          </div>
          <div style={{ height: 8, background: '#f0f0f0', borderRadius: 4, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${item.progress}%`, background: '#0052d9', borderRadius: 4, transition: 'width 0.3s' }} />
          </div>
        </div>
      ))}
    </div>
  );
}
