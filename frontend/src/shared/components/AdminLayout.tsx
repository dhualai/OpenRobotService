import { Suspense } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Navbar, Loading } from 'tdesign-mobile-react';
import type { ReactNode } from 'react';
import UserAvatarMenu from './UserAvatarMenu';

interface AdminLayoutProps {
  children?: ReactNode;
}

interface MenuItem {
  path: string;
  label: string;
  emoji: string;
}

const adminMenuItems: MenuItem[] = [
  // === 仪表盘首页 ===
  { path: '/admin', label: '仪表盘', emoji: '🏠' },

  // === 三大核心功能 ===
  { path: '/admin/ticket-monitor', label: '工单状态监测', emoji: '🎫' },
  { path: '/admin/project-progress', label: '项目进度管理', emoji: '📊' },
  { path: '/admin/daily-reports', label: '日报管理', emoji: '📋' },
  { path: '/admin/daily-summary', label: '日报周报', emoji: '🤖' },

  // === 次级功能 ===
  { path: '/admin/project-manage', label: '项目管理', emoji: '📁' },
  { path: '/admin/risks', label: '风险管理', emoji: '⚠️' },
  { path: '/admin/reports', label: '报表分析', emoji: '📈' },

  // === 项目管理操作入口 ===
  { path: '/admin/project-edit', label: '新建/编辑项目', emoji: '✏️' },

  // === 管理工具 ===
  { path: '/admin/users', label: '用户管理', emoji: '👤' },
  { path: '/admin/roles', label: '角色管理', emoji: '🏷️' },
  { path: '/admin/assign-role', label: '分配角色', emoji: '👤' },
  { path: '/admin/user-setup', label: '设置用户', emoji: '🔀' },
  { path: '/admin/permissions', label: '权限管理', emoji: '🔑' },
  { path: '/admin/wechat', label: '微信管理', emoji: '💬' },
  { path: '/admin/data-import', label: '数据导入', emoji: '📥' },
  { path: '/admin/resources', label: '资源管理', emoji: '🗂️' },

  // === 系统日志 ===
  { path: '/admin/operation-logs', label: '操作记录', emoji: '📝' },
];

function matchMenuPath(items: MenuItem[], currentPath: string): string {
  const exact = items.find((item) => item.path === currentPath);
  if (exact) return exact.path;
  // 取最长前缀匹配，避免「/admin」（仪表盘首页）作为短前缀误吞所有 /admin/* 子路径
  const prefixMatches = items
    .filter((item) => item.path !== '/admin' && currentPath.startsWith(item.path + '/'))
    .sort((a, b) => b.path.length - a.path.length);
  return prefixMatches[0]?.path || '';
}

export default function AdminLayout({ children }: AdminLayoutProps) {
  const navigate = useNavigate();
  const location = useLocation();

  const activePath = matchMenuPath(adminMenuItems, location.pathname);
  const currentLabel = adminMenuItems.find((item) => item.path === activePath)?.label || '后台管理';

  return (
    <div className="mobile-shell" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Navbar
        title={currentLabel}
        leftArrow
        onLeftClick={() => navigate(-1)}
        right={<UserAvatarMenu />}
        fixed
      />

      <div style={{ flex: 1, overflow: 'auto', paddingTop: 48, paddingBottom: 16 }}>
        <Suspense fallback={<Loading text="加载中..." />}>
          {children || <Outlet />}
        </Suspense>
      </div>
    </div>
  );
}