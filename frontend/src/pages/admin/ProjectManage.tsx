// 项目管理（二级页面）—— 「项目导入」「项目授权」两部分
// ProjectImport: 项目增删改查
// 项目授权区：项目选择器 + ProjectAuth 授权记录 + ProjectPeople 人员关联
// 样式参考 macaron projects.auth 页：surface-card 折叠区 + 嵌套折叠区 + 弹层项目选择。
import { useEffect, useState, type ReactNode } from 'react';
import { Loading, Toast, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';
import ProjectImport from './ProjectImport';
import ProjectAuth from './ProjectAuth';
import ProjectPeople from './ProjectPeople';
import {
  MacChevronDown, MacChevronRight, MacSearch, MacCheck,
  MacFolderClosed, MacWallet, MacKeyRound, MacUsers,
} from '@/shared/components/macaronIcons';

interface Project { id?: string; project_code?: string; name: string; }

// 可折叠区域：点击标题切换展开/收起（参考 macaron CollapsibleSection：
// card/nested 两变体 + grid-template-rows 高度过渡动画）
const CollapsibleSection = ({ icon, title, variant = 'card', open, onToggle, children }: {
  icon: ReactNode;
  title: string;
  variant?: 'card' | 'nested';
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) => (
  <div className={`mac-collapsible ${variant === 'card' ? 'mac-collapsible--card' : 'mac-collapsible--nested'} ${open ? 'is-open' : ''}`}>
    <button type="button" className="mac-collapsible__header" onClick={onToggle}>
      <span className="mac-collapsible__icon">{icon}</span>
      <span className="mac-collapsible__title">{title}</span>
      <span className="mac-collapsible__chevron"><MacChevronDown size={16} /></span>
    </button>
    <div className="mac-collapsible__bodywrap">
      <div className="mac-collapsible__bodyinner">
        <div className="mac-collapsible__body">{children}</div>
      </div>
    </div>
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
      const blob = new Blob([buffer], { type: 'application/x-bzip2' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `project_${projectCode}_${type}_export.bz2`;
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
    <div className="mac-page" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* 可折叠区域：项目导入 */}
      <CollapsibleSection icon={<MacFolderClosed size={16} />} title="项目导入" open={sectionImportOpen} onToggle={() => setSectionImportOpen((v) => !v)}>
        <ProjectImport />
      </CollapsibleSection>

      {/* 可折叠区域：项目授权（含项目选择器 + 授权记录 + 人员关联） */}
      <CollapsibleSection icon={<MacWallet size={16} />} title="项目授权" open={sectionAuthOpen} onToggle={() => setSectionAuthOpen((v) => !v)}>
        {/* 项目选择器（已从页面顶部下移至此） */}
        <div style={{ paddingBottom: 12 }}>
          {projectLoading ? <Loading text="加载项目..." /> : (
            <button
              type="button"
              className="mac-selector mac-selector--soft"
              onClick={() => setProjectPickerVisible(true)}
            >
              <span className="mac-selector__body">
                {selectedProject ? (
                  <>
                    <span className="mac-selector__name">{selectedProject.name}</span>
                    {selectedProject.project_code && (
                      <span className="mac-selector__meta">项目代码：{selectedProject.project_code}</span>
                    )}
                  </>
                ) : (
                  <span className="mac-selector__placeholder">请选择项目</span>
                )}
              </span>
              <span className="mac-selector__chevron"><MacChevronRight size={16} /></span>
            </button>
          )}
        </div>

        <CollapsibleSection variant="nested" icon={<MacKeyRound size={16} />} title="项目licences授权" open={subLicensesOpen} onToggle={() => setSubLicensesOpen((v) => !v)}>
          <ProjectAuth selectedProject={selectedProject} />
        </CollapsibleSection>

        <CollapsibleSection variant="nested" icon={<MacUsers size={16} />} title="项目人员授权" open={subPeopleOpen} onToggle={() => setSubPeopleOpen((v) => !v)}>
          <ProjectPeople selectedProject={selectedProject} />
        </CollapsibleSection>

        {/* 导出按钮组 */}
        <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8, borderTop: '1px solid rgba(232,234,234,0.6)', paddingTop: 12 }}>
          <button
            type="button"
            className="mac-btn mac-btn--outline mac-btn--block"
            disabled={!selectedProject || exportLoading !== null}
            onClick={() => handleExport('license')}
          >
            {exportLoading === 'license' ? '导出中...' : '导出licence授权'}
          </button>
          <button
            type="button"
            className="mac-btn mac-btn--outline mac-btn--block"
            disabled={!selectedProject || exportLoading !== null}
            onClick={() => handleExport('users')}
          >
            {exportLoading === 'users' ? '导出中...' : '导出人员授权'}
          </button>
          <button
            type="button"
            className="mac-btn mac-btn--primary mac-btn--block"
            disabled={!selectedProject || exportLoading !== null}
            onClick={() => handleExport('all')}
          >
            {exportLoading === 'all' ? '导出中...' : '导出完整授权'}
          </button>
        </div>
      </CollapsibleSection>

      {/* 项目选择弹层 */}
      <Popup visible={projectPickerVisible} onClose={() => setProjectPickerVisible(false)} placement="bottom" showOverlay>
        <div className="mac-sheet" style={{ maxHeight: '70vh', overflow: 'auto' }}>
          <h4 className="mac-sheet__title">选择项目</h4>
          <div className="mac-search" style={{ marginBottom: 12 }}>
            <MacSearch size={16} />
            <input
              className="mac-search__input"
              value={projectSearch}
              onChange={(e) => setProjectSearch(e.target.value)}
              placeholder="输入项目名称关键词模糊查找"
            />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {filteredProjects.map((p) => {
              const active = selectedProject?.name === p.name;
              return (
                <button
                  key={p.id || p.name}
                  type="button"
                  className={`mac-pick-item ${active ? 'is-active' : ''}`}
                  onClick={() => handleProjectSelect(p)}
                >
                  <span className="mac-pick-item__name">{p.name}</span>
                  {p.project_code && <span className="mac-pick-item__code">#{p.project_code}</span>}
                  {active && (
                    <span className="mac-pick-item__check"><MacCheck size={16} /></span>
                  )}
                </button>
              );
            })}
            {filteredProjects.length === 0 && (
              <div className="mac-empty">未找到匹配的项目</div>
            )}
          </div>
        </div>
      </Popup>
    </div>
  );
}
