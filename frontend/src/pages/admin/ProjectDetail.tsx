// 项目详情 —— 展示/编辑 GET|PUT /api/admin/projects/{id} 的真实项目数据
// 该项目数据经 backend/app/integrations/sources/wecom/adapter.py 从企业微信项目表同步而来，
// 落库为 backend/app/models/delivery.py 的 Project；本页每一项都直接对应 Project 的一个真实列，
// 不再使用 field_links 承载编造的扩展字段。system_id 即企业微信原始记录 record_id，用于溯源。
import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Navbar, Loading, Toast, Popup, BackTop } from 'tdesign-mobile-react';
import { Input, Textarea } from 'tdesign-mobile-react';
import { createRequest, ApiError, clearCache } from '@/api/client';
import API_CONFIG from '@/config/api';
import { STATUS_OPTIONS, PROJECT_ABORTED, calcLifecycleProgress } from '@/shared/utils/projectLifecycle';
import {
  MacCheck, MacChevronRight, MacChevronDown, MacChevronUp, MacPencil, MacRefreshCw,
  MacSparkles, MacBarChart3,
} from '@/shared/components/macaronIcons';
import ProjectInfoCard from './ProjectInfoCard';
import ProjectTicketsCard from './ProjectTicketsCard';
import ProjectActivityCard from './ProjectActivityCard';

interface ProjectDocument {
  name: string;
  resource_id: number;
}

interface ProjectDetailData {
  id: string;
  project_code: string;
  system_id?: string | null;
  name: string;
  description?: string | null;
  contact_person?: string | null;
  status: string;
  expected_trend?: string | null;
  issues: number;
  risks: number;
  personnel_plan?: string | null;
  risk_list?: string | null;
  deployment_date?: string | null;
  deployment_version?: string | null;
  recent_delivery_date?: string | null;
  recent_delivery_content?: string | null;
  final_delivery_date?: string | null;
  project_summary?: string | null;
  task_execution_status?: string | null;
  field_links?: Record<string, string> | null;
  category_basis: string;
  project_type?: string | null;
  stage_notes?: Record<string, string> | null;
  risk_carrying_type?: string | null;
  special_attention?: string | null;
  risk_task_description?: string | null;
  management_strategy?: string | null;
  project_documents?: ProjectDocument[] | null;
  sales?: string | null;
  pre_sales?: string | null;
  project_manager?: string | null;
  project_contact?: string | null;
  field_engineer?: string | null;
  internal_code?: string | null;
  project_region?: string | null;
  total_vehicle_count?: number | null;
  controller_vendor?: string | null;
  system_integration?: string[] | null;
  server_deployment_status?: string | null;
  settlement_period?: string | null;
  ext_info?: { overview?: Record<string, unknown>; activity?: Record<string, unknown> } | null;
}

// 项目阶段枚举与「项目时间进度」计算见 shared/utils/projectLifecycle.ts（与项目进度列表共用同一口径）

// 与 backend ProjectCategory 枚举严格一致
const CATEGORY_OPTIONS = ['重要紧急', '紧急不重要', '重要不紧急', '不紧急不重要'];

// 与 backend ProjectStatus 枚举严格一致（企业微信「项目生命周期」列实时同步）
type PickerKey = 'status' | 'category_basis';

const PICKERS: { key: PickerKey; label: string; options: string[] }[] = [
  { key: 'status', label: '项目阶段', options: STATUS_OPTIONS },
  { key: 'category_basis', label: '项目类别', options: CATEGORY_OPTIONS },
];

// USP项目「新建」入口复用本页作为空白详情页：路由参数 id === 'new' 时不请求已有项目，
// 而是以该空白对象作为起点，各字段编辑仅在本地暂存，直到点击右上角「创建」才 POST /projects/
const BLANK_PROJECT: ProjectDetailData = {
  id: '',
  project_code: '',
  name: '',
  status: '售前方案',
  category_basis: '重要紧急',
  issues: 0,
  risks: 0,
};

