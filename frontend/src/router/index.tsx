import { lazy } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import { AuthGuard } from '@/shared/utils/authGuard';

// 懒加载页面
const Login = lazy(() => import('@/pages/Login'));
const NoPermission = lazy(() => import('@/pages/NoPermission'));
const AIChat = lazy(() => import('@/pages/call/AIChat'));
const NewTicket = lazy(() => import('@/pages/call/NewTicket'));
const TicketList = lazy(() => import('@/pages/tasks/TicketList'));
const TicketDetail = lazy(() => import('@/pages/tasks/TicketDetail'));

// Admin 页面
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
const ResourceManage = lazy(() => import('@/pages/admin/ResourceManage');

const AdminLayout = lazy(() => import('@/shared/components/AdminLayout'));

export const router = createBrowserRouter([
  { path: '/login', element: <Login /> },
  { path: '/no-permission', element: <NoPermission /> },

  // call 模块
  {
    path: '/call',
    element: <AuthGuard><div className="mobile-shell" /></AuthGuard>,
    children: [
      { index: true, element: <Navigate to="/call/ai-chat" replace /> },
      { path: 'ai-chat', element: <AIChat /> },
      { path: 'new-ticket', element: <NewTicket /> },
    ],
  },

  // tasks 模块
  {
    path: '/tasks',
    element: <AuthGuard><div className="mobile-shell" /></AuthGuard>,
    children: [
      { index: true, element: <TicketList /> },
      { path: ':id', element: <TicketDetail /> },
    ],
  },

  // admin 模块
  {
    path: '/admin',
    element: <AuthGuard requireAdmin><AdminLayout /></AuthGuard>,
    children: [
      { index: true, element: <Navigate to="/admin/dashboard" replace /> },
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
    ],
  },

  { path: '/', element: <Navigate to="/call" replace /> },
  { path: '*', element: <Navigate to="/call" replace /> },
]);
