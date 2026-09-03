// 工作台主布局：底部三 Tab（我要摇人 / 系统任务 / 后台管理）= 三视角
// 底部导航与路由同步，切换时同步 workbench store 的 activeTab，
// 使跨视图联动（goToTab）能正确驱动导航高亮。
// 导航样式参考 macaron-minimal-ui 的 BottomNav：玻璃条 + 选中淡蓝圆角块 + 描边图标。
// 「系统任务」按钮右上角展示「待我处理」工单数角标（口径与系统任务页一致）。
import { Suspense, useEffect, useRef, useState, useCallback } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Loading } from 'tdesign-mobile-react';
import { useWorkbenchStore, type WorkbenchTab } from '@/stores/workbench';
import { useAuthStore } from '@/stores/auth';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { buildRelevanceFilters } from '@/shared/utils/ticketFilters';
import AdminDataAssistant from '@/shared/components/AdminDataAssistant';

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

/** 底部三 Tab 图标：与 macaron BottomNav 的 lucide 图标同款线条（radio / layout-grid / gauge） */
function NavIcon({ type }: { type: WorkbenchTab }) {
  const common = {
    width: 18,
    height: 18,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
  if (type === 'call') {
    // lucide radio：广播波纹
    return (
      <svg {...common}>
        <path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9" />
        <path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5" />
        <circle cx="12" cy="12" r="2" />
        <path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5" />
        <path d="M19.1 4.9C23 8.8 23 15.2 19.1 19.1" />
      </svg>
    );
  }
  if (type === 'tasks') {
    // lucide layout-grid：四宫格
    return (
      <svg {...common}>
        <rect width="7" height="7" x="3" y="3" rx="1" />
        <rect width="7" height="7" x="14" y="3" rx="1" />
        <rect width="7" height="7" x="14" y="14" rx="1" />
        <rect width="7" height="7" x="3" y="14" rx="1" />
      </svg>
    );
  }
  // lucide gauge：仪表盘
  return (
    <svg {...common}>
      <path d="m12 14 4-4" />
      <path d="M3.34 19a10 10 0 1 1 17.32 0" />
    </svg>
  );
}

const NAV_ITEMS: { tab: WorkbenchTab; label: string }[] = [
  { tab: 'call', label: '我要摇人' },
  { tab: 'tasks', label: '系统任务' },
  { tab: 'admin', label: '后台管理' },
];

export default function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const activeTab = useWorkbenchStore((s) => s.activeTab);
  const setActiveTab = useWorkbenchStore((s) => s.setActiveTab);
  const { username, userId, projectIds } = useAuthStore();

  // 「待我处理」工单数角标：与系统任务页共用同一相关性过滤口径（size=1 只取 total），
  // 每 30 秒刷新；无用户名时无法计算「待我处理」，不展示角标。
  const [mineTicketCount, setMineTicketCount] = useState<number | null>(null);

  const fetchMineTicketCount = useCallback(async () => {
    if (!username && !userId) return;
    try {
      const request = createRequest(API_CONFIG.TASKS.BASE_URL, 'Tasks');
      const data = await request<{ total: number }>('/filter', {
        method: 'POST',
        body: JSON.stringify({
          filters: buildRelevanceFilters('mine', userId || username, projectIds),
          sorts: [],
          page: 1,
          size: 1,
        }),
        skipCache: true,
      });
      setMineTicketCount(data.total);
    } catch {
      // 计数失败保留旧角标，不打扰页面
    }
  }, [username, userId, projectIds]);

  useEffect(() => {
    fetchMineTicketCount();
    const timer = window.setInterval(fetchMineTicketCount, 30000);
    return () => window.clearInterval(timer);
  }, [fetchMineTicketCount]);

  // 外层滚动容器（.tabbar-shell__content）：Dashboard(/admin) 直接渲染在这里，
  // 而 AdminLayout 子页(/admin/*) 又嵌套在此容器内。从 Dashboard 滚到底部再点进
  // 子页时，外层容器的 scrollTop 被保留，导致整个 AdminLayout 被推到视口外，
  // 子页自身重置 scrollTop 也救不回来。路由切换时一并重置外层。
  const contentRef = useRef<HTMLDivElement>(null);

  // 路由变化 → 同步 store（直接访问 URL / 浏览器后退时保持高亮正确）
  useEffect(() => {
    console.log('[MainLayout] route changed →', location.pathname);
    setActiveTab(pathToTab(location.pathname));
  }, [location.pathname, setActiveTab]);

  useEffect(() => {
    if (contentRef.current) contentRef.current.scrollTop = 0;
  }, [location.pathname]);

  const handleChange = (tab: WorkbenchTab) => {
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
      {/* 底部三 Tab：马卡龙极简风（参考 macaron BottomNav） */}
      <nav className="app-bottom-nav" data-testid="app-bottom-nav">
        <div className="app-bottom-nav__items">
          {NAV_ITEMS.map(({ tab, label }) => (
            <button
              key={tab}
              type="button"
              data-testid={`nav-item-${tab}`}
              className={`app-bottom-nav__item ${activeTab === tab ? 'is-active' : ''}`}
              onClick={() => handleChange(tab)}
            >
              <span className="app-bottom-nav__icon">
                <NavIcon type={tab} />
                {/* 系统任务：图标右上角「待我处理」数量角标（蓝底白字，>0 时展示） */}
                {tab === 'tasks' && mineTicketCount != null && mineTicketCount > 0 && (
                  <span className="app-bottom-nav__badge" data-testid="nav-badge-tasks">
                    {mineTicketCount > 999 ? '999+' : mineTicketCount}
                  </span>
                )}
              </span>
              <span className="app-bottom-nav__label">{label}</span>
            </button>
          ))}
        </div>
      </nav>
      {/* 后台管理：AI 数据助手入口（UI 原型，仅 /admin 下渲染，组件内部自判路由） */}
      <AdminDataAssistant />
    </div>
  );
}
