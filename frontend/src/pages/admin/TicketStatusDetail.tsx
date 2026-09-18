// 工单状态下钻明细 —— 点击仪表盘某个状态标签后展示该状态下的工单列表
import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { navigateInWechat } from '@/shared/utils/wechatJsSdk';
import { Navbar, Loading } from 'tdesign-mobile-react';
import { fetchTicketsByStatus, type TicketListItem } from '@/api/dashboard';
import { TICKET_STATUS_MAP } from '@/shared/constants/dashboard';
import { macTone } from '@/shared/components/macaronBits';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';
import { formatDateTime } from '@/shared/utils/url';
import { formatOverdueDuration } from '@/shared/utils/deadline';

// 仪表盘统计卡下钻的特殊 scope key → 展示名（组合口径，非单一状态）
const SCOPE_LABELS: Record<string, string> = {
  all: '全部工单',
  pending: '待处理',
  overdue: '超时工单',
};

// 挂起工单：后端 TaskStatus.PENDING（前端仪表盘 key 为 paused ——「暂停/挂起」，
// 映射见 backend/app/modules/admin/services/task_dashboard_service.py FRONTEND_STATUS_MAP），
// 本列表接口返回的是原始枚举值 "pending"，故按 "pending" 判定。
const SUSPENDED_STATUS = 'pending';
const SUSPENDED_META = TICKET_STATUS_MAP.paused;
// 配色取设计系统为该状态分配的色调（paused.tone = status-3 → --mac-status-3）：蓝色阶是
// 状态类唯一保留的色彩（见 global.css 顶部说明），且与仪表盘环图/图例的「暂停/挂起」同一支色，
// 不用 TICKET_STATUS_MAP.paused.color —— 那是旧的非蓝阶取色。
const SUSPENDED_BAR = macTone(SUSPENDED_META.tone);
const SUSPENDED_BG = `color-mix(in oklab, ${SUSPENDED_BAR} 7%, transparent)`;
const SUSPENDED_TAG_BG = `color-mix(in oklab, ${SUSPENDED_BAR} 18%, transparent)`;
// 标签文字用浅底可读的深蓝（percentLabelColor 对浅色弧段用的是同一支），
// 不用色条本身的 #46aede —— 11px 小字压在上面对比度不够。
const SUSPENDED_TAG_FG = macTone('blue-1');

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
            items.map((t) => {
              const suspended = t.status === SUSPENDED_STATUS;
              const overdue = formatOverdueDuration(t.deadline_at);
              return (
                <div
                  key={t.id}
                  style={{
                    background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10,
                    boxShadow: '0 1px 3px rgba(0,0,0,0.06)', cursor: 'pointer',
                    // 挂起工单：左侧整条色条 + 极淡同色底，扫读时与普通工单一眼区分
                    ...(suspended
                      ? { borderLeft: `4px solid ${SUSPENDED_BAR}`, background: SUSPENDED_BG }
                      : null),
                  }}
                  onClick={() => navigateInWechat(navigate, `/tasks/${t.id}`)}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{
                      flex: 1, minWidth: 0, fontWeight: 600, fontSize: 15,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {t.title || '无标题'}
                    </span>
                    {/* 色条之外再给文字标签：颜色不作为唯一信号，色觉障碍/黑白下同样可辨 */}
                    {suspended && (
                      <span style={{
                        flexShrink: 0, fontSize: 11, padding: '2px 8px', borderRadius: 10,
                        background: SUSPENDED_TAG_BG, color: SUSPENDED_TAG_FG, fontWeight: 500,
                      }}>
                        {SUSPENDED_META.label}
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>
                    {t.priority ? `${t.priority}优先级` : ''}
                    {t.assignee_name ? ` · 处理人: ${t.assignee_name}` : ''}
                    {t.created_at ? ` · ${formatDateTime(t.created_at).slice(0, 10)}` : ''}
                    {/* 超时时长：与「超时最久在前」的排序相互印证，故比其余次要信息稍重 */}
                    {overdue ? <span style={{ color: '#666', fontWeight: 500 }}> · 已超时 {overdue}</span> : null}
                  </div>
                </div>
              );
            })
          )
        )}
      </div>
    </div>
  );
}
