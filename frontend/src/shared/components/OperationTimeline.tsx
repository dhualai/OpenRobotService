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

// ── 阶段性处理（step）二级分组 ──────────────────────────────────────────────
// 状态段（一级）内，按 step 边界日志切分「阶段段」（二级），具体操作日志为三级子项。
// step 边界：完成阶段进入下一节点 / 打回重开；数据优先取 detail.from_step/to_step，
// 缺失时回退到描述文本正则（描述格式由后端固定）。

/** step 边界日志：返回进入的新阶段名与离开的旧阶段名 */
const matchStepBoundary = (log: OperationLog): { enter: string; from: string | null } | null => {
  const desc = log.description || '';
  const isComplete = desc.includes('完成阶段') && desc.includes('进入');
  const isReopen = desc.includes('打回重开');
  if (!isComplete && !isReopen) return null;
  const detail = (log.detail || {}) as Record<string, unknown>;
  const detailTo = typeof detail.to_step === 'string' ? detail.to_step : null;
  const detailFrom = typeof detail.from_step === 'string' ? detail.from_step : null;
  if (isComplete) {
    const m = desc.match(/完成阶段「([^」]+)」，进入「([^」]+)」/);
    return { enter: detailTo || m?.[2] || '新阶段', from: detailFrom || m?.[1] || null };
  }
  const m = desc.match(/从「([^」]+)」重新开始/);
  return { enter: detailTo || m?.[1] || '新阶段', from: detailFrom };
};

/** 带阶段名的 step 日志（预期时间/协商 / 确认同意）：返回所属阶段名 */
const matchStepName = (log: OperationLog): string | null => {
  const desc = log.description || '';
  const detail = (log.detail || {}) as Record<string, unknown>;
  // 新文案：预期「X」时间（…），节点由「A」调整为「B」
  if (desc.includes('预期「') && desc.includes('时间')) {
    const m = desc.match(/预期「([^」]+)」时间/);
    return (typeof detail.to_step === 'string' ? detail.to_step : null) || m?.[1] || null;
  }
  // 旧文案（历史数据）：协商将节点「A」调整为「B」 / 协商节点「X」
  if (desc.includes('协商将节点')) {
    const m = desc.match(/协商将节点「[^」]+」调整为「([^」]+)」/);
    return (typeof detail.to_step === 'string' ? detail.to_step : null) || m?.[1] || null;
  }
  if (desc.includes('协商节点')) {
    const m = desc.match(/协商节点「([^」]+)」/);
    return (typeof detail.to_step === 'string' ? detail.to_step : null) || m?.[1] || null;
  }
  if (desc.includes('确认同意节点') || desc.includes('确认协商节点')) {
    // 兼容三种文案：确认同意节点「X」/ 确认协商节点「X」/ 确认同意协商节点「X」
    const m = desc.match(/确认(?:同意(?:协商)?|协商)节点「([^」]+)」/);
    return m?.[1] || null;
  }
  return null;
};

/** 与 step 相关但描述中不含阶段名的日志（升级上报 / 一锤定音） */
const isUnnamedStepLog = (log: OperationLog): boolean => {
  const desc = log.description || '';
  const detail = (log.detail || {}) as Record<string, unknown>;
  return log.operation_type === 'escalate'
    || detail.finalized === true
    || desc.includes('一锤定音')
    || desc.includes('升级了工单');
};

const isAgreeLog = (log: OperationLog): boolean => {
  const desc = log.description || '';
  const detail = (log.detail || {}) as Record<string, unknown>;
  // 一锤定音（升级后处理人直接定时间，curr_step_agreed=True）与"达成一致"等价
  return detail.finalized === true
    || desc.includes('一锤定音')
    // 非首次：确认同意节点并达成一致；首次响应：确认协商节点开始处理
    || (desc.includes('确认同意节点') && desc.includes('达成协商一致'))
    || (desc.includes('确认协商节点') && desc.includes('开始处理'));
};

