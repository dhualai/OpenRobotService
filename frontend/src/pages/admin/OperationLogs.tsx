// 操作记录查询 - 从 BackgroundService OperationLogs 迁移
// 样式按 macaron 设计语言：卡片搜索框 + surface-card 日志卡（用户/时间/操作/详情），原型无独立页。
import { useState, useEffect, useCallback } from 'react';
import { Loading, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import Pagination from '@/shared/components/Pagination';
import { formatDateTime } from '@/shared/utils/url';
import { MacSearch } from '@/shared/components/macaronIcons';

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
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');
  // TODO: 新后端暂无 /logs/ 接口，需后端补充操作日志端点

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
    <div className="mac-page">
      <div className="mac-search mac-search--card" style={{ marginBottom: 12 }}>
        <MacSearch size={16} />
        <input
          className="mac-search__input"
          placeholder="搜索操作记录..."
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
        />
      </div>
      {loading ? (
        <Loading text="加载中..." />
      ) : (
        <>
          {logs.map((log) => (
            <div key={log.id} className="mac-log-card">
              <div className="mac-log-card__head">
                <span className="mac-log-card__user">{log.user}</span>
                <span className="mac-log-card__time">{formatDateTime(log.timestamp)}</span>
              </div>
              <div className="mac-log-card__action">{log.action} - {log.resource}</div>
              {log.detail && <div className="mac-log-card__detail">{log.detail}</div>}
            </div>
          ))}
          {logs.length === 0 && (
            <div className="mac-empty">暂无操作记录</div>
          )}
        </>
      )}
      <Pagination current={page} total={total} pageSize={pageSize} onChange={setPage} />
    </div>
  );
}
