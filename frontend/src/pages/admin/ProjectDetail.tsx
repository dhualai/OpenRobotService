// 项目详情 —— 展示/编辑 GET|PUT /api/admin/projects/{id} 的真实项目数据
// 该项目数据经 backend/app/integrations/sources/wecom/adapter.py 从企业微信项目表同步而来，
// 落库为 backend/app/models/delivery.py 的 Project；本页每一项都直接对应 Project 的一个真实列，
// 不再使用 field_links 承载编造的扩展字段。system_id 即企业微信原始记录 record_id，用于溯源。
import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Loading, Toast, Popup } from 'tdesign-mobile-react';
import { Input, Textarea } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

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
}

// 与 backend ProjectStatus 枚举严格一致（顺序即生命周期顺序），"项目中止"为终止分支单独处理
const STATUS_OPTIONS = [
  '售前方案', '签单洽谈', '已签合同', '出厂测试', '即将进场', '延期进场',
  '正在实施', '实施暂停', '实施运行', '试运行中', '验收运营', '项目中止', '项目结束',
];
const LIFECYCLE_STATUSES = STATUS_OPTIONS.filter((s) => s !== '项目中止');

// 与 backend ProjectCategory 枚举严格一致
const CATEGORY_OPTIONS = ['重要紧急', '紧急不重要', '重要不紧急', '不紧急不重要'];

const STATUS_COLOR: Record<string, string> = {
  '售前方案': '#8e5fd9', '签单洽谈': '#0052d9', '已签合同': '#0089ff', '出厂测试': '#00a3c4',
  '即将进场': '#2ba471', '延期进场': '#e37318', '正在实施': '#00a870', '实施暂停': '#ed7b2f',
  '实施运行': '#2f9bed', '试运行中': '#2f9bed', '验收运营': '#2eb872', '项目结束': '#666666',
  '项目中止': '#d54941',
};

const URGENCY_COLOR: Record<string, string> = {
  '重要紧急': '#d54941', '紧急不重要': '#0052d9', '重要不紧急': '#e37318', '不紧急不重要': '#999999',
};

const PICKERS: { key: 'status' | 'category_basis'; label: string; options: string[] }[] = [
  { key: 'status', label: '项目阶段', options: STATUS_OPTIONS },
  { key: 'category_basis', label: '项目类别', options: CATEGORY_OPTIONS },
];

