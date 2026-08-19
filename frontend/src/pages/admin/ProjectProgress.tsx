// 项目进度管理 —— 聚合项目列表 + 风险状态，侧重视觉化项目进度
// 样式参考 macaron projects.index 页：双指标卡 + 卡片搜索框 + surface-card 项目卡
// （阶段标签 + 进度条 + 四格小指标），保留长按删除与看板筛选下钻。
import { useState, useEffect, useCallback, useRef } from 'react';
import { Toast, Loading, Popup, Dialog } from 'tdesign-mobile-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { aiGet } from '@/api/ai';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { currentYearMonth, normalizeSettlementPeriod } from '@/shared/utils/settlement';
import { calcLifecycleProgress, PROJECT_ABORTED } from '@/shared/utils/projectLifecycle';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';
import { MacStat } from '@/shared/components/macaronBits';
import { MacSearch, MacFolderClosed } from '@/shared/components/macaronIcons';

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
  settlement_period?: string | null; // 业绩核算期，手工填写常见 YYYYMM（如 202608），兼容 YYYY-MM，来自企业微信同步
  deployment_date?: string | null;   // 部署时间
  final_delivery_date?: string | null; // 最终交付时间
}

// 企业微信实时台账记录（GET /api/ai/wecom/projects 返回，values 的键为企业微信智能表格列名）
interface WecomProjectRecord {
  record_id: string;
  values?: Record<string, unknown>;
}


// 与跨项目看板的统计入口对应的筛选类型：
// 项目总数/本月新增项目数/风险项目数/缺少对接人项目数 + 调度项目看板点击某月柱（month）
type ProjectFilter = 'new' | 'risk' | 'no_contact' | 'month';

const FILTER_LABELS: Record<ProjectFilter, string> = {
  new: '本月新增项目数',
  risk: '风险项目数',
  no_contact: '对接人缺省',
  month: '指定核算期项目',
};

