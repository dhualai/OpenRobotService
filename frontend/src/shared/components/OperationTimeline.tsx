import React from 'react';
import type { OperationLog, OperationType } from '@/api/ticket';
import { formatDuration } from '@/api/ticket';
import { parseUtcDate } from '@/shared/utils/url';
import { Loading, Empty } from 'tdesign-mobile-react';
import './OperationTimeline.css';

interface OperationTimelineProps {
  logs: OperationLog[];
  loading?: boolean;
}

// 状态中文映射
const STATUS_MAP: Record<string, string> = {
  new: '新建',
  in_progress: '处理中',
  pending: '待处理',
  resolved: '已解决',
  canceled: '已取消',
  closed: '已关闭',
  initial: '初始状态',
};

// 状态颜色映射
const STATUS_COLOR: Record<string, string> = {
  new: '#3b82f6',        // 蓝色 - 新建
  in_progress: '#f59e0b', // 橙色 - 处理中
  pending: '#6b7280',     // 灰色 - 待处理
  resolved: '#10b981',    // 绿色 - 已解决
  canceled: '#ef4444',    // 红色 - 已取消
  closed: '#4b5563',      // 深灰 - 已关闭
  initial: '#9ca3af',
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

// 操作类型图标
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
  const date = parseUtcDate(isoString);
  if (!date) return '';
  const month = date.getMonth() + 1;
  const day = date.getDate();
  const hours = date.getHours().toString().padStart(2, '0');
  const mins = date.getMinutes().toString().padStart(2, '0');
  return `${month}月${day}日 ${hours}:${mins}`;
};

interface TimelineGroup {
  status: string;
  statusTime: string;
  triggerOperator: string;
  children: OperationLog[];
}

/**
 * 构建时间线分组
 * logs 按时间倒序（最新在前）。
 * 状态变更(status_change)作为主节点，其后的操作（时间更新）归入该状态分组。
 */
const buildTimelineGroups = (logs: OperationLog[]): TimelineGroup[] => {
  if (!logs.length) return [];

  const groups: TimelineGroup[] = [];
  let pendingChildren: OperationLog[] = [];

  for (let i = 0; i < logs.length; i++) {
    const log = logs[i];
    if ((log.operation_type === 'status_change' && log.to_status) || log.operation_type === 'create') {
      const statusKey = log.to_status || 'new';
      // 跳过紧随其后的、指向相同状态的 status_change（冗余日志）
      while (i + 1 < logs.length) {
        const next = logs[i + 1];
        if (next.operation_type === 'status_change' && next.to_status === statusKey) {
          i++;
        } else {
          break;
        }
      }
      groups.push({
        status: statusKey,
        statusTime: log.created_at,
        triggerOperator: log.operator_name || log.operator,
        children: pendingChildren,
      });
      pendingChildren = [];
    } else {
      pendingChildren.push(log);
    }
  }

  if (pendingChildren.length && !groups.length) {
    groups.push({
      status: 'initial',
      statusTime: pendingChildren[0]?.created_at || '',
      triggerOperator: '',
      children: pendingChildren,
    });
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
      {groups.map((group, idx) => {
        const color = STATUS_COLOR[group.status] || '#9ca3af';
        const statusLabel = STATUS_MAP[group.status] || group.status;
        const isLatest = idx === 0;
        // 下一个状态（时间更旧，即此状态结束后转入的状态）；倒序中 idx+1 是时间更旧的
        const prevGroup = idx < groups.length - 1 ? groups[idx + 1] : null;

        return (
          <div className="op-segment" key={idx}>
            {/* 左侧彩色状态条 + 两端圆点 */}
            <div className="op-segment__track">
              {/* 顶部圆点（状态起点） */}
              <div className="op-segment__dot op-segment__dot--start" style={{ backgroundColor: color, borderColor: color }} />
              {/* 彩色条主体（两端圆点之间） */}
              <div className="op-segment__bar" style={{ backgroundColor: color }} />
              {/* 底部圆点（状态终点） */}
              <div className="op-segment__dot op-segment__dot--end" style={{ backgroundColor: color, borderColor: color }} />
            </div>

            {/* 右侧内容区 */}
            <div className="op-segment__content">
              {/* 状态起点：结束该状态 */}
              <div className="op-segment__start">
                <div className="op-segment__start-row">
                  <span className="op-segment__status" style={{ color }}>
                    {isLatest ? '持续至今' : `结束「${statusLabel}」状态`}
                  </span>
                  {!isLatest && prevGroup ? (
                    <span className="op-segment__time">{formatTime(prevGroup.statusTime)}</span>
                  ) : null}
                </div>
              </div>

              {/* 子节点操作 */}
              {group.children.length > 0 && (
                <div className="op-segment__children">
                  {group.children.map((log) => {
                    const style = OP_TYPE_STYLE[log.operation_type];
                    return (
                      <div className="op-segment__sub" key={log.id}>
                        <div className="op-segment__sub-dot" style={{ borderColor: style.color }}>
                          <span className="op-segment__sub-icon">{style.icon}</span>
                        </div>
                        <div className="op-segment__sub-content">
                          <div className="op-segment__action">
                            {log.description || `${OP_TYPE_LABEL[log.operation_type]}操作`}
                            {log.operation_type === 'view' && formatDuration(log.duration_seconds) && (
                              <span className="op-segment__duration">（停留 {formatDuration(log.duration_seconds)}）</span>
                            )}
                          </div>
                          <div className="op-segment__sub-meta">
                            <span className="op-segment__time">{formatTime(log.created_at)}</span>
                            {log.operator_name && (
                              <span className="op-segment__sub-operator">{log.operator_name}</span>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* 状态终点：进入该状态 */}
              <div className="op-segment__end">
                <div className="op-segment__start-row">
                  <span className="op-segment__status" style={{ color }}>
                    进入「{statusLabel}」状态
                  </span>
                  <span className="op-segment__time">{formatTime(group.statusTime)}</span>
                </div>
                {group.triggerOperator && (
                  <div className="op-segment__operator">由 {group.triggerOperator} 操作</div>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default OperationTimeline;
