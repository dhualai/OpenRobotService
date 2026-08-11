import React from 'react';
import type { OperationLog, OperationType } from '@/api/ticket';
import { Loading, Empty } from 'tdesign-mobile-react';
import './OperationTimeline.css';

interface OperationTimelineProps {
  logs: OperationLog[];
  loading?: boolean;
}

// 状态映射
const STATUS_MAP: Record<string, string> = {
  new: '新建',
  in_progress: '处理中',
  pending: '待处理',
  resolved: '已解决',
  canceled: '已取消',
  closed: '已关闭',
};

// 操作类型标签映射
const OP_TYPE_LABEL: Record<OperationType, string> = {
  create: '创建',
  status_change: '状态变更',
  assign: '指派',
  escalate: '升级',
  return: '退回',
  reassign: '重新指派',
  update: '修改',
  comment: '评论',
  view: '查看',
  ai_diagnose: 'AI诊断',
  ai_assign: 'AI派单',
};

// 操作类型图标/颜色
const OP_TYPE_STYLE: Record<OperationType, { color: string; icon: string }> = {
  create: { color: '#0052D9', icon: '📋' },
  status_change: { color: '#ED7B2F', icon: '🔄' },
  assign: { color: '#1199A3', icon: '👤' },
  escalate: { color: '#D54941', icon: '⬆️' },
  return: { color: '#8B5CF6', icon: '↩️' },
  reassign: { color: '#1199A3', icon: '🔁' },
  update: { color: '#47A358', icon: '✏️' },
  comment: { color: '#6B7280', icon: '💬' },
  view: { color: '#9CA3AF', icon: '👁️' },
  ai_diagnose: { color: '#7C3AED', icon: '🤖' },
  ai_assign: { color: '#7C3AED', icon: '🤖' },
};

// 格式化时间
const formatTime = (isoString: string): string => {
  try {
    const date = new Date(isoString);
    const month = date.getMonth() + 1;
    const day = date.getDate();
    const hours = date.getHours().toString().padStart(2, '0');
    const mins = date.getMinutes().toString().padStart(2, '0');
    return `${month}月${day}日 ${hours}:${mins}`;
  } catch {
    return '';
  }
};

interface TimelineGroup {
  status: string;
  statusTime: string;
  triggerOperator: string;
  children: OperationLog[];
}

/**
 * 构建时间线分组
 * 按 to_status 非空的记录作为主节点（状态变更），其余归入对应状态分组
 */
const buildTimelineGroups = (logs: OperationLog[]): TimelineGroup[] => {
  if (!logs.length) return [];

  const groups: TimelineGroup[] = [];
  let currentGroup: TimelineGroup | null = null;

  for (const log of logs) {
    if (log.operation_type === 'status_change' && log.to_status) {
      // 新的主节点（状态变更）
      currentGroup = {
        status: log.to_status,
        statusTime: log.created_at,
        triggerOperator: log.operator_name || log.operator,
        children: [],
      };
      groups.push(currentGroup);
    } else if (currentGroup) {
      // 归入当前状态分组
      currentGroup.children.push(log);
    } else {
      // 在第一个状态变更前的操作，创建一个"初始"分组
      currentGroup = {
        status: 'initial',
        statusTime: log.created_at,
        triggerOperator: log.operator_name || log.operator,
        children: [log],
      };
      groups.push(currentGroup);
    }
  }

  return groups;
};

const OperationTimeline: React.FC<OperationTimelineProps> = ({ logs, loading = false }) => {
  if (loading) {
    return (
      <div className="op-timeline__loading">
        <Loading theme="circular" size="24" />
      </div>
    );
  }

  if (!logs.length) {
    return (
      <div className="op-timeline__empty">
        <Empty description="暂无操作记录" />
      </div>
    );
  }

  const groups = buildTimelineGroups(logs);

  return (
    <div className="op-timeline">
      {groups.map((group, groupIdx) => (
        <div className="op-timeline__group" key={groupIdx}>
          {/* 主节点：状态变更 */}
          <div className="op-timeline__main">
            <div className="op-timeline__dot op-timeline__dot--main" />
            <div className="op-timeline__content">
              <div className="op-timeline__header">
                <span className="op-timeline__status">
                  {group.status === 'initial' ? '初始状态' : STATUS_MAP[group.status] || group.status}
                </span>
                <span className="op-timeline__time">{formatTime(group.statusTime)}</span>
              </div>
              {group.triggerOperator && (
                <div className="op-timeline__operator">
                  {group.triggerOperator}
                </div>
              )}
            </div>
          </div>

          {/* 子节点：该状态下的操作 */}
          {group.children.map((log) => {
            const style = OP_TYPE_STYLE[log.operation_type];
            return (
              <div className="op-timeline__sub" key={log.id}>
                <div className="op-timeline__dot op-timeline__dot--sub" style={{ borderColor: style.color }}>
                  <span className="op-timeline__icon">{style.icon}</span>
                </div>
                <div className="op-timeline__content">
                  <div className="op-timeline__action">
                    {log.description || `${OP_TYPE_LABEL[log.operation_type]}操作`}
                  </div>
                  <div className="op-timeline__meta">
                    <span className="op-timeline__time">{formatTime(log.created_at)}</span>
                    {log.operator_name && (
                      <span className="op-timeline__operator">{log.operator_name}</span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}

          {/* 连接线（最后一个分组不需要） */}
          {groupIdx < groups.length - 1 && (
            <div className="op-timeline__connector" />
          )}
        </div>
      ))}
    </div>
  );
};

export default OperationTimeline;
