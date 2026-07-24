import React from 'react';
import ReactDOM from 'react-dom/client';
import { RouterProvider, createBrowserRouter, Navigate, Outlet, useRouteError } from 'react-router-dom';
import { AuthGuard } from '@/shared/utils/authGuard';
import { RAW_BASE } from '@/config/api';
import 'tdesign-mobile-react/es/style/index.css';
import '@/shared/styles/global.css';

// 初始化认证状态
import { useAuthStore } from '@/stores/auth';
import { checkUrlTokens } from '@/shared/utils/url';
// 必须在 RouterProvider 渲染前从 URL 提取 token：访问 /p/app/call?token=... 这类
// 只有单层 /app 的链接时，会命中旧路由 <Navigate> 重定向到 /app/call，query 会被丢弃，
// 导致 AuthGuard 里的 checkUrlTokens 再也取不到 token。提前在这里取出存入 localStorage，
// 再恢复登录态，带 token 的直链即可直接登录。
checkUrlTokens();
useAuthStore.getState().checkLoginStatus();

// 懒加载兜底：部署后旧 chunk hash 失效，import() 会 reject。
//   - lazyImport：失败时自动刷新页面（取最新 index.html，含新 chunk 引用），
//                 sessionStorage 限 10s 内只刷一次，防死循环。
//   - GlobalError：渲染层兜底，给用户「刷新重试」按钮，避免白屏。
const CHUNK_FAIL_RELOAD_KEY = 'chunk-fail-reload-at';
function isChunkLoadError(msg: unknown): boolean {
  return (
    typeof msg === 'string' &&
    (msg.includes('Failed to fetch dynamically imported module') ||
      msg.includes('Importing a module script failed'))
  );
}
function lazyImport<T extends React.ComponentType<any>>(loader: () => Promise<{ default: T }>) {
  return React.lazy(async () => {
    try {
      return await loader();
    } catch (err) {
      if (isChunkLoadError(err instanceof Error ? err.message : String(err))) {
        const last = Number(sessionStorage.getItem(CHUNK_FAIL_RELOAD_KEY) || 0);
        if (Date.now() - last > 10000) {
          sessionStorage.setItem(CHUNK_FAIL_RELOAD_KEY, String(Date.now()));
          window.location.reload();
          // 等待 reload；返回永不 resolve 的 Promise 让类型自洽、不再抛错
          return await new Promise<{ default: T }>(() => {});
        }
      }
      throw err;
    }
  });
}

function GlobalError() {
  const error = useRouteError();
  const isChunkFail = isChunkLoadError(error instanceof Error ? error.message : String(error));
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh',
        padding: 24,
        textAlign: 'center',
        color: '#333',
      }}
    >
      <div style={{ fontSize: 40, marginBottom: 8 }}>{isChunkFail ? '🔄' : '⚠️'}</div>
      <p style={{ fontSize: 16, fontWeight: 600 }}>{isChunkFail ? '页面版本已更新' : '页面出错了'}</p>
      <p style={{ fontSize: 13, color: '#999', maxWidth: 320, marginTop: 4 }}>
        {isChunkFail ? '系统已更新，刷新即可加载最新版本。' : '加载遇到问题，请尝试刷新页面。'}
      </p>
      <button
        onClick={() => window.location.reload()}
        style={{
          marginTop: 16,
          padding: '8px 24px',
          borderRadius: 6,
          border: 'none',
          background: '#0052d9',
          color: '#fff',
          fontSize: 15,
          cursor: 'pointer',
        }}
      >
        刷新重试
      </button>
    </div>
  );
}

// 懒加载页面
const Login = lazyImport(() => import('@/pages/Login'));
const NoPermission = lazyImport(() => import('@/pages/NoPermission'));
const MainLayout = lazyImport(() => import('@/shared/components/MainLayout'));
const CallView = lazyImport(() => import('@/pages/call/CallView'));
const TicketDetailPage = lazyImport(() => import('@/pages/call/TicketDetailPage'));
const TasksView = lazyImport(() => import('@/pages/tasks/TasksView'));
const TaskDetailPage = lazyImport(() => import('@/pages/tasks/TaskDetailPage'));

const Dashboard = lazyImport(() => import('@/pages/admin/Dashboard'));
const AdminEntries = lazyImport(() => import('@/pages/admin/AdminEntries'));
const AdminLayout = lazyImport(() => import('@/shared/components/AdminLayout'));

// 仪表盘下钻明细
const TicketStatusDetail = lazyImport(() => import('@/pages/admin/TicketStatusDetail'));
const ProjectCategoryDetail = lazyImport(() => import('@/pages/admin/ProjectCategoryDetail'));
const ProjectDetail = lazyImport(() => import('@/pages/admin/ProjectDetail'));

// 三大核心功能（明细列表页）
const TicketMonitor = lazyImport(() => import('@/pages/admin/TicketMonitor'));
const ProjectProgress = lazyImport(() => import('@/pages/admin/ProjectProgress'));

