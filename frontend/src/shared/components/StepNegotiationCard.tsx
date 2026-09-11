import { Button, Popup, Form, FormItem, Textarea } from 'tdesign-mobile-react';
import { DatePicker } from 'antd';
import dayjs from 'dayjs';
import { AlarmClock } from 'lucide-react';
import type { useStepNegotiation, StepNegotiationTicket } from '@/shared/hooks/useStepNegotiation';
import { getDeadlineRange, makeDisabledDate, makeDisabledTime, parseDeadlineString } from '@/shared/utils/deadline';
import { formatRawDateTime } from '@/shared/utils/url';

/** 工单阶段性处理卡所需的工单信息（阶段字段 + 展示/弹窗所需的基础字段） */
export interface StepCardTicket extends StepNegotiationTicket {
  status?: string;
  priority?: string;
  created_at?: string;
  comments?: Array<{ content?: string; created_at?: string }>;
}

interface StepRoles {
  isAssignee: boolean;
  isReporter: boolean;
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

  const { isAssignee, isReporter } = roles;

  const status = (detail?.status || '').toLowerCase();
  // 终态（已解决/已关闭/已取消）：保留节点信息展示，但隐藏卡内所有操作按钮
  const isTerminal = ['resolved', 'closed', 'canceled', 'cancelled'].includes(status);
  const total = stepTemplate.length;
  const currIdx = stepTemplate.findIndex((s) => s.id === detail?.curr_step_id);
  const stepName = detail?.curr_step_name || (currIdx >= 0 ? stepTemplate[currIdx].step_name : '');
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
  const canRespond = !!detail?.curr_step_id && (status === 'new' || (status === 'in_progress' && !stepAgreed));
  const canNegotiate = !!detail?.curr_step_id;
  const currSeq = currIdx >= 0 ? stepTemplate[currIdx].sequence : null;
  const hasNext = currSeq === null ? true : stepTemplate.some((s) => s.sequence > currSeq);
  // 回合展示
  const round = detail?.step_negotiation_round ?? 0;
  const maxRound = detail?.step_neg_max_rounds ?? 3;
  const isEscalated = (detail?.escalate_count ?? 0) > 0;
  const escalateCount = detail?.escalate_count ?? 0;
  const reachedMax = !isEscalated && round >= maxRound;
  const lastStepBy = detail?.step_last_updated_by;
  const canOperate = isAssignee || isReporter;
  const myTurn = (!lastStepBy && isAssignee)
    || (lastStepBy === 'assigned' && isReporter)
    || (lastStepBy === 'creator' && isAssignee);
  let pillBg = 'rgba(100,116,139,0.15)';
  let pillColor = 'var(--muted-foreground)';
  if (round >= maxRound) { pillBg = 'rgba(220,38,38,0.15)'; pillColor = '#b91c1c'; }
  else if (round === maxRound - 1) { pillBg = 'rgba(234,179,8,0.2)'; pillColor = '#8a6400'; }
  else if (myTurn) { pillBg = 'rgba(37,99,235,0.15)'; pillColor = 'var(--blue-2)'; }
  const respondBtnDisabled = !canRespond || reachedMax;
  const negotiateDisabled = !canNegotiate || reachedMax;
  const completeDisabled = !hasNext;

