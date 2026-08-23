// 工单状态下钻明细 —— 点击仪表盘某个状态标签后展示该状态下的工单列表
import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Loading } from 'tdesign-mobile-react';
import { fetchTicketsByStatus, type TicketListItem } from '@/api/dashboard';
import { TICKET_STATUS_MAP } from '@/shared/constants/dashboard';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';
import { formatDateTime } from '@/shared/utils/url';

// 仪表盘统计卡下钻的特殊 scope key → 展示名（组合口径，非单一状态）
const SCOPE_LABELS: Record<string, string> = {
  all: '全部工单',
  pending: '待处理',
  overdue: '超时工单',
};

export default function TicketStatusDetail() {
  const { status = '' } = useParams<{ status: string }>();
  const navigate = useNavigate();
  const { projectIds, hasPermission } = useAuthStore();
  const canViewAll = hasPermission(PERMISSION_VIEW_ALL);
  const [items, setItems] = useState<TicketListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  const meta = TICKET_STATUS_MAP[status];
  // 仪表盘统计卡下钻的特殊 scope（组合口径，非单一状态）：
  // all=全部 / pending=待处理（处理中+暂停挂起）/ overdue=超时工单，见 dashboard.ts 与后端 task_dashboard_service
  const scopeLabel = SCOPE_LABELS[status];
  const title = scopeLabel ?? meta?.label ?? status;
  const backendReady = scopeLabel ? true : Boolean(meta?.backendReady);

  const load = useCallback(async () => {
    setLoading(true);
    const res = await fetchTicketsByStatus(status, canViewAll ? undefined : projectIds);
    setItems(res.items);
    setTotal(res.total);
    setLoading(false);
  }, [status, projectIds, canViewAll]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <Navbar title={`${title} · 工单明细`} leftArrow onLeftClick={() => navigate(-1)} fixed />
      <div style={{ padding: '16px', paddingTop: 64 }}>
        <p style={{ fontSize: 13, color: '#999', marginBottom: 12 }}>共 {total} 条</p>

        {loading ? <Loading text="加载中..." /> : (
          items.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
              暂无数据
              {!backendReady && (
                <div style={{ marginTop: 8, fontSize: 12, color: '#bbb' }}>
                  该状态后端接口尚未接入，见 docs/工程文档.md
                </div>
              )}
            </div>
          ) : (
            items.map((t) => (
              <div
                key={t.id}
                style={{
                  background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10,
                  boxShadow: '0 1px 3px rgba(0,0,0,0.06)', cursor: 'pointer',
                }}
                onClick={() => navigate(`/tasks/${t.id}`)}
              >
                <div style={{ fontWeight: 600, fontSize: 15 }}>{t.title || '无标题'}</div>
                <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>
                  {t.priority ? `${t.priority}优先级` : ''}
                  {t.assignee_name ? ` · 处理人: ${t.assignee_name}` : ''}
                  {t.created_at ? ` · ${formatDateTime(t.created_at).slice(0, 10)}` : ''}
                </div>
              </div>
            ))
          )
        )}
      </div>
    </div>
  );
}
