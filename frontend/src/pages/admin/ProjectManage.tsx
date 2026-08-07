// 项目管理（二级页面）—— 「项目导入」「项目授权」两部分
// ProjectImport: 项目增删改查
// 项目授权区：项目选择器 + ProjectAuth 授权记录 + ProjectPeople 人员关联
import { useEffect, useState, type ReactNode } from 'react';
import { Loading, Toast, Input, Popup, Button } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';
import ProjectImport from './ProjectImport';
import ProjectAuth from './ProjectAuth';
import ProjectPeople from './ProjectPeople';

interface Project { id?: string; project_code?: string; name: string; }

// 可折叠区域：点击标题切换展开/收起，默认展开
const CollapsibleSection = ({ icon, title, open, onToggle, children }: { icon: string; title: string; open: boolean; onToggle: () => void; children: ReactNode }) => (
  <div style={{ marginTop: 16 }}>
    <div
      onClick={onToggle}
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '12px 14px', background: '#fff', borderRadius: 8,
        boxShadow: '0 1px 3px rgba(0,0,0,0.06)', cursor: 'pointer',
        fontWeight: 600, fontSize: 15,
      }}
    >
      <span>{icon} {title}</span>
      <span style={{ color: '#999', display: 'inline-block', transition: 'transform 0.2s', transform: open ? 'rotate(90deg)' : 'none' }}>›</span>
    </div>
    {open && <div style={{ paddingTop: 12 }}>{children}</div>}
  </div>
);

