// 数据导入页 - 从 BackgroundService DataImport 迁移
// 说明：本页承载两类数据导入 ——
//   1) 文件导入：DAS 数据包文件（.bz2 / .json），解析后按天切分入库 CollectionData；
//   2) JSON导入：搬运效率汇总 {summary, robots}，直接写入 ProjectTransportEfficiency。
// 项目、日期选择是整页共用的。
import { useState, useEffect } from 'react';
import { Tabs, TabPanel, Button, Upload, Toast, Loading, Popup, Input } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';

interface Project { id?: string; code?: string; name: string; }

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
        return p.name.toLowerCase().includes(kw) || (p.code || '').toLowerCase().includes(kw);
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
          <Input
            value={search}
            onChange={(v) => setSearch(String(v))}
            placeholder="输入项目名称关键词模糊查找"
            clearable
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
              {p.code && <div style={{ fontSize: 12, color: '#999' }}>项目代码：{p.code}</div>}
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

  const [projects, setProjects] = useState<Project[]>([]);

  useEffect(() => {
    request(canViewAll ? '/projects/' : '/projects/me')
      .then((data) => setProjects(normalizeList<Project>(data)))
      .catch(() => { /* 静默失败，切到各 Tab 时不强制要求项目列表已加载 */ });
  }, [canViewAll]);

  // 整页共用：项目，两个 Tab（文件导入 / JSON导入）都是提交搬运效率数据的方式
  const [project, setProject] = useState<Project | null>(null);

  const [jsonText, setJsonText] = useState('');

  const handleFileUpload = async (file: File) => {
    if (!project) { Toast({ message: '请先选择项目', theme: 'warning' }); return; }
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('project', project.code || project.name);
      const data = await request<{ success?: boolean; message?: string }>('/data/upload-file', {
        method: 'POST',
        body: formData,
      });
      if (data.success === false) {
        Toast({ message: data.message || '导入失败', theme: 'error' });
      } else {
        Toast({ message: '数据包解析导入成功', theme: 'success' });
      }
    } catch (err) {
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
    try {
      const data = await request<{ message: string }>('/transport-efficiency/import/json', {
        method: 'POST',
        body: JSON.stringify({
          project_code: project.code || project.name,
          report_date: todayStr(),
          summary: parsed.summary || {},
          robots: parsed.robots || [],
        }),
      });
      Toast({ message: data.message || '导入成功', theme: 'success' });
    } catch (err) {
      Toast({ message: `导入失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: 16 }}>
      <ProjectPickerField projects={projects} value={project} onChange={setProject} />

      <Tabs value={tab} onChange={(v) => setTab(String(v))}>
        <TabPanel value="file" label="文件导入">
          <div style={{ padding: '24px 0' }}>
            <Upload
              accept=".json,.bz2"
              max={1}
              onSuccess={({ fileList }) => {
                if (fileList?.[0]?.raw) handleFileUpload(fileList[0].raw);
              }}
            />
            <p style={{ color: '#999', fontSize: 13, marginTop: 12, textAlign: 'center' }}>
              上传 DAS 数据包文件（.bz2 或 .json，含 GroupEfficiency 等指标数据），选定项目后上传
            </p>
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
          </div>
        </TabPanel>
      </Tabs>
      {loading && <Loading text="导入中..." />}
    </div>
  );
}
