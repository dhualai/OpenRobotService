// 后台管理（管理视角）—— 看板入口卡片网格
// 按角色动态渲染入口，点击跳现有 admin 子路由（复用 AdminLayout 内的页面）。
import { useNavigate } from 'react-router-dom';
import { Navbar } from 'tdesign-mobile-react';
import { useAuthStore } from '@/stores/auth';

interface Entry { path: string; label: string; emoji: string; adminOnly?: boolean; }

const entries: Entry[] = [
  { path: '/admin/dashboard', label: '项目看板', emoji: '📊' },
  { path: '/admin/project-manage', label: '项目管理', emoji: '📁' },
  { path: '/admin/risks', label: '风险红黄灯', emoji: '⚠️' },
  { path: '/admin/progress', label: '进度看板', emoji: '📈' },
  { path: '/admin/reports', label: '报表分析', emoji: '📋' },
  { path: '/admin/data-import', label: '机器人数据', emoji: '📥' },
  { path: '/admin/wechat', label: '微信管理', emoji: '💬', adminOnly: true },
  { path: '/admin/users', label: '用户管理', emoji: '👤', adminOnly: true },
  { path: '/admin/roles', label: '角色管理', emoji: '🏷️', adminOnly: true },
  { path: '/admin/permissions', label: '权限管理', emoji: '🔑', adminOnly: true },
  { path: '/admin/resources', label: '资源管理', emoji: '🗂️', adminOnly: true },
];

export default function AdminView() {
  const navigate = useNavigate();
  const { isAdmin } = useAuthStore();
  const visible = entries.filter((e) => !e.adminOnly || isAdmin);

  return (
    <div className="admin-view">
      <Navbar title="后台管理" fixed />
      <p className="admin-view__slogan">东厂管不了的事，我西厂管！</p>
      <div className="admin-grid">
        {visible.map((e) => (
          <div key={e.path} className="admin-card" onClick={() => navigate(e.path)}>
            <span className="admin-card__emoji">{e.emoji}</span>
            <span className="admin-card__label">{e.label}</span>
          </div>
        ))}
      </div>
      {!isAdmin && <p className="admin-view__tip">部分管理功能仅对管理员可见</p>}
    </div>
  );
}