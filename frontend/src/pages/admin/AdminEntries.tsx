import { lazy, Suspense } from 'react';
import type { ReactNode } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router-dom';
import { Loading, Navbar } from 'tdesign-mobile-react';
import {
  MacUsers, MacTags, MacKeyRound, MacUserCog, MacShuffle, MacScrollText, MacClipboardList,
  MacTable2, MacWallet, MacFileText,
} from '@/shared/components/macaronIcons';
import { useAuthStore } from '@/stores/auth';
import { PERM_DISPATCH_DEV } from '@/shared/constants/dispatchDev';
import { PERM_TASK_POLICY } from '@/pages/admin/TaskPolicyPage';
import { PERM_WECOM_SHEETS } from '@/api/wecomSheets';
import { PERM_WXPAY_ADMIN } from '@/api/wxpay';
import { PERM_ANNOUNCEMENT } from '@/api/announcement';

const AdminUserStatistics = lazy(() => import('./AdminUserStatistics'));

interface Entry {
  path: string;
  label: string;
  desc: string;
  icon: ReactNode;
  tone: string;
}

interface EntryGroup {
  id: string;
  label: string;
  icon: ReactNode;
  tone: string;
  entries: Entry[];
}

const entryGroups: EntryGroup[] = [
  {
    id: 'users',
    label: '用户与权限',
    icon: <MacUsers />,
    tone: 'blue-1',
    entries: [
      { path: '/admin/users', label: '用户管理', desc: '用户账号CRUD、派单画像', icon: <MacUsers />, tone: 'blue-1' },
      { path: '/admin/roles', label: '角色管理', desc: '角色定义、权限绑定', icon: <MacTags />, tone: 'blue-2' },
      { path: '/admin/permissions', label: '权限管理', desc: '权限项定义、分配', icon: <MacKeyRound />, tone: 'blue-3' },
      { path: '/admin/assign-role', label: '分配角色', desc: '为用户在项目中分配角色', icon: <MacUserCog />, tone: 'blue-2' },
      { path: '/admin/user-setup', label: '设置用户', desc: '迁移用户数据、合并账号', icon: <MacShuffle />, tone: 'blue-3' },
    ],
  },
  {
    id: 'dispatch',
    label: '工单与派单',
    icon: <MacClipboardList />,
    tone: 'blue-2',
    entries: [
      { path: '/admin/module-tree', label: '责任模块树', desc: '产品→界面→功能维护、工程师认领', icon: <MacTags />, tone: 'blue-2' },
      { path: '/admin/dual-tree', label: '双树试做', desc: '本机试挂组织树和技能树，不改现有派单', icon: <MacTags />, tone: 'blue-3' },
      { path: '/admin/task-policy', label: '工单关联规则', desc: '阻塞开关、重复工单状态同步', icon: <MacKeyRound />, tone: 'blue-3' },
    ],
  },
  {
    id: 'operations',
    label: '运营与数据',
    icon: <MacTable2 />,
    tone: 'blue-3',
    entries: [
      { path: '/admin/qrcode-manage', label: '二维码管理', desc: '带参数二维码批量创建、发布、状态流转', icon: <MacScrollText />, tone: 'blue-3' },
      { path: '/admin/wecom-sheets', label: '企微表格', desc: '企微智能表格接入、测试连接、定时同步', icon: <MacTable2 />, tone: 'blue-2' },
      { path: '/admin/announcements', label: '公告管理', desc: '活动中心公告发布、上下线、置顶', icon: <MacFileText />, tone: 'blue-2' },
      { path: '/admin/wxpay-rewards', label: '奖励管理', desc: '转账测试、转账单查单与撤销、奖励记录', icon: <MacWallet />, tone: 'blue-1' },
    ],
  },
  {
    id: 'system',
    label: '系统工具',
    icon: <MacKeyRound />,
    tone: 'blue-4',
    entries: [
      { path: '/admin/operation-logs', label: '操作记录', desc: '操作日志审计与追溯', icon: <MacScrollText />, tone: 'blue-4' },
      { path: '/admin/dispatch-dev', label: '开发者模式', desc: '派单调试、界面图鉴、可达环境、长期记忆', icon: <MacClipboardList />, tone: 'blue-4' },
    ],
  },
];

export default function AdminEntries() {
  const navigate = useNavigate();
  const { category } = useParams<{ category: string }>();
  const canShowDispatchDev = useAuthStore((state) => state.hasPermission(PERM_DISPATCH_DEV));
  const canShowTaskPolicy = useAuthStore((state) => state.hasPermission(PERM_TASK_POLICY));
  const canShowWecomSheets = useAuthStore((state) => state.hasPermission(PERM_WECOM_SHEETS));
  const canShowWxpayRewards = useAuthStore((state) => state.hasPermission(PERM_WXPAY_ADMIN));
  const canShowAnnouncement = useAuthStore((state) => state.hasPermission(PERM_ANNOUNCEMENT));
  const visibleGroups = entryGroups.map((group) => ({
    ...group,
    entries: group.entries.filter((entry) =>
      (entry.path !== '/admin/dispatch-dev' || canShowDispatchDev) &&
      (entry.path !== '/admin/task-policy' || canShowTaskPolicy) &&
      (entry.path !== '/admin/wecom-sheets' || canShowWecomSheets) &&
      (entry.path !== '/admin/wxpay-rewards' || canShowWxpayRewards) &&
      (entry.path !== '/admin/announcements' || canShowAnnouncement),
    ),
  })).filter((group) => group.entries.length > 0);
  const selectedGroup = visibleGroups.find((group) => group.id === category);

  if (category && !selectedGroup) return <Navigate to="/admin/entries" replace />;

  const visibleEntries: Entry[] = selectedGroup?.entries ?? visibleGroups.map((group) => ({
    path: `/admin/entries/${group.id}`,
    label: group.label,
    desc: group.entries.map((entry) => entry.label).join(' · '),
    icon: group.icon,
    tone: group.tone,
  }));

  return (
    <div className="admin-view" key={category || 'entries'}>
      <Navbar
        title={selectedGroup?.label ?? '其他'}
        leftArrow
        onLeftClick={() => navigate(selectedGroup ? '/admin/entries' : '/admin')}
        fixed
      />
      <div className="admin-entries-grid">
        {visibleEntries.map((entry) => (
          <button
            key={entry.path}
            type="button"
            className="admin-entries-card"
            data-tone={entry.tone}
            onClick={() => navigate(entry.path)}
          >
            <span className="admin-entries-card__icon">{entry.icon}</span>
            <span className="admin-entries-card__body">
              <span className="admin-entries-card__label">{entry.label}</span>
              <span className="admin-entries-card__desc">{entry.desc}</span>
            </span>
          </button>
        ))}
      </div>
      {selectedGroup?.id === 'users' && (
        <Suspense fallback={<div className="admin-entries-stats"><Loading text="加载用户统计..." /></div>}>
          <AdminUserStatistics />
        </Suspense>
      )}
    </div>
  );
}
