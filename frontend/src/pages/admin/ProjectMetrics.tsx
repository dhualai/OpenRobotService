// 项目指标仪表盘 - 从 BackgroundService tmp-project 迁移
import { useState, useEffect } from 'react';
import { Loading, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import ReactECharts from 'echarts-for-react';

interface MetricsData {
  total_projects: number;
  active_projects: number;
  total_tickets: number;
  open_tickets: number;
  avg_resolution_time: number;
  project_stats: Array<{ name: string; tickets: number; resolved: number }>;
}

export default function ProjectMetrics() {
  const [data, setData] = useState<MetricsData | null>(null);
  const [loading, setLoading] = useState(true);
  const request = createRequest(API_CONFIG.PROJECT.BASE_URL, 'Project');

  useEffect(() => {
    const fetchData = async () => {
      try {
        const result = await request<MetricsData>('/metrics/dashboard');
        setData(result);
      } catch (err) {
        console.error('Failed to load metrics:', err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  if (loading) return <Loading text="加载仪表盘..." />;

  const chartOption = {
    tooltip: { trigger: 'axis' as const },
    xAxis: { type: 'category' as const, data: data?.project_stats?.map((p) => p.name) || [] },
    yAxis: { type: 'value' as const },
    series: [
      { name: '工单总数', type: 'bar', data: data?.project_stats?.map((p) => p.tickets) || [], color: '#0052d9' },
      { name: '已解决', type: 'bar', data: data?.project_stats?.map((p) => p.resolved) || [], color: '#2ba471' },
    ],
  };

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12, marginBottom: 16 }}>
        <StatCard label="项目总数" value={data?.total_projects || 0} color="#0052d9" />
        <StatCard label="活跃项目" value={data?.active_projects || 0} color="#2ba471" />
        <StatCard label="工单总数" value={data?.total_tickets || 0} color="#e37318" />
        <StatCard label="未关闭工单" value={data?.open_tickets || 0} color="#d54941" />
      </div>
      <div style={{ background: '#fff', borderRadius: 8, padding: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.08)' }}>
        <h4 style={{ marginBottom: 12 }}>项目统计</h4>
        <ReactECharts option={chartOption} style={{ height: 300 }} />
      </div>
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={{ background: '#fff', borderRadius: 8, padding: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.08)', textAlign: 'center' }}>
      <div style={{ fontSize: 28, fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: 13, color: '#999', marginTop: 4 }}>{label}</div>
    </div>
  );
}