function cardStyle(): React.CSSProperties {
  return { background: '#fff', borderRadius: 8, padding: 16, marginBottom: 12, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' };
}

function cardTitleStyle(): React.CSSProperties {
  return { fontSize: 12, color: '#999', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, paddingBottom: 10, borderBottom: '1px solid #f0f0f0', marginBottom: 14 };
}

export default function ProjectDetail() {
  const { id = '' } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [project, setProject] = useState<ProjectDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [activePicker, setActivePicker] = useState<'status' | 'category_basis' | null>(null);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request<ProjectDetailData>(`/projects/${id}?include_risks=true`);
      setProject(data);
    } catch (err) {
      Toast({ message: `加载项目详情失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally { setLoading(false); }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  // 保存单个 Project 字段（真实列），仅回写发生变化的那一个字段
  const saveField = async (key: keyof ProjectDetailData, value: string) => {
    try {
      await request(`/projects/${id}`, { method: 'PUT', body: JSON.stringify({ [key]: value }) });
      setProject((prev) => (prev ? { ...prev, [key]: value } : prev));
      Toast({ message: '已保存', theme: 'success' });
    } catch (err) {
      Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  const handlePickSingle = (key: 'status' | 'category_basis', value: string) => {
    setActivePicker(null);
    saveField(key, value);
  };

  if (loading) return <Loading text="加载项目详情..." />;
  if (!project) return <div style={{ padding: 40, textAlign: 'center', color: '#999' }}>项目不存在</div>;

  const lifecycleIndex = LIFECYCLE_STATUSES.indexOf(project.status);
  const progressPct = lifecycleIndex >= 0 ? Math.round((lifecycleIndex / (LIFECYCLE_STATUSES.length - 1)) * 100) : 0;
  const isAborted = project.status === '项目中止';
  const activePickerConfig = PICKERS.find((p) => p.key === activePicker);

  return (
    <div>
      <Navbar title="项目详情" leftArrow onLeftClick={() => navigate(-1)} fixed />
      <div style={{ padding: 16, paddingTop: 64 }}>
        {/* 概要卡片 */}
        <div style={cardStyle()}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <div style={{ fontSize: 17, fontWeight: 600 }}>{project.name || '未命名项目'}</div>
              <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>
                项目编号: {project.project_code}{project.system_id ? ` · 企业微信记录ID: ${project.system_id}` : ''}
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
              <span
                onClick={() => setActivePicker('status')}
                style={{ padding: '2px 10px', borderRadius: 999, fontSize: 12, fontWeight: 500, color: '#fff', background: STATUS_COLOR[project.status] || '#999', cursor: 'pointer' }}
              >
                {project.status} ›
              </span>
              <span
                onClick={() => setActivePicker('category_basis')}
                style={{ padding: '2px 10px', borderRadius: 999, fontSize: 12, fontWeight: 500, color: '#fff', background: URGENCY_COLOR[project.category_basis] || '#999', cursor: 'pointer' }}
              >
                {project.category_basis || '未分类'} ›
              </span>
            </div>
          </div>

          {project.category_basis === '重要紧急' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 10, marginTop: 12, background: '#fff7ed', borderRadius: 8, border: '1px solid #fed7aa' }}>
              <span>⚠️</span>
              <span style={{ fontSize: 13, color: '#e37318', fontWeight: 500 }}>已标记为特别关注项目（重要紧急）</span>
            </div>
          )}

          <div style={{ height: 1, background: '#f0f0f0', margin: '14px 0' }} />

          {!isAborted && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#999', marginBottom: 6 }}>
                <span>项目时间进度</span>
                <span style={{ color: '#0052d9' }}>{progressPct}%</span>
              </div>
              <div style={{ width: '100%', height: 6, background: '#f0f0f0', borderRadius: 999, overflow: 'hidden' }}>
                <div style={{ width: `${progressPct}%`, height: '100%', background: 'linear-gradient(90deg,#0052d9,#5a9cff)', borderRadius: 999 }} />
              </div>
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
            <div>
              <div style={{ fontSize: 12, color: '#999' }}>部署时间</div>
              <div style={{ fontSize: 13, fontWeight: 500, marginTop: 2 }}>{project.deployment_date || '-'}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: '#999' }}>近期交付</div>
              <div style={{ fontSize: 13, fontWeight: 500, marginTop: 2 }}>{project.recent_delivery_date || '-'}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: '#999' }}>最终交付</div>
              <div style={{ fontSize: 13, fontWeight: 500, marginTop: 2 }}>{project.final_delivery_date || '-'}</div>
            </div>
          </div>

          {project.task_execution_status && (
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid #f0f0f0', fontSize: 12, color: '#666' }}>
              近7天任务执行：{project.task_execution_status}
            </div>
          )}
        </div>

        {/* 项目基础画像 */}
        <div style={cardStyle()}>
          <h3 style={cardTitleStyle()}>项目基础画像</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <EditableField label="项目名称" value={project.name} onSave={(v) => saveField('name', v)} />
            <EditableField label="项目描述" value={project.description || ''} placeholder="未填写" multiline onSave={(v) => saveField('description', v)} />
            <PickerField label="项目类别" value={project.category_basis || '未设置'} onClick={() => setActivePicker('category_basis')} />
            <PickerField label="项目阶段" value={project.status || '未设置'} onClick={() => setActivePicker('status')} />
            <EditableField label="车型&车数" value={project.recent_delivery_content || ''} placeholder="未填写" onSave={(v) => saveField('recent_delivery_content', v)} />
            <EditableField label="部署版本" value={project.deployment_version || ''} placeholder="未填写" onSave={(v) => saveField('deployment_version', v)} />
          </div>
        </div>

        {/* 项目生命周期 */}
        <div style={cardStyle()}>
          <h3 style={cardTitleStyle()}>项目生命周期</h3>
          {isAborted ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 12, background: '#fef2f2', borderRadius: 8, border: '1px solid #fecaca' }}>
              <span>⛔</span>
              <span style={{ fontSize: 13, color: '#d54941', fontWeight: 500 }}>项目已中止</span>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16, paddingLeft: 4, position: 'relative' }}>
              <div style={{ position: 'absolute', left: 11, top: 12, bottom: 12, width: 2, background: '#f0f0f0' }} />
              {LIFECYCLE_STATUSES.map((stage, idx) => {
                const done = lifecycleIndex >= 0 && idx < lifecycleIndex;
                const current = idx === lifecycleIndex;
                return (
                  <div key={stage} style={{ display: 'flex', alignItems: 'flex-start', gap: 12, position: 'relative', zIndex: 1 }}>
                    <div style={{
                      width: 24, height: 24, borderRadius: '50%', flexShrink: 0,
                      display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, color: '#fff',
                      background: done ? '#0052d9' : current ? STATUS_COLOR[stage] : '#e5e5e5',
                      border: current ? '3px solid #fff' : 'none',
                      boxShadow: current ? '0 0 0 1px ' + (STATUS_COLOR[stage] || '#999') : 'none',
                    }}>
                      {done ? '✓' : ''}
                    </div>
                    <div style={{ paddingTop: 2 }}>
                      <div style={{ fontSize: 14, fontWeight: 500, color: done || current ? '#1a1a1a' : '#999' }}>{stage}</div>
                      <div style={{ fontSize: 12, color: '#999' }}>{done ? '已完成' : current ? '进行中' : '待开始'}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* 风险管理 */}
        <div style={cardStyle()}>
          <h3 style={cardTitleStyle()}>风险管理</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <ReadonlyField label="未关闭风险数" value={String(project.risks)} />
              <ReadonlyField label="待处理问题数" value={String(project.issues)} />
            </div>
            <EditableField label="预期走向" value={project.expected_trend || ''} placeholder="未设置" onSave={(v) => saveField('expected_trend', v)} />
            <ReadonlyField label="风险清单" value={project.risk_list || '无'} />
            <ReadonlyField label="风险详情" value={project.project_summary || '无'} multiline />
          </div>
        </div>

        {/* 责任体系 */}
        <div style={cardStyle()}>
          <h3 style={cardTitleStyle()}>责任体系</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <EditableField label="项目对接人" value={project.contact_person || ''} placeholder="未指定" onSave={(v) => saveField('contact_person', v)} />
            <EditableField label="人员计划" value={project.personnel_plan || ''} placeholder="无" multiline onSave={(v) => saveField('personnel_plan', v)} />
          </div>
        </div>

        {/* 相关链接：仅当 field_links 中存在真实数据时展示，不编造字段 */}
        {project.field_links && Object.keys(project.field_links).length > 0 && (
          <div style={cardStyle()}>
            <h3 style={cardTitleStyle()}>相关链接</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {Object.entries(project.field_links).map(([k, v]) => (
                <div key={k} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                  <span style={{ color: '#999' }}>{k}</span>
                  <a href={v} target="_blank" rel="noreferrer" style={{ color: '#0052d9', textDecoration: 'none', maxWidth: '70%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v}</a>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 项目阶段 / 项目类别 —— 单选弹窗（真实枚举，与 backend ProjectStatus / ProjectCategory 一致） */}
      <Popup visible={!!activePicker} onClose={() => setActivePicker(null)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '70vh', overflow: 'auto' }}>
          <h4 style={{ marginBottom: 16 }}>{activePickerConfig?.label}</h4>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {activePickerConfig?.options.map((opt) => (
              <div
                key={opt}
                onClick={() => handlePickSingle(activePickerConfig.key, opt)}
                style={{ padding: '12px 4px', borderBottom: '1px solid #f5f5f5', fontSize: 14, cursor: 'pointer' }}
              >
                {opt}
              </div>
            ))}
          </div>
        </div>
      </Popup>
    </div>
  );
}

function ReadonlyField({ label, value, multiline }: { label: string; value: string; multiline?: boolean }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <label style={{ fontSize: 12, color: '#999' }}>{label}</label>
      <div style={{
        fontSize: 14, color: '#1a1a1a', background: '#f8fafc', borderRadius: 8, padding: '10px 12px',
        whiteSpace: multiline ? 'pre-wrap' : 'nowrap', overflow: multiline ? 'visible' : 'hidden', textOverflow: 'ellipsis',
      }}>
        {value}
      </div>
    </div>
  );
}

function PickerField({ label, value, onClick }: { label: string; value: string; onClick: () => void }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <label style={{ fontSize: 12, color: '#999' }}>{label}</label>
      <div
        onClick={onClick}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          fontSize: 14, background: '#f8fafc', borderRadius: 8, padding: '10px 12px', cursor: 'pointer',
        }}
      >
        <span>{value}</span>
        <span style={{ color: '#999' }}>›</span>
      </div>
    </div>
  );
}

function EditableField({ label, value, placeholder, multiline, onSave }: { label: string; value: string; placeholder?: string; multiline?: boolean; onSave: (v: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);

  useEffect(() => { setDraft(value); }, [value]);

  const commit = () => {
    setEditing(false);
    if (draft !== value) onSave(draft);
  };

  if (editing) {
    const Field = multiline ? Textarea : Input;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <label style={{ fontSize: 12, color: '#999' }}>{label}</label>
        <Field
          value={draft}
          onChange={(v: string | number) => setDraft(String(v))}
          onBlur={commit}
          autofocus
          placeholder={placeholder}
        />
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <label style={{ fontSize: 12, color: '#999' }}>{label}</label>
      <div
        onClick={() => setEditing(true)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
          fontSize: 14, color: value ? '#1a1a1a' : '#999', background: '#f8fafc', borderRadius: 8, padding: '10px 12px', cursor: 'pointer',
          whiteSpace: multiline ? 'pre-wrap' : 'nowrap', overflow: multiline ? 'visible' : 'hidden', textOverflow: 'ellipsis',
        }}
      >
        <span>{value || placeholder}</span>
        <span style={{ color: '#ccc', flexShrink: 0 }}>✎</span>
      </div>
    </div>
  );
}
