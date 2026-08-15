// 工作台主布局：底部三 Tab（我要摇人 / 系统任务 / 后台管理）= 三视角
// TabBar 的 value 与路由同步，切换时同步 workbench store 的 activeTab，
// 使跨视图联动（goToTab）能正确驱动 TabBar 高亮。
import { Suspense, useEffect, useRef } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { TabBar, TabBarItem, Loading } from 'tdesign-mobile-react';
import { Radio, LayoutGrid, Gauge } from 'lucide-react';
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

  // 外层滚动容器（.tabbar-shell__content）：Dashboard(/admin) 直接渲染在这里，
  // 而 AdminLayout 子页(/admin/*) 又嵌套在此容器内。从 Dashboard 滚到底部再点进
  // 子页时，外层容器的 scrollTop 被保留，导致整个 AdminLayout 被推到视口外，
  // 子页自身重置 scrollTop 也救不回来。路由切换时一并重置外层。
  const contentRef = useRef<HTMLDivElement>(null);

  // 路由变化 → 同步 store（直接访问 URL / 浏览器后退时保持高亮正确）
  useEffect(() => {
    setActiveTab(pathToTab(location.pathname));
  }, [location.pathname, setActiveTab]);

  useEffect(() => {
    if (contentRef.current) contentRef.current.scrollTop = 0;
  }, [location.pathname]);

  const handleChange = (value: string | number) => {
    const tab = String(value) as WorkbenchTab;
    setActiveTab(tab);
    navigate(TAB_PATHS[tab]);
  };

  return (
    <div className="tabbar-shell">
      <div ref={contentRef} className="tabbar-shell__content">
        <Suspense fallback={<Loading text="加载中..." />}>
          <Outlet />
        </Suspense>
      </div>
      {/* 底部三 Tab：lucide 线性图标（Radio/LayoutGrid/Gauge，与设计稿一致） + 文字，
          玻璃栏 + 36×36 圆角方形胶囊激活态见 global.css */}
      <TabBar value={activeTab} onChange={handleChange} placeholder>
        <TabBarItem value="call" icon={<Radio size={18} strokeWidth={2} />}>
          我要摇人
        </TabBarItem>
        <TabBarItem value="tasks" icon={<LayoutGrid size={18} strokeWidth={2} />}>
          系统任务
        </TabBarItem>
        <TabBarItem value="admin" icon={<Gauge size={18} strokeWidth={2} />}>
          后台管理
        </TabBarItem>
      </TabBar>
    </div>
  );
}