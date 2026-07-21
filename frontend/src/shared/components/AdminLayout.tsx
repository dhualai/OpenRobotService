import { Suspense, useState, useCallback } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Navbar, Loading, Button } from 'tdesign-mobile-react';
import type { ReactNode } from 'react';

interface AdminLayoutProps {
  children?: ReactNode;
}

interface MenuItem {
  path: string;
  label: string;
  emoji: string;
}

const adminMenuItems: MenuItem[] = [
  { path: '/admin/dashboard', label: '仪表盘', emoji: '📊' },
  { path: '/admin/project-manage', label: '项目管理', emoji: '📁' },
  { path: '/admin/project-edit', label: '新建/编辑项目', emoji: '✏️' },
  { path: '/admin/progress', label: '进度看板', emoji: '📈' },
  { path: '/admin/personnel', label: '人员分配', emoji: '👥' },
  { path: '/admin/project-hr', label: '人力资源', emoji: '🧑‍💼' },
  { path: '/admin/project-auth', label: '项目授权', emoji: '🔐' },
  { path: '/admin/risks', label: '风险管理', emoji: '⚠️' },
  { path: '/admin/reports', label: '报表分析', emoji: '📋' },
  { path: '/admin/data-import', label: '数据导入', emoji: '📥' },
  { path: '/admin/wechat', label: '微信管理', emoji: '💬' },
  { path: '/admin/operation-logs', label: '操作记录', emoji: '📝' },
  { path: '/admin/users', label: '用户管理', emoji: '👤' },
  { path: '/admin/roles', label: '角色管理', emoji: '🏷️' },
  { path: '/admin/permissions', label: '权限管理', emoji: '🔑' },
  { path: '/admin/resources', label: '资源管理', emoji: '🗂️' },
  { path: '/admin/daily-reports', label: '日报管理', emoji: '📰' },
];

function matchMenuPath(items: MenuItem[], currentPath: string): string {
  const exact = items.find((item) => item.path === currentPath);
  if (exact) return exact.path;
  const prefix = items.find((item) => currentPath.startsWith(item.path + '/'));
  if (prefix) return prefix.path;
  return '';
}

export default function AdminLayout({ children }: AdminLayoutProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [menuVisible, setMenuVisible] = useState(false);

  const activePath = matchMenuPath(adminMenuItems, location.pathname);
  const currentLabel = adminMenuItems.find((item) => item.path === activePath)?.label || '后台管理';

  const handleMenuClick = useCallback(
    (path: string) => {
      navigate(path);
      setMenuVisible(false);
    },
    [navigate],
  );

  return (
    <div className="mobile-shell" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Navbar
        title={currentLabel}
        leftArrow
        onLeftClick={() => navigate(-1)}
        fixed
      >
        <Button
          variant="text"
          size="small"
          onClick={() => setMenuVisible(true)}
          style={{ color: '#0052d9', fontSize: 18 }}
        >
          ☰
        </Button>
      </Navbar>

      {menuVisible && (
        <div
          onClick={() => setMenuVisible(false)}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 1000,
            backgroundColor: 'rgba(0, 0, 0, 0.5)',
            transition: 'opacity 0.3s',
          }}
        />
      )}

      <div
        style={{
          position: 'fixed',
          top: 0,
          left: menuVisible ? 0 : -280,
          width: 260,
          height: '100vh',
          zIndex: 1001,
          backgroundColor: '#fff',
          boxShadow: '2px 0 12px rgba(0,0,0,0.15)',
          transition: 'left 0.3s ease',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div
          style={{
            padding: '20px 16px 16px',
            borderBottom: '1px solid #f0f0f0',
            fontSize: 18,
            fontWeight: 600,
            color: '#0052d9',
          }}
        >
          ⚙️ 后台管理
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: '8px 0' }}>
          {adminMenuItems.map((item) => (
            <div
              key={item.path}
              onClick={() => handleMenuClick(item.path)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '12px 20px',
                margin: '2px 8px',
                borderRadius: 8,
                fontSize: 15,
                color: activePath === item.path ? '#0052d9' : '#333',
                backgroundColor: activePath === item.path ? '#e8f2ff' : 'transparent',
                fontWeight: activePath === item.path ? 600 : 400,
                cursor: 'pointer',
                transition: 'all 0.15s',
              }}
              onMouseEnter={(e) => {
                if (activePath !== item.path) {
                  (e.currentTarget as HTMLDivElement).style.backgroundColor = '#f5f5f5';
                }
              }}
              onMouseLeave={(e) => {
                if (activePath !== item.path) {
                  (e.currentTarget as HTMLDivElement).style.backgroundColor = 'transparent';
                }
              }}
            >
              <span style={{ fontSize: 18 }}>{item.emoji}</span>
              <span>{item.label}</span>
            </div>
          ))}
        </div>
      </div>

      <div style={{ flex: 1, overflow: 'auto', paddingTop: 48, paddingBottom: 16 }}>
        <Suspense fallback={<Loading text="加载中..." />}>
          {children || <Outlet />}
        </Suspense>
      </div>
    </div>
  );
}