export default function ProjectProgress() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const filter = (searchParams.get('filter') as ProjectFilter | null) || null;
  const period = searchParams.get('period');
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
      list = list.filter((p) => normalizeSettlementPeriod(p.settlement_period) === ym);
    } else if (filter === 'month' && period) {
      // 调度项目看板点击某月柱进入：按业绩核算期精确匹配该月（period 为 YYYY-MM）
      list = list.filter((p) => normalizeSettlementPeriod(p.settlement_period) === period);
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
    <div className="mac-page">
      {/* 概览卡片（对照原型：双 surface-card 指标卡） */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12, marginBottom: 12 }}>
        <div className="mac-card" style={{ padding: 16 }}>
          <MacStat value={projects.length} label="项目总数" tone="blue-2" />
        </div>
        <div className="mac-card" style={{ padding: 16 }}>
          <MacStat value={activeCount} label="活跃项目" tone="blue-3" />
        </div>
      </div>

      {/* 项目名称搜索 */}
      <div className="mac-search mac-search--card" style={{ marginBottom: 12 }}>
        <MacSearch size={16} />
        <input
          className="mac-search__input"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="搜索项目名称 · 长按删除项目"
        />
      </div>

      {/* 筛选提示条：从跨项目看板某个统计数字/月份柱点进来时显示，可点击返回全部项目 */}
      {filter && (
        <div className="mac-filter-banner">
          <span>当前筛选：<strong>{filter === 'month' && period ? `${period} 核算期项目` : FILTER_LABELS[filter]}</strong>（{displayProjects.length}）</span>
          <button type="button" className="mac-filter-banner__back" onClick={() => setSearchParams({})}>
            查看全部
          </button>
        </div>
      )}

      {/* 项目列表 */}
      {displayProjects.length === 0 ? (
        <div className="mac-empty" style={{ padding: '40px 0' }}>
          {filter ? '暂无符合条件的项目' : '暂无项目数据'}
        </div>
      ) : (
        displayProjects.map((p) => {
          const hasRisk = p.risks > 0;
          const wecomManager = wecomManagerOf(p);
          const completionRate = p.task_execution_stats?.completion_rate;
          const timeProgress = calcLifecycleProgress(p.status);

          return (
            <div
              key={p.id}
              className="mac-proj-card"
              onClick={() => {
                // 长按刚触发时不再进入详情页，避免删除操作被导航打断
                if (longPressFired.current) { longPressFired.current = false; return; }
                navigate(`/admin/project-detail/${p.id}`);
              }}
              onTouchStart={startLongPress(p)}
              onTouchEnd={cancelLongPress}
              onTouchMove={cancelLongPress}
              onMouseDown={(e) => { if (e.button === 0) startLongPress(p)(); }}
              onMouseUp={cancelLongPress}
              onMouseLeave={cancelLongPress}
            >
              <div className="mac-proj-card__title">{p.name}</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
                <span className="mac-chip mac-chip--tag mac-chip--blue">{p.status}</span>
                <span style={{ fontSize: 11.5, color: 'var(--mac-muted-fg)' }}>
                  {p.project_code} · 项目经理: {wecomManager || p.project_manager || '未指定'}
                </span>
              </div>

              {/* 项目时间进度（对照原型：与项目详情页同一口径 —— 按生命周期阶段线性计算；仅「项目中止」隐藏） */}
              {p.status !== PROJECT_ABORTED && (
                <div className="mac-progress">
                  <div className="mac-progress__head">
                    <span>项目时间进度</span>
                    <span className="mac-progress__pct">{timeProgress}%</span>
                  </div>
                  <div className="mac-progress__track">
                    <div className="mac-progress__fill" style={{ width: `${timeProgress}%` }} />
                  </div>
                </div>
              )}

              {/* 任务统计：任务总数 / 已完成任务 / 任务完成率 / 切手动次数 */}
              <div className="mac-ministat-grid">
                <div className="mac-ministat">
                  <div className="mac-ministat__value">{p.task_execution_stats?.total_tasks ?? '-'}</div>
                  <div className="mac-ministat__label">任务总数</div>
                </div>
                <div className="mac-ministat">
                  <div className="mac-ministat__value">{p.task_execution_stats?.finished_tasks ?? '-'}</div>
                  <div className="mac-ministat__label">已完成任务</div>
                </div>
                <div className="mac-ministat">
                  <div className="mac-ministat__value">
                    {completionRate != null ? `${Math.round(completionRate * 100)}%` : '-'}
                  </div>
                  <div className="mac-ministat__label">任务完成率</div>
                </div>
                <div className="mac-ministat">
                  <div className="mac-ministat__value">{p.latest_manual_switch_count ?? '-'}</div>
                  <div className="mac-ministat__label">切手动次数</div>
                </div>
              </div>

              {/* 风险提示 */}
              {hasRisk && (
                <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid rgba(232,234,234,0.6)' }}>
                  <span style={{ fontSize: 12, color: '#ad4545' }}>⚠ {p.risks} 项未关闭风险</span>
                </div>
              )}
            </div>
          );
        })
      )}

      {/* 快捷入口 */}
      <div style={{ marginTop: 20 }}>
        <button
          type="button"
          className="mac-btn mac-btn--primary mac-btn--block"
          onClick={() => navigate('/admin/project-manage')}
        >
          <MacFolderClosed size={16} />
          全部项目管理
        </button>
      </div>

      {/* 长按项目 → 「删除项目」操作卡片 */}
      <Popup visible={!!longPressProject} onClose={() => setLongPressProject(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title" style={{ fontSize: 15 }}>
            {longPressProject?.name}
          </h4>
          <button
            type="button"
            className="mac-list-item"
            style={{ background: '#fbecec', color: '#ad4545' }}
            onClick={() => {
              setDeleteConfirmProject(longPressProject);
              setLongPressProject(null);
            }}
          >
            <span style={{ fontSize: 14, fontWeight: 500 }}>删除项目</span>
          </button>
          <div style={{ textAlign: 'right', marginTop: 12 }}>
            <button
              type="button"
              className="mac-back-link"
              onClick={() => setLongPressProject(null)}
            >
              取消
            </button>
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
        <p style={{ fontSize: 14, lineHeight: 1.7, color: 'var(--mac-fg)' }}>
          确定要删除项目「<strong>{deleteConfirmProject?.name}</strong>」吗？此操作不可恢复。
        </p>
      </Dialog>
    </div>
  );
}