// 次级功能
const ProjectMetrics = lazyImport(() => import('@/pages/admin/ProjectMetrics'));
const DataImport = lazyImport(() => import('@/pages/admin/DataImport'));
const OperationLogs = lazyImport(() => import('@/pages/admin/OperationLogs'));
// const ProgressBoard = lazyImport(() => import('@/pages/admin/ProgressBoard'));   // 已并入 ProjectProgress
// const PersonnelBoard = lazyImport(() => import('@/pages/admin/PersonnelBoard')); // 已从导航移除
const ProjectEdit = lazyImport(() => import('@/pages/admin/ProjectEdit'));
// const ProjectHR = lazyImport(() => import('@/pages/admin/ProjectHR'));           // 已从导航移除
// 项目管理二级页面：上（项目导入 ProjectImport）+ 下（项目授权 ProjectAuth）并列，
// 两个子页面由 ProjectManage.tsx 内部静态引入，不再各自单独挂路由。
const ProjectManage = lazyImport(() => import('@/pages/admin/ProjectManage'));
const RiskList = lazyImport(() => import('@/pages/admin/RiskList'));
const RiskEdit = lazyImport(() => import('@/pages/admin/RiskEdit'));
const ReportsAnalytics = lazyImport(() => import('@/pages/admin/ReportsAnalytics'));
const UserManage = lazyImport(() => import('@/pages/admin/UserManage'));
const RoleManage = lazyImport(() => import('@/pages/admin/RoleManage'));
const PermissionManage = lazyImport(() => import('@/pages/admin/PermissionManage'));
const ResourceManage = lazyImport(() => import('@/pages/admin/ResourceManage'));
const DailyReportManage = lazyImport(() => import('@/pages/admin/DailyReportManage'));
const DailySummaryAgent = lazyImport(() => import('@/pages/admin/DailySummaryAgent'));
const WechatManage = lazyImport(() => import('@/pages/admin/WechatManage'));

const router = createBrowserRouter([
  {
    // 根级错误边界：捕获任意子路由渲染错误（含懒加载 chunk 失效），
    // 兜底展示「刷新重试」，避免 React Router 默认 ErrorBoundary 的白屏。
    errorElement: <GlobalError />,
    children: [
      { path: '/login', element: <Login /> },
      { path: '/no-permission', element: <NoPermission /> },

      // 工作台主入口：底部三 Tab（我要摇人 / 系统任务 / 后台管理）
      {
        path: '/',
        element: <AuthGuard><MainLayout /></AuthGuard>,
        children: [
          { index: true, element: <Navigate to="/call" replace /> },
          { path: 'call', element: <CallView /> },
          { path: 'call/ticket/:id', element: <TicketDetailPage /> },
          { path: 'tasks', element: <TasksView /> },
          { path: 'tasks/:id', element: <TaskDetailPage /> },
          {
            path: 'admin',
            element: <Outlet />,
            children: [
              // 默认首页：上中下三段式仪表盘（工单状态 / 跨项目看板 / 待补）
              { index: true, element: <Dashboard /> },
              // 仪表盘下钻明细：点击状态/分类标签后展示对应列表
              { path: 'dashboard/tickets/:status', element: <TicketStatusDetail /> },
              { path: 'dashboard/projects/:dimension/:key', element: <ProjectCategoryDetail /> },
              // 项目详情：点击项目管理列表条目后展示（原样复用项目详情设计稿，见 pages/admin/ProjectDetail.tsx）
              { path: 'project-detail/:id', element: <ProjectDetail /> },
              // 次级入口：三大功能传统列表页 + 管理员工具
              { path: 'entries', element: <AdminEntries /> },
              {
                element: <AdminLayout />,
                children: [
                  // === 三大核心功能 ===
                  { path: 'ticket-monitor', element: <TicketMonitor /> },
                  { path: 'project-progress', element: <ProjectProgress /> },
                  { path: 'daily-reports', element: <DailyReportManage /> },
                  { path: 'daily-summary', element: <DailySummaryAgent /> },

                  // === 次级功能 ===
                  { path: 'dashboard', element: <ProjectMetrics /> },
                  { path: 'data-import', element: <DataImport /> },
                  { path: 'operation-logs', element: <OperationLogs /> },
                  // { path: 'progress', element: <ProgressBoard /> },       // 已并入 ProjectProgress
                  // { path: 'personnel', element: <PersonnelBoard /> },     // 已从导航移除
                  { path: 'project-edit/:id?', element: <ProjectEdit /> },
                  // { path: 'project-hr', element: <ProjectHR /> },         // 已从导航移除
                  // 项目管理二级页面：内部并列展示项目导入 + 项目授权，见 ProjectManage.tsx
                  { path: 'project-manage', element: <ProjectManage /> },
                  { path: 'risks', element: <RiskList /> },
                  { path: 'risk-edit/:id?', element: <RiskEdit /> },
                  { path: 'reports', element: <ReportsAnalytics /> },
                  { path: 'users', element: <UserManage /> },
                  { path: 'roles', element: <RoleManage /> },
                  { path: 'permissions', element: <PermissionManage /> },
                  { path: 'resources', element: <ResourceManage /> },
                  { path: 'wechat', element: <WechatManage /> },
                ],
              },
            ],
          },
        ],
      },

      // 兼容旧路由 → 统一收敛到工作台
      { path: '/call', element: <Navigate to="/call" replace /> },
      { path: '/call/ai-chat', element: <Navigate to="/call" replace /> },
      { path: '/call/new-ticket', element: <Navigate to="/call" replace /> },
      { path: '/tasks', element: <Navigate to="/tasks" replace /> },
      { path: '/admin', element: <Navigate to="/admin" replace /> },
      { path: '/admin/*', element: <Navigate to="/admin" replace /> },
      { path: '/home', element: <Navigate to="/call" replace /> },
      { path: '*', element: <Navigate to="/call" replace /> },
    ],
  },
], {
  basename: RAW_BASE,
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>
);
