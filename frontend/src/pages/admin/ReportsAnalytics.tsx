// 报表分析 - 从 BackgroundService tmp-project 迁移
import { useState, useEffect } from 'react';
import { Loading } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import ReactECharts from 'echarts-for-react';

interface ReportData { labels: string[]; values: number[]; summary: string; }

export default function ReportsAnalytics() {
  const [data, setData] = useState<ReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const request = createRequest(API_CONFIG.PROJECT.BASE_URL, 'Project');

  useEffect(() => {
    request<ReportData>('/reports/analytics').then(setData).catch(console.error).finally(() => setLoading(false));
  }, []);

  if (loading) return <Loading text="加载报表..." />;

  const chartOption = {
    tooltip: { trigger: 'item' as const },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      data: (data?.labels || []).map((l: string, i: number) => ({ name: l, value: data?.values?.[i] || 0 })),
      label: { show: true },
    }],
  };

  return (
    <div style={{ padding: 16 }}>
      <h4 style={{ marginBottom: 16 }}>报表分析</h4>
      <div style={{ background: '#fff', borderRadius: 8, padding: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.08)', marginBottom: 16 }}>
        <ReactECharts option={chartOption} style={{ height: 300 }} />
      </div>
      {data?.summary && (
        <div style={{ background: '#fff', borderRadius: 8, padding: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.08)' }}>
          <h5 style={{ marginBottom: 8 }}>分析总结</h5>
          <p style={{ fontSize: 14, color: '#666', lineHeight: 1.6 }}>{data.summary}</p>
        </div>
      )}
    </div>
  );
}
