// 操作记录查询 - 从 BackgroundService OperationLogs 迁移
import { useState, useEffect, useCallback } from 'react';
import { Input, Loading, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import Pagination from '@/shared/components/Pagination';
import { formatDateTime } from '@/shared/utils/url';

interface Log {
  id: string;
  user: string;
  action: string;
  resource: string;
  detail: string;
  timestamp: string;
}

export default function OperationLogs() {
  const [logs, setLogs] = useState<Log[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const pageSize = 20;
  const request = createRequest(API_CONFIG.PROJECT.BASE_URL, 'Project');

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(pageSize), ...(search && { search }) });
      const data = await request<{ items: Log[]; total: number }>(`/logs/?${params}`);
      setLogs(data.items || []);
      setTotal(data.total || 0);
    } catch (err) {
      Toast({ message: `加载失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [page, search]);

  useEffect(() => { fetchLogs(); }, [fetchLogs]);

  return (
    <div style={{ padding: 16 }}>
      <Input placeholder="搜索操作记录..." value={search} onChange={(v) => { setSearch(String(v)); setPage(1); }} clearable style={{ marginBottom: 16 }} />
      {loading ? <Loading text="加载中..." /> : logs.map((log) => (
        <div key={log.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <strong>{log.user}</strong>
            <span style={{ fontSize: 12, color: '#999' }}>{formatDateTime(log.timestamp)}</span>
          </div>
          <div style={{ fontSize: 13, color: '#666' }}>{log.action} - {log.resource}</div>
          {log.detail && <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>{log.detail}</div>}
        </div>
      ))}
      <Pagination current={page} total={total} pageSize={pageSize} onChange={setPage} />
    </div>
  );
}
