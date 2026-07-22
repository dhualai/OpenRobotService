// 项目进度管理 —— 聚合项目列表 + 风险状态，侧重视觉化项目进度
import { useState, useEffect, useCallback } from 'react';
import { Button, Toast, Loading } from 'tdesign-mobile-react';
import { useNavigate } from 'react-router-dom';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface ProjectItem {
  id: string;
  project_code: string;
  name: string;
  status: string;
  contact_person: string;
  risks: number;
  project_summary: string;
  task_execution_status: string;
}

const STATUS_COLOR: Record<string, string> = {
  '正常': '#00a870',
  '延迟': '#e37318',
  '阻塞': '#d54941',
};

export default function ProjectProgress() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectItem[]>([]);
  const [loading, setLoading] = useState(true);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const fetchProjects = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request<ProjectItem[]>('/projects/?include_analysis=true');
      setProjects(normalizeList<ProjectItem>(data));
    } catch (err) {
      Toast({ message: String(err), theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchProjects(); }, [fetchProjects]);

  if (loading) return <Loading text="加载项目..." />;

  const activeCount = projects.filter((p) =>
    !['项目中止', '项目结束'].includes(p.status)
  ).length;

  return (
    <div style={{ padding: 16 }}>
      {/* 概览卡片 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12, marginBottom: 16 }}>
        <StatCard label="项目总数" value={projects.length} color="#0052d9" />
        <StatCard label="活跃项目" value={activeCount} color="#2ba471" />
      </div>

      {/* 项目列表 */}
      {projects.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>暂无项目数据</div>
      ) : (
        projects.map((p) => {
          const execStatus = p.task_execution_status || '';
          const execColor = STATUS_COLOR[execStatus] || '#999';
          const hasRisk = p.risks > 0;

          return (
            <div
              key={p.id}
              style={{
                background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10,
                boxShadow: '0 1px 3px rgba(0,0,0,0.06)', cursor: 'pointer',
                borderLeft: hasRisk ? '3px solid #d54941' : '3px solid transparent',
              }}
              onClick={() => navigate(`/admin/project-progress-detail/${p.id}`)}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 15 }}>{p.name}</div>
                  <div style={{ fontSize: 12, color: '#999', marginTop: 2 }}>
                    {p.project_code} · 对接人: {p.contact_person || '未指定'}
                  </div>
                </div>
                <div style={{ textAlign: 'right', flexShrink: 0, marginLeft: 8 }}>
                  <span style={{
                    fontSize: 11, padding: '2px 8px', borderRadius: 10,
                    background: '#f0f0f0', color: '#666',
                  }}>
                    {p.status}
                  </span>
                  {execStatus && (
                    <div style={{ fontSize: 11, color: execColor, marginTop: 4, fontWeight: 500 }}>
                      ● {execStatus}
                    </div>
                  )}
                </div>
              </div>

              {/* 进度条简易展示 */}
              {hasRisk && (
                <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid #f0f0f0' }}>
                  <span style={{ fontSize: 12, color: '#d54941' }}>⚠ {p.risks} 项未关闭风险</span>
                </div>
              )}
              {!hasRisk && p.project_summary && p.project_summary !== '无风险' && (
                <div style={{ marginTop: 8, fontSize: 12, color: '#666', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {p.project_summary}
                </div>
              )}
            </div>
          );
        })
      )}

      {/* 快捷入口 */}
      <div style={{ marginTop: 20, display: 'flex', gap: 12 }}>
        <Button theme="primary" block onClick={() => navigate('/admin/project-manage')}>
          📁 全部项目管理
        </Button>
      </div>
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={{ background: '#fff', borderRadius: 8, padding: '14px 12px', boxShadow: '0 1px 3px rgba(0,0,0,0.08)', textAlign: 'center' }}>
      <div style={{ fontSize: 26, fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: 13, color: '#999', marginTop: 2 }}>{label}</div>
    </div>
  );
}
