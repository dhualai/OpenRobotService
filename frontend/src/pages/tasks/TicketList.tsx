// 任务收件箱 - 从 HelpDesk TicketsList 迁移（53KB 原文件，保留核心功能）
import { useState, useEffect, useCallback } from 'react';
import { NavBar, Tabs, TabPanel, Input, Button, Loading, Toast } from 'tdesign-mobile-react';
import { useNavigate } from 'react-router-dom';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import Pagination from '@/shared/components/Pagination';
import { normalizeStatus, STATUS_DISPLAY_MAP } from '@/shared/constants/ticket';
import { formatDateTime } from '@/shared/utils/url';

interface Ticket {
  id: string;
  title: string;
  status: string;
  priority: string;
  ticket_type: string;
  project_name?: string;
  created_at: string;
  updated_at: string;
}

export default function TicketList() {
  const navigate = useNavigate();
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const pageSize = 20;

  const request = createRequest(API_CONFIG.FQA.BASE_URL, 'FQA');

  const fetchTickets = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
        ...(search && { search }),
        ...(statusFilter !== 'all' && { status: statusFilter }),
      });
      const data = await request<{ items: Ticket[]; total: number }>(
        `/tickets/?${params.toString()}`
      );
      setTickets(data.items || []);
      setTotal(data.total || 0);
    } catch (err) {
      Toast({ message: `加载失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [page, search, statusFilter]);

  useEffect(() => {
    fetchTickets();
  }, [fetchTickets]);

  const statusTabs = [
    { value: 'all', label: '全部' },
    ...Object.entries(STATUS_DISPLAY_MAP).map(([value, label]) => ({ value, label })),
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
      <NavBar title="系统任务" fixed />
      <div style={{ paddingTop: 48 }}>
        <div style={{ padding: '12px 16px', background: '#fff' }}>
          <Input
            placeholder="搜索工单..."
            value={search}
            onChange={(v) => { setSearch(String(v)); setPage(1); }}
            clearable
          />
        </div>
        <Tabs value={statusFilter} onChange={(v) => { setStatusFilter(String(v)); setPage(1); }}>
          {statusTabs.map((tab) => (
            <TabPanel key={tab.value} value={tab.value} label={tab.label} />
          ))}
        </Tabs>
        <div style={{ padding: '0 16px' }}>
          {loading ? (
            <Loading text="加载中..." />
          ) : tickets.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 60, color: '#999' }}>暂无工单</div>
          ) : (
            tickets.map((ticket) => (
              <div
                key={ticket.id}
                onClick={() => navigate(`/tasks/${ticket.id}`)}
                style={{
                  background: '#fff',
                  borderRadius: 8,
                  padding: 16,
                  marginBottom: 12,
                  boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
                  cursor: 'pointer',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                  <h4 style={{ flex: 1, fontSize: 15, fontWeight: 500, margin: 0 }}>{ticket.title}</h4>
                  <span style={{
                    fontSize: 12,
                    padding: '2px 8px',
                    borderRadius: 4,
                    background: ticket.status === 'closed' ? '#e8f5e9' : ticket.status === 'new' ? '#e3f2fd' : '#fff3e0',
                    color: ticket.status === 'closed' ? '#2e7d32' : ticket.status === 'new' ? '#1565c0' : '#e65100',
                    whiteSpace: 'nowrap',
                    marginLeft: 8,
                  }}>
                    {normalizeStatus(ticket.status)}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 12, fontSize: 12, color: '#999' }}>
                  {ticket.project_name && <span>📁 {ticket.project_name}</span>}
                  <span>🕐 {formatDateTime(ticket.created_at)}</span>
                </div>
              </div>
            ))
          )}
          <Pagination current={page} total={total} pageSize={pageSize} onChange={setPage} />
        </div>
      </div>
    </div>
  );
}
