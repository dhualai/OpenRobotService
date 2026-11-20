import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { useNavigate } from 'react-router-dom';
import { getOperationLogs, formatDuration, type OperationLog } from '@/api/ticket';
import { parseUtcDate } from '@/shared/utils/url';
import { navigateInWechat } from '@/shared/utils/wechatJsSdk';

/**
 * 工单动态卡片（共享组件）：系统任务详情页 TaskDetailPage 与历史工单详情页 TicketDetailPage 复用。
 * 数据源：GET /api/tasks/{taskId}/operation-logs；点击跳转 /tasks/{taskId}/operations（OperationLogsPage）。
 */
interface TicketDynamicsCardProps {
  /** Task.id（历史工单需剥掉 db_ 前缀） */
  taskId: number | string;
}

export default function TicketDynamicsCard({ taskId }: TicketDynamicsCardProps) {
  const navigate = useNavigate();
  const [opLogs, setOpLogs] = useState<OperationLog[]>([]);

  useEffect(() => {
    if (!taskId) return;
    getOperationLogs(taskId)
      .then((data) => setOpLogs(data || []))
      .catch(() => setOpLogs([]));
  }, [taskId]);

  // 后端返回 naive datetime（UTC），需经 parseUtcDate 标记为 UTC 后由浏览器按本地时区自动 +8
  const formatTime = (ts: string) => {
    const d = parseUtcDate(ts);
    if (!d) return '';
    return `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  };

  const items = opLogs.map((l) => {
    // 查看记录追加停留时长
    const dur = l.operation_type === 'view' ? formatDuration(l.duration_seconds) : '';
    const suffix = dur ? `（停留 ${dur}）` : '';
    return {
      key: String(l.id),
      text: `${formatTime(l.created_at)} · ${l.description || l.operation_type}${suffix}`,
    };
  });

  // 复制一份用于无缝循环滚动
  const loopItems = [...items, ...items];
  const scrollStyle = { '--count': items.length } as CSSProperties;
  const scrollAttrs = items.length <= 3 ? { 'data-count-lte': '3' } : {};

  return (
    <div
      className="detail-card ticket-dynamics-card"
      onClick={() => navigateInWechat(navigate, `/tasks/${taskId}/operations`)}
      role="button"
      tabIndex={0}
    >
      <h4 className="detail-card__h">
        工单动态
        <span className="ticket-dynamics-card__more">查看全部 ›</span>
      </h4>
      {opLogs.length === 0 ? (
        <p className="detail-card__body detail-card__body--muted">暂无动态</p>
      ) : (
        <div className="ticket-dynamics-scroll" style={scrollStyle} {...scrollAttrs}>
          <div className="ticket-dynamics-scroll__track">
            {loopItems.map((it, i) => (
              <div className="ticket-dynamics-scroll__item" key={`${it.key}-${i}`}>{it.text}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
