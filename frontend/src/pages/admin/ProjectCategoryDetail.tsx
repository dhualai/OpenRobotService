// 项目标签下钻明细 —— 点击仪表盘中「调度阶段」或「紧急度」标签后展示对应项目列表
// 通过路由参数 dimension（stage / urgency）区分两种下钻来源，复用同一个页面。
import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Loading } from 'tdesign-mobile-react';
import { fetchProjectsByStage, fetchProjectsByUrgency, type ProjectListItem } from '@/api/dashboard';
import { PROJECT_STAGE_MAP, URGENCY_MAP } from '@/shared/constants/dashboard';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';

export default function ProjectCategoryDetail() {
  const { dimension = 'stage', key = '' } = useParams<{ dimension: string; key: string }>();
  const navigate = useNavigate();
  const { projectIds, hasPermission } = useAuthStore();
  const canViewAll = hasPermission(PERMISSION_VIEW_ALL);
  const [items, setItems] = useState<ProjectListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  const isUrgency = dimension === 'urgency';
  const meta = isUrgency ? URGENCY_MAP[key] : PROJECT_STAGE_MAP[key];
  const backendReady = isUrgency ? true : (PROJECT_STAGE_MAP[key]?.backendReady ?? false);

  const load = useCallback(async () => {
    setLoading(true);
    const filterIds = canViewAll ? undefined : projectIds;
    const res = isUrgency ? await fetchProjectsByUrgency(key, filterIds) : await fetchProjectsByStage(key, filterIds);
    setItems(res.items);
    setTotal(res.total);
    setLoading(false);
  }, [key, isUrgency, projectIds, canViewAll]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <Navbar title={`${meta?.label || key} · 项目明细`} leftArrow onLeftClick={() => navigate(-1)} fixed />
      <div style={{ padding: '16px', paddingTop: 64 }}>
        <p style={{ fontSize: 13, color: '#999', marginBottom: 12 }}>共 {total} 个项目</p>

        {loading ? <Loading text="加载中..." /> : (
          items.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
              暂无数据
              {!backendReady && (
                <div style={{ marginTop: 8, fontSize: 12, color: '#bbb' }}>
                  该分类后端接口/枚举尚未接入，见 docs/工程文档.md
                </div>
              )}
            </div>
          ) : (
            items.map((p) => {
              const hasRisk = (p.risks ?? 0) > 0;

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
                  </div>
                  <div style={{ fontSize: 12, color: '#999', marginTop: 6 }}>
                    {p.project_code} · 项目经理: {p.project_manager || '未指定'}
                  </div>

                  {/* 任务统计：任务总数 / 已完成任务 / 任务完成率 / 切手动次数 */}
                  <div style={{
                    marginTop: 10,
                    display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 6,
                  }}>
                    <MiniStat label="任务总数" value={p.task_execution_stats?.total_tasks ?? '-'} />
                    <MiniStat label="已完成任务" value={p.task_execution_stats?.finished_tasks ?? '-'} />
                    <MiniStat
                      label="任务完成率"
                      value={
                        p.task_execution_stats?.completion_rate != null
                          ? `${Math.round(p.task_execution_stats.completion_rate * 100)}%`
                          : '-'
                      }
                    />
                    <MiniStat label="切手动次数" value={p.latest_manual_switch_count ?? '-'} />
                  </div>

                  {hasRisk && (
                    <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid #f0f0f0' }}>
                      <span style={{ fontSize: 12, color: '#d54941' }}>⚠ {p.risks} 项未关闭风险</span>
                    </div>
                  )}
                </div>
              );
            })
          )
        )}
      </div>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div style={{ background: '#f7f8fa', borderRadius: 6, padding: '6px 4px', textAlign: 'center' }}>
      <div style={{ fontSize: 14, fontWeight: 600, color: '#333' }}>{value}</div>
      <div style={{ fontSize: 10, color: '#999', marginTop: 2 }}>{label}</div>
    </div>
  );
}
