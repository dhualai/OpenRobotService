// 后台管理 —— 次级功能入口卡片网格（从仪表盘「更多功能」进入）
// 仪表盘（/admin，Dashboard.tsx）是默认首页；本页承载三大核心功能的传统列表入口 + 管理员工具。
import { useNavigate } from 'react-router-dom';
import { Navbar } from 'tdesign-mobile-react';
import { useAuthStore } from '@/stores/auth';

interface Entry { path: string; label: string; emoji: string; desc: string; adminOnly?: boolean; }

const entries: Entry[] = [
  { path: '/admin/ticket-monitor', label: '工单状态监测', emoji: '🎫', desc: '实时查看所有工单状态、筛选、跟踪闭环' },
  { path: '/admin/project-progress', label: '项目进度管理', emoji: '📊', desc: '项目列表、执行状态、风险一览' },
  { path: '/admin/daily-reports', label: '日报 / 周报', emoji: '📋', desc: '项目日报填写、查看、周报汇总' },
];

// 管理员专属入口（保留基础管理能力，但不在主入口突出展示）
const adminEntries: Entry[] = [
  { path: '/admin/users', label: '用户管理', emoji: '👤', desc: '用户列表、角色分配', adminOnly: true },
  { path: '/admin/roles', label: '角色管理', emoji: '🏷️', desc: '角色定义、权限绑定', adminOnly: true },
  { path: '/admin/wechat', label: '微信管理', emoji: '💬', desc: '菜单/标签配置', adminOnly: true },
];

export default function AdminEntries() {
  const navigate = useNavigate();
  const { isAdmin } = useAuthStore();

  return (
    <div className="admin-view">
      <Navbar title="更多功能" leftArrow onLeftClick={() => navigate('/admin')} fixed />
      <p className="admin-view__slogan">东厂管不了的事，我西厂管！</p>

      {/* 三大核心功能 */}
      <div className="admin-grid">
        {entries.map((e) => (
          <div key={e.path} className="admin-card" onClick={() => navigate(e.path)}>
            <span className="admin-card__emoji">{e.emoji}</span>
            <span className="admin-card__label">{e.label}</span>
            <span style={{ fontSize: 11, color: '#999', marginTop: 2 }}>{e.desc}</span>
          </div>
        ))}
      </div>

      {/* 管理员专属入口（折叠在下方） */}
      {isAdmin && (
        <>
          <p style={{ margin: '20px 0 8px', fontSize: 13, color: '#999', padding: '0 4px' }}>管理员工具</p>
          <div className="admin-grid">
            {adminEntries.map((e) => (
              <div key={e.path} className="admin-card" onClick={() => navigate(e.path)}>
                <span className="admin-card__emoji">{e.emoji}</span>
                <span className="admin-card__label">{e.label}</span>
                <span style={{ fontSize: 11, color: '#999', marginTop: 2 }}>{e.desc}</span>
              </div>
            ))}
          </div>
        </>
      )}
      {!isAdmin && <p className="admin-view__tip">部分管理功能仅对管理员可见</p>}
    </div>
  );
}