export default function ProjectManage() {
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');
  const { hasPermission } = useAuthStore();
  const canViewAll = hasPermission(PERMISSION_VIEW_ALL);

  // 共享状态：当前选中的项目（供授权、人员关联两个子模块使用）
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [projectSearch, setProjectSearch] = useState('');
  const [projectLoading, setProjectLoading] = useState(true);
  const [projectPickerVisible, setProjectPickerVisible] = useState(false);

  // 两个区域折叠状态（默认展开）
  const [sectionImportOpen, setSectionImportOpen] = useState(true);
  const [sectionAuthOpen, setSectionAuthOpen] = useState(true);
  // 「项目授权」内部两个子区域折叠状态（默认展开）
  const [subLicensesOpen, setSubLicensesOpen] = useState(true);
  const [subPeopleOpen, setSubPeopleOpen] = useState(true);

  // 导出按钮加载状态
  const [exportLoading, setExportLoading] = useState<string | null>(null);

  // 加载项目列表：canViewAll 时获取全部项目，否则仅当前用户关联项目（/projects/me 按 token 过滤）
  useEffect(() => {
    request(canViewAll ? '/projects/' : '/projects/me')
      .then((data) => setProjects(normalizeList<Project>(data)))
      .catch((err) => Toast({ message: `加载项目失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }))
      .finally(() => setProjectLoading(false));
  }, [canViewAll]);

  const handleProjectSelect = (project: Project) => {
    setSelectedProject(project);
    setProjectSearch('');
    setProjectPickerVisible(false);
  };

  const filteredProjects = projectSearch.trim()
    ? projects.filter((p) => {
        const kw = projectSearch.trim().toLowerCase();
        return p.name.toLowerCase().includes(kw) || (p.project_code || '').toLowerCase().includes(kw);
      })
    : projects;

  const handleExport = async (type: 'license' | 'users' | 'all') => {
    if (!selectedProject) {
      Toast({ message: '请先选择项目', theme: 'warning' });
      return;
    }
    const projectCode = selectedProject.project_code || selectedProject.name;
    const labels: Record<string, string> = { license: 'licence授权', users: '人员授权', all: '完整授权' };
    setExportLoading(type);
    try {
      const buffer = await request<ArrayBuffer>(
        `/export/project/${encodeURIComponent(projectCode)}?type=${type}`,
        { method: 'POST', responseType: 'arrayBuffer', timeout: 60000 },
      );
      const blob = new Blob([buffer], { type: 'application/gzip' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `project_${projectCode}_${type}_export.gz`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      Toast({ message: `${labels[type]}导出成功`, theme: 'success' });
    } catch (err) {
      Toast({
        message: `导出失败: ${err instanceof Error ? err.message : '未知错误'}`,
        theme: 'error',
      });
    } finally {
      setExportLoading(null);
    }
  };

  return (
    <div style={{ padding: '16px 16px 24px' }}>
      {/* 可折叠区域：项目导入 */}
      <CollapsibleSection icon="📁" title="项目导入" open={sectionImportOpen} onToggle={() => setSectionImportOpen((v) => !v)}>
        <ProjectImport />
      </CollapsibleSection>

      {/* 可折叠区域：项目授权（含项目选择器 + 授权记录 + 人员关联） */}
      <CollapsibleSection icon="🔐" title="项目授权" open={sectionAuthOpen} onToggle={() => setSectionAuthOpen((v) => !v)}>
        {/* 项目选择器（已从页面顶部下移至此） */}
        <div style={{ padding: '4px 0 12px' }}>
          {projectLoading ? <Loading text="加载项目..." /> : (
            <div
              onClick={() => setProjectPickerVisible(true)}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                background: '#fff', borderRadius: 8, padding: '12px 14px',
                boxShadow: '0 1px 3px rgba(0,0,0,0.06)', cursor: 'pointer',
              }}
            >
              <div>
                {selectedProject ? (
                  <>
                    <div style={{ fontWeight: 500 }}>{selectedProject.name}</div>
                    {selectedProject.project_code && <div style={{ fontSize: 12, color: '#999' }}>项目代码：{selectedProject.project_code}</div>}
                  </>
                ) : (
                  <span style={{ color: '#bbb', fontSize: 14 }}>请选择项目</span>
                )}
              </div>
              <span style={{ color: '#999' }}>›</span>
            </div>
          )}

          <Popup visible={projectPickerVisible} onClose={() => setProjectPickerVisible(false)} placement="bottom" showOverlay>
            <div style={{ padding: 20, maxHeight: '70vh', overflow: 'auto' }}>
              <h4 style={{ marginBottom: 12 }}>选择项目</h4>
              <Input
                value={projectSearch}
                onChange={(v) => setProjectSearch(String(v))}
                placeholder="输入项目名称关键词模糊查找"
                clearable
                style={{ marginBottom: 12 }}
              />
              {filteredProjects.map((p) => (
                <div
                  key={p.id || p.name}
                  onClick={() => handleProjectSelect(p)}
                  style={{
                    background: selectedProject?.name === p.name ? '#e8f2ff' : '#fff',
                    borderRadius: 8,
                    padding: '12px 14px',
                    marginBottom: 8,
                    cursor: 'pointer',
                    border: selectedProject?.name === p.name ? '1px solid #0052d9' : '1px solid transparent',
                  }}
                >
                  <div style={{ fontWeight: 500 }}>{p.name}</div>
                  {p.project_code && <div style={{ fontSize: 12, color: '#999' }}>项目代码：{p.project_code}</div>}
                </div>
              ))}
              {filteredProjects.length === 0 && (
                <div style={{ textAlign: 'center', padding: 30, color: '#999' }}>未找到匹配的项目</div>
              )}
            </div>
          </Popup>
        </div>

        {/* 两个子区域整体左缩进，体现层级 */}
        <div style={{ paddingLeft: 12 }}>
          <CollapsibleSection icon="🔑" title="项目licences授权" open={subLicensesOpen} onToggle={() => setSubLicensesOpen((v) => !v)}>
            <ProjectAuth selectedProject={selectedProject} />
          </CollapsibleSection>

          <CollapsibleSection icon="👥" title="项目人员授权" open={subPeopleOpen} onToggle={() => setSubPeopleOpen((v) => !v)}>
            <ProjectPeople selectedProject={selectedProject} />
          </CollapsibleSection>

          {/* 导出按钮组 */}
          <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Button
              theme="primary"
              variant="outline"
              block
              loading={exportLoading === 'license'}
              disabled={!selectedProject || exportLoading !== null}
              onClick={() => handleExport('license')}
            >
              导出licence授权
            </Button>
            <Button
              theme="primary"
              variant="outline"
              block
              loading={exportLoading === 'users'}
              disabled={!selectedProject || exportLoading !== null}
              onClick={() => handleExport('users')}
            >
              导出人员授权
            </Button>
            <Button
              theme="primary"
              block
              loading={exportLoading === 'all'}
              disabled={!selectedProject || exportLoading !== null}
              onClick={() => handleExport('all')}
            >
              导出完整授权
            </Button>
          </div>
        </div>
        
      </CollapsibleSection>
    </div>
  );
}
