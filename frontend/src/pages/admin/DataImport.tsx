// 数据导入页 - 从 BackgroundService DataImport 迁移
// 说明：本页承载两类数据导入 ——
//   1) 文件导入：DAS 数据包文件（.bz2 / .json），解析后按天切分入库 CollectionData；
//   2) JSON导入：搬运效率汇总 {summary, robots}，直接写入 ProjectTransportEfficiency。
// 项目、日期选择是整页共用的。
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Tabs, TabPanel, Button, Upload, Toast, Loading, Popup } from 'tdesign-mobile-react';
import ClearableInput from '@/shared/components/ClearableInput';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';

// 与 /projects/、/projects/me 实际返回字段对齐（ProjectResponse）：项目标识是 project_code（与 id 同值），
// 没有 code 字段 —— 之前用 project.code 取标识，永远取不到值会静默回退成 project.name（中文项目名），
// 导致导入数据落库的 project key 与「项目详情页-搬运效率分析」按 project_code 查询的 key 对不上，同项目的数据传不过去。
interface Project { id?: string; project_code?: string; name: string; }

// 文件导入结果 —— 后端 /data/upload-file 返回，用于展示导入成功的条目
interface ImportChunk {
  start_time: string;
  end_time: string;
  groups?: string[];
}

interface FileImportResult {
  success?: boolean;
  message?: string;
  filename?: string;
  file_size?: string;
  project?: string;
  indicator?: string;
  chunk_count?: number;
  chunks?: ImportChunk[];
}

const todayStr = (): string => {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};

interface ProjectPickerFieldProps {
  projects: Project[];
  value: Project | null;
  onChange: (p: Project) => void;
}

function ProjectPickerField({ projects, value, onChange }: ProjectPickerFieldProps) {
  const [visible, setVisible] = useState(false);
  const [search, setSearch] = useState('');

  const filtered = search.trim()
    ? projects.filter((p) => {
        const kw = search.trim().toLowerCase();
        return p.name.toLowerCase().includes(kw) || (p.project_code || '').toLowerCase().includes(kw);
      })
    : projects;

  return (
    <>
      <div
        onClick={() => setVisible(true)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          background: '#fff', border: '1px solid #dcdcdc', borderRadius: 8, padding: '12px 14px', marginBottom: 12,
          cursor: 'pointer',
        }}
      >
        <div>
          {value ? (
            <div style={{ fontWeight: 500 }}>{value.name}</div>
          ) : (
            <span style={{ color: '#bbb', fontSize: 14 }}>请选择项目</span>
          )}
        </div>
        <span style={{ color: '#999' }}>›</span>
      </div>

      <Popup visible={visible} onClose={() => setVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '70vh', overflow: 'auto' }}>
          <h4 style={{ marginBottom: 12 }}>选择项目</h4>
          <ClearableInput
            value={search}
            onChange={(v) => setSearch(String(v))}
            placeholder="输入项目名称关键词模糊查找"
            style={{ marginBottom: 12 }}
          />
          {filtered.map((p) => (
            <div
              key={p.id || p.name}
              onClick={() => { onChange(p); setSearch(''); setVisible(false); }}
              style={{
                background: value?.name === p.name ? '#e8f2ff' : '#fff',
                borderRadius: 8,
                padding: '12px 14px',
                marginBottom: 8,
                cursor: 'pointer',
                border: value?.name === p.name ? '1px solid #0052d9' : '1px solid transparent',
              }}
            >
              <div style={{ fontWeight: 500 }}>{p.name}</div>
              {p.project_code && <div style={{ fontSize: 12, color: '#999' }}>项目代码：{p.project_code}</div>}
            </div>
          ))}
          {filtered.length === 0 && (
            <div style={{ textAlign: 'center', padding: 30, color: '#999' }}>未找到匹配的项目</div>
          )}
        </div>
      </Popup>
    </>
  );
}

