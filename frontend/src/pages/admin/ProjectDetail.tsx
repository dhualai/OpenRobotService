// 项目详情 —— 展示/编辑 GET|PUT /api/admin/projects/{id} 的真实项目数据
// 该项目数据经 backend/app/integrations/sources/wecom/adapter.py 从企业微信项目表同步而来，
// 落库为 backend/app/models/delivery.py 的 Project；本页每一项都直接对应 Project 的一个真实列，
// 不再使用 field_links 承载编造的扩展字段。system_id 即企业微信原始记录 record_id，用于溯源。
import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Loading, Toast, Popup, Upload, Checkbox } from 'tdesign-mobile-react';
import { Input, Textarea } from 'tdesign-mobile-react';
import { createRequest, ApiError, clearCache } from '@/api/client';
import API_CONFIG from '@/config/api';
import { useAuthStore } from '@/stores/auth';
import { STATUS_OPTIONS, LIFECYCLE_STATUSES, PROJECT_ABORTED, calcLifecycleProgress } from '@/shared/utils/projectLifecycle';
import { MacCheck, MacChevronRight, MacFileText, MacPencil, MacPlus, MacRefreshCw } from '@/shared/components/macaronIcons';

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
}

// 项目阶段枚举与「项目时间进度」计算见 shared/utils/projectLifecycle.ts（与项目进度列表共用同一口径）

// 与 backend ProjectCategory 枚举严格一致
const CATEGORY_OPTIONS = ['重要紧急', '紧急不重要', '重要不紧急', '不紧急不重要'];

// 与 backend ProjectType 枚举严格一致（企业微信项目类型表头原值）
const PROJECT_TYPE_OPTIONS = [
  '受关注项目', '大客户项目', '展会/演示项目', '展厅项目', 'PK项目',
  '试点项目', '试用项目', '内部/测试项目', '普通项目', '增补项目',
];

// 与 backend RiskCarryingType 枚举严格一致
const RISK_CARRYING_OPTIONS = [
  '数据同步错误', '公司评审不通过', '缺前置承接', '高风险承接',
  '中风险承接', '低风险承接', '方案变动不承接', '调度主动不承接',
];

// 与 backend ProjectRegion 枚举严格一致
const PROJECT_REGION_OPTIONS = [
  '大陆(China Mainland)', '亚洲(Asia)', '欧洲(Europe)', '北美(North America)',
  '南美(South America)', '大洋洲(Oceania)', '港澳台',
];

// 与 backend ControllerVendor 枚举严格一致
const CONTROLLER_VENDOR_OPTIONS = [
  '自研', '睿芯行', '利科钛', '海康', '华睿', '中兴', '科聪', '有光', '特定',
];

// 与 backend ServerDeploymentStatus 枚举严格一致
const SERVER_DEPLOYMENT_OPTIONS = [
  '已布-中力服务器', '在布-中力服务器', '待布-中力服务器', '已布-客户服务器',
  '待布-客户服务器', '已布-云服务器', '待布-云服务器', '已布', '待布',
];

// 与 backend SystemIntegrationType 枚举严格一致（多选）
const SYSTEM_INTEGRATION_OPTIONS = [
  'DAS', '客户WMS', '客户MES/ERP', '客户系统', '数字孪生', 'PDA', '平板', '电梯',
  '输送线/辊筒线', '自动门', '红绿灯', '呼叫器', '机械臂', '其他外设', '其他',
  '码垛机/叠盘机', '缠膜机',
];

// 字段映射与 CATEGORY 转换已统一在后端 adapter.py 中实现，前端只展示 DB 数据，不再做合并。

type PickerKey = 'status' | 'category_basis' | 'project_type' | 'risk_carrying_type' | 'project_region' | 'controller_vendor' | 'server_deployment_status';