  return (
    <>
      <div className="detail-card">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <h4 className="detail-card__h" style={{ marginBottom: 0 }}>工单阶段性处理</h4>
            {myTurn && !reachedMax && !stepAgreed && !isTerminal && (
              <span style={{ fontSize: 12, color: 'var(--blue-2)', fontWeight: 500 }}>
                ● 轮到你确认/答复
              </span>
            )}
            {reachedMax && !stepAgreed && (
              <span style={{ fontSize: 12, color: '#b91c1c', fontWeight: 500 }}>
                {myTurn
                  ? '● 已达最大回合。'
                  : '● 已达最大回合，待你确认/升级'}
              </span>
            )}
            {isEscalated && (
              <span style={{ fontSize: 12, color: '#92400e', fontWeight: 500 }}>
                ● 已升级上报（第{escalateCount}次），协商不受回合限制
              </span>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span
              title={isEscalated ? `已升级上报（第${escalateCount}次），协商不受回合限制` : "协商回合：接单人↔提单人来回应答计数"}
              style={{
                display: 'inline-block', padding: '3px 10px', borderRadius: 999,
                background: isEscalated ? '#fef3c7' : pillBg, color: isEscalated ? '#92400e' : pillColor, fontSize: 12, fontWeight: 500, lineHeight: 1.4,
              }}
            >
              {isEscalated ? `已升级×${escalateCount} · 不受回合限制` : `交涉回合 ${round} / ${maxRound}`}
            </span>
          </div>
        </div>
        <div style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
            <span className="detail-step-current">
              <span className="detail-step-current__label">当前阶段</span>
              <span className="detail-step-current__name">「{stepName}」</span>
            </span>
            {total > 0 && (
              <span style={{
                fontSize: 11, color: 'var(--muted-foreground)',
                padding: '1px 8px', borderRadius: 999, background: 'rgba(100,116,139,0.12)',
              }}>
                第 {currIdx >= 0 ? currIdx + 1 : '-'} / {total} 步
              </span>
            )}
            <span style={{
              fontSize: 11, fontWeight: 500, padding: '1px 8px', borderRadius: 999,
              background: stepAgreed ? 'rgba(22,163,74,0.12)' : 'rgba(234,179,8,0.18)',
              color: stepAgreed ? '#15803d' : '#8a6400',
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
                    (lastStepBy === 'creator' && isReporter);
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
            (lastStepBy === 'creator' && isReporter);
          return (
            <div style={{
              fontSize: 12, color: 'var(--foreground)', marginBottom: 12, lineHeight: 1.7,
              padding: '8px 12px', background: 'rgba(100,116,139,0.08)',
              borderRadius: 6, borderLeft: '3px solid #94a3b8',
            }}>
              <div style={{ color: 'var(--muted-foreground)', marginBottom: 2 }}>
                {proposerIsMe ? '你的提议理由' : '对方提议理由'}
              </div>
              “{latestNegotiateReason}”
            </div>
          );
        })()}
        {!isTerminal && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {canOperate ? (
              reachedMax ? (
                stepAgreed ? (
                  isAssignee ? (
                    hasNext ? (
                      <Button block size="small" theme="primary" loading={completing} disabled={completeDisabled} onClick={openCompleteStep}>
                        当前阶段完成
                      </Button>
                    ) : (
                      <Button block size="small" theme="primary" onClick={onResolve}>
                        最末阶段结束，处理完成
                      </Button>
                    )
                  ) : null
                ) : myTurn ? (
                  <>
                    <Button
                      block
                      size="small"
                      theme="primary"
                      loading={responding}
                      disabled={!canRespond}
                      onClick={handleRespond}
                    >
                      确认同意
                    </Button>
                    <Button block size="small" theme="danger" onClick={() => onEscalate(round, maxRound)}>
                      有异议，升级上报
                    </Button>
                  </>
                ) : null
              ) : stepAgreed ? (
                isAssignee ? (
                  hasNext ? (
                    <Button
                      block
                      size="small"
                      theme="primary"
                      loading={completing}
                      disabled={completeDisabled}
                      onClick={openCompleteStep}
                    >
                      当前阶段完成
                    </Button>
                  ) : (
                    <Button
                      block
                      size="small"
                      theme="primary"
                      onClick={onResolve}
                    >
                      最末阶段结束，处理完成
                    </Button>
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
                    <Button
                      block
                      size="small"
                      theme="primary"
                      loading={responding}
                      disabled={respondBtnDisabled}
                      onClick={handleRespond}
                    >
                      确认同意
                    </Button>
                  </>
                ) : null
              )
            ) : (
              <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>
                仅工单创建人和处理人可执行操作
              </span>
            )}
          </div>
        )}
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
          <p style={{ color: '#666', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            可将节点调整为当前或之后的任一节点，并设置节点结束时间（SLA），协商理由必填。
          </p>
          <div style={{ marginBottom: 12 }}>
            <label style={{ display: 'block', fontSize: 13, color: '#666', marginBottom: 4 }}>
              协商节点<span style={{ color: '#e34d59' }}>*</span>
            </label>
            <select
              value={negotiateStepId ?? ''}
              onChange={(e) => setNegotiateStepId(e.target.value ? Number(e.target.value) : null)}
              style={{
                width: '100%', padding: '8px 10px', fontSize: 14,
                border: '1px solid var(--component-border, #dcdcdc)', borderRadius: 6,
                background: '#fff',
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
                value={negotiateEndTime ? parseDeadlineString(negotiateEndTime) : null}
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
              <p style={{ color: '#666', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
                完成当前阶段后，工单将进入"未一致"状态，回合交给创建人确认。
              </p>
              <div style={{ marginBottom: 12 }}>
                <label style={{ display: 'block', fontSize: 13, color: '#666', marginBottom: 4 }}>
                  下一阶段<span style={{ color: '#e34d59' }}>*</span>
                </label>
                <select
                  value={completeNextStepId ?? ''}
                  onChange={(e) => setCompleteNextStepId(e.target.value ? Number(e.target.value) : null)}
                  style={{
                    width: '100%', padding: '8px 10px', fontSize: 14,
                    border: '1px solid var(--component-border, #dcdcdc)', borderRadius: 6,
                    background: '#fff',
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
                style={{ width: '100%', marginBottom: 12 }}
                placeholder="点击选择下一阶段结束时间"
                format="YYYY-MM-DD HH:mm"
                showTime={{ defaultValue: dayjs().hour(18).minute(0), format: 'HH:mm', showNow: false }}
                showNow={false}
                placement="topLeft"
                getPopupContainer={() => document.body}
                value={completeNextEndTime ? parseDeadlineString(completeNextEndTime) : null}
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
                <Button theme="primary" loading={submittingComplete} onClick={handleStepComplete} disabled={!completeNextStepId || !completeNextEndTime}>确认</Button>
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
              <p style={{ color: '#666', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
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
                value={setStepTimeValue ? parseDeadlineString(setStepTimeValue) : null}
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
    </>
  );
}
