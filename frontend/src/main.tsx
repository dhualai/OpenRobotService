import React from 'react';
import ReactDOM from 'react-dom/client';
import { RouterProvider, createBrowserRouter, Navigate, Outlet, useRouteError } from 'react-router-dom';
import { AuthGuard } from '@/shared/utils/authGuard';
import { RAW_BASE } from '@/config/api';
import 'tdesign-mobile-react/es/style/index.css';
import '@/shared/styles/global.css';

// 初始化认证状态
import { useAuthStore } from '@/stores/auth';
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

const AdminView = lazyImport(() => import('@/pages/admin/AdminView'));
const AdminLayout = lazyImport(() => import('@/shared/components/AdminLayout'));
const ProjectMetrics = lazyImport(() => import('@/pages/admin/ProjectMetrics'));
const DataImport = lazyImport(() => import('@/pages/admin/DataImport'));
const OperationLogs = lazyImport(() => import('@/pages/admin/OperationLogs'));
const ProgressBoard = lazyImport(() => import('@/pages/admin/ProgressBoard'));
const PersonnelBoard = lazyImport(() => import('@/pages/admin/PersonnelBoard'));
const ProjectAuth = lazyImport(() => import('@/pages/admin/ProjectAuth'));
const ProjectEdit = lazyImport(() => import('@/pages/admin/ProjectEdit'));
const ProjectHR = lazyImport(() => import('@/pages/admin/ProjectHR'));
const ProjectManage = lazyImport(() => import('@/pages/admin/ProjectManage'));
const RiskList = lazyImport(() => import('@/pages/admin/RiskList'));
const RiskEdit = lazyImport(() => import('@/pages/admin/RiskEdit'));
const ReportsAnalytics = lazyImport(() => import('@/pages/admin/ReportsAnalytics'));
const UserManage = lazyImport(() => import('@/pages/admin/UserManage'));
const RoleManage = lazyImport(() => import('@/pages/admin/RoleManage'));
const PermissionManage = lazyImport(() => import('@/pages/admin/PermissionManage'));
const ResourceManage = lazyImport(() => import('@/pages/admin/ResourceManage'));
const DailyReportManage = lazyImport(() => import('@/pages/admin/DailyReportManage'));
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
        path: '/app',
        element: <AuthGuard><MainLayout /></AuthGuard>,
        children: [
          { index: true, element: <Navigate to="/app/call" replace /> },
          { path: 'call', element: <CallView /> },
          { path: 'call/ticket/:id', element: <TicketDetailPage /> },
          { path: 'tasks', element: <TasksView /> },
          { path: 'tasks/:id', element: <TasksView /> },
          {
            path: 'admin',
            element: <Outlet />,
            children: [
              { index: true, element: <AdminView /> },
              {
                element: <AdminLayout />,
                children: [
                  { path: 'dashboard', element: <ProjectMetrics /> },
                  { path: 'data-import', element: <DataImport /> },
                  { path: 'operation-logs', element: <OperationLogs /> },
                  { path: 'progress', element: <ProgressBoard /> },
                  { path: 'personnel', element: <PersonnelBoard /> },
                  { path: 'project-auth', element: <ProjectAuth /> },
                  { path: 'project-edit/:id?', element: <ProjectEdit /> },
                  { path: 'project-hr', element: <ProjectHR /> },
                  { path: 'project-manage', element: <ProjectManage /> },
                  { path: 'risks', element: <RiskList /> },
                  { path: 'risk-edit/:id?', element: <RiskEdit /> },
                  { path: 'reports', element: <ReportsAnalytics /> },
                  { path: 'users', element: <UserManage /> },
                  { path: 'roles', element: <RoleManage /> },
                  { path: 'permissions', element: <PermissionManage /> },
                  { path: 'resources', element: <ResourceManage /> },
                  { path: 'daily-reports', element: <DailyReportManage /> },
                  { path: 'wechat', element: <WechatManage /> },
                ],
              },
            ],
          },
        ],
      },

      // 兼容旧路由 → 统一收敛到工作台
      { path: '/', element: <Navigate to="/app/call" replace /> },
      { path: '/call', element: <Navigate to="/app/call" replace /> },
      { path: '/call/ai-chat', element: <Navigate to="/app/call" replace /> },
      { path: '/call/new-ticket', element: <Navigate to="/app/call" replace /> },
      { path: '/tasks', element: <Navigate to="/app/tasks" replace /> },
      { path: '/tasks/:id', element: <Navigate to="/app/tasks" replace /> },
      { path: '/admin', element: <Navigate to="/app/admin" replace /> },
      { path: '/admin/*', element: <Navigate to="/app/admin" replace /> },
      { path: '/home', element: <Navigate to="/app/call" replace /> },
      { path: '*', element: <Navigate to="/app/call" replace /> },
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
