import React from 'react';
import ReactDOM from 'react-dom/client';
import { RouterProvider, createBrowserRouter, Navigate, Outlet } from 'react-router-dom';
import { AuthGuard } from '@/shared/utils/authGuard';
import { RAW_BASE } from '@/config/api';
import 'tdesign-mobile-react/es/style/index.css';
import '@/shared/styles/global.css';

// 初始化认证状态
import { useAuthStore } from '@/stores/auth';
useAuthStore.getState().checkLoginStatus();

// 懒加载页面
const Login = React.lazy(() => import('@/pages/Login'));
const NoPermission = React.lazy(() => import('@/pages/NoPermission'));
const MainLayout = React.lazy(() => import('@/shared/components/MainLayout'));
const CallView = React.lazy(() => import('@/pages/call/CallView'));
const TicketDetailPage = React.lazy(() => import('@/pages/call/TicketDetailPage'));
const TasksView = React.lazy(() => import('@/pages/tasks/TasksView'));

const AdminView = React.lazy(() => import('@/pages/admin/AdminView'));
const AdminLayout = React.lazy(() => import('@/shared/components/AdminLayout'));
const ProjectMetrics = React.lazy(() => import('@/pages/admin/ProjectMetrics'));
const DataImport = React.lazy(() => import('@/pages/admin/DataImport'));
const OperationLogs = React.lazy(() => import('@/pages/admin/OperationLogs'));
const ProgressBoard = React.lazy(() => import('@/pages/admin/ProgressBoard'));
const PersonnelBoard = React.lazy(() => import('@/pages/admin/PersonnelBoard'));
const ProjectAuth = React.lazy(() => import('@/pages/admin/ProjectAuth'));
const ProjectEdit = React.lazy(() => import('@/pages/admin/ProjectEdit'));
const ProjectHR = React.lazy(() => import('@/pages/admin/ProjectHR'));
const ProjectManage = React.lazy(() => import('@/pages/admin/ProjectManage'));
const RiskList = React.lazy(() => import('@/pages/admin/RiskList'));
const RiskEdit = React.lazy(() => import('@/pages/admin/RiskEdit'));
const ReportsAnalytics = React.lazy(() => import('@/pages/admin/ReportsAnalytics'));
const UserManage = React.lazy(() => import('@/pages/admin/UserManage'));
const RoleManage = React.lazy(() => import('@/pages/admin/RoleManage'));
const PermissionManage = React.lazy(() => import('@/pages/admin/PermissionManage'));
const ResourceManage = React.lazy(() => import('@/pages/admin/ResourceManage'));
const DailyReportManage = React.lazy(() => import('@/pages/admin/DailyReportManage'));
const WechatManage = React.lazy(() => import('@/pages/admin/WechatManage'));

const router = createBrowserRouter([
  { path: '/login', element: <Login /> },
  { path: '/no-permission', element: <NoPermission /> },

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
], {
  basename: RAW_BASE,
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>
);
