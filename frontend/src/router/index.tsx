import { lazy } from 'react';
import { createBrowserRouter, Navigate, Outlet } from 'react-router-dom';
import { AuthGuard } from '@/shared/utils/authGuard';

// 懒加载页面
const Login = lazy(() => import('@/pages/Login'));
const NoPermission = lazy(() => import('@/pages/NoPermission'));
const MainLayout = lazy(() => import('@/shared/components/MainLayout'));
const CallView = lazy(() => import('@/pages/call/CallView'));
const TicketDetailPage = lazy(() => import('@/pages/call/TicketDetailPage'));
const TasksView = lazy(() => import('@/pages/tasks/TasksView'));

// Admin
const Dashboard = lazy(() => import('@/pages/admin/Dashboard'));
const AdminEntries = lazy(() => import('@/pages/admin/AdminEntries'));
const AdminLayout = lazy(() => import('@/shared/components/AdminLayout'));

// 仪表盘下钻明细
const TicketStatusDetail = lazy(() => import('@/pages/admin/TicketStatusDetail'));
const ProjectCategoryDetail = lazy(() => import('@/pages/admin/ProjectCategoryDetail'));

// 三大核心功能（明细列表页）
const TicketMonitor = lazy(() => import('@/pages/admin/TicketMonitor'));
const ProjectProgress = lazy(() => import('@/pages/admin/ProjectProgress'));

// 次级功能
const ProjectMetrics = lazy(() => import('@/pages/admin/ProjectMetrics'));
const DataImport = lazy(() => import('@/pages/admin/DataImport'));
const OperationLogs = lazy(() => import('@/pages/admin/OperationLogs'));
// const ProgressBoard = lazy(() => import('@/pages/admin/ProgressBoard'));   // 已并入 ProjectProgress
// const PersonnelBoard = lazy(() => import('@/pages/admin/PersonnelBoard')); // 已从导航移除
const ProjectAuth = lazy(() => import('@/pages/admin/ProjectAuth'));
const ProjectEdit = lazy(() => import('@/pages/admin/ProjectEdit'));
// const ProjectHR = lazy(() => import('@/pages/admin/ProjectHR'));           // 已从导航移除
const ProjectManage = lazy(() => import('@/pages/admin/ProjectManage'));
const RiskList = lazy(() => import('@/pages/admin/RiskList'));
const RiskEdit = lazy(() => import('@/pages/admin/RiskEdit'));
const ReportsAnalytics = lazy(() => import('@/pages/admin/ReportsAnalytics'));
const UserManage = lazy(() => import('@/pages/admin/UserManage'));
const RoleManage = lazy(() => import('@/pages/admin/RoleManage'));
const PermissionManage = lazy(() => import('@/pages/admin/PermissionManage'));
const ResourceManage = lazy(() => import('@/pages/admin/ResourceManage'));
const DailyReportManage = lazy(() => import('@/pages/admin/DailyReportManage'));
const WechatManage = lazy(() => import('@/pages/admin/WechatManage'));

export const router = createBrowserRouter([
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
      { path: 'tasks/:id', element: <TasksView /> },
      {
        path: 'admin',
        element: <Outlet />,
        children: [
          // 默认首页：上中下三段式仪表盘（工单状态 / 跨项目看板 / 待补）
          { index: true, element: <Dashboard /> },
          // 仪表盘下钻明细：点击状态/分类标签后展示对应列表
          { path: 'dashboard/tickets/:status', element: <TicketStatusDetail /> },
          { path: 'dashboard/projects/:dimension/:key', element: <ProjectCategoryDetail /> },
          // 次级入口：三大功能传统列表页 + 管理员工具
          { path: 'entries', element: <AdminEntries /> },
          {
            element: <AdminLayout />,
            children: [
              // === 三大核心功能 ===
              { path: 'ticket-monitor', element: <TicketMonitor /> },
              { path: 'project-progress', element: <ProjectProgress /> },
              { path: 'daily-reports', element: <DailyReportManage /> },

              // === 次级功能 ===
              { path: 'dashboard', element: <ProjectMetrics /> },
              { path: 'data-import', element: <DataImport /> },
              { path: 'operation-logs', element: <OperationLogs /> },
              // { path: 'progress', element: <ProgressBoard /> },       // 已并入 ProjectProgress
              // { path: 'personnel', element: <PersonnelBoard /> },     // 已从导航移除
              { path: 'project-auth', element: <ProjectAuth /> },
              { path: 'project-edit/:id?', element: <ProjectEdit /> },
              // { path: 'project-hr', element: <ProjectHR /> },         // 已从导航移除
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
  { path: '/tasks/:id', element: <Navigate to="/tasks" replace /> },
  { path: '/admin', element: <Navigate to="/admin" replace /> },
  { path: '/admin/*', element: <Navigate to="/admin" replace /> },
  { path: '/home', element: <Navigate to="/call" replace /> },
  { path: '*', element: <Navigate to="/call" replace /> },
]);
