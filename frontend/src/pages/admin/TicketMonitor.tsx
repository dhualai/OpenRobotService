// 工单状态监测 —— 展示所有历史工单，支持状态/类型筛选 + 分页
import { useState, useEffect, useCallback } from 'react';
import { Button, Toast, Loading, Tabs, Badge } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { formatDateTime } from '@/shared/utils/url';

interface Ticket {
  id: number;
  session_id: string;
  title: string;
  description: string;
  type: string;
  priority: string;
  status: string;
  contact: string;
  location: string;
  robot_type: string;
  fault_code: string;
  severity: string;
  created_at: string;
  updated_at: string;
}

interface TicketStats {
  total: number;
  by_status: Record<string, number>;
}

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: '待派单', color: '#e37318' },
  dispatched: { label: '已派单', color: '#0052d9' },
  in_progress: { label: '处理中', color: '#2ba471' },
  resolved: { label: '已解决', color: '#00a870' },
  closed: { label: '已关闭', color: '#999' },
};

const TYPE_MAP: Record<string, string> = {
  problem: '报障',
  bug: '缺陷',
  feature: '功能需求',
  support: '支持请求',
  other: '其他',
};

const STATUS_TABS = [
  { value: '', label: '全部' },
  { value: 'pending', label: '待派单' },
  { value: 'dispatched', label: '已派单' },
  { value: 'in_progress', label: '处理中' },
  { value: 'resolved', label: '已解决' },
  { value: 'closed', label: '已关闭' },
];

const PAGE_SIZE = 20;

export default function TicketMonitor() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [stats, setStats] = useState<TicketStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState('');
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const adminRequest = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const fetchStats = useCallback(async () => {
    try {
      const res = await adminRequest<{ code: number; data: TicketStats }>('/tickets/stats');
      if (res.code === 0) setStats(res.data);
    } catch { /* 统计失败不影响列表 */ }
  }, []);

  const fetchTickets = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set('skip', String(page * PAGE_SIZE));
      params.set('limit', String(PAGE_SIZE));
      if (statusFilter) params.set('status', statusFilter);

      const res = await adminRequest<{ code: number; data: { total: number; items: Ticket[] } }>(
        `/tickets/?${params.toString()}`,
      );
      if (res.code === 0) {
        setTickets(res.data.items || []);
        setTotal(res.data.total || 0);
      }
    } catch (err) {
      Toast({ message: String(err), theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter]);

  useEffect(() => {
    fetchStats();
  }, []);

  useEffect(() => {
    fetchTickets();
  }, [fetchTickets]);

  const totalPages = Math.ceil(total / PAGE_SIZE);

  const toggleExpand = (id: number) => {
    setExpandedId(expandedId === id ? null : id);
  };

  return (
    <div style={{ padding: 16 }}>
      {/* 统计卡片行 */}
      {stats && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, marginBottom: 16 }}>
          <StatCard label="总工单" value={stats.total} color="#0052d9" />
          <StatCard label="待处理" value={(stats.by_status.pending || 0) + (stats.by_status.dispatched || 0) + (stats.by_status.in_progress || 0)} color="#e37318" />
          <StatCard label="已关闭" value={(stats.by_status.resolved || 0) + (stats.by_status.closed || 0)} color="#00a870" />
        </div>
      )}

      {/* 状态筛选 Tab */}
      <div style={{ display: 'flex', gap: 6, overflow: 'auto', marginBottom: 12, paddingBottom: 4 }}>
        {STATUS_TABS.map((tab) => (
          <Button
            key={tab.value}
            size="small"
            variant={statusFilter === tab.value ? 'base' : 'outline'}
            theme={statusFilter === tab.value ? 'primary' : 'default'}
            onClick={() => { setStatusFilter(tab.value); setPage(0); }}
          >
            {tab.label}
          </Button>
        ))}
      </div>

      {loading ? <Loading text="加载工单..." /> : (
        <>
          {tickets.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>暂无工单数据</div>
          ) : (
            tickets.map((t) => {
              const st = STATUS_MAP[t.status] || { label: t.status, color: '#999' };
              const isExpanded = expandedId === t.id;
              return (
                <div
                  key={t.id}
                  style={{
                    background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10,
                    boxShadow: '0 1px 3px rgba(0,0,0,0.06)', cursor: 'pointer',
                  }}
                  onClick={() => toggleExpand(t.id)}
                >
                  {/* 标题行 */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: 15, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {t.title || '无标题'}
                      </div>
                      <div style={{ fontSize: 12, color: '#999', marginTop: 2 }}>
                        {TYPE_MAP[t.type] || t.type} · {t.priority}优先级
                        {t.robot_type ? ` · ${t.robot_type}` : ''}
                      </div>
                    </div>
                    <span style={{
                      fontSize: 11, padding: '2px 8px', borderRadius: 10,
                      background: st.color + '18', color: st.color, fontWeight: 500,
                      whiteSpace: 'nowrap', marginLeft: 8,
                    }}>
                      {st.label}
                    </span>
                  </div>

                  {/* 展开详情 */}
                  {isExpanded && (
                    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid #f0f0f0', fontSize: 13 }}>
                      <DetailRow label="描述" value={t.description} />
                      <DetailRow label="现场位置" value={t.location} />
                      <DetailRow label="联系人" value={t.contact} />
                      <DetailRow label="故障码" value={t.fault_code} />
                      <DetailRow label="严重程度" value={t.severity} />
                      <DetailRow label="创建时间" value={t.created_at ? formatDateTime(t.created_at) : ''} />
                      <DetailRow label="更新时间" value={t.updated_at ? formatDateTime(t.updated_at) : ''} />
                    </div>
                  )}
                </div>
              );
            })
          )}

          {/* 分页 */}
          {totalPages > 1 && (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 12, marginTop: 16 }}>
              <Button size="small" variant="outline" disabled={page === 0} onClick={() => setPage(page - 1)}>
                上一页
              </Button>
              <span style={{ fontSize: 13, color: '#666' }}>{page + 1} / {totalPages}</span>
              <Button size="small" variant="outline" disabled={page >= totalPages - 1} onClick={() => setPage(page + 1)}>
                下一页
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={{ background: '#fff', borderRadius: 8, padding: '12px 10px', boxShadow: '0 1px 3px rgba(0,0,0,0.08)', textAlign: 'center' }}>
      <div style={{ fontSize: 24, fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: 12, color: '#999', marginTop: 2 }}>{label}</div>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string | undefined | null }) {
  if (!value) return null;
  return (
    <div style={{ display: 'flex', marginBottom: 6 }}>
      <span style={{ color: '#999', minWidth: 70, flexShrink: 0 }}>{label}</span>
      <span style={{ color: '#333', wordBreak: 'break-all' }}>{value}</span>
    </div>
  );
}
