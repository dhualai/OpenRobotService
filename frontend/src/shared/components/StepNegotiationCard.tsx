import { Button, Popup, Form, FormItem, Textarea } from 'tdesign-mobile-react';
import { DatePicker } from 'antd';
import dayjs from 'dayjs';
import { AlarmClock } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { Toast } from 'tdesign-mobile-react';
import type { useStepNegotiation, StepNegotiationTicket } from '@/shared/hooks/useStepNegotiation';
import { getDeadlineRange, makeDisabledDate, makeDisabledTime, parseDeadlineString } from '@/shared/utils/deadline';
import { formatRawDateTime } from '@/shared/utils/url';

function TestMarkedButton({
  testId,
  ...props
}: ComponentProps<typeof Button> & { testId: string }) {
  return (
    <>
      <span data-testid={testId} hidden />
      <Button {...props} />
    </>
  );
}

/** 特殊 step（等待外部条件 sequence=-2 / 等待前置工单 sequence=-1）的条件输入块。
 *  协商弹窗与完成弹窗复用：label 自适应 step 类型；等待前置工单时额外显示已有前置工单卡片 + +添加 按钮。 */
function SpecialStepConditionBlock({
  step,
  reason,
  onReasonChange,
  predecessorIds,
  onAddPredecessor,
  testIdPrefix,
}: {
  step: { id: number; step_name: string; sequence: number } | null | undefined;
  reason: string;
  onReasonChange: (v: string) => void;
  predecessorIds?: number[];
  onAddPredecessor?: () => void;
  testIdPrefix?: string;
}) {
  if (!step || step.sequence >= 0) return null;
  const label = step.sequence === -2 ? '等待外部条件的理由' : '等待前置工单的理由';
  const placeholder = step.sequence === -2
    ? '请说明需要等待的外部条件是什么（例如：等待客户确认方案 / 等待第三方接口上线 / 等待安全审计通过）'
    : '请说明为什么需要等待前置工单（例如：当前工单依赖工单 #123 的修复结果）';
  return (
    <>
      <div style={{ marginBottom: 12 }}>
        <label style={{ display: 'block', fontSize: 13, color: 'var(--muted-foreground)', marginBottom: 4 }}>
          {label}<span style={{ color: 'var(--danger)' }}>*</span>
        </label>
        <Textarea
          data-testid={testIdPrefix ? `${testIdPrefix}-reason` : undefined}
          value={reason}
          onChange={(v) => onReasonChange(String(v))}
          placeholder={placeholder}
          autosize={{ minRows: 3, maxRows: 6 }}
          maxlength={500}
        />
      </div>
      {step.sequence === -1 && (
        <div style={{ marginBottom: 12, padding: 10, background: 'var(--muted)', borderRadius: 'var(--radius-sm)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>前置工单</span>
            <Button size="small" theme="primary" onClick={onAddPredecessor}>+ 添加</Button>
          </div>
          {(predecessorIds ?? []).length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>尚无前置工单，点击上方 + 添加</div>
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {(predecessorIds ?? []).map((pid) => (
                <span key={pid} style={{
                  padding: '2px 8px', background: 'var(--card)', borderRadius: 4,
                  fontSize: 12, color: 'var(--foreground)', border: '1px solid var(--border)',
                }}>#{pid}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}

/** 工单阶段性处理卡所需的工单信息（阶段字段 + 展示/弹窗所需的基础字段） */
export interface StepCardTicket extends StepNegotiationTicket {
  status?: string;
  priority?: string;
  created_at?: string;
  comments?: Array<{ content?: string; created_at?: string }>;
  metadata_info?: Record<string, unknown>;
  predecessor_ids?: number[];
}

interface StepRoles {
  isAssignee: boolean;
  isReporter: boolean;
  /** 代他人提单：被代理人（**仅已确认跟进**时方可参与协商，归 creator 侧）。
   *  pending 期间由调用方传 false（决策 10：待确认期只读、不参与回合协商）。 */
  isPrincipal?: boolean;
}

type StepNegotiation = ReturnType<typeof useStepNegotiation>;

interface StepNegotiationCardProps {
  /** useStepNegotiation 的返回值（由页面顶层调用，与顶部操作按钮共享 stepTemplate/弹窗状态） */
  negotiation: StepNegotiation;
  detail: StepCardTicket | null;
  roles: StepRoles;
  /** 最末阶段结束 → 父组件结束工单（打开解决方式弹窗） */
  onResolve: () => void;
  /** 有异议升级上报 → 父组件打开升级上报弹窗 */
  onEscalate: (round: number, maxRound: number) => void;
  /** 重新指派 → 父组件打开重新指派弹窗 */
  onReassign: () => void;
  /** 暂停请求回应：确认暂停（→pending，无理由）或驳回（→in_progress，必须带理由） */
  onPauseResponse?: (nextStatus: 'pending' | 'in_progress', rejectReason?: string) => void;
  /** 无法解决请求回应：确认（→unsolvable，无理由）或驳回（→in_progress，必须带理由） */
  onUnsolvableResponse?: (nextStatus: 'unsolvable' | 'in_progress', rejectReason?: string) => void;
}

/**
 * 工单阶段性处理（协商节点）卡片：系统任务详情页与历史工单详情页复用。
 * 内部不调用 hook，由页面顶层调用 useStepNegotiation 后把返回值传入（与顶部「未解决打回」等按钮共享状态）。
 * 渲染：协商卡 + 协商节点时间 / 完成阶段 / 设置节点时间 3 个弹窗。
 * 结束工单 / 升级上报 / 重新指派通过回调委托父组件。
 */
export default function StepNegotiationCard({
  negotiation,
  detail,
  roles,
  onResolve,
  onEscalate,
  onReassign,
  onPauseResponse,
  onUnsolvableResponse,
}: StepNegotiationCardProps) {
  const {
    stepTemplate, responding, completing,
    showNegotiateStepPopup, setShowNegotiateStepPopup,
    negotiateStepId, setNegotiateStepId,
    negotiateEndTime, setNegotiateEndTime,
    negotiateReason, setNegotiateReason, submittingNegotiate,
    showCompleteStepPopup, setShowCompleteStepPopup,
    completeNextStepId, setCompleteNextStepId,
    completeNextEndTime, setCompleteNextEndTime, submittingComplete,
    showSetStepTimePopup, setShowSetStepTimePopup,
    setStepTimeValue, setSetStepTimeValue, submittingSetStepTime,
    handleRespond, handleStepComplete, handleSetStepTime, handleNegotiateStep,
    openNegotiate, openCompleteStep,
    refreshDetail,
  } = negotiation;

  // 稳定 DatePicker 受控 value 引用（内联 parse 每次渲染生成新 dayjs 实例会触发 rc-picker 受控同步，重置面板暂存值）
  const negotiateEndTimeValue = useMemo(
    () => (negotiateEndTime ? parseDeadlineString(negotiateEndTime) : null),
    [negotiateEndTime],
  );
  const completeNextEndTimeValue = useMemo(
    () => (completeNextEndTime ? parseDeadlineString(completeNextEndTime) : null),
    [completeNextEndTime],
  );
  const setStepTimePickerValue = useMemo(
    () => (setStepTimeValue ? parseDeadlineString(setStepTimeValue) : null),
    [setStepTimeValue],
  );

  const { isAssignee, isReporter, isPrincipal = false } = roles;
  // 被代理人（已确认跟进）与代理人同侧（creator 侧，代表问题方）：
  // 谈判对象始终是「问题方 ↔ 处理人」，与后端 _actor_side 口径一致（决策 7）。
  const isCreatorSide = isReporter || isPrincipal;

  const status = (detail?.status || '').toLowerCase();
  // 终态（已解决/已关闭/已取消）：保留节点信息展示，但隐藏卡内所有操作按钮
  const isTerminal = ['resolved', 'closed', 'canceled', 'cancelled'].includes(status);
  // 暂停请求中：处理人已请求暂停、等待提单人确认——节点信息保留、回合协商冻结
  const isPauseRequested = status === 'pending_requested';
  // 无法解决确认中：处理人标记无法解决、等待提单人确认——同暂停，协商冻结
  const isUnsolvableRequested = status === 'unsolvable_requested';
  // 特殊 step（等待外部条件 sequence=-2 / 等待前置工单 sequence=-1）：与正常推进序无关
  const normalSteps = stepTemplate.filter((s) => s.sequence >= 0);
  const specialSteps = stepTemplate.filter((s) => s.sequence < 0);
  const total = normalSteps.length;
  const currIdx = normalSteps.findIndex((s) => s.id === detail?.curr_step_id);
  const stepName = detail?.curr_step_name || (currIdx >= 0 ? normalSteps[currIdx].step_name : '');
  // 当前 step 是否特殊 step（用于卡片正文展示条件理由）
  const currentStepRow = stepTemplate.find((s) => s.id === detail?.curr_step_id);
  const currIsSpecial = !!currentStepRow && currentStepRow.sequence < 0;
  // 暂停请求理由（metadata_info.pause_reason，仅 pending_requested 状态存在）
  const pauseReason = (detail?.metadata_info as Record<string, unknown> | undefined)?.pause_reason as string | undefined;
  // 无法解决理由（metadata_info.unsolvable_reason，仅 unsolvable_requested 状态存在）
  const unsolvableReason = (detail?.metadata_info as Record<string, unknown> | undefined)?.unsolvable_reason as string | undefined;
  // 上一次驳回理由（metadata_info.reject_reason，仅 IN_PROGRESS 状态存在，处理人侧可见）
  const rejectReason = (detail?.metadata_info as Record<string, unknown> | undefined)?.reject_reason as string | undefined;
  // 特殊 step 条件理由
  const waitingExternalReason = (detail?.metadata_info as Record<string, unknown> | undefined)?.waiting_external_reason as string | undefined;
  const waitingPredecessorReason = (detail?.metadata_info as Record<string, unknown> | undefined)?.waiting_predecessor_reason as string | undefined;

  // 驳回暂停理由弹窗（本地 state，仅组件内部使用）
  const [showRejectPopup, setShowRejectPopup] = useState(false);
  const [rejectReasonDraft, setRejectReasonDraft] = useState('');
  // 完成弹窗：特殊 step 条件理由草稿
  const [completeReasonDraft, setCompleteReasonDraft] = useState('');
  // 等待前置工单 +添加 弹窗
  const [showAddPredecessorPopup, setShowAddPredecessorPopup] = useState(false);
  const [predecessorIdInput, setPredecessorIdInput] = useState('');
  // 前置工单 ID 兜底缓存：详情接口如果漏返回 predecessor_ids，就自己调 relations 反推。
  // 以 detail.id 为 key 缓存，避免每次弹窗打开都重复请求。
  const [fallbackPredecessorIds, setFallbackPredecessorIds] = useState<Record<string, number[]>>({});

  const openAddPredecessorPopup = () => {
    setPredecessorIdInput('');
    setShowAddPredecessorPopup(true);
  };

  /** 统一取当前工单的前置工单 ID 列表：优先用详情接口字段，缺失时从 relations 接口兜底。 */
  const currentPredecessorIds = useMemo(() => {
    const direct = detail?.predecessor_ids;
    if (direct && direct.length > 0) return direct;
    if (detail?.id) return fallbackPredecessorIds[String(detail.id)] ?? [];
    return [];
  }, [detail?.id, detail?.predecessor_ids, fallbackPredecessorIds]);

  /** 当详情接口漏返回 predecessor_ids 时，自动调 relations 兜底（只拉一次，缓存结果）。 */
  useEffect(() => {
    const taskId = detail?.id;
    if (!taskId) return;
    const direct = detail?.predecessor_ids;
    if (direct && direct.length > 0) return;          // 详情接口有就不用兜底
    const key = String(taskId);
    if (fallbackPredecessorIds[key]) return;          // 已兜底过就不重复拉
    let cancelled = false;
    (async () => {
      try {
        const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
        const rels: Array<{ source_task_id: number; target_task_id: number; relation_type: string }> =
          await request(`/${taskId}/relations`);
        if (cancelled) return;
        const preds = rels
          .filter((r) => r.relation_type === 'predecessor' && r.source_task_id === Number(taskId))
          .map((r) => r.target_task_id);
        setFallbackPredecessorIds((prev) => ({ ...prev, [key]: preds }));
      } catch {
        // 兜底拉失败就静默，不影响主流程
      }
    })();
    return () => { cancelled = true; };
  }, [detail?.id, detail?.predecessor_ids?.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleAddPredecessor = async () => {
    const raw = predecessorIdInput.trim().replace(/^#/, '');
    const targetId = Number(raw);
    if (!detail?.id || !targetId || Number.isNaN(targetId)) {
      Toast({ message: '请输入有效的工单 ID（如 #123）', theme: 'warning' });
      return;
    }
    if (targetId === Number(detail.id)) {
      Toast({ message: '不能把自己设为前置工单', theme: 'warning' });
      return;
    }
    try {
      const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
      await request(`/${detail.id}/relations`, {
        method: 'POST',
        body: JSON.stringify({ target_task_id: targetId, relation_type: 'predecessor' }),
      });
      Toast({ message: '前置工单已添加', theme: 'success' });
      setShowAddPredecessorPopup(false);
      await refreshDetail();
    } catch (err: any) {
      Toast({ message: err?.message || '添加失败', theme: 'error' });
    }
  };
  // 尚未开始阶段性处理（当前节点未初始化）→ 整卡隐藏
  if (!stepName) return null;

  const endtimeText = detail?.curr_step_endtime ? formatRawDateTime(detail.curr_step_endtime) : '';
  // 最新协商理由：从系统评论中解析（negotiate-step 评论含"缘由："/旧文案"理由："）。
  const latestNegotiateReason = (() => {
    const list = detail?.comments ?? [];
    const sorted = [...list].sort(
      (a, b) => new Date(a.created_at ?? '').getTime() - new Date(b.created_at ?? '').getTime(),
    );
    for (let i = sorted.length - 1; i >= 0; i--) {
      const c = sorted[i]?.content || '';
      if (c.includes('完成阶段「') || c.includes('标记工单未解决') || c.includes('打回重开')) return '';
      const m = c.match(/[理缘]由：([\s\S]+)$/);
      if (m && (c.includes('预期「') || c.includes('协商节点') || c.includes('协商将节点'))) {
        return m[1]
          .replace(/（本轮协商回合[\s\S]*$/, '')
          .replace(/（首次响应[\s\S]*$/, '')
          .replace(/⚠️[\s\S]*$/, '')
          .trim();
      }
    }
    return '';
  })();
  const stepAgreed = !!detail?.curr_step_agreed;
  // 暂停请求中 / 无法解决确认中 均冻结协商/响应操作
  const canRespond = !isPauseRequested && !isUnsolvableRequested && !!detail?.curr_step_id && (status === 'new' || (status === 'in_progress' && !stepAgreed));
  const canNegotiate = !isPauseRequested && !isUnsolvableRequested && !!detail?.curr_step_id;
  const currSeq = currIdx >= 0 ? normalSteps[currIdx].sequence : null;
  // hasNext：当前在正常 step 且有后续正常 step → true
  // 当前在特殊 step 或 currIdx=-1（当前是特殊 step） → 也认为有"继续推进"的选项（特殊 step 之后可以回到正常 step）
  const hasNext = currSeq === null
    ? (specialSteps.length > 0 || normalSteps.length > 0)
    : normalSteps.some((s) => s.sequence > currSeq);
  // 回合展示
  const round = detail?.step_negotiation_round ?? 0;
  const maxRound = detail?.step_neg_max_rounds ?? 3;
  const isEscalated = (detail?.escalate_count ?? 0) > 0;
  const escalateCount = detail?.escalate_count ?? 0;
  const reachedMax = !isEscalated && round >= maxRound;
  const lastStepBy = detail?.step_last_updated_by;
  const canOperate = isAssignee || isCreatorSide;
  const myTurn = (!lastStepBy && isAssignee)
    || (lastStepBy === 'assigned' && isCreatorSide)
    || (lastStepBy === 'creator' && isAssignee);
  let pillBg = 'var(--muted)';
  let pillColor = 'var(--muted-foreground)';
  if (round >= maxRound) { pillBg = 'var(--rose-soft)'; pillColor = 'var(--danger)'; }
  else if (round === maxRound - 1) { pillBg = 'var(--apricot-soft)'; pillColor = 'var(--apricot)'; }
  else if (myTurn) { pillBg = 'var(--blue-soft)'; pillColor = 'var(--blue-2)'; }
  const respondBtnDisabled = !canRespond || reachedMax;
  const negotiateDisabled = !canNegotiate || reachedMax;
  const completeDisabled = !hasNext;

  return (
    <>
      <div className="detail-card">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <h4 className="detail-card__h" style={{ marginBottom: 0 }}>工单阶段性处理</h4>
            {myTurn && !reachedMax && !stepAgreed && !isTerminal && !isPauseRequested && !isUnsolvableRequested && (
              <span style={{ fontSize: 12, color: 'var(--blue-2)', fontWeight: 500 }}>
                ● 轮到你确认/答复
              </span>
            )}
            {isPauseRequested && (
              <span style={{ fontSize: 12, color: 'var(--apricot)', fontWeight: 500 }}>
                ● 处理人已请求暂停工单，等待提单人确认
              </span>
            )}
            {isUnsolvableRequested && (
              <span style={{ fontSize: 12, color: 'var(--danger)', fontWeight: 500 }}>
                ● 处理人标记无法解决，等待提单人确认
              </span>
            )}
            {reachedMax && !stepAgreed && (
              <span style={{ fontSize: 12, color: 'var(--danger)', fontWeight: 500 }}>
                {myTurn
                  ? '● 已达最大回合。'
                  : '● 已达最大回合，待你确认/升级'}
              </span>
            )}
            {isEscalated && (
              <span style={{ fontSize: 12, color: 'var(--apricot)', fontWeight: 500 }}>
                ● 已升级上报（第{escalateCount}次），协商不受回合限制
              </span>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span
              title={isEscalated ? `已升级上报（第${escalateCount}次），协商不受回合限制` : "协商回合：接单人↔提单人来回应答计数"}
              style={{
                display: 'inline-block', padding: '3px 10px', borderRadius: 999,
                background: isEscalated ? 'var(--apricot-soft)' : pillBg, color: isEscalated ? 'var(--apricot)' : pillColor, fontSize: 12, fontWeight: 500, lineHeight: 1.4,
              }}
            >
              {isEscalated ? `已升级×${escalateCount} · 不受回合限制` : `交涉回合 ${round} / ${maxRound}`}
            </span>
          </div>
        </div>
        <div style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
            <span data-testid="task-current-step" className="detail-step-current">
              <span className="detail-step-current__label">当前阶段</span>
              <span className="detail-step-current__name">「{stepName}」</span>
            </span>
            {total > 0 && (
              <span style={{
                fontSize: 11, color: 'var(--muted-foreground)',
                padding: '1px 8px', borderRadius: 999, background: 'var(--muted)',
              }}>
                第 {currIdx >= 0 ? currIdx + 1 : '-'} / {total} 步
              </span>
            )}
            <span style={{
              fontSize: 11, fontWeight: 500, padding: '1px 8px', borderRadius: 999,
              background: stepAgreed ? 'var(--sky-soft)' : 'var(--apricot-soft)',
              color: stepAgreed ? 'var(--sky)' : 'var(--apricot)',
            }}>
              {stepAgreed ? '已达成一致' : (myTurn ? '待你确认' : '待对方确认')}
            </span>
          </div>
          {endtimeText && (
            <div style={{ fontSize: 12, color: 'var(--muted-foreground)', lineHeight: 1.6, display: 'flex', alignItems: 'center', gap: 4 }}>
              <AlarmClock size={13} strokeWidth={2} />
              {stepAgreed ? (
                <span>预计解决时间 <span style={{ color: 'var(--foreground)', fontWeight: 500 }}>{endtimeText}</span></span>
              ) : (
                (() => {
                  const proposerIsMe =
                    (lastStepBy === 'assigned' && isAssignee) ||
                    (lastStepBy === 'creator' && isCreatorSide);
                  return (
                    <span>
                      {proposerIsMe ? '你期望在' : '对方期望在'}{' '}
                      <span style={{ color: 'var(--foreground)', fontWeight: 500 }}>{endtimeText}</span>{' '}
                      前完成本阶段
                    </span>
                  );
                })()
              )}
            </div>
          )}
        </div>
        {latestNegotiateReason && !stepAgreed && (() => {
          const proposerIsMe =
            (lastStepBy === 'assigned' && isAssignee) ||
            (lastStepBy === 'creator' && isCreatorSide);
          return (
            <div style={{
              fontSize: 12, color: 'var(--foreground)', marginBottom: 12, lineHeight: 1.7,
              padding: '8px 12px', background: 'var(--muted)',
              borderRadius: 'var(--radius-sm)', borderLeft: '3px solid var(--border)',
            }}>
              <div style={{ color: 'var(--muted-foreground)', marginBottom: 2 }}>
                {proposerIsMe ? '你的提议理由' : '对方提议理由'}
              </div>
              “{latestNegotiateReason}”
            </div>
          );
        })()}
        {!isTerminal && !isPauseRequested ? (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {canOperate ? (
              <>
                {/* 处理人：无论 stepAgreed 与否、回合在谁，都可直接完成当前阶段（创建人未确认节点时间也不阻止推进） */}
                {isAssignee && (
                  hasNext ? (
                    <TestMarkedButton
                      testId="task-complete-step"
                      block
                      size="small"
                      theme="primary"
                      loading={completing}
                      disabled={completeDisabled}
                      onClick={openCompleteStep}
                    >
                      当前阶段完成
                    </TestMarkedButton>
                  ) : (
                    <TestMarkedButton
                      testId="task-resolve"
                      block
                      size="small"
                      theme="primary"
                      onClick={onResolve}
                    >
                      最末阶段结束，处理完成
                    </TestMarkedButton>
                  )
                )}
                {/* 处理人侧 · 对方待确认：允许修改之前设置的节点时间 */}
                {isAssignee && !reachedMax && !stepAgreed && !myTurn && (
                  <Button
                    block
                    size="small"
                    theme="default"
                    disabled={negotiateDisabled}
                    onClick={openNegotiate}
                  >
                    重新协商节点时间
                  </Button>
                )}
                {/* 协商/响应按钮：仅回合到我方、且未达成一致时显示 */}
                {!reachedMax && !stepAgreed && myTurn && (
                  <>
                    {isAssignee && (
                      <Button
                        block
                        size="small"
                        theme="default"
                        onClick={onReassign}
                      >
                        重新指派
                      </Button>
                    )}
                    {isAssignee && isEscalated && (
                      <Button
                        block
                        size="small"
                        theme="primary"
                        onClick={() => { setSetStepTimeValue(null); setShowSetStepTimePopup(true); }}
                      >
                        设置节点时间
                      </Button>
                    )}
                    <Button
                      block
                      size="small"
                      theme="default"
                      disabled={negotiateDisabled}
                      onClick={openNegotiate}
                    >
                      协商节点时间
                    </Button>
                    <TestMarkedButton
                      testId="task-accept"
                      block
                      size="small"
                      theme="primary"
                      loading={responding}
                      disabled={respondBtnDisabled}
                      onClick={handleRespond}
                    >
                      确认同意
                    </TestMarkedButton>
                  </>
                )}
                {reachedMax && !stepAgreed && myTurn && (
                  <>
                    <TestMarkedButton
                      testId="task-accept"
                      block
                      size="small"
                      theme="primary"
                      loading={responding}
                      disabled={!canRespond}
                      onClick={handleRespond}
                    >
                      确认同意
                    </TestMarkedButton>
                    <Button block size="small" theme="danger" onClick={() => onEscalate(round, maxRound)}>
                      有异议，升级上报
                    </Button>
                  </>
                )}
              </>
            ) : (
              <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>
                仅工单创建人和处理人可执行操作
              </span>
            )}
          </div>
        ) : isPauseRequested ? (
          // 暂停请求中：阶段协商/推进操作已冻结，回应按钮放在卡内
          <div style={{
            padding: '10px 14px', background: 'var(--apricot-soft)',
            borderRadius: 'var(--radius-sm)', borderLeft: '3px solid var(--apricot)',
            fontSize: 13, color: 'var(--foreground)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: pauseReason ? 8 : 0 }}>
              <span style={{ fontSize: 18 }}>⏸</span>
              <span>
                {isAssignee
                  ? '暂停请求已发送，等待提单人确认后将进入"已挂起"状态。'
                  : '处理人已请求暂停此工单，请查看理由后决定是否同意。'}
              </span>
            </div>
            {pauseReason && (
              <div style={{
                padding: '8px 12px', background: 'var(--background)',
                borderRadius: 'var(--radius-sm)', fontSize: 13, lineHeight: 1.6,
                color: 'var(--foreground)', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>
                <span style={{ color: 'var(--muted-foreground)', fontSize: 12 }}>暂停理由：</span>
                {pauseReason}
              </div>
            )}
            {isCreatorSide && onPauseResponse && (
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 12 }}>
                <Button size="small" theme="default" onClick={() => { setRejectReasonDraft(''); setShowRejectPopup(true); }}>驳回暂停</Button>
                <Button size="small" theme="primary" onClick={() => onPauseResponse('pending')}>确认暂停</Button>
              </div>
            )}
          </div>
        ) : isUnsolvableRequested ? (
          // 无法解决确认中：协商冻结，提单人侧显示确认/驳回按钮
          <div style={{
            padding: '10px 14px', background: 'var(--rose-soft)',
            borderRadius: 'var(--radius-sm)', borderLeft: '3px solid var(--danger)',
            fontSize: 13, color: 'var(--foreground)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: unsolvableReason ? 8 : 0 }}>
              <span style={{ fontSize: 18 }}>✕</span>
              <span>
                {isAssignee
                  ? '无法解决请求已发送，等待提单人确认后工单进入"无法解决"终态。'
                  : '处理人标记无法解决此工单，请查看理由后决定是否确认。'}
              </span>
            </div>
            {unsolvableReason && (
              <div style={{
                padding: '8px 12px', background: 'var(--background)',
                borderRadius: 'var(--radius-sm)', fontSize: 13, lineHeight: 1.6,
                color: 'var(--foreground)', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>
                <span style={{ color: 'var(--muted-foreground)', fontSize: 12 }}>无法解决理由：</span>
                {unsolvableReason}
              </div>
            )}
            {isCreatorSide && onUnsolvableResponse && (
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 12 }}>
                <Button size="small" theme="default" onClick={() => { setRejectReasonDraft(''); setShowRejectPopup(true); }}>驳回</Button>
                <Button size="small" theme="primary" onClick={() => onUnsolvableResponse('unsolvable')}>确认无法解决</Button>
              </div>
            )}
          </div>
        ) : currIsSpecial ? (
          // 当前处于特殊 step：展示条件理由
          <div style={{
            padding: '10px 14px', background: 'var(--muted)',
            borderRadius: 'var(--radius-sm)', borderLeft: '3px solid var(--blue-2)',
            fontSize: 13, color: 'var(--foreground)', marginBottom: 10,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <span style={{ fontSize: 16 }}>{currentStepRow?.sequence === -2 ? '⏳' : '🔗'}</span>
              <span style={{ fontWeight: 600 }}>{currentStepRow?.step_name || '等待条件'}</span>
            </div>
            {waitingExternalReason && (
              <div style={{ padding: '6px 10px', background: 'var(--background)', borderRadius: 'var(--radius-sm)', fontSize: 13, lineHeight: 1.6, color: 'var(--foreground)', whiteSpace: 'pre-wrap' }}>
                <span style={{ color: 'var(--muted-foreground)', fontSize: 12 }}>等待理由：</span>{waitingExternalReason}
              </div>
            )}
            {waitingPredecessorReason && (
              <div style={{ padding: '6px 10px', background: 'var(--background)', borderRadius: 'var(--radius-sm)', fontSize: 13, lineHeight: 1.6, color: 'var(--foreground)', whiteSpace: 'pre-wrap' }}>
                <span style={{ color: 'var(--muted-foreground)', fontSize: 12 }}>等待理由：</span>{waitingPredecessorReason}
              </div>
            )}
          </div>
        ) : rejectReason && isAssignee && status === 'in_progress' ? (
          // 刚被驳回暂停（IN_PROGRESS 状态保留 reject_reason），提示处理人上次为什么被驳回
          <div style={{
            padding: '8px 14px', background: 'var(--apricot-soft)',
            borderRadius: 'var(--radius-sm)', borderLeft: '3px solid var(--apricot)',
            fontSize: 13, color: 'var(--foreground)', marginBottom: 10,
          }}>
            <span style={{ color: 'var(--muted-foreground)', fontSize: 12 }}>上次驳回理由：</span>
            <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{rejectReason}</span>
          </div>
        ) : null}
      </div>

      {/* 协商节点时间弹窗 */}
      <Popup
        visible={showNegotiateStepPopup}
        onClose={() => { if (!submittingNegotiate) setShowNegotiateStepPopup(false); }}
        placement="bottom"
        showOverlay
        destroyOnClose
      >
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">协商节点时间</h4>
          <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            可将节点调整为当前或之后的任一节点，并设置节点结束时间（SLA），协商理由必填。
          </p>
          <div style={{ marginBottom: 12 }}>
            <label style={{ display: 'block', fontSize: 13, color: 'var(--muted-foreground)', marginBottom: 4 }}>
              协商节点<span style={{ color: 'var(--danger)' }}>*</span>
            </label>
            <select
              value={negotiateStepId ?? ''}
              onChange={(e) => setNegotiateStepId(e.target.value ? Number(e.target.value) : null)}
              style={{
                width: '100%', padding: '8px 10px', fontSize: 14,
                border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
                background: 'var(--card)', color: 'var(--foreground)',
              }}
            >
              {(() => {
                const isPhaseRound0 = !(detail?.step_phase_round) || detail.step_phase_round === 0;
                const curSeqForNegotiate = normalSteps.find((s) => s.id === detail?.curr_step_id)?.sequence ?? -1;
                // 正常 step：当前及之后（或 round0 全可选）
                const normalSelectable = (normalSteps.length > 0 ? normalSteps : [])
                  .filter((s) => isPhaseRound0 || s.sequence >= curSeqForNegotiate)
                  .sort((a, b) => a.sequence - b.sequence);
                // 特殊 step（等待外部条件 / 等待前置工单）始终可选，追加在后面
                const selectable = [...normalSelectable, ...specialSteps];
                const fallback = detail?.curr_step_id
                  ? [{ id: detail.curr_step_id, step_name: detail.curr_step_name || '当前节点', sequence: 0 }]
                  : [];
                return (selectable.length > 0 ? selectable : fallback);
              })().map((s) => {
                const isSpecial = s.sequence < 0;
                return (
                  <option key={s.id} value={s.id}>{isSpecial ? `★ ${s.step_name}` : `第${s.sequence + 1}步 · ${s.step_name}`}</option>
                );
              })}
            </select>
          </div>
          {(() => {
            const range = getDeadlineRange(detail?.priority, detail?.created_at);
            return (
              <DatePicker
                style={{ width: '100%', marginBottom: 12 }}
                placeholder="点击选择节点结束时间"
                format="YYYY-MM-DD HH:mm"
                showTime={{ defaultValue: dayjs().hour(18).minute(0), format: 'HH:mm', showNow: false }}
                showNow={false}
                placement="topLeft"
                getPopupContainer={() => document.body}
                value={negotiateEndTimeValue}
                disabledDate={range ? makeDisabledDate(range.min) : undefined}
                disabledTime={range ? makeDisabledTime(range.min) : undefined}
                onChange={(d: dayjs.Dayjs | null) =>
                  setNegotiateEndTime(d ? d.second(0).millisecond(0).toISOString() : null)
                }
                allowClear
                styles={{ popup: { root: { zIndex: 13000 } } }}
              />
            );
          })()}
          {(() => {
            const selectedNegotiateStep = stepTemplate.find((s) => s.id === negotiateStepId);
            const isNegotiateSpecial = !!selectedNegotiateStep && selectedNegotiateStep.sequence < 0;
            if (isNegotiateSpecial) {
              return (
                <SpecialStepConditionBlock
                  step={selectedNegotiateStep}
                  reason={negotiateReason}
                  onReasonChange={setNegotiateReason}
                  predecessorIds={currentPredecessorIds}
                  onAddPredecessor={openAddPredecessorPopup}
                  testIdPrefix="negotiate-step"
                />
              );
            }
            return (
              <Form initialData={{}}>
                <FormItem label="协商理由" name="negotiateReason" labelAlign="top" requiredMark>
                  <Textarea
                    value={negotiateReason}
                    onChange={(v) => setNegotiateReason(String(v))}
                    placeholder="请输入协商理由（必填）"
                    autosize={{ minRows: 3, maxRows: 6 }}
                    maxlength={500}
                  />
                </FormItem>
              </Form>
            );
          })()}
          <div className="ticket-edit__btns">
            <Button theme="default" disabled={submittingNegotiate} onClick={() => setShowNegotiateStepPopup(false)}>取消</Button>
            <Button theme="primary" loading={submittingNegotiate} onClick={handleNegotiateStep} disabled={!negotiateEndTime || !negotiateStepId || !negotiateReason.trim()}>保存</Button>
          </div>
        </div>
      </Popup>

      {/* 当前阶段完成弹窗：选择下一阶段 + 节点结束时间 */}
      {(() => {
        const currIdxLocal = normalSteps.findIndex((s) => s.id === detail?.curr_step_id);
        const currSeqLocal = currIdxLocal >= 0 ? normalSteps[currIdxLocal].sequence : null;
        // 正常 step：严格大于当前；特殊 step（等待条件）始终可选
        const normalNextOptions = (normalSteps.length > 0 ? normalSteps : [])
          .filter((s) => currSeqLocal === null || s.sequence > currSeqLocal)
          .sort((a, b) => a.sequence - b.sequence);
        const nextStepOptions = [...normalNextOptions, ...specialSteps];
        const range = getDeadlineRange(detail?.priority, detail?.created_at);
        const selectedStep = stepTemplate.find((s) => s.id === completeNextStepId);
        const isCompleteSpecial = !!selectedStep && selectedStep.sequence < 0;
        return (
          <Popup
            visible={showCompleteStepPopup}
            onClose={() => {
              if (!submittingComplete) {
                setShowCompleteStepPopup(false);
                setCompleteReasonDraft('');
              }
            }}
            placement="bottom"
            showOverlay
            destroyOnClose
          >
            <div className="ticket-edit">
              <h4 className="ticket-edit__title">请选择下一阶段</h4>
              <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
                完成当前阶段后，工单将进入"未一致"状态，回合交给创建人确认。
              </p>
              <div style={{ marginBottom: 12 }}>
                <label style={{ display: 'block', fontSize: 13, color: 'var(--muted-foreground)', marginBottom: 4 }}>
                  下一阶段<span style={{ color: 'var(--danger)' }}>*</span>
                </label>
                <select
                  data-testid="task-step-next"
                  value={completeNextStepId ?? ''}
                  onChange={(e) => setCompleteNextStepId(e.target.value ? Number(e.target.value) : null)}
                  style={{
                    width: '100%', padding: '8px 10px', fontSize: 14,
                    border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
                    background: 'var(--card)', color: 'var(--foreground)',
                  }}
                >
                  {nextStepOptions.length === 0 && (
                    <option value="" disabled>无可选下一阶段</option>
                  )}
                  {nextStepOptions.map((s) => {
                    const isSpecial = s.sequence < 0;
                    return (
                      <option key={s.id} value={s.id}>{isSpecial ? `★ ${s.step_name}` : `第${s.sequence + 1}步 · ${s.step_name}`}</option>
                    );
                  })}
                </select>
              </div>
              <DatePicker
                data-testid="task-step-endtime"
                style={{ width: '100%', marginBottom: 12 }}
                placeholder="点击选择下一阶段结束时间"
                format="YYYY-MM-DD HH:mm"
                showTime={{ defaultValue: dayjs().hour(18).minute(0), format: 'HH:mm', showNow: false }}
                showNow={false}
                placement="topLeft"
                getPopupContainer={() => document.body}
                value={completeNextEndTimeValue}
                disabledDate={range ? makeDisabledDate(range.min) : undefined}
                disabledTime={range ? makeDisabledTime(range.min) : undefined}
                onChange={(d: dayjs.Dayjs | null) =>
                  setCompleteNextEndTime(d ? d.second(0).millisecond(0).toISOString() : null)
                }
                allowClear
                styles={{ popup: { root: { zIndex: 13000 } } }}
              />
              {/* 特殊 step 的条件输入区域 */}
              <SpecialStepConditionBlock
                step={isCompleteSpecial ? selectedStep : null}
                reason={completeReasonDraft}
                onReasonChange={setCompleteReasonDraft}
                predecessorIds={currentPredecessorIds}
                onAddPredecessor={openAddPredecessorPopup}
                testIdPrefix="task-step-complete"
              />
              <div className="ticket-edit__btns">
                <Button theme="default" disabled={submittingComplete} onClick={() => setShowCompleteStepPopup(false)}>取消</Button>
                <TestMarkedButton
                  testId="task-step-submit"
                  theme="primary"
                  loading={submittingComplete}
                  onClick={() => handleStepComplete(completeReasonDraft)}
                  disabled={!completeNextStepId || !completeNextEndTime || (isCompleteSpecial && !completeReasonDraft.trim())}
                >
                  确认
                </TestMarkedButton>
              </div>
            </div>
          </Popup>
        );
      })()}

      {/* 设置节点时间弹窗（已升级工单，处理人一锤定音） */}
      {(() => {
        const range = getDeadlineRange(detail?.priority, detail?.created_at);
        return (
          <Popup
            visible={showSetStepTimePopup}
            onClose={() => { if (!submittingSetStepTime) setShowSetStepTimePopup(false); }}
            placement="bottom"
            showOverlay
            destroyOnClose
          >
            <div className="ticket-edit">
              <h4 className="ticket-edit__title">设置节点时间</h4>
              <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
                升级上报后的工单，处理人可直接设置节点时间，无需协商（一锤定音）。
              </p>
              <DatePicker
                style={{ width: '100%', marginBottom: 12 }}
                placeholder="点击选择节点结束时间"
                format="YYYY-MM-DD HH:mm"
                showTime={{ defaultValue: dayjs().hour(18).minute(0), format: 'HH:mm', showNow: false }}
                showNow={false}
                placement="topLeft"
                getPopupContainer={() => document.body}
                value={setStepTimePickerValue}
                disabledDate={range ? makeDisabledDate(range.min) : undefined}
                disabledTime={range ? makeDisabledTime(range.min) : undefined}
                onChange={(d: dayjs.Dayjs | null) =>
                  setSetStepTimeValue(d ? d.second(0).millisecond(0).toISOString() : null)
                }
                allowClear
                styles={{ popup: { root: { zIndex: 13000 } } }}
              />
              <div className="ticket-edit__btns">
                <Button theme="default" disabled={submittingSetStepTime} onClick={() => setShowSetStepTimePopup(false)}>取消</Button>
                <Button theme="primary" loading={submittingSetStepTime} onClick={handleSetStepTime} disabled={!setStepTimeValue}>确认</Button>
              </div>
            </div>
          </Popup>
        );
      })()}

      {/* 驳回请求理由弹窗（暂停 / 无法解决 共用，文案与回调随当前状态切换） */}
      <Popup
        visible={showRejectPopup}
        onClose={() => { setShowRejectPopup(false); setRejectReasonDraft(''); }}
        placement="bottom"
        showOverlay
        destroyOnClose
      >
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">{isUnsolvableRequested ? '驳回无法解决请求' : '驳回暂停请求'}</h4>
          <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            请说明驳回理由，处理人将看到。
          </p>
          <Form initialData={{}}>
            <FormItem label="驳回理由" name="rejectReason" labelAlign="top" requiredMark>
              <Textarea
                value={rejectReasonDraft}
                onChange={(v) => setRejectReasonDraft(String(v))}
                placeholder={isUnsolvableRequested ? '请说明为什么不同意标记无法解决' : '请说明为什么不同意暂停'}
                autosize={{ minRows: 3, maxRows: 6 }}
                maxlength={500}
              />
            </FormItem>
          </Form>
          <div className="ticket-edit__btns">
            <Button theme="default" onClick={() => { setShowRejectPopup(false); setRejectReasonDraft(''); }}>取消</Button>
            <Button
              theme="primary"
              disabled={!rejectReasonDraft.trim()}
              onClick={() => {
                if (isUnsolvableRequested) {
                  onUnsolvableResponse?.('in_progress', rejectReasonDraft.trim());
                } else {
                  onPauseResponse?.('in_progress', rejectReasonDraft.trim());
                }
                setShowRejectPopup(false);
                setRejectReasonDraft('');
              }}
            >
              确认驳回
            </Button>
          </div>
        </div>
      </Popup>

      {/* 等待前置工单 → +添加 弹窗：输入已有工单 ID 建立 predecessor 关系 */}
      <Popup
        visible={showAddPredecessorPopup}
        onClose={() => { setShowAddPredecessorPopup(false); setPredecessorIdInput(''); }}
        placement="bottom"
        showOverlay
        destroyOnClose
      >
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">添加前置工单</h4>
          <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            前置工单完成前，当前工单会被阻塞。已有的前置工单会自动显示在下方。
          </p>
          <div style={{ marginBottom: 12 }}>
            <label style={{ display: 'block', fontSize: 13, color: 'var(--muted-foreground)', marginBottom: 4 }}>
              前置工单 ID<span style={{ color: 'var(--danger)' }}>*</span>
            </label>
            <input
              type="text"
              value={predecessorIdInput}
              onChange={(e) => setPredecessorIdInput(e.target.value)}
              placeholder="输入工单 ID，如 123 或 #123"
              style={{
                width: '100%', padding: '8px 10px', fontSize: 14,
                border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
                background: 'var(--card)', color: 'var(--foreground)',
              }}
              onKeyDown={(e) => { if (e.key === 'Enter') handleAddPredecessor(); }}
            />
          </div>
          <div className="ticket-edit__btns">
            <Button theme="default" onClick={() => { setShowAddPredecessorPopup(false); setPredecessorIdInput(''); }}>取消</Button>
            <Button theme="primary" disabled={!predecessorIdInput.trim()} onClick={handleAddPredecessor}>确认添加</Button>
          </div>
        </div>
      </Popup>
    </>
  );
}
