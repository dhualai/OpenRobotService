// 项目进度管理 —— 聚合项目列表 + 风险状态，侧重视觉化项目进度
import { useState, useEffect, useCallback } from 'react';
import { Button, Toast, Loading, Input } from 'tdesign-mobile-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface ProjectItem {
  id: string;
  project_code: string;
  name: string;
  status: string;
  contact_person: string;
  project_manager?: string | null;
  risks: number;
  project_summary: string;
  task_execution_status: string;
  settlement_period?: string | null; // 业绩核算期，格式 YYYY-MM，来自企业微信同步
}

const STATUS_COLOR: Record<string, string> = {
  '正常': '#00a870',
  '延迟': '#e37318',
  '阻塞': '#d54941',
};

// 与跨项目看板的四个统计数字（项目总数/本月新增项目数/风险项目数/缺少对接人项目数）对应的筛选类型
type ProjectFilter = 'new' | 'risk' | 'no_contact';

const FILTER_LABELS: Record<ProjectFilter, string> = {
  new: '本月新增项目数',
  risk: '风险项目数',
  no_contact: '对接人缺省',
};

function currentYearMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

export default function ProjectProgress() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const filter = (searchParams.get('filter') as ProjectFilter | null) || null;
  const [projects, setProjects] = useState<ProjectItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState('');
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

  const displayProjects = (() => {
    let list = projects;
    if (filter === 'new') {
      const ym = currentYearMonth();
      list = list.filter((p) => p.settlement_period === ym);
    } else if (filter === 'risk') {
      list = list.filter((p) => p.risks > 0);
    } else if (filter === 'no_contact') {
      list = list.filter((p) => !p.contact_person);
    }
    if (keyword.trim()) {
      const kw = keyword.trim().toLowerCase();
      list = list.filter((p) => p.name && p.name.toLowerCase().includes(kw));
    }
    return list;
  })();

  return (
    <div style={{ padding: 16 }}>
      {/* 概览卡片 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12, marginBottom: 16 }}>
        <StatCard label="项目总数" value={projects.length} color="#0052d9" />
        <StatCard label="活跃项目" value={activeCount} color="#2ba471" />
      </div>

      {/* 项目名称搜索 */}
      <div style={{ marginBottom: 12 }}>
        <Input
          value={keyword}
          onChange={(v) => setKeyword(String(v))}
          placeholder="搜索项目名称"
          clearable
        />
      </div>

      {/* 筛选提示条：从跨项目看板某个统计数字点进来时显示，可点击返回全部项目 */}
      {filter && (
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          background: '#eef1f4', borderRadius: 8, padding: '10px 14px', marginBottom: 12, fontSize: 13,
        }}>
          <span>当前筛选：<strong>{FILTER_LABELS[filter]}</strong>（{displayProjects.length}）</span>
          <span
            style={{ color: '#0052d9', cursor: 'pointer' }}
            onClick={() => setSearchParams({})}
          >
            查看全部
          </span>
        </div>
      )}

      {/* 项目列表 */}
      {displayProjects.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
          {filter ? '暂无符合条件的项目' : '暂无项目数据'}
        </div>
      ) : (
        displayProjects.map((p) => {
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
              onClick={() => navigate(`/admin/project-detail/${p.id}`)}
            >
              <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 600, fontSize: 15 }}>
                {p.name}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
                <span style={{
                  fontSize: 11, padding: '2px 8px', borderRadius: 10,
                  background: '#f0f0f0', color: '#666',
                }}>
                  {p.status}
                </span>
                {execStatus && (
                  <span style={{ fontSize: 11, color: execColor, fontWeight: 500 }}>
                    ● {execStatus}
                  </span>
                )}
              </div>
              <div style={{ fontSize: 12, color: '#999', marginTop: 6 }}>
                {p.project_code} · 项目经理: {p.project_manager || '未指定'}
              </div>

              {/* 进度条简易展示 */}
              {hasRisk && (
                <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid #f0f0f0' }}>
                  <span style={{ fontSize: 12, color: '#d54941' }}>⚠ {p.risks} 项未关闭风险</span>
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
