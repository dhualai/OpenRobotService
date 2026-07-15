// 项目授权管理 - 基于接口文档 GET /api/admin/projects/licenses/{project_code}
import { useState, useEffect } from 'react';
import { Button, Toast, Loading, Dialog, Input } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface AuthItem { id: string; project: string; user: string; role: string; granted_at: string; }
interface Project { id?: string; code?: string; name: string; }

export default function ProjectAuth() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [projectCodeInput, setProjectCodeInput] = useState('');
  const [items, setItems] = useState<AuthItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [projectLoading, setProjectLoading] = useState(true);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  // 加载项目列表供选择
  useEffect(() => {
    request('/projects/')
      .then((data) => setProjects(normalizeList<Project>(data)))
      .catch((err) => Toast({ message: `加载项目失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }))
      .finally(() => setProjectLoading(false));
  }, []);

  // 根据选中的项目代码获取授权信息
  const fetchLicenses = async (projectCode: string) => {
    if (!projectCode) return;
    setLoading(true);
    try {
      const data = await request(`/projects/licenses/${encodeURIComponent(projectCode)}`);
      setItems(normalizeList<AuthItem>(data));
    } catch (err) {
      Toast({ message: `加载授权失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      setItems([]);
    } finally { setLoading(false); }
  };

  const handleProjectSelect = (project: Project) => {
    setSelectedProject(project);
    setProjectCodeInput('');
    const code = project.code || project.name; // 使用 code 或 name 作为 project_code
    fetchLicenses(code);
  };

  const handleCustomCodeSubmit = () => {
    if (!projectCodeInput.trim()) return;
    setSelectedProject({ name: projectCodeInput.trim() });
    fetchLicenses(projectCodeInput.trim());
  };

  const handleRevoke = (item: AuthItem) => {
    Dialog.confirm?.({
      title: '确认撤销',
      content: '确定要撤销此授权吗？',
      onConfirm: async () => {
        try {
          // 注意：接口文档暂无删除授权的独立端点，使用原有路径
          await request(`/project/auth/${item.id}`, { method: 'DELETE' });
          Toast({ message: '授权已撤销', theme: 'success' });
          if (selectedProject) {
            const code = selectedProject.code || selectedProject.name;
            fetchLicenses(code);
          }
        } catch (err) {
          Toast({ message: `撤销失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        }
      },
    });
  };

  return (
    <div style={{ padding: 16 }}>
      <h4 style={{ marginBottom: 12, fontSize: 15, fontWeight: 600 }}>选择项目查看授权</h4>

      {/* 项目列表选择 */}
      {projectLoading ? <Loading text="加载项目..." /> : (
        <div style={{ marginBottom: 16 }}>
          {projects.map((p) => (
            <div
              key={p.id || p.name}
              onClick={() => handleProjectSelect(p)}
              style={{
                background: selectedProject?.name === p.name ? '#e8f2ff' : '#fff',
                borderRadius: 8,
                padding: '12px 14px',
                marginBottom: 8,
                boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
                cursor: 'pointer',
                border: selectedProject?.name === p.name ? '1px solid #0052d9' : '1px solid transparent',
              }}
            >
              <div style={{ fontWeight: 500 }}>{p.name}</div>
              {p.code && <div style={{ fontSize: 12, color: '#999' }}>项目代码：{p.code}</div>}
            </div>
          ))}
        </div>
      )}

      {/* 手动输入项目代码 */}
      <div style={{ marginBottom: 16, display: 'flex', gap: 8 }}>
        <Input
          value={projectCodeInput}
          onChange={(v) => setProjectCodeInput(String(v))}
          placeholder="或输入项目代码查询"
          clearable
          style={{ flex: 1 }}
        />
        <Button size="small" theme="primary" onClick={handleCustomCodeSubmit} disabled={!projectCodeInput.trim()}>
          查询
        </Button>
      </div>

      {/* 授权列表 */}
      {!selectedProject ? (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>请选择一个项目查看授权信息</div>
      ) : loading ? <Loading text="加载授权..." /> : (
        <>
          <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 12, color: '#0052d9' }}>
            {selectedProject.name} - 授权记录 ({items.length})
          </div>
          {items.map((item) => (
            <div key={item.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontWeight: 500 }}>{item.project}</div>
                  <div style={{ fontSize: 13, color: '#666' }}>{item.user} - {item.role}</div>
                </div>
                <Button size="small" theme="danger" variant="outline" onClick={() => handleRevoke(item)}>撤销</Button>
              </div>
            </div>
          ))}
          {items.length === 0 && (
            <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>该项目暂无授权记录</div>
          )}
        </>
      )}
    </div>
  );
}
