import { Suspense, useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { NavBar, TabBar, Loading } from 'tdesign-mobile-react';
import type { ReactNode } from 'react';

interface AdminLayoutProps {
  children?: ReactNode;
}

// 后台管理侧边栏菜单配置
const adminMenuItems = [
  { path: '/admin/dashboard', label: '仪表盘', icon: 'dashboard' },
  { path: '/admin/data-import', label: '数据导入', icon: 'upload' },
  { path: '/admin/operation-logs', label: '操作记录', icon: 'file-paste' },
  { path: '/admin/progress', label: '进度看板', icon: 'chart-bar' },
  { path: '/admin/personnel', label: '人员分配', icon: 'usergroup' },
  { path: '/admin/project-auth', label: '项目授权', icon: 'secured' },
  { path: '/admin/project-edit', label: '项目编辑', icon: 'edit' },
  { path: '/admin/project-hr', label: '人力资源', icon: 'user-list' },
  { path: '/admin/project-manage', label: '项目管理', icon: 'folder-open' },
  { path: '/admin/risks', label: '风险管理', icon: 'error-circle' },
  { path: '/admin/reports', label: '报表分析', icon: 'chart-pie' },
  { path: '/admin/users', label: '用户管理', icon: 'user' },
  { path: '/admin/roles', label: '角色管理', icon: 'usergroup-clear' },
  { path: '/admin/permissions', label: '权限管理', icon: 'lock-on' },
  { path: '/admin/resources', label: '资源管理', icon: 'folder' },
];

export default function AdminLayout({ children }: AdminLayoutProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [menuVisible, setMenuVisible] = useState(false);

  const currentLabel = adminMenuItems.find((item) => item.path === location.pathname)?.label || '后台管理';

  return (
    <div className="mobile-shell" style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <NavBar
        title={currentLabel}
        leftIcon
        onLeftClick={() => setMenuVisible(!menuVisible)}
        fixed
      />
      <div style={{ flex: 1, overflow: 'auto', paddingTop: 48, paddingBottom: 60 }}>
        <Suspense fallback={<Loading text="加载中..." />}>
          {children || <Outlet />}
        </Suspense>
      </div>
      <TabBar fixed value={location.pathname} onChange={(value) => navigate(String(value))}>
        <TabBar.TabBarItem value="/call/ai-chat" icon={() => <span>💬</span>}>
          摇人
        </TabBar.TabBarItem>
        <TabBar.TabBarItem value="/tasks" icon={() => <span>📋</span>}>
          任务
        </TabBar.TabBarItem>
        <TabBar.TabBarItem value="/admin/dashboard" icon={() => <span>⚙️</span>}>
          管理
        </TabBar.TabBarItem>
      </TabBar>
    </div>
  );
}