const isNegotiateLog = (log: OperationLog): boolean => {
  const desc = log.description || '';
  // 新文案「预期「X」时间…缘由：…」+ 旧文案「协商节点/协商将节点」（历史数据）
  return desc.includes('预期「')
    || desc.includes('协商节点')
    || desc.includes('协商将节点');
};

type ChildItem =
  | { kind: 'step'; stepName: string; state: 'agreed' | 'negotiating'; escalated: boolean; round: number | null;
      ended: boolean; endTime: string | null; enterTime: string; enterOperator: string | null; logs: OperationLog[] }
  | { kind: 'log'; log: OperationLog };

interface StepBlock {
  stepName: string | null;
  hasEntry: boolean;
  enterLog: OperationLog | null;   // 进入本阶段的边界日志（完成阶段/打回重开）；首个阶段无边界日志时为 null
  ended: boolean;                  // 本阶段是否已结束（被后续「完成阶段」推进）
  endTime: string | null;          // 结束时间（= 后一个边界日志时间）
  logs: OperationLog[];
  agreeIndex: number;   // 块内（新→旧）「达成一致」日志位置，-1 无
  negotiateIndex: number;
  escalateIndex: number;
  round: number | null; // 最新一次协商回合数
}

const newStepBlock = (stepName: string | null, ended = false, endTime: string | null = null): StepBlock => ({
  stepName, hasEntry: false, enterLog: null, ended, endTime,
  logs: [], agreeIndex: -1, negotiateIndex: -1, escalateIndex: -1, round: null,
});

/**
 * 将状态段内的子日志（时间新→旧）切分为 阶段段 / 游离日志。
 * 边界日志（完成阶段/打回重开）归入其「进入」的阶段段（作为该阶段的起始事件）；
 * 阶段段内的非 step 日志（评论、改派、字段修改等）按发生时段归入当前阶段段；
 * 早于一切阶段活动的游离日志直接挂在状态段下。
 */
const buildStepItems = (children: OperationLog[]): ChildItem[] => {
  const items: ChildItem[] = [];
  let block: StepBlock | null = null;
  // 比已识别阶段更新的游离日志（如协商后的查看/评论）：当前阶段进行中，这些操作属于该阶段，
  // 待首个阶段块创建时吸收；若整段无任何阶段活动，最后作为游离日志输出。
  let leadingLoose: OperationLog[] = [];

  const absorbLeading = (b: StepBlock) => {
    if (leadingLoose.length) {
      b.logs = [...leadingLoose, ...b.logs];
      leadingLoose = [];
    }
  };

  const flushBlock = () => {
    if (!block || block.logs.length === 0) {
      block = null;
      return;
    }
    if (block.stepName) {
      // 主状态只有两种：时间达成一致 / 时间协商中。
      // 「已升级上报」是额外事实标记（该阶段内发生过升级），不替代协商状态。
      const agreed = block.agreeIndex !== -1
        && (block.negotiateIndex === -1 || block.agreeIndex < block.negotiateIndex);
      const tagState: 'agreed' | 'negotiating' = agreed ? 'agreed' : 'negotiating';
      const escalated = block.escalateIndex !== -1;
      // 进入时间/操作人：有边界日志用边界日志；否则用块内最早一条日志（首个阶段由首个协商动作开启）
      const oldestLog = block.logs[block.logs.length - 1];
      const enterTime = block.enterLog?.created_at || oldestLog?.created_at || '';
      const enterOperator = block.enterLog?.operator_name || oldestLog?.operator_name || null;
      items.push({ kind: 'step', stepName: block.stepName, state: tagState, escalated, round: block.round,
        ended: block.ended, endTime: block.endTime, enterTime, enterOperator, logs: block.logs });
    } else {
      // 无法归属到具体阶段的日志，降级为游离日志
      block.logs.forEach((log) => items.push({ kind: 'log', log }));
    }
    block = null;
  };

  children.forEach((log) => {
    const boundary = matchStepBoundary(log);
    if (boundary) {
      if (!block) block = newStepBlock(null);
      absorbLeading(block);
      block.logs.push(log);
      block.hasEntry = true;
      if (!block.stepName) block.stepName = boundary.enter;
      if (!block.enterLog) block.enterLog = log;  // 进入该阶段的边界事件
      flushBlock();
      // 边界之前（更早）的日志属于被完成/打回前的旧阶段——该阶段在此边界处结束
      block = newStepBlock(boundary.from, true, log.created_at);
      return;
    }
    const name = matchStepName(log);
    // 阶段相关日志：能识别阶段名 / 升级·一锤定音 / 协商 / 确认同意。
    // isAgreeLog/isNegotiateLog 作为兜底，即使阶段名正则失配也必须纳入阶段块并更新状态索引。
    const stepRelated = !!name || isUnnamedStepLog(log) || isAgreeLog(log) || isNegotiateLog(log);
    if (stepRelated) {
      if (!block) block = newStepBlock(null);
      absorbLeading(block);
      block.logs.push(log);
      if (name && !block.stepName) block.stepName = name;
      markStepLogIndex(block, log);
    } else if (block) {
      block.logs.push(log);
      markStepLogIndex(block, log);  // 双保险：游离归入的日志也更新状态索引
    } else {
      leadingLoose.push(log);
    }
  });
  flushBlock();
  // 该状态段内从未出现阶段活动：缓存的游离日志直接输出
  leadingLoose.forEach((log) => items.push({ kind: 'log', log }));

  return items;
};

