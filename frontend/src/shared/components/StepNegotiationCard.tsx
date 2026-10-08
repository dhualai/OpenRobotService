import { Button, Popup, Form, FormItem, Textarea } from 'tdesign-mobile-react';
import { DatePicker } from 'antd';
import dayjs from 'dayjs';
import { AlarmClock } from 'lucide-react';
import { useMemo } from 'react';
import type { ComponentProps } from 'react';
import { useState } from 'react';
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

/** 工单阶段性处理卡所需的工单信息（阶段字段 + 展示/弹窗所需的基础字段） */
export interface StepCardTicket extends StepNegotiationTicket {
  status?: string;
  priority?: string;
  created_at?: string;
  comments?: Array<{ content?: string; created_at?: string }>;
  metadata_info?: Record<string, unknown>;
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
  const total = stepTemplate.length;
  const currIdx = stepTemplate.findIndex((s) => s.id === detail?.curr_step_id);
  const stepName = detail?.curr_step_name || (currIdx >= 0 ? stepTemplate[currIdx].step_name : '');
  // 暂停请求理由（metadata_info.pause_reason，仅 pending_requested 状态存在）
  const pauseReason = (detail?.metadata_info as Record<string, unknown> | undefined)?.pause_reason as string | undefined;
  // 上一次驳回理由（metadata_info.reject_reason，仅 IN_PROGRESS 状态存在，处理人侧可见）
  const rejectReason = (detail?.metadata_info as Record<string, unknown> | undefined)?.reject_reason as string | undefined;

  // 驳回暂停理由弹窗（本地 state，仅组件内部使用）
  const [showRejectPopup, setShowRejectPopup] = useState(false);
  const [rejectReasonDraft, setRejectReasonDraft] = useState('');
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
  // 暂停请求期间冻结所有协商/响应操作，等待提单人侧确认或驳回
  const canRespond = !isPauseRequested && !!detail?.curr_step_id && (status === 'new' || (status === 'in_progress' && !stepAgreed));
  const canNegotiate = !isPauseRequested && !!detail?.curr_step_id;
  const currSeq = currIdx >= 0 ? stepTemplate[currIdx].sequence : null;
  const hasNext = currSeq === null ? true : stepTemplate.some((s) => s.sequence > currSeq);
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
            {myTurn && !reachedMax && !stepAgreed && !isTerminal && !isPauseRequested && (
              <span style={{ fontSize: 12, color: 'var(--blue-2)', fontWeight: 500 }}>
                ● 轮到你确认/答复
              </span>
            )}
            {isPauseRequested && (
              <span style={{ fontSize: 12, color: 'var(--apricot)', fontWeight: 500 }}>
                ● 处理人已请求暂停工单，等待提单人确认
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
              reachedMax ? (
                stepAgreed ? (
                  isAssignee ? (
                    hasNext ? (
                      <TestMarkedButton testId="task-complete-step" block size="small" theme="primary" loading={completing} disabled={completeDisabled} onClick={openCompleteStep}>
                        当前阶段完成
                      </TestMarkedButton>
                    ) : (
                      <TestMarkedButton testId="task-resolve" block size="small" theme="primary" onClick={onResolve}>
                        最末阶段结束，处理完成
                      </TestMarkedButton>
                    )
                  ) : null
                ) : myTurn ? (
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
                ) : null
              ) : stepAgreed ? (
                isAssignee ? (
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
                ) : null
              ) : (
                myTurn ? (
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
                ) : null
              )
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
                const curSeqForNegotiate = stepTemplate.find((s) => s.id === detail?.curr_step_id)?.sequence ?? -1;
                const negotiableSteps = (stepTemplate.length > 0 ? stepTemplate : [])
                  .filter((s) => isPhaseRound0 || s.sequence >= curSeqForNegotiate)
                  .sort((a, b) => a.sequence - b.sequence);
                const fallback = detail?.curr_step_id
                  ? [{ id: detail.curr_step_id, step_name: detail.curr_step_name || '当前节点', sequence: 0 }]
                  : [];
                return (negotiableSteps.length > 0 ? negotiableSteps : fallback);
              })().map((s) => (
                <option key={s.id} value={s.id}>{`第${s.sequence + 1}步 · ${s.step_name}`}</option>
              ))}
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
          <div className="ticket-edit__btns">
            <Button theme="default" disabled={submittingNegotiate} onClick={() => setShowNegotiateStepPopup(false)}>取消</Button>
            <Button theme="primary" loading={submittingNegotiate} onClick={handleNegotiateStep} disabled={!negotiateEndTime || !negotiateStepId || !negotiateReason.trim()}>保存</Button>
          </div>
        </div>
      </Popup>

      {/* 当前阶段完成弹窗：选择下一阶段 + 节点结束时间 */}
      {(() => {
        const currIdxLocal = stepTemplate.findIndex((s) => s.id === detail?.curr_step_id);
        const currSeqLocal = currIdxLocal >= 0 ? stepTemplate[currIdxLocal].sequence : null;
        const nextStepOptions = (stepTemplate.length > 0 ? stepTemplate : [])
          .filter((s) => currSeqLocal === null || s.sequence > currSeqLocal)
          .sort((a, b) => a.sequence - b.sequence);
        const range = getDeadlineRange(detail?.priority, detail?.created_at);
        return (
          <Popup
            visible={showCompleteStepPopup}
            onClose={() => { if (!submittingComplete) setShowCompleteStepPopup(false); }}
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
                  {nextStepOptions.map((s) => (
                    <option key={s.id} value={s.id}>{`第${s.sequence + 1}步 · ${s.step_name}`}</option>
                  ))}
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
              <div className="ticket-edit__btns">
                <Button theme="default" disabled={submittingComplete} onClick={() => setShowCompleteStepPopup(false)}>取消</Button>
                <TestMarkedButton testId="task-step-submit" theme="primary" loading={submittingComplete} onClick={handleStepComplete} disabled={!completeNextStepId || !completeNextEndTime}>确认</TestMarkedButton>
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

      {/* 驳回暂停理由弹窗（仅提单人侧可触发） */}
      <Popup
        visible={showRejectPopup}
        onClose={() => { setShowRejectPopup(false); setRejectReasonDraft(''); }}
        placement="bottom"
        showOverlay
        destroyOnClose
      >
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">驳回暂停请求</h4>
          <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            请说明驳回理由，处理人将看到。
          </p>
          <Form initialData={{}}>
            <FormItem label="驳回理由" name="rejectReason" labelAlign="top" requiredMark>
              <Textarea
                value={rejectReasonDraft}
                onChange={(v) => setRejectReasonDraft(String(v))}
                placeholder="请说明为什么不同意暂停"
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
                onPauseResponse?.('in_progress', rejectReasonDraft.trim());
                setShowRejectPopup(false);
                setRejectReasonDraft('');
              }}
            >
              确认驳回
            </Button>
          </div>
        </div>
      </Popup>
    </>
  );
}
