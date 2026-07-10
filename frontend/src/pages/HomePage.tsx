import { useNavigate, useLocation } from 'react-router-dom';
import { Navbar, Cell, CellGroup } from 'tdesign-mobile-react';

interface MenuItem {
  path: string;
  label: string;
}

interface MenuGroup {
  title: string;
  items: MenuItem[];
}

const menuGroups: MenuGroup[] = [
  {
    title: 'Call 模块',
    items: [
      { path: '/call/ai-chat', label: 'AI 聊天' },
      { path: '/call/new-ticket', label: '新建工单' },
    ],
  },
  {
    title: 'Tasks 模块',
    items: [
      { path: '/tasks', label: '工单列表' },
      { path: '/tasks/1', label: '工单详情（示例）' },
    ],
  },
  {
    title: 'Admin 模块',
    items: [
      { path: '/admin/dashboard', label: '仪表盘' },
      { path: '/admin/data-import', label: '数据导入' },
      { path: '/admin/operation-logs', label: '操作记录' },
      { path: '/admin/progress', label: '进度看板' },
      { path: '/admin/personnel', label: '人员分配' },
      { path: '/admin/project-auth', label: '项目授权' },
      { path: '/admin/project-edit', label: '项目编辑' },
      { path: '/admin/project-hr', label: '人力资源' },
      { path: '/admin/project-manage', label: '项目管理' },
      { path: '/admin/risks', label: '风险管理' },
      { path: '/admin/reports', label: '报表分析' },
      { path: '/admin/users', label: '用户管理' },
      { path: '/admin/roles', label: '角色管理' },
      { path: '/admin/permissions', label: '权限管理' },
      { path: '/admin/resources', label: '资源管理' },
    ],
  },
];

export default function HomePage() {
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#f5f5f5' }}>
      <Navbar
        title="页面导航"
        leftArrow
        onLeftClick={() => navigate(-1)}
        fixed
      />

      <div style={{ paddingTop: 56, paddingBottom: 24 }}>
        {menuGroups.map((group) => (
          <div key={group.title} style={{ margin: '16px 16px 0' }}>
            <h3 style={{
              fontSize: 14,
              color: '#999',
              margin: '0 0 8px 12px',
              fontWeight: 500,
            }}>
              {group.title}
            </h3>
            <CellGroup>
              {group.items.map((item) => (
                <Cell
                  key={item.path}
                  title={item.label}
                  description={item.path}
                  arrow
                  onClick={() => navigate(item.path)}
                  style={{
                    backgroundColor: location.pathname === item.path ? '#ecf2fe' : '#fff',
                  }}
                />
              ))}
            </CellGroup>
          </div>
        ))}
      </div>
    </div>
  );
}
