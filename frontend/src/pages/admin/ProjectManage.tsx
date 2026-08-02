// 项目管理（二级页面）—— 「项目导入」「项目授权」两部分
// ProjectImport: 项目增删改查
// 项目授权区：项目选择器 + ProjectAuth 授权记录 + ProjectPeople 人员关联
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Loading, Toast, Input, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';
import ProjectImport from './ProjectImport';
import ProjectAuth from './ProjectAuth';
import ProjectPeople from './ProjectPeople';

interface Project { id?: string; code?: string; name: string; }

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
  const rootRef = useRef<HTMLDivElement>(null);
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

  // 加载项目列表：canViewAll 时获取全部项目，否则仅当前用户关联项目（/projects/me 按 token 过滤）
  useEffect(() => {
    request(canViewAll ? '/projects/' : '/projects/me')
      .then((data) => setProjects(normalizeList<Project>(data)))
      .catch((err) => Toast({ message: `加载项目失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }))
      .finally(() => setProjectLoading(false));
  }, [canViewAll]);

  // 进入页面时滚动到最上端（延迟到下一帧，确保布局已完成）
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      let el: HTMLElement | null = rootRef.current;
      while (el) {
        const style = window.getComputedStyle(el);
        if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
          el.scrollTop = 0;
          break;
        }
        el = el.parentElement;
      }
      window.scrollTo(0, 0);
    });
    return () => cancelAnimationFrame(raf);
  }, []);

  const handleProjectSelect = (project: Project) => {
    setSelectedProject(project);
    setProjectSearch('');
    setProjectPickerVisible(false);
  };

  const filteredProjects = projectSearch.trim()
    ? projects.filter((p) => {
        const kw = projectSearch.trim().toLowerCase();
        return p.name.toLowerCase().includes(kw) || (p.code || '').toLowerCase().includes(kw);
      })
    : projects;

  return (
    <div ref={rootRef} style={{ padding: '16px 16px 24px' }}>
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
                    {selectedProject.code && <div style={{ fontSize: 12, color: '#999' }}>项目代码：{selectedProject.code}</div>}
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
                  {p.code && <div style={{ fontSize: 12, color: '#999' }}>项目代码：{p.code}</div>}
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
        </div>
      </CollapsibleSection>
    </div>
  );
}
