// 项目进度管理 —— 聚合项目列表 + 风险状态，侧重视觉化项目进度
import { useState, useEffect, useCallback, useRef } from 'react';
import { Button, Toast, Loading, Popup, Dialog } from 'tdesign-mobile-react';
import ClearableInput from '@/shared/components/ClearableInput';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { aiGet } from '@/api/ai';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';

interface TaskExecutionStats {
  total_tasks: number;
  finished_tasks: number;
  completion_rate: number | null;
}

interface ProjectItem {
  id: string;
  project_code: string;
  system_id?: string | null;
  name: string;
  status: string;
  contact_person: string;
  project_manager?: string | null;
  risks: number;
  project_summary: string;
  task_execution_status: string;
  task_execution_stats?: TaskExecutionStats | null;
  latest_manual_switch_count?: number | null;
  settlement_period?: string | null; // 业绩核算期，格式 YYYY-MM，来自企业微信同步
}

// 企业微信实时台账记录（GET /api/ai/wecom/projects 返回，values 的键为企业微信智能表格列名）
interface WecomProjectRecord {
  record_id: string;
  values?: Record<string, unknown>;
}


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
  const { hasPermission } = useAuthStore();
  const canViewAll = hasPermission(PERMISSION_VIEW_ALL);

  const fetchProjects = useCallback(async () => {
    setLoading(true);
    try {
      const url = canViewAll ? '/projects/?include_analysis=true' : '/projects/me?include_analysis=true';
      const data = await request<ProjectItem[]>(url);
      setProjects(normalizeList<ProjectItem>(data));
    } catch (err) {
      Toast({ message: String(err), theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [canViewAll]);

  useEffect(() => { fetchProjects(); }, [fetchProjects]);

  // ── 长按删除项目 ──
  // 长按项目卡片 → 弹出「删除项目」操作卡片；再次点击「删除项目」→ 二次确认对话框 → 真实删除
  const longPressTimer = useRef<number | null>(null);
  // 长按已触发后，抑制紧随其后的 onClick 导航到详情页
  const longPressFired = useRef(false);
  const [longPressProject, setLongPressProject] = useState<ProjectItem | null>(null);
  const [deleteConfirmProject, setDeleteConfirmProject] = useState<ProjectItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  const startLongPress = (p: ProjectItem) => () => {
    longPressFired.current = false;
    longPressTimer.current = window.setTimeout(() => {
      longPressFired.current = true;
      setLongPressProject(p);
    }, 600);
  };
  const cancelLongPress = () => {
    if (longPressTimer.current !== null) { clearTimeout(longPressTimer.current); longPressTimer.current = null; }
  };

  const confirmDelete = async () => {
    if (!deleteConfirmProject || deleting) return;
    setDeleting(true);
    try {
      await request(`/projects/${deleteConfirmProject.id}`, { method: 'DELETE' });
      Toast({ message: '项目已删除', theme: 'success' });
      setDeleteConfirmProject(null);
      setLongPressProject(null);
      await fetchProjects(); // 重新拉取，列表同步移除
    } catch (err) {
      Toast({ message: `删除失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setDeleting(false);
    }
  };

  // 企业微信实时台账的项目经理名单（GET /api/ai/wecom/projects，AI 服务）；
  // 卡片项目经理优先用台账值（按 项目编号 / record_id 匹配），台账不可用时回退本地 project_manager
  const [wecomManagerByCode, setWecomManagerByCode] = useState<Map<string, string>>(new Map());
  const [wecomManagerByRecord, setWecomManagerByRecord] = useState<Map<string, string>>(new Map());

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await aiGet<{ code: number; data?: { records?: WecomProjectRecord[] }; message?: string }>('/wecom/projects');
        if (!alive) return;
        const records = res?.data?.records || [];
        const byCode = new Map<string, string>();
        const byRecord = new Map<string, string>();
        for (const r of records) {
          const manager = String(r.values?.['项目经理'] ?? '').trim();
          if (!manager) continue;
          const code = String(r.values?.['项目编号'] ?? '').trim();
          if (code) byCode.set(code, manager);
          if (r.record_id) byRecord.set(r.record_id, manager);
        }
        setWecomManagerByCode(byCode);
        setWecomManagerByRecord(byRecord);
      } catch {
        // wecom 台账接口不可用时静默降级：沿用本地 project_manager
      }
    })();
    return () => { alive = false; };
  }, []);

  const wecomManagerOf = (p: ProjectItem): string | null => {
    const byCode = wecomManagerByCode.get(String(p.project_code ?? '').trim());
    if (byCode) return byCode;
    if (p.system_id) {
      const byRecord = wecomManagerByRecord.get(String(p.system_id).trim());
      if (byRecord) return byRecord;
    }
    return null;
  };

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
        <ClearableInput
          value={keyword}
          onChange={(v) => setKeyword(String(v))}
          placeholder="搜索项目名称 · 长按删除项目"
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
          const hasRisk = p.risks > 0;
          const wecomManager = wecomManagerOf(p);

          return (
            <div
              key={p.id}
              style={{
                background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10,
                boxShadow: '0 1px 3px rgba(0,0,0,0.06)', cursor: 'pointer',
                borderLeft: hasRisk ? '3px solid #d54941' : '3px solid transparent',
                userSelect: 'none', WebkitUserSelect: 'none', touchAction: 'manipulation',
              }}
              onClick={() => {
                // 长按刚触发时不再进入详情页，避免删除操作被导航打断
                if (longPressFired.current) { longPressFired.current = false; return; }
                navigate(`/admin/project-detail/${p.id}`);
              }}
              onTouchStart={startLongPress(p)}
              onTouchEnd={cancelLongPress}
              onTouchMove={cancelLongPress}
              onMouseDown={startLongPress(p)}
              onMouseUp={cancelLongPress}
              onMouseLeave={cancelLongPress}
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
                {p.project_code} · 项目经理: {wecomManager || p.project_manager || '未指定'}
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

      {/* 长按项目 → 「删除项目」操作卡片 */}
      <Popup visible={!!longPressProject} onClose={() => setLongPressProject(null)} placement="bottom" showOverlay>
        <div style={{ padding: 20 }}>
          <h4 style={{ marginBottom: 16, fontSize: 15, fontWeight: 600 }}>
            {longPressProject?.name}
          </h4>
          <div
            onClick={() => {
              setDeleteConfirmProject(longPressProject);
              setLongPressProject(null);
            }}
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '12px 14px', borderRadius: 8,
              background: '#fef2f2', color: '#d54941',
              fontWeight: 500, fontSize: 14, cursor: 'pointer',
            }}
          >
            <span>🗑</span>
            <span>删除项目</span>
          </div>
          <div style={{ textAlign: 'right', marginTop: 16 }}>
            <span
              onClick={() => setLongPressProject(null)}
              style={{ fontSize: 13, color: '#999', cursor: 'pointer', padding: 4 }}
            >
              取消
            </span>
          </div>
        </div>
      </Popup>

      {/* 二次确认对话框：确认删除项目 */}
      <Dialog
        visible={!!deleteConfirmProject}
        title="确认删除项目"
        confirmBtn={deleting ? '删除中...' : '删除'}
        cancelBtn="取消"
        onConfirm={confirmDelete}
        onCancel={() => setDeleteConfirmProject(null)}
        onClose={() => setDeleteConfirmProject(null)}
      >
        <p style={{ fontSize: 14, lineHeight: 1.7, color: '#333' }}>
          确定要删除项目「<strong>{deleteConfirmProject?.name}</strong>」吗？此操作不可恢复。
        </p>
      </Dialog>
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

function MiniStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div style={{ background: '#f7f8fa', borderRadius: 6, padding: '6px 4px', textAlign: 'center' }}>
      <div style={{ fontSize: 14, fontWeight: 600, color: '#333' }}>{value}</div>
      <div style={{ fontSize: 10, color: '#999', marginTop: 2 }}>{label}</div>
    </div>
  );
}
