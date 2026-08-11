import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Loading, Toast } from 'tdesign-mobile-react';
import { getOperationLogs } from '@/api/ticket';
import type { OperationLog } from '@/api/ticket';
import OperationTimeline from '@/shared/components/OperationTimeline';
import './OperationLogsPage.css';

const OperationLogsPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [logs, setLogs] = useState<OperationLog[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchLogs = async () => {
      if (!id) return;
      setLoading(true);
      try {
        const data = await getOperationLogs(id);
        setLogs(data || []);
      } catch (error) {
        console.error('获取工单操作日志失败:', error);
        Toast({ theme: 'error', message: '获取操作日志失败' });
      } finally {
        setLoading(false);
      }
    };
    fetchLogs();
  }, [id]);

  return (
    <div className="operation-logs-page">
      <Navbar
        title="工单流转记录"
        leftArrow
        onLeftClick={() => navigate(-1)}
      />
      <div className="operation-logs-page__content">
        <OperationTimeline logs={logs} loading={loading} />
      </div>
    </div>
  );
};

export default OperationLogsPage;
