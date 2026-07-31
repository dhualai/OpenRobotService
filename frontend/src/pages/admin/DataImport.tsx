// 数据导入页 - 从 BackgroundService DataImport 迁移
import { useState, useEffect } from 'react';
import { Tabs, TabPanel, Button, Upload, Toast, Loading, Popup, DateTimePicker, Input } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

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
  // TODO: 新后端导入接口为 /export/ 和 /wechat/import-data，需确认对应端点

  const [projects, setProjects] = useState<Project[]>([]);

  useEffect(() => {
    request('/projects/')
      .then((data) => setProjects(normalizeList<Project>(data)))
      .catch(() => { /* 静默失败，切到各 Tab 时不强制要求项目列表已加载 */ });
  }, []);

  // 文件导入：需先选择项目
  const [fileProject, setFileProject] = useState<Project | null>(null);

  // JSON导入：需先选择项目
  const [jsonProject, setJsonProject] = useState<Project | null>(null);
  const [jsonText, setJsonText] = useState('');

  // 搬运效率导入：项目 + 日期 + Excel 文件
  const [teProject, setTeProject] = useState<Project | null>(null);
  const [teDate, setTeDate] = useState(todayStr());
  const [teDatePickerVisible, setTeDatePickerVisible] = useState(false);
  const [teLoading, setTeLoading] = useState(false);

  const handleTransportEfficiencyUpload = async (file: File) => {
    if (!teProject) { Toast({ message: '请先选择项目', theme: 'warning' }); return; }
    if (!teDate) { Toast({ message: '请先选择日期', theme: 'warning' }); return; }
    setTeLoading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('project_code', teProject.code || teProject.name);
      formData.append('report_date', teDate);
      await request('/transport-efficiency/import/file', {
        method: 'POST',
        body: formData,
      });
      Toast({ message: '搬运效率数据导入成功', theme: 'success' });
    } catch (err) {
      Toast({ message: `导入失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setTeLoading(false);
    }
  };

  const handleFileUpload = async (file: File) => {
    if (!fileProject) { Toast({ message: '请先选择项目', theme: 'warning' }); return; }
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('project_code', fileProject.code || fileProject.name);
      const data = await request<{ message: string }>('/data/import/file', {
        method: 'POST',
        body: formData,
      });
      Toast({ message: data.message || '导入成功', theme: 'success' });
    } catch (err) {
      Toast({ message: `导入失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const handleJsonImport = async () => {
    if (!jsonProject) { Toast({ message: '请先选择项目', theme: 'warning' }); return; }
    if (!jsonText.trim()) { Toast({ message: '请输入JSON数据', theme: 'warning' }); return; }

    let parsed: unknown;
    try {
      parsed = JSON.parse(jsonText);
    } catch {
      Toast({ message: 'JSON格式有误，请检查后重试', theme: 'error' });
      return;
    }

    setLoading(true);
    try {
      const data = await request<{ message: string }>('/data/import/json', {
        method: 'POST',
        body: JSON.stringify({ project_code: jsonProject.code || jsonProject.name, data: parsed }),
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
      <Tabs value={tab} onChange={(v) => setTab(String(v))}>
        <TabPanel value="file" label="文件导入">
          <div style={{ padding: '24px 0' }}>
            <ProjectPickerField projects={projects} value={fileProject} onChange={setFileProject} />
            <Upload
              accept=".csv,.xlsx,.xls"
              max={1}
              onSuccess={({ fileList }) => {
                if (fileList?.[0]?.raw) handleFileUpload(fileList[0].raw);
              }}
            />
            <p style={{ color: '#999', fontSize: 13, marginTop: 12, textAlign: 'center' }}>
              支持 CSV、Excel 格式，选定项目后上传
            </p>
          </div>
        </TabPanel>
        <TabPanel value="json" label="JSON导入">
          <div style={{ padding: '24px 0' }}>
            <ProjectPickerField projects={projects} value={jsonProject} onChange={setJsonProject} />
            <textarea
              value={jsonText}
              placeholder='[{"name": "...", "value": ...}]'
              rows={8}
              style={{ width: '100%', padding: 12, border: '1px solid #ddd', borderRadius: 8, fontSize: 14 }}
              onChange={(e) => setJsonText(e.target.value)}
            />
            <Button theme="primary" block style={{ marginTop: 16 }} onClick={handleJsonImport}>
              导入JSON数据
            </Button>
          </div>
        </TabPanel>
        <TabPanel value="transport-efficiency" label="搬运效率导入">
          <div style={{ padding: '24px 0' }}>
            <ProjectPickerField projects={projects} value={teProject} onChange={setTeProject} />

            <div
              onClick={() => setTeDatePickerVisible(true)}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                background: '#fff', border: '1px solid #dcdcdc', borderRadius: 8, padding: '12px 14px', marginBottom: 12,
                cursor: 'pointer',
              }}
            >
              <div style={{ fontWeight: 500 }}>{teDate}</div>
              <span style={{ color: '#999' }}>›</span>
            </div>

            <Upload
              accept=".xlsx"
              max={1}
              onSuccess={({ fileList }) => {
                if (fileList?.[0]?.raw) handleTransportEfficiencyUpload(fileList[0].raw);
              }}
            />
            <p style={{ color: '#999', fontSize: 13, marginTop: 12, textAlign: 'center' }}>
              上传搬运效率 Excel（含"汇总"与"机型明细"工作表），选定项目和日期后上传
            </p>

            <Popup visible={teDatePickerVisible} onClose={() => setTeDatePickerVisible(false)} placement="bottom">
              <DateTimePicker
                mode="date"
                title="选择日期"
                format="YYYY-MM-DD"
                value={teDate || undefined}
                onConfirm={(v) => { setTeDate(String(v)); setTeDatePickerVisible(false); }}
                onCancel={() => setTeDatePickerVisible(false)}
              />
            </Popup>

            {teLoading && <Loading text="导入中..." />}
          </div>
        </TabPanel>
      </Tabs>
      {loading && <Loading text="导入中..." />}
    </div>
  );
}