export default function DataImport() {
  const [tab, setTab] = useState('file');
  const [loading, setLoading] = useState(false);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');
  const { hasPermission } = useAuthStore();
  const canViewAll = hasPermission(PERMISSION_VIEW_ALL);
  const navigate = useNavigate();

  const [projects, setProjects] = useState<Project[]>([]);

  useEffect(() => {
    request(canViewAll ? '/projects/' : '/projects/me')
      .then((data) => setProjects(normalizeList<Project>(data)))
      .catch(() => { /* 静默失败，切到各 Tab 时不强制要求项目列表已加载 */ });
  }, [canViewAll]);

  // 整页共用：项目，两个 Tab（文件导入 / JSON导入）都是提交搬运效率数据的方式
  const [project, setProject] = useState<Project | null>(null);

  const [jsonText, setJsonText] = useState('');

  // JSON导入结果：与文件导入一致，展示成功/失败反馈（此前只有 Toast，刷新或切页后无迹可循）
  const [jsonResult, setJsonResult] = useState<{ summary?: unknown; robots?: unknown[] } | null>(null);
  const [jsonError, setJsonError] = useState<string | null>(null);

  // 文件导入结果：成功时展示条目，失败时展示错误提示
  const [fileResult, setFileResult] = useState<FileImportResult | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  const handleFileUpload = async (file: File) => {
    if (!project) {
      Toast({ message: '请先选择项目', theme: 'warning' });
      return;
    }
    setLoading(true);
    setFileResult(null);
    setFileError(null);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('project', project.project_code || project.id || project.name);
      const data = await request<FileImportResult>('/data/upload-file', {
        method: 'POST',
        body: formData,
      });
      if (data.success === false) {
        setFileError(data.message || '导入失败');
      } else {
        setFileResult(data);
        Toast({ message: data.message || '数据包解析导入成功', theme: 'success' });
      }
    } catch (err) {
      console.error('[DataImport] 上传失败:', err);
      setFileError(err instanceof Error ? err.message : '未知错误');
      Toast({ message: `导入失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const handleJsonImport = async () => {
    if (!project) { Toast({ message: '请先选择项目', theme: 'warning' }); return; }
    if (!jsonText.trim()) { Toast({ message: '请输入JSON数据', theme: 'warning' }); return; }

    let parsed: { summary?: Record<string, unknown>; robots?: Record<string, unknown>[] };
    try {
      parsed = JSON.parse(jsonText);
    } catch {
      Toast({ message: 'JSON格式有误，请检查后重试', theme: 'error' });
      return;
    }

    setLoading(true);
    setJsonResult(null);
    setJsonError(null);
    try {
      const data = await request<{ message: string; summary?: unknown; robots?: unknown[] }>('/transport-efficiency/import/json', {
        method: 'POST',
        body: JSON.stringify({
          project_code: project.project_code || project.id || project.name,
          report_date: todayStr(),
          summary: parsed.summary || {},
          robots: parsed.robots || [],
        }),
      });
      setJsonResult({ summary: data.summary, robots: data.robots });
      Toast({ message: data.message || '导入成功', theme: 'success' });
    } catch (err) {
      const msg = err instanceof Error ? err.message : '未知错误';
      setJsonError(msg);
      Toast({ message: `导入失败: ${msg}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: 16 }}>
      <ProjectPickerField
        projects={projects}
        value={project}
        onChange={(p) => {
          // 切换项目后清空上一个项目的导入结果展示，避免误认为是当前项目的导入反馈
          setProject(p);
          setFileResult(null);
          setFileError(null);
          setJsonResult(null);
          setJsonError(null);
        }}
      />

      <Tabs value={tab} onChange={(v) => setTab(String(v))}>
        <TabPanel value="file" label="文件导入">
          <div style={{ padding: '24px 0' }}>
            <Upload
              accept=".json,.bz2"
              max={1}
              autoUpload={false}
              onSelectChange={(files) => {
                if (files?.[0]) {
                  handleFileUpload(files[0]);
                }
              }}
            />
            <p style={{ color: '#999', fontSize: 13, marginTop: 12, textAlign: 'center' }}>
              上传 数据包文件（.bz2 或 .json，含 GroupEfficiency 等指标数据），选定项目后上传
            </p>

            {/* 导入失败提示 */}
            {fileError && (
              <div style={{
                background: '#fff1f0', border: '1px solid #ffccc7', borderRadius: 8,
                padding: 12, color: '#f5222d', marginTop: 16, fontSize: 13,
              }}>
                <div style={{ fontWeight: 600 }}>✗ 导入失败</div>
                <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{fileError}</div>
              </div>
            )}

            {/* 导入成功：信息卡片 + 导入条目列表 */}
            {fileResult && fileResult.success !== false && (
              <div style={{ marginTop: 16 }}>
                {/* 成功提示条 */}
                <div style={{
                  background: '#f6ffed', border: '1px solid #b7eb8f', borderRadius: 8,
                  padding: '10px 14px', color: '#52c41a', fontSize: 13, fontWeight: 500, marginBottom: 12,
                }}>
                  ✓ {fileResult.message || '导入成功'}
                </div>

                {/* 信息卡片（参考 ProjectMetricsList 的 header-info） */}
                <div style={{ background: '#f9f9f9', borderRadius: 8, padding: '12px 16px', border: '1px solid #e8e8e8', marginBottom: 12 }}>
                  <InfoLine label="项目名称" value={project?.name || fileResult.project || '-'} />
                  <InfoLine label="指标标签" value={fileResult.indicator || '-'} />
                  <InfoLine label="文件名称" value={fileResult.filename || '-'} />
                  <InfoLine label="文件大小" value={fileResult.file_size || '-'} />
                  <InfoLine label="导入条目数" value={String(fileResult.chunk_count ?? 0)} />
                </div>

                {/* 导入的数据条目列表 */}
                {fileResult.chunks && fileResult.chunks.length > 0 && (
                  <div style={{ background: '#fff', borderRadius: 8, padding: 14, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
                    <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 10 }}>导入的数据条目</div>
                    {fileResult.chunks.map((c, idx) => (
                      <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid #f5f5f5', fontSize: 13, gap: 8 }}>
                        <span style={{ color: '#1a1a1a', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {c.start_time} ~ {c.end_time}
                        </span>
                        <span style={{ color: '#999', flexShrink: 0 }}>
                          {c.groups && c.groups.length ? c.groups.join('、') : '—'}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                {/* 跳转到同一项目的搬运效率分析页，验证数据已生效 */}
                {project?.id && (
                  <Button
                    theme="primary"
                    variant="outline"
                    block
                    style={{ marginTop: 12 }}
                    onClick={() => navigate(`/admin/project-detail/${project.id}/transport-efficiency`)}
                  >
                    查看该项目搬运效率分析 ›
                  </Button>
                )}
              </div>
            )}
          </div>
        </TabPanel>
        <TabPanel value="json" label="JSON导入">
          <div style={{ padding: '24px 0' }}>
            <textarea
              value={jsonText}
              placeholder='{"summary": {...}, "robots": [...]}'
              rows={8}
              style={{ width: '100%', padding: 12, border: '1px solid #ddd', borderRadius: 8, fontSize: 14 }}
              onChange={(e) => setJsonText(e.target.value)}
            />
            <Button theme="primary" block style={{ marginTop: 16 }} onClick={handleJsonImport}>
              导入JSON数据
            </Button>

            {/* 导入失败提示 */}
            {jsonError && (
              <div style={{
                background: '#fff1f0', border: '1px solid #ffccc7', borderRadius: 8,
                padding: 12, color: '#f5222d', marginTop: 16, fontSize: 13,
              }}>
                <div style={{ fontWeight: 600 }}>✗ 导入失败</div>
                <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{jsonError}</div>
              </div>
            )}

            {/* 导入成功提示 + 跳转搬运效率分析 */}
            {jsonResult && (
              <div style={{ marginTop: 16 }}>
                <div style={{
                  background: '#f6ffed', border: '1px solid #b7eb8f', borderRadius: 8,
                  padding: '10px 14px', color: '#52c41a', fontSize: 13, fontWeight: 500, marginBottom: 12,
                }}>
                  ✓ 导入成功（项目：{project?.name || '-'}，日期：{todayStr()}）
                </div>
                {project?.id && (
                  <Button
                    theme="primary"
                    variant="outline"
                    block
                    onClick={() => navigate(`/admin/project-detail/${project.id}/transport-efficiency`)}
                  >
                    查看该项目搬运效率分析 ›
                  </Button>
                )}
              </div>
            )}
          </div>
        </TabPanel>
      </Tabs>
      {loading && <Loading text="导入中..." />}
    </div>
  );
}

// 信息卡片的一行：左侧灰色标签 + 右侧数值（参考 ProjectMetricsList 的 header-info）
function InfoLine({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '5px 0', fontSize: 13, gap: 12 }}>
      <span style={{ color: '#666', flexShrink: 0 }}>{label}</span>
      <span style={{ color: '#1a1a1a', fontWeight: 500, textAlign: 'right' }}>{value}</span>
    </div>
  );
}
