// 工作台主布局：底部三 Tab（我要摇人 / 系统任务 / 后台管理）= 三视角
// TabBar 的 value 与路由同步，切换时同步 workbench store 的 activeTab，
// 使跨视图联动（goToTab）能正确驱动 TabBar 高亮。
import { Suspense, useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { TabBar, TabBarItem, Loading } from 'tdesign-mobile-react';
import { useWorkbenchStore, type WorkbenchTab } from '@/stores/workbench';

const TAB_PATHS: Record<WorkbenchTab, string> = {
  call: '/call',
  tasks: '/tasks',
  admin: '/admin',
};

/** 由当前路径反推激活的 Tab */
function pathToTab(pathname: string): WorkbenchTab {
  if (pathname.startsWith('/tasks')) return 'tasks';
  if (pathname.startsWith('/admin')) return 'admin';
  return 'call';
}

export default function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const activeTab = useWorkbenchStore((s) => s.activeTab);
  const setActiveTab = useWorkbenchStore((s) => s.setActiveTab);

  // 路由变化 → 同步 store（直接访问 URL / 浏览器后退时保持高亮正确）
  useEffect(() => {
    setActiveTab(pathToTab(location.pathname));
  }, [location.pathname, setActiveTab]);

  const handleChange = (value: string | number) => {
    const tab = String(value) as WorkbenchTab;
    setActiveTab(tab);
    navigate(TAB_PATHS[tab]);
  };

  return (
    <div className="tabbar-shell">
      <div className="tabbar-shell__content">
        <Suspense fallback={<Loading text="加载中..." />}>
          <Outlet />
        </Suspense>
      </div>
      <TabBar value={activeTab} onChange={handleChange}>
        <TabBarItem value="call" icon={<span>🆘</span>}>
          我要摇人
        </TabBarItem>
        <TabBarItem value="tasks" icon={<span>📥</span>}>
          系统任务
        </TabBarItem>
        <TabBarItem value="admin" icon={<span>📊</span>}>
          后台管理
        </TabBarItem>
      </TabBar>
    </div>
  );
}