/** 日志进入阶段块后，更新 协商/一致/升级 的最新位置索引与回合数（logs 为新→旧，索引越小越新） */
const markStepLogIndex = (block: StepBlock, log: OperationLog) => {
  const idx = block.logs.length - 1;
  if (isAgreeLog(log) && block.agreeIndex === -1) block.agreeIndex = idx;
  if (isNegotiateLog(log)) {
    if (block.negotiateIndex === -1) block.negotiateIndex = idx;
    const r = (log.detail as Record<string, unknown> | null)?.negotiation_round;
    if (typeof r === 'number') block.round = r;
  }
  if (log.operation_type === 'escalate' && block.escalateIndex === -1) block.escalateIndex = idx;
};

const STEP_STATE_TAG: Record<'agreed' | 'negotiating', string> = {
  agreed: '时间达成一致',
  negotiating: '时间协商中',
};

/**
 * 判断日志是否为状态转换节点（create 或 status_change）。
 * create 与初始 status_change 通常代表同一次状态进入，需要去重。
 */
const isStatusTransition = (log: OperationLog): boolean =>
  (log.operation_type === 'status_change' && !!log.to_status) || log.operation_type === 'create';

const getStatusKey = (log: OperationLog): string => log.to_status || 'new';

/**
 * 构建时间线分组
 * logs 按时间倒序（最新在前）。
 * 状态变更(status_change)作为主节点，其后的操作（时间更新）归入该状态分组。
 *
 * 去重逻辑：create 与初始 status_change 指向同一状态且时间戳相同时，
 * 视为同一次状态转换；二者之间可能夹着同时间戳的 view 等非转换日志，
 * 这些日志应归入该分组的 children，而不是把它们拆成两个分组。
 */
