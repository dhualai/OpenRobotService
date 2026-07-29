// 后台管理 —— "其他"入口（从仪表盘「更多功能」进入）
// 仪表盘（/admin，Dashboard.tsx）是默认首页；本页仅承载不常用的管理员工具入口。
import { useNavigate } from 'react-router-dom';
import { Navbar } from 'tdesign-mobile-react';

interface Entry { path: string; label: string; emoji: string; desc: string; }

const adminEntries: Entry[] = [
  { path: '/admin/users', label: '用户管理', emoji: '👥', desc: '用户账号CRUD、派单画像' },
  { path: '/admin/roles', label: '角色管理', emoji: '🏷️', desc: '角色定义、权限绑定' },
  { path: '/admin/permissions', label: '权限管理', emoji: '🔑', desc: '权限项定义、分配' },
  { path: '/admin/assign-role', label: '分配角色', emoji: '👤', desc: '为用户在项目中分配角色' },
  { path: '/admin/operation-logs', label: '操作记录', emoji: '📝', desc: '操作日志审计与追溯' },
];

export default function AdminEntries() {
  const navigate = useNavigate();

  return (
    <div className="admin-view">
      <Navbar title="其他" leftArrow onLeftClick={() => navigate('/admin')} fixed />
      <div className="admin-grid">
        {adminEntries.map((e) => (
          <div key={e.path} className="admin-card" onClick={() => navigate(e.path)}>
            <span className="admin-card__emoji">{e.emoji}</span>
            <span className="admin-card__label">{e.label}</span>
            <span style={{ fontSize: 11, color: '#999', marginTop: 2 }}>{e.desc}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