const PICKERS: { key: PickerKey; label: string; options: string[] }[] = [
  { key: 'status', label: '项目阶段', options: STATUS_OPTIONS },
  { key: 'category_basis', label: '项目类别', options: CATEGORY_OPTIONS },
  { key: 'project_type', label: '项目类型', options: PROJECT_TYPE_OPTIONS },
  { key: 'risk_carrying_type', label: '风险承接', options: RISK_CARRYING_OPTIONS },
  { key: 'project_region', label: '项目区域/地点', options: PROJECT_REGION_OPTIONS },
  { key: 'controller_vendor', label: '控制器选择', options: CONTROLLER_VENDOR_OPTIONS },
  { key: 'server_deployment_status', label: '服务器部署', options: SERVER_DEPLOYMENT_OPTIONS },
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
  const username = useAuthStore((s) => s.username);
  const [project, setProject] = useState<ProjectDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [activePicker, setActivePicker] = useState<PickerKey | null>(null);
  const [noteStage, setNoteStage] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState('');
  const [systemIntegrationOpen, setSystemIntegrationOpen] = useState(false);
  const [systemIntegrationDraft, setSystemIntegrationDraft] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
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

  const openNoteEditor = (stage: string) => {
    setNoteDraft(project?.stage_notes?.[stage] || '');
    setNoteStage(stage);
  };

  const openSystemIntegrationEditor = () => {
    setSystemIntegrationDraft(project?.system_integration || []);
    setSystemIntegrationOpen(true);
  };

  const toggleSystemIntegration = (opt: string) => {
    setSystemIntegrationDraft((prev) => (prev.includes(opt) ? prev.filter((v) => v !== opt) : [...prev, opt]));
  };

  const saveSystemIntegration = () => {
    setSystemIntegrationOpen(false);
    saveField('system_integration', systemIntegrationDraft);
  };

  const saveNote = () => {
    if (!noteStage || !project) return;
    const merged = { ...(project.stage_notes || {}), [noteStage]: noteDraft };
    setNoteStage(null);
    saveField('stage_notes', merged);
  };

  const handleUploadDocument = async (file: File) => {
    if (!project) return;
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('owner_id', username || 'admin');
      formData.append('resource_type', 'document');
      formData.append('category', '项目文档');
      formData.append('description', `项目 ${project.project_code} 文档`);
      const resource = await request<{ id: number; resource_name: string }>('/resource-manager/resources/', {
        method: 'POST',
        body: formData,
      });
      const docs = [...(project.project_documents || []), { name: resource.resource_name, resource_id: resource.id }];
      await saveField('project_documents', docs);
    } catch (err) {
      Toast({ message: `上传失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setUploading(false);
    }
  };

  const removeDocument = (resourceId: number) => {
    if (!project) return;
    const docs = (project.project_documents || []).filter((d) => d.resource_id !== resourceId);
    saveField('project_documents', docs);
  };

  if (loading) return <Loading text="加载项目详情..." />;
  if (!project) return <div style={{ padding: 40, textAlign: 'center', color: '#999' }}>{isNew ? '初始化失败' : '项目不存在'}</div>;

  const lifecycleIndex = LIFECYCLE_STATUSES.indexOf(project.status);
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
        {/* 概要卡片（对照原型 overview SectionCard：名称/编号 + 右上标签 + MetaRow + 进度 + 三列日期） */}
        <section className="mac-card mac-card--pad" style={{ marginBottom: 12 }}>
          <div className="mac-summary-head">
            <div className="mac-summary-head__main">
              <EditableField label="项目名称" value={project.name || '未命名项目'} placeholder="未命名项目" onSave={(v) => saveField('name', v)} title required={isNew} />
              <div className="mac-summary-head__code">
                <EditableField label="项目编号" value={project.project_code || '未填写'} placeholder="未填写" onSave={(v) => saveField('project_code', v)} inlineLabel="项目编号" required={isNew} meta plain />
                {project.system_id ? ` · 企业微信记录ID: ${project.system_id}` : ''}
              </div>
            </div>
            <div className="mac-summary-head__side">
              <span className="mac-chip mac-chip--tag" style={{ background: 'var(--mac-blue-2)', color: '#fff' }} onClick={() => navigate(`/admin/project-detail/${id}/transport-efficiency`)}>
                搬运效率分析 ›
              </span>
              <span
                className="mac-chip mac-chip--tag"
                style={{ background: 'var(--mac-black)', color: '#fff' }}
                onClick={() => setActivePicker('category_basis')}
              >
                {project.category_basis || '未分类'} ›
              </span>
            </div>
          </div>

          {/* 项目经理 / 对接人 —— MetaRow 可编辑；绑定 project_manager / contact_person（企业微信「项目经理」「调度对接人」列实时同步） */}
          <div style={{ marginTop: 10 }}>
            <EditableField label="项目经理" value={project.project_manager || ''} placeholder="未指定" onSave={(v) => saveField('project_manager', v)} meta />
          </div>
          <EditableField label="对接人" value={project.contact_person || ''} placeholder="未指定" onSave={(v) => saveField('contact_person', v)} meta strong />

          {/* 项目时间进度（与项目进度列表同一口径：按生命周期阶段线性计算；仅「项目中止」隐藏） */}
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

          {/* 部署 / 近期交付 / 最终交付 */}
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

          {project.task_execution_status && (
            <div className="mac-task-exec">近7天任务执行：{project.task_execution_status}</div>
          )}
        </section>

        {/* 项目基础画像（对照原型 SectionCard + FieldRow） */}
        <section className="mac-card mac-card--pad" style={{ marginBottom: 12 }}>
          <h3 className="mac-card-title">项目基础画像</h3>
          <EditableField label="项目名称" value={project.name} onSave={(v) => saveField('name', v)} required={isNew} />
          <EditableField label="项目编号" value={project.project_code} onSave={(v) => saveField('project_code', v)} required={isNew} />
          <EditableField label="内部编号" value={project.internal_code || ''} placeholder="未填写" onSave={(v) => saveField('internal_code', v)} />
          <EditableField label="搬运数据自动同步编号" value={project.system_id || ''} placeholder="未填写" onSave={(v) => saveField('system_id', v)} />
          <EditableField label="项目描述" value={project.description || ''} placeholder="未填写" multiline onSave={(v) => saveField('description', v)} />
          <PickerField label="项目类型" value={project.project_type || '未设置'} onClick={() => setActivePicker('project_type')} />
          <PickerField label="项目区域/地点" value={project.project_region || '未设置'} onClick={() => setActivePicker('project_region')} />
          <EditableField
            label="总车数"
            type="number"
            value={project.total_vehicle_count != null ? String(project.total_vehicle_count) : ''}
            placeholder="未填写"
            onSave={(v) => saveField('total_vehicle_count', v ? Number(v) : null)}
          />
          <EditableField label="车型&车数" value={project.recent_delivery_content || ''} placeholder="未填写" onSave={(v) => saveField('recent_delivery_content', v)} />
          <PickerField label="控制器选择" value={project.controller_vendor || '未设置'} onClick={() => setActivePicker('controller_vendor')} />
          <div className="mac-field-stack">
            <div className="mac-field-stack__label">系统/外设对接</div>
            <button type="button" className="mac-field-stack__row" onClick={openSystemIntegrationEditor}>
              <span className={`mac-field-stack__value${project.system_integration?.length ? '' : ' is-empty'}`}>
                {project.system_integration?.length ? project.system_integration.join('、') : '未设置'}
              </span>
              <span className="mac-field-stack__icon"><MacChevronRight size={16} /></span>
            </button>
          </div>
          <PickerField label="服务器部署" value={project.server_deployment_status || '未设置'} onClick={() => setActivePicker('server_deployment_status')} />
          <EditableField label="部署版本" value={project.deployment_version || ''} placeholder="未填写" onSave={(v) => saveField('deployment_version', v)} />
        </section>

        {/* 项目生命周期（对照原型 SectionCard + 纵向时间线） */}
        <section className="mac-card mac-card--pad" style={{ marginBottom: 12 }}>
          <h3 className="mac-card-title">项目生命周期</h3>
          {/* 项目阶段 —— 位于生命周期标题下方，便于直接查看/修改当前阶段；绑定 status（企业微信「项目生命周期」列实时同步） */}
          <PickerField label="项目阶段" value={project.status || '未设置'} onClick={() => setActivePicker('status')} required={isNew} />
          {isAborted ? (
            <div className="mac-timeline__aborted">
              <span>⛔</span>
              <span>项目已中止</span>
            </div>
          ) : (
            <div className="mac-timeline">
              {LIFECYCLE_STATUSES.map((stage, idx) => {
                const done = lifecycleIndex >= 0 && idx < lifecycleIndex;
                const current = idx === lifecycleIndex;
                const note = project.stage_notes?.[stage];
                const state = done ? 'is-done' : current ? 'is-active' : 'is-pending';
                return (
                  <div key={stage} className="mac-timeline__item">
                    {idx < LIFECYCLE_STATUSES.length - 1 && <div className="mac-timeline__line" />}
                    <span className={`mac-timeline__dot ${state}`}>
                      {done ? <MacCheck size={14} /> : current ? <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--mac-blue-2)' }} /> : null}
                    </span>
                    <div className="mac-timeline__stage">
                      <div className="mac-timeline__head">
                        <span className={`mac-timeline__name ${state}`}>{stage}</span>
                        <span className="mac-timeline__status">{done ? '已完成' : current ? '进行中' : '待开始'}</span>
                        <button type="button" className="mac-timeline__note-btn" onClick={() => openNoteEditor(stage)}>
                          {note ? '编辑说明' : '+ 补充说明'}
                        </button>
                      </div>
                      {note && <div className="mac-timeline__note">{note}</div>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* 风险管理（对照原型 SectionCard + FieldRow + 项目文档） */}
        <section className="mac-card mac-card--pad" style={{ marginBottom: 12 }}>
          <h3 className="mac-card-title">风险管理</h3>
          <PickerField label="风险承接" value={project.risk_carrying_type || '未设置'} onClick={() => setActivePicker('risk_carrying_type')} />
          <EditableField label="特别关注" value={project.special_attention || ''} placeholder="无" multiline onSave={(v) => saveField('special_attention', v)} />
          <EditableField label="风险和任务描述" value={project.risk_task_description || ''} placeholder="无" multiline onSave={(v) => saveField('risk_task_description', v)} />
          <EditableField label="项目管理策略" value={project.management_strategy || ''} placeholder="无" multiline onSave={(v) => saveField('management_strategy', v)} />
          <EditableField label="预期走向" value={project.expected_trend || ''} placeholder="未设置" onSave={(v) => saveField('expected_trend', v)} />

          {/* 项目文档 */}
          <div className="mac-field-stack">
            <div className="mac-field-stack__label">项目文档</div>
            {(project.project_documents || []).map((doc) => (
              <div key={doc.resource_id} className="mac-doc-row">
                <a
                  className="mac-doc-row__link"
                  href={`${API_CONFIG.ADMIN.BASE_URL}/resource-manager/resources/${doc.resource_id}/download`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <MacFileText size={14} />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{doc.name}</span>
                </a>
                <button type="button" className="mac-doc-row__del" onClick={() => removeDocument(doc.resource_id)}>删除</button>
              </div>
            ))}
            <div className="mac-upload-wrap">
              <Upload
                accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg"
                max={1}
                disabled={uploading}
                onSuccess={({ fileList }) => {
                  const raw = fileList?.[0]?.raw;
                  if (raw) handleUploadDocument(raw);
                }}
              />
              {uploading && <div style={{ fontSize: 12, color: 'var(--mac-muted-fg)', marginTop: 6 }}>上传中...</div>}
            </div>
          </div>
        </section>

        {/* 责任体系（对照原型 SectionCard + FieldRow） */}
        <section className="mac-card mac-card--pad" style={{ marginBottom: 12 }}>
          <h3 className="mac-card-title">责任体系</h3>
          <EditableField label="销售" value={project.sales || ''} placeholder="未指定" onSave={(v) => saveField('sales', v)} />
          <EditableField label="售前" value={project.pre_sales || ''} placeholder="未指定" onSave={(v) => saveField('pre_sales', v)} />
          <EditableField label="项目经理" value={project.project_manager || ''} placeholder="未指定" onSave={(v) => saveField('project_manager', v)} />
          <EditableField label="实施工程师" value={project.field_engineer || ''} placeholder="未指定" onSave={(v) => saveField('field_engineer', v)} />
          <EditableField label="人员计划" value={project.personnel_plan || ''} placeholder="无" multiline onSave={(v) => saveField('personnel_plan', v)} />
        </section>
      </div>

      {/* 项目阶段 / 项目类别 / 项目类型 / 风险承接 —— 单选弹窗（真实枚举，与 backend 对应 Enum 一致） */}
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

      {/* 生命周期阶段补充说明 —— 存入 stage_notes（JSON，键为阶段名） */}
      <Popup visible={!!noteStage} onClose={() => setNoteStage(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">{noteStage} · 补充说明</h4>
          <Textarea
            value={noteDraft}
            onChange={(v: string | number) => setNoteDraft(String(v))}
            placeholder="填写该阶段的详细内容..."
            autosize={{ minRows: 4, maxRows: 10 }}
          />
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
            <button type="button" className="mac-btn mac-btn--primary" onClick={saveNote}>保存</button>
          </div>
        </div>
      </Popup>

      {/* 系统/外设对接 —— 多选，存入 system_integration（JSON数组） */}
      <Popup visible={systemIntegrationOpen} onClose={() => setSystemIntegrationOpen(false)} placement="bottom" showOverlay>
        <div className="mac-sheet" style={{ maxHeight: '70vh', overflow: 'auto' }}>
          <h4 className="mac-sheet__title">系统/外设对接</h4>
          <div style={{ marginBottom: 16 }}>
            {SYSTEM_INTEGRATION_OPTIONS.map((opt) => (
              <div key={opt} className="mac-choice" onClick={() => toggleSystemIntegration(opt)}>
                <Checkbox checked={systemIntegrationDraft.includes(opt)} />
                <span className="mac-choice__label">{opt}</span>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button type="button" className="mac-btn mac-btn--primary" onClick={saveSystemIntegration}>确定</button>
          </div>
        </div>
      </Popup>
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

function EditableField({ label, value, placeholder, multiline, type, title, meta, plain, strong, inlineLabel, required, onSave }: {
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
