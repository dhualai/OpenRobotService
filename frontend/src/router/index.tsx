import { lazy } from 'react';
import { createBrowserRouter, Navigate, Outlet } from 'react-router-dom';
import { AuthGuard } from '@/shared/utils/authGuard';

// 懒加载页面
const Login = lazy(() => import('@/pages/Login'));
const NoPermission = lazy(() => import('@/pages/NoPermission'));
const MainLayout = lazy(() => import('@/shared/components/MainLayout'));
const CallView = lazy(() => import('@/pages/call/CallView'));
const TasksView = lazy(() => import('@/pages/tasks/TasksView'));

// Admin
const AdminView = lazy(() => import('@/pages/admin/AdminView'));
const AdminLayout = lazy(() => import('@/shared/components/AdminLayout'));
const ProjectMetrics = lazy(() => import('@/pages/admin/ProjectMetrics'));
const DataImport = lazy(() => import('@/pages/admin/DataImport'));
const OperationLogs = lazy(() => import('@/pages/admin/OperationLogs'));
const ProgressBoard = lazy(() => import('@/pages/admin/ProgressBoard'));
const PersonnelBoard = lazy(() => import('@/pages/admin/PersonnelBoard'));
const ProjectAuth = lazy(() => import('@/pages/admin/ProjectAuth'));
const ProjectEdit = lazy(() => import('@/pages/admin/ProjectEdit'));
const ProjectHR = lazy(() => import('@/pages/admin/ProjectHR'));
const ProjectManage = lazy(() => import('@/pages/admin/ProjectManage'));
const RiskList = lazy(() => import('@/pages/admin/RiskList'));
const RiskEdit = lazy(() => import('@/pages/admin/RiskEdit'));
const ReportsAnalytics = lazy(() => import('@/pages/admin/ReportsAnalytics'));
const UserManage = lazy(() => import('@/pages/admin/UserManage'));
const RoleManage = lazy(() => import('@/pages/admin/RoleManage'));
const PermissionManage = lazy(() => import('@/pages/admin/PermissionManage'));
const ResourceManage = lazy(() => import('@/pages/admin/ResourceManage'));
const DailyReportManage = lazy(() => import('@/pages/admin/DailyReportManage'));

export const router = createBrowserRouter([
  { path: '/login', element: <Login /> },
  { path: '/no-permission', element: <NoPermission /> },

  // 工作台主入口：底部三 Tab（我要摇人 / 系统任务 / 后台管理）
  {
    path: '/app',
    element: <AuthGuard><MainLayout /></AuthGuard>,
    children: [
      { index: true, element: <Navigate to="/app/call" replace /> },
      { path: 'call', element: <CallView /> },
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
]);
