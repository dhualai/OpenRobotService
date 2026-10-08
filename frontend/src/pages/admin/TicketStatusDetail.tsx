// 工单下钻明细 —— 点某个汇总数字后展示该口径下的工单列表。两个入口共用这一个页面：
//   仪表盘统计卡：/admin/dashboard/tickets/:status（全部工单 / 待处理 / 超时工单 + 单一状态）
//   项目工单卡三格：/admin/project-detail/:id/tickets/:status（总工单 / 正在处理 / 超期工单）
// 后端同一个 /dashboard/tickets 接口，带 project_ids 就只列这些项目。
import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { navigateInWechat } from '@/shared/utils/wechatJsSdk';
import { Navbar, Loading, Button } from 'tdesign-mobile-react';
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

// 从项目详情页的「项目工单」卡下钻时用卡片自己的词（同一个数只在两处叫法不同：
// 卡片说「正在处理 / 超期工单数」，仪表盘说「待处理 / 超时工单」）——
// 点进来的标题与刚点的格子对得上，不必回头猜是不是同一个口径。
const PROJECT_SCOPE_LABELS: Record<string, string> = {
  all: '总工单',
  pending: '正在处理',
  overdue: '超期工单',
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
const PAGE_SIZE = 20;

export default function TicketStatusDetail() {
  // 两条入口共用一个页面：仪表盘 = /admin/dashboard/tickets/:status（可看全部的人看全部），
  // 项目工单卡 = /admin/project-detail/:id/tickets/:status（只看这一个项目）。
  const { status = '', id: scopedProjectId } = useParams<{ status: string; id?: string }>();
  const navigate = useNavigate();
  const { projectIds, hasPermission } = useAuthStore();
  const canViewAll = hasPermission(PERMISSION_VIEW_ALL);
  const [items, setItems] = useState<TicketListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const requestIdRef = useRef(0);

  const meta = TICKET_STATUS_MAP[status];
  // 统计卡下钻的特殊 scope（组合口径，非单一状态）：
  // all=全部 / pending=待处理（处理中+暂停挂起）/ overdue=超时工单，见 dashboard.ts 与后端 task_dashboard_service
  const scopeLabel = SCOPE_LABELS[status];
  const title = (scopedProjectId ? PROJECT_SCOPE_LABELS[status] : scopeLabel) ?? meta?.label ?? status;
  const backendReady = scopeLabel ? true : Boolean(meta?.backendReady);

  const load = useCallback(async (skip = 0) => {
    const requestId = ++requestIdRef.current;
    setError('');
    if (skip === 0) {
      setLoading(true);
      setLoadingMore(false);
      setItems([]);
      setTotal(0);
    } else {
      setLoadingMore(true);
    }
    // 带项目 id 时收窄成这一个项目（后端 project_ids 就是任务表 project_id 的 in 过滤，
    // 与卡片概览 get_ticket_summary(db, [project_id]) 同一口径，条数因此对得上）；
    // 否则沿用仪表盘原口径：能看全部的人不过滤，其余人限于自己关联的项目。
    try {
      const res = await fetchTicketsByStatus(
        status,
        scopedProjectId ? [scopedProjectId] : (canViewAll ? undefined : projectIds),
        { skip, limit: PAGE_SIZE },
      );
      if (requestId !== requestIdRef.current) return;
      setItems((previous) => skip === 0 ? res.items : [...previous, ...res.items]);
      setTotal(res.total);
    } catch {
      if (requestId === requestIdRef.current) setError('工单列表加载失败，请重试');
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [status, scopedProjectId, projectIds, canViewAll]);

  useEffect(() => {
    void load();
    return () => { requestIdRef.current += 1; };
  }, [load]);

  return (
    <div>
      <Navbar title={`${title} · 工单明细`} leftArrow onLeftClick={() => navigate(-1)} fixed />
      <div style={{ padding: '16px', paddingTop: 64 }}>
        <p style={{ fontSize: 13, color: '#999', marginBottom: 12 }}>
          共 {total} 条{items.length < total ? `，已加载 ${items.length} 条` : ''}
        </p>

        {loading ? <Loading text="加载中..." /> : (
          items.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
              {error ? error : '暂无数据'}
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
        {!loading && (items.length < total || error) && (
          <div style={{ textAlign: 'center', marginTop: 16 }}>
            {error && items.length > 0 && <p role="alert">{error}</p>}
            <Button
              block
              variant="outline"
              disabled={loadingMore}
              onClick={() => { void load(items.length); }}
            >
              {loadingMore ? '加载中...' : error ? '重试' : '加载更多'}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