export default function ProjectDetail() {
  const { id = '' } = useParams<{ id: string }>();
  const isNew = id === 'new';
  const navigate = useNavigate();
  const [project, setProject] = useState<ProjectDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [activePicker, setActivePicker] = useState<PickerKey | null>(null);
  // 项目概况里后端 Project 表暂无对应列的三项：客户信息 / AGV 数量 / USP 版本。
  // 按要求先置空、保留就地编辑能力；编辑结果暂存在本页 state，待后端补列后再改为 saveField。
  const [overviewDraft, setOverviewDraft] = useState({ client: '', agvCount: '', uspVersion: '' });
  const [summaryExpanded, setSummaryExpanded] = useState(false);
  // AI 项目摘要：存 ext_info.overview.ai_summary，由 POST /projects/{id}/ai-summary 生成
  // （后端读取「项目信息管理」整棵信息树 + 项目基础字段，与文件导入同一大模型），
  // 刷新页面后随项目详情接口读回，不需要单独的加载逻辑。
  const [aiGenerating, setAiGenerating] = useState(false);
  const aiSummary = String((project?.ext_info?.overview?.ai_summary as string | undefined) || '');
  // 「项目动态」卡刷新令牌：信息卡里星标关注/取消关注后翻动，动态卡据此重新拉取
  const [activityToken, setActivityToken] = useState(0);
  const [lastSyncTime, setLastSyncTime] = useState<string | null>(null);
  const [syncFailed, setSyncFailed] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');
  // tasks 路由独立于 admin 路由，单独建一个 request 实例供 /sources/wecom/projects/sync 调用
  const tasksRequest = createRequest(API_CONFIG.TASKS.BASE_URL, 'Tasks');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request<ProjectDetailData>(`/projects/${id}?include_risks=true`);
      setProject(data);
    } catch (err) {
      Toast({ message: `加载项目详情失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally { setLoading(false); }
  }, [id]);

  useEffect(() => {
    if (isNew) {
      setProject({ ...BLANK_PROJECT });
      setLoading(false);
      return;
    }
    load();
  }, [isNew, load]);

  // 触发后端企业微信项目同步（POST /api/tasks/sources/wecom/projects/sync），
  // 后端拉取企微 Smartsheet → 字段映射 → upsert project 表；完成后重载本页详情。
  // 与 dags/wecom_projects_sync_dag.py 共用同一入口，DB 是单一数据源，前端不再做合并。
  // 5 分钟自动轮询已迁移到 Airflow DAG，本函数仅由「立即同步」按钮触发。
  const triggerSync = useCallback(async () => {
    if (syncing) return;
    setSyncing(true);
    try {
      await tasksRequest('/sources/wecom/projects/sync', { method: 'POST' });
      // 请求层有 5 分钟 GET 缓存，sync 后必须清掉，load() 才能拿到 DB 最新值
      clearCache();
      await load();
      setSyncFailed(false);
      setLastSyncTime(new Date().toLocaleTimeString());
    } catch (err) {
      setSyncFailed(true);
      Toast({ message: `同步失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSyncing(false);
    }
  }, [syncing, load]);

  // 保存单个 Project 字段（真实列），仅回写发生变化的那一个字段；
  // 新建模式下项目尚未落库，仅更新本地草稿，待「创建」时一并提交
  const saveField = async (key: keyof ProjectDetailData, value: unknown) => {
    if (isNew) {
      setProject((prev) => (prev ? { ...prev, [key]: value } : prev));
      return;
    }
    try {
      await request(`/projects/${id}`, { method: 'PUT', body: JSON.stringify({ [key]: value }) });
      setProject((prev) => (prev ? { ...prev, [key]: value } : prev));
      Toast({ message: '已保存', theme: 'success' });
    } catch (err) {
      // 唯一键冲突（项目编号/项目名称已被其他项目占用）返回 409，直接提示原始信息
      if (err instanceof ApiError && err.statusCode === 409) {
        Toast({ message: err.message, theme: 'warning' });
      } else {
        Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      }
    }
  };

  // 项目概况中暂无后端列的三项（客户信息 / AGV 数量 / USP 版本）：控件可本地编辑，
  // 但明确提示尚未保存到后端（待接入），不伪装成已落库。
  const saveOverviewDraft = (key: 'client' | 'agvCount' | 'uspVersion', label: string) => (value: string) => {
    setOverviewDraft((draft) => ({ ...draft, [key]: value }));
    Toast({ message: `「${label}」暂存在本页，后端字段接入后才会保存`, theme: 'warning' });
  };

  // 生成 AI 项目摘要：后端汇总项目基础字段与信息树全文调大模型，写回
  // ext_info.overview.ai_summary 并随响应返回（大模型耗时较长，超时放宽到 180s）
  const generateAiSummary = async () => {
    if (aiGenerating || isNew) return;
    setAiGenerating(true);
    try {
      const res = await request<{ summary: string; ext_info?: ProjectDetailData['ext_info'] }>(
        `/projects/${id}/ai-summary`,
        { method: 'POST', timeout: 180000 },
      );
      setProject((prev) => (prev ? { ...prev, ext_info: res.ext_info ?? prev.ext_info } : prev));
      setSummaryExpanded(true);
      Toast({ message: '摘要已生成并保存', theme: 'success' });
    } catch (err) {
      Toast({ message: `生成失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setAiGenerating(false);
    }
  };

  const handleCreate = async () => {
    if (!project) return;
    // 必填字段：项目名称 / 项目编号 / 项目状态
    if (!project.name.trim()) {
      Toast({ message: '请填写项目名称', theme: 'warning' });
      return;
    }
    if (!project.project_code.trim()) {
      Toast({ message: '请填写项目编号', theme: 'warning' });
      return;
    }
    if (!project.status.trim()) {
      Toast({ message: '请选择项目状态', theme: 'warning' });
      return;
    }
    setCreating(true);
    try {
      const { id: _draftId, ...payload } = project;
      const created = await request<ProjectDetailData>('/projects/', { method: 'POST', body: JSON.stringify(payload) });
      Toast({ message: '创建成功', theme: 'success' });
      clearCache(); // 清除请求缓存，确保返回项目管理页时能拉取到最新数据
      navigate(`/admin/project-detail/${created.id}`, { replace: true });
    } catch (err) {
      // 后端唯一键校验（项目编号/项目名称已存在）返回 409，直接提示用户重新输入
      if (err instanceof ApiError && err.statusCode === 409) {
        Toast({ message: err.message, theme: 'warning' });
      } else {
        Toast({ message: `创建失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      }
    } finally {
      setCreating(false);
    }
  };


  const handlePickSingle = (key: PickerKey, value: string) => {
    setActivePicker(null);
    saveField(key, value);
  };

  if (loading) return <Loading text="加载项目详情..." />;
  if (!project) return <div style={{ padding: 40, textAlign: 'center', color: '#999' }}>{isNew ? '初始化失败' : '项目不存在'}</div>;

  const progressPct = calcLifecycleProgress(project.status);
  const isAborted = project.status === PROJECT_ABORTED;
  const activePickerConfig = PICKERS.find((p) => p.key === activePicker);
  const activeValue = activePickerConfig ? String(project[activePickerConfig.key] ?? '') : '';

  return (
    <div>
      <Navbar
        title={isNew ? '新建USP项目' : '项目详情'}
        leftArrow
        onLeftClick={() => navigate(-1)}
        right={isNew ? (
          <span onClick={creating ? undefined : handleCreate} style={{ fontSize: 14, fontWeight: 500, color: creating ? '#999' : '#0052d9' }}>
            {creating ? '创建中...' : '创建'}
          </span>
        ) : undefined}
        fixed
      />
      <div style={{ padding: 16, paddingTop: 64 }}>
        {/* 企业微信同步状态条：5min 自动轮询已迁移至 Airflow DAG，本页仅保留手动「立即同步」 */}
        {!isNew && (
          <div className="mac-sync-banner">
            <span className="mac-sync-banner__icon"><MacRefreshCw size={14} /></span>
            <span className="mac-sync-banner__title">企业微信数据</span>
            <span className={syncFailed ? 'mac-sync-banner__time is-error' : 'mac-sync-banner__time'}>
              {syncFailed ? '同步失败，点击重试' : lastSyncTime ? `上次同步 ${lastSyncTime}` : '点击右侧按钮立即同步'}
            </span>
            <button
              type="button"
              className="mac-sync-banner__sync"
              onClick={triggerSync}
              disabled={syncing}
            >
              {syncing ? '同步中...' : '立即同步'}
            </button>
          </div>
        )}
        {/* 项目概况（对照原型 ProjectOverviewCard：标题 / 名称·编号·企微记录ID / 客户信息 / 项目经理·对接人
            / 时间进度 / 三个交付日期 / AGV·USP + 搬运效率分析 / AI 项目摘要） */}
        <section className="mac-card mac-card--pad" style={{ marginBottom: 12 }}>
          <h3 className="mac-card-title">项目概况</h3>
          <div className="mac-summary-head">
            <div className="mac-summary-head__main">
              <EditableField label="项目名称" value={project.name || '未命名项目'} placeholder="未命名项目" onSave={(v) => saveField('name', v)} title required={isNew} />
              <div className="mac-summary-head__code">
                <EditableField label="项目编号" value={project.project_code || '未填写'} placeholder="未填写" onSave={(v) => saveField('project_code', v)} inlineLabel="项目编号" required={isNew} meta plain />
                {project.system_id ? ` · 企业微信记录ID: ${project.system_id}` : ''}
              </div>
            </div>
            <div className="mac-summary-head__side">
              <span
                className="mac-chip mac-chip--tag"
                style={{ background: 'var(--mac-black)', color: '#fff' }}
                onClick={() => setActivePicker('category_basis')}
              >
                {project.category_basis || '未分类'} ›
              </span>
            </div>
          </div>

          {/* 客户信息（Project 表暂无对应列：先置空，可就地编辑）+ 项目经理 / 对接人（企业微信「项目经理」「调度对接人」列实时同步） */}
          <div className="mac-ov-block">
            <EditableField
              variant="row"
              pending
              label="客户信息"
              value={overviewDraft.client}
              placeholder="未填写"
              onSave={saveOverviewDraft('client', '客户信息')}
            />
            <div className="mac-ov-grid">
              <EditableField variant="cell" label="项目经理" value={project.project_manager || ''} placeholder="未指定" onSave={(v) => saveField('project_manager', v)} />
              <EditableField variant="cell" label="对接人" value={project.contact_person || ''} placeholder="未指定" onSave={(v) => saveField('contact_person', v)} />
            </div>
          </div>

          {/* 项目阶段 + 项目时间进度：同属进度管控，整体与上方客户信息块用浅灰线隔开。
              阶段编辑入口随「项目生命周期」卡移除后挪进概况（进度由它决定），
              绑定 status（企业微信「项目生命周期」列实时同步）；
              进度与项目进度列表同一口径（按生命周期阶段线性计算；仅「项目中止」隐藏） */}
          <div className="mac-ov-divider">
            <PickerField label="项目阶段" value={project.status || '未设置'} onClick={() => setActivePicker('status')} required={isNew} />
            {!isAborted && (
              <div className="mac-progress mac-progress--detail" style={{ marginTop: 14 }}>
                <div className="mac-progress__head">
                  <span>项目时间进度</span>
                  <span className="mac-progress__pct">{progressPct}%</span>
                </div>
                <div className="mac-progress__track">
                  <div className="mac-progress__fill" style={{ width: `${progressPct}%` }} />
                </div>
              </div>
            )}
          </div>

          {/* 部署 / 近期交付 / 最终交付：与上方项目阶段、时间进度同属时间信息一个功能块，
              不再单独加分隔线（mac-dates 自带 margin-top 与进度条留白） */}
          <div className="mac-dates">
            <div>
              <div className="mac-dates__label">部署时间</div>
              <div className="mac-dates__value">{project.deployment_date || '-'}</div>
            </div>
            <div>
              <div className="mac-dates__label">近期交付</div>
              <div className="mac-dates__value">{project.recent_delivery_date || '-'}</div>
            </div>
            <div>
              <div className="mac-dates__label">最终交付</div>
              <div className="mac-dates__value">{project.final_delivery_date || '-'}</div>
            </div>
          </div>

          {/* AGV 数量 / USP 版本（Project 表暂无对应列：先置空，可就地编辑）+ 搬运效率分析入口（对照原型：从右上标签移到底部按钮） */}
          <div className="mac-ov-actions">
            <EditableField
              variant="cell"
              pending
              label="AGV 数量"
              value={overviewDraft.agvCount}
              placeholder="未填写"
              onSave={saveOverviewDraft('agvCount', 'AGV 数量')}
            />
            <EditableField
              variant="cell"
              pending
              label="USP 版本"
              value={overviewDraft.uspVersion}
              placeholder="未填写"
              onSave={saveOverviewDraft('uspVersion', 'USP 版本')}
            />
            <button
              type="button"
              className="mac-btn mac-btn--primary mac-ov-cta"
              onClick={() => navigate(`/admin/project-detail/${id}/transport-efficiency`)}
            >
              <MacBarChart3 size={14} />搬运效率分析
            </button>
          </div>

          {project.task_execution_status && (
            <div className="mac-task-exec">近7天任务执行：{project.task_execution_status}</div>
          )}

          {/* AI 项目摘要：POST /projects/{id}/ai-summary 生成（后端读取项目信息管理整棵树 +
              项目基础字段，与文件导入同一大模型），结果存 ext_info.overview.ai_summary */}
          <div className="mac-ai">
            <div className="mac-ai__head">
              <span className="mac-ai__icon"><MacSparkles size={14} /></span>
              <span className="mac-ai__title">项目摘要</span>
              <div className="mac-ai__head-right">
                {!isNew && (
                  <button type="button" className="mac-ai__gen" disabled={aiGenerating} onClick={generateAiSummary}>
                    {aiGenerating ? '生成中...' : aiSummary ? '重新生成' : '点击生成'}
                  </button>
                )}
                <button type="button" className="mac-ai__toggle" onClick={() => setSummaryExpanded((value) => !value)}>
                  {summaryExpanded ? '收起' : '展开'}
                  {summaryExpanded ? <MacChevronUp size={13} /> : <MacChevronDown size={13} />}
                </button>
              </div>
            </div>
            {/* 摘要为大模型输出的结构化 Markdown（## 小节 + - 要点），用 react-markdown 渲染为 React 元素（天然防 XSS） */}
            <div className={`mac-ai__body${aiSummary ? (summaryExpanded ? '' : ' is-collapsed') : ' is-empty'}`}>
              {aiSummary ? (
                <div className="mac-ai__md">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{aiSummary}</ReactMarkdown>
                </div>
              ) : '暂无数据'}
            </div>
          </div>
        </section>

        {/* 项目信息管理（项目详细信息）：对照原型 ProjectDetailCard，位于项目概况之后（与原型顺序一致）。
            子节点右侧星标 = 关注该节点；关注变化后翻动 activityToken 让下方「项目动态」重新拉取 */}
        <ProjectInfoCard
          projectId={id}
          canEdit={!isNew}
          onMarkChange={() => setActivityToken((value) => value + 1)}
        />

        {/* 项目工单（对照原型 ProjectTicketsCard）：总数/各状态数量 + 核心阻滞工单 +
            近 8 周变化趋势；新建模式下项目未落库（也没有工单），不渲染 */}
        {!isNew && <ProjectTicketsCard projectId={id} />}

        {/* 项目动态（对照原型 ProjectActivityCard 的「关注节点变动」分组）：
            被关注节点的最新一条变动，只展示变动内容（不带时间与人员）；新建模式下项目未落库，不渲染 */}
        {!isNew && <ProjectActivityCard projectId={id} reloadToken={activityToken} />}
      </div>

      {/* 项目阶段 / 项目类别 —— 单选弹窗（真实枚举，与 backend 对应 Enum 一致） */}
      <Popup visible={!!activePicker} onClose={() => setActivePicker(null)} placement="bottom" showOverlay>
        <div className="mac-sheet" style={{ maxHeight: '70vh', overflow: 'auto' }}>
          <h4 className="mac-sheet__title">{activePickerConfig?.label}</h4>
          {activePickerConfig?.options.map((opt) => (
            <button
              key={opt}
              type="button"
              className={`mac-choice${opt === activeValue ? ' is-active' : ''}`}
              onClick={() => handlePickSingle(activePickerConfig.key, opt)}
            >
              <span className="mac-choice__dot">{opt === activeValue ? <MacCheck size={12} /> : null}</span>
              <span className="mac-choice__label">{opt}</span>
            </button>
          ))}
        </div>
      </Popup>

      {/* 一键回到顶部：滚动超过 200px 时出现在右下角（页面滚动容器是 MainLayout 的 .tabbar-shell__content）。
          底部 TabBar 高约 56px + 安全区，按钮上移到 TabBar 之上。 */}
      <BackTop
        container={() => document.querySelector('.tabbar-shell__content') as HTMLElement}
        visibilityHeight={200}
        theme="round"
        style={{ bottom: 'calc(56px + env(safe-area-inset-bottom) + 12px)' }}
      />
    </div>
  );
}