const buildTimelineGroups = (logs: OperationLog[]): TimelineGroup[] => {
  if (!logs.length) return [];

  const groups: TimelineGroup[] = [];
  let pendingChildren: OperationLog[] = [];

  for (let i = 0; i < logs.length; i++) {
    const log = logs[i];
    if (isStatusTransition(log)) {
      const statusKey = getStatusKey(log);
      const currentTime = log.created_at;
      // 收集同时间戳的非转换日志作为本分组子节点，
      // 并跳过指向相同状态、相同时间戳的重复转换日志（create + status_change）
      const extraChildren: OperationLog[] = [];
      while (i + 1 < logs.length) {
        const next = logs[i + 1];
        if (
          isStatusTransition(next) &&
          getStatusKey(next) === statusKey &&
          next.created_at === currentTime
        ) {
          // 同一次状态转换的重复记录，跳过
          i++;
        } else if (!isStatusTransition(next) && next.created_at === currentTime) {
          // 同时间戳的非转换日志（如 view），归入本分组
          extraChildren.push(next);
          i++;
        } else {
          break;
        }
      }
      groups.push({
        status: statusKey,
        statusTime: log.created_at,
        triggerOperator: log.operator_name || log.operator,
        children: [...pendingChildren, ...extraChildren],
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

  /** 渲染单条操作日志（三级子项），游离日志与阶段段内日志共用 */
  const renderSubLog = (log: OperationLog, key: React.Key) => {
    const style = OP_TYPE_STYLE[log.operation_type];
    const subCls = `op-segment__sub${
      log.operation_type === 'reassign' ? ' op-segment__sub--reassign' : ''
    }${isAgreeLog(log) ? ' op-segment__sub--agreed' : ''}`;
    return (
      <div className={subCls} key={key}>
        <div className="op-segment__sub-dot" style={{ borderColor: isAgreeLog(log) ? '#10b981' : style.color }}>
          <span className="op-segment__sub-icon">{isAgreeLog(log) ? '✅' : style.icon}</span>
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
  };

  return (
    <div className="op-timeline">
      {groups.map((group, idx) => {
        const color = STATUS_COLOR[group.status] || '#9ca3af';
        const statusLabel = STATUS_MAP[group.status] || group.status;
        const isLatest = idx === 0;
        // 下一个（更新的）状态：本状态结束 = 下一个状态开始。
        // 倒序中 idx-1 是时间更新的分组，取其 statusTime 作为本状态「结束」时间。
        const nextGroup = idx > 0 ? groups[idx - 1] : null;

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
                  {!isLatest && nextGroup ? (
                    <span className="op-segment__time">{formatTime(nextGroup.statusTime)}</span>
                  ) : null}
                </div>
              </div>

              {/* 子节点操作：状态段内再按「阶段」切二级节点 */}
              {group.children.length > 0 && (
                <div className="op-segment__children">
                  {buildStepItems(group.children).map((item, iIdx) => {
                    if (item.kind === 'log') {
                      return renderSubLog(item.log, `loose-${iIdx}`);
                    }
                    return (
                      <div className={`op-segment__step${item.ended ? ' op-segment__step--ended' : ''}`} key={`step-${iIdx}`}>
                        <div className="op-segment__step-head">
                          {item.ended ? (
                            <>
                              <span className="op-segment__step-marker">🚩</span>
                              <span className="op-segment__step-name">结束「{item.stepName}」阶段</span>
                              {item.endTime && (
                                <span className="op-segment__time">{formatTime(item.endTime)}</span>
                              )}
                            </>
                          ) : (
                            <>
                              <span className="op-segment__step-marker">🚩</span>
                              <span className="op-segment__step-name">阶段「{item.stepName}」进行中</span>
                              <span className={`op-segment__step-tag op-segment__step-tag--${item.state}`}>
                                {STEP_STATE_TAG[item.state]}
                              </span>
                              {item.escalated && (
                                <span className="op-segment__step-tag op-segment__step-tag--escalated">
                                  已升级上报
                                </span>
                              )}
                              {item.round !== null && (
                                <span className="op-segment__step-round">回合 {item.round}</span>
                              )}
                            </>
                          )}
                        </div>
                        <div className="op-segment__step-children">
                          {item.logs.map((log) => renderSubLog(log, `step-${iIdx}-${log.id}`))}
                        </div>
                        <div className="op-segment__step-foot">
                          <span className="op-segment__step-marker">🚩</span>
                          <span className="op-segment__step-enter">进入「{item.stepName}」阶段</span>
                          {item.enterTime && (
                            <span className="op-segment__time">{formatTime(item.enterTime)}</span>
                          )}
                          {item.enterOperator && (
                            <span className="op-segment__sub-operator">{item.enterOperator}</span>
                          )}
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
