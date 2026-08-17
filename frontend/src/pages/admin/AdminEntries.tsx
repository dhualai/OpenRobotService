// 后台管理 —— "其他"入口（从仪表盘「更多功能」进入）
// 仪表盘（/admin，Dashboard.tsx）是默认首页；本页仅承载不常用的管理员工具入口。
// 样式参考 macaron other 页：surface-card 行式入口 + 色调淡色图标圆角块。
import { useNavigate } from 'react-router-dom';
import { Navbar } from 'tdesign-mobile-react';
import type { ReactNode } from 'react';
import {
  MacUsers, MacTags, MacKeyRound, MacUserCog, MacShuffle, MacScrollText,
} from '@/shared/components/macaronIcons';

interface Entry { path: string; label: string; desc: string; icon: ReactNode; tone: string; }

const adminEntries: Entry[] = [
  { path: '/admin/users', label: '用户管理', desc: '用户账号CRUD、派单画像', icon: <MacUsers />, tone: 'blue-1' },
  { path: '/admin/roles', label: '角色管理', desc: '角色定义、权限绑定', icon: <MacTags />, tone: 'blue-2' },
  { path: '/admin/permissions', label: '权限管理', desc: '权限项定义、分配', icon: <MacKeyRound />, tone: 'blue-3' },
  { path: '/admin/assign-role', label: '分配角色', desc: '为用户在项目中分配角色', icon: <MacUserCog />, tone: 'blue-2' },
  { path: '/admin/user-setup', label: '设置用户', desc: '迁移用户数据、合并账号', icon: <MacShuffle />, tone: 'blue-3' },
  { path: '/admin/operation-logs', label: '操作记录', desc: '操作日志审计与追溯', icon: <MacScrollText />, tone: 'blue-4' },
];

export default function AdminEntries() {
  const navigate = useNavigate();

  return (
    <div className="admin-view">
      <Navbar title="其他" leftArrow onLeftClick={() => navigate('/admin')} fixed />
      <div className="admin-entries-grid">
        {adminEntries.map((e) => (
          <button
            key={e.path}
            type="button"
            className="admin-entries-card"
            data-tone={e.tone}
            onClick={() => navigate(e.path)}
          >
            <span className="admin-entries-card__icon">{e.icon}</span>
            <span className="admin-entries-card__body">
              <span className="admin-entries-card__label">{e.label}</span>
              <span className="admin-entries-card__desc">{e.desc}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