function PickerField({ label, value, onClick, required }: { label: string; value: string; onClick: () => void; required?: boolean }) {
  return (
    <div className="mac-field-stack">
      <div className="mac-field-stack__label">{label}{required && <span style={{ color: '#ad4545', marginLeft: 2 }}>*</span>}</div>
      <button type="button" className="mac-field-stack__row" onClick={onClick}>
        <span className={`mac-field-stack__value${isFieldEmpty(value) ? ' is-empty' : ''}`}>{value}</span>
        <span className="mac-field-stack__icon"><MacChevronRight size={16} /></span>
      </button>
    </div>
  );
}

const FIELD_EMPTY_VALUES = ['未设置', '未填写', '未指定', '无', ''];
function isFieldEmpty(v: string): boolean { return FIELD_EMPTY_VALUES.includes(v); }

function EditableField({ label, value, placeholder, multiline, type, title, meta, plain, strong, inlineLabel, required, variant, pending, onSave }: {
  label: string;
  value: string;
  placeholder?: string;
  multiline?: boolean;
  type?: 'text' | 'number';
  title?: boolean;
  meta?: boolean;
  plain?: boolean;
  strong?: boolean;
  inlineLabel?: string;
  required?: boolean;
  // 项目概况卡专用排布：row = 左标签右值（客户信息）；cell = 标签在上、值在下（项目经理/对接人/AGV/USP）
  variant?: 'row' | 'cell';
  // 后端暂无对应列/接口的字段：标签旁标注「待接入」，编辑仅本地暂存
  pending?: boolean;
  onSave: (v: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);

  useEffect(() => { setDraft(value); }, [value]);

  const commit = () => {
    setEditing(false);
    if (draft !== value) onSave(draft);
  };

  const requiredMark = <span style={{ color: '#ad4545', marginLeft: 2 }}>*</span>;
  const InputField = multiline ? Textarea : Input;
  const fieldProps = { ...(!multiline && type ? { type } : {}) };

  // 项目概况·客户信息行（对照原型：左标签 / 右值 + 铅笔，行下细分隔线）
  if (variant === 'row') {
    return (
      <div className="mac-ov-row">
        <span className="mac-ov-row__label">{label}{required && requiredMark}{pending && <span className="mac-ov-pending">待接入</span>}</span>
        {editing ? (
          <div style={{ flex: 1, minWidth: 0 }}>
            <InputField value={draft} onChange={(v: string | number) => setDraft(String(v))} onBlur={commit} autofocus placeholder={placeholder} {...fieldProps} />
          </div>
        ) : (
          <button type="button" className={`mac-ov-row__value${value ? '' : ' is-empty'}`} onClick={() => setEditing(true)}>
            <span>{value || placeholder || '未填写'}</span>
            <span className="mac-ov-pencil"><MacPencil size={13} /></span>
          </button>
        )}
      </div>
    );
  }

  // 项目概况·字段格（对照原型：标签在上 / 值 + 铅笔，无灰底）
  if (variant === 'cell') {
    return (
      <div className="mac-ov-cell">
        <div className="mac-ov-cell__label">
          <span>{label}{required && requiredMark}</span>
          {pending && <span className="mac-ov-pending">待接入</span>}
        </div>
        {editing ? (
          <InputField value={draft} onChange={(v: string | number) => setDraft(String(v))} onBlur={commit} autofocus placeholder={placeholder} {...fieldProps} />
        ) : (
          <button type="button" className={`mac-ov-cell__value${value ? '' : ' is-empty'}`} onClick={() => setEditing(true)}>
            <span>{value || placeholder || '未填写'}</span>
            <span className="mac-ov-pencil"><MacPencil size={13} /></span>
          </button>
        )}
      </div>
    );
  }

  // 概要卡 MetaRow（项目经理 / 对接人 / 内嵌项目编号）
  if (meta) {
    return (
      <div className={`mac-meta-row${plain ? ' mac-meta-row--plain' : ''}`}>
        <span className="mac-meta-row__label">{inlineLabel || label}{required && requiredMark}</span>
        {editing ? (
          <div style={{ flex: 1, minWidth: 0, marginLeft: 8 }}>
            <InputField value={draft} onChange={(v: string | number) => setDraft(String(v))} onBlur={commit} autofocus placeholder={placeholder} {...fieldProps} />
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }} onClick={() => setEditing(true)}>
            <span className={`mac-meta-row__value${strong ? ' is-strong' : ''}${value ? '' : ' is-empty'}`}>
              {value || placeholder || '未指定'}
            </span>
            <span className="mac-meta-row__pencil"><MacPencil size={13} /></span>
          </div>
        )}
      </div>
    );
  }

  // 概要卡标题（项目名称）
  if (title) {
    return (
      <div>
        <div className="mac-summary-head__label">{label}{required && requiredMark}</div>
        {editing ? (
          <InputField value={draft} onChange={(v: string | number) => setDraft(String(v))} onBlur={commit} autofocus placeholder={placeholder} {...fieldProps} />
        ) : (
          <div className="mac-summary-head__name" style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }} onClick={() => setEditing(true)}>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{value || placeholder}</span>
            <span style={{ color: 'var(--mac-muted-fg)', flexShrink: 0, display: 'inline-flex' }}><MacPencil size={13} /></span>
          </div>
        )}
      </div>
    );
  }

  // 常规 FieldRow（对照原型 FieldRow：标签在上 + 可点行）
  return (
    <div className="mac-field-stack">
      <div className="mac-field-stack__label">{label}{required && requiredMark}</div>
      {editing ? (
        <InputField value={draft} onChange={(v: string | number) => setDraft(String(v))} onBlur={commit} autofocus placeholder={placeholder} {...fieldProps} />
      ) : (
        <button type="button" className="mac-field-stack__row" onClick={() => setEditing(true)}>
          <span className={`mac-field-stack__value${value ? '' : ' is-empty'}`}>
            {inlineLabel && <span style={{ fontSize: 11.5, color: 'var(--mac-muted-fg)' }}>{inlineLabel}{required && requiredMark}： </span>}
            {value || placeholder}
          </span>
          <span className="mac-field-stack__icon"><MacPencil size={14} /></span>
        </button>
      )}
    </div>
  );
}
