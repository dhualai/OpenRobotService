import { useEffect, useMemo, useState } from 'react';
import { Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

/** 协商阶段模板步骤（GET /{task_id}/steps 返回） */
export interface StepTemplate {
  id: number;
  step_name: string;
  sequence: number;
}

/** 工单里「工单阶段性处理」相关的字段最小契约（TaskDetailPage 的 Ticket 与 TicketDetailPage 的 AiTicket 都满足） */
export interface StepNegotiationTicket {
  id?: string;
  curr_step_id?: number | null;
  curr_step_name?: string | null;
  curr_step_endtime?: string | null;
  step_last_updated_by?: 'assigned' | 'creator' | null;
  step_negotiation_round?: number;
  step_phase_round?: number;
  step_neg_max_rounds?: number;
  curr_step_agreed?: boolean;
  escalate_count?: number;
}

/**
 * 工单阶段性处理（协商节点）逻辑 hook：系统任务详情页与历史工单详情页复用。
 * 封装：协商阶段模板拉取、4 组弹窗状态、5 个操作 handler（确认同意/完成阶段/设置节点时间/协商节点/打回重开）。
 * 结束工单、升级上报、重新指派属于页面其它功能，由调用方通过回调处理，不在本 hook 内。
 */
export function useStepNegotiation(
  taskId: string | number,
  detail: StepNegotiationTicket | null,
  refreshDetail: () => Promise<void>,
) {
  const request = useMemo(() => createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务'), []);

  const [stepTemplate, setStepTemplate] = useState<StepTemplate[]>([]);
  const [responding, setResponding] = useState(false);
  const [completing, setCompleting] = useState(false);
  // 协商节点时间弹窗
  const [showNegotiateStepPopup, setShowNegotiateStepPopup] = useState(false);
  const [negotiateStepId, setNegotiateStepId] = useState<number | null>(null);
  const [negotiateEndTime, setNegotiateEndTime] = useState<string | null>(null);
  const [negotiateReason, setNegotiateReason] = useState('');
  const [submittingNegotiate, setSubmittingNegotiate] = useState(false);
  // 未解决打回弹窗
  const [showReopenPopup, setShowReopenPopup] = useState(false);
  const [reopenStepId, setReopenStepId] = useState<number | null>(null);
  const [reopenEndTime, setReopenEndTime] = useState<string | null>(null);
  const [submittingReopen, setSubmittingReopen] = useState(false);
  // 当前阶段完成弹窗
  const [showCompleteStepPopup, setShowCompleteStepPopup] = useState(false);
  const [completeNextStepId, setCompleteNextStepId] = useState<number | null>(null);
  const [completeNextEndTime, setCompleteNextEndTime] = useState<string | null>(null);
  const [submittingComplete, setSubmittingComplete] = useState(false);
  // 设置节点时间弹窗（已升级工单，处理人一锤定音）
  const [showSetStepTimePopup, setShowSetStepTimePopup] = useState(false);
  const [setStepTimeValue, setSetStepTimeValue] = useState<string | null>(null);
  const [submittingSetStepTime, setSubmittingSetStepTime] = useState(false);

  // 拉取协商阶段模板（按工单 task_type），用于「工单阶段性处理」当前节点描述
  useEffect(() => {
    if (!taskId) return;
    request<{ code: number; data: { steps: StepTemplate[] } }>(`/${taskId}/steps`)
      .then((res) => setStepTemplate(res?.data?.steps || []))
      .catch(() => setStepTemplate([]));
  }, [taskId, request]);

  // 首次响应（确认同意）：确认当前协商节点，工单 new → in_progress
  const handleRespond = async () => {
    if (!detail?.id) return;
    setResponding(true);
    try {
      await request(`/${detail.id}/respond`, {
        method: 'POST',
        body: JSON.stringify({ curr_step_id: detail.curr_step_id ?? null }),
      });
      await refreshDetail();
      Toast({ message: '已确认协商节点，开始处理', theme: 'success' });
    } catch (err) {
      Toast({ message: `响应失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setResponding(false);
    }
  };

  // 当前阶段完成：处理人选择下一阶段节点 + 节点结束时间，提交后回合交给创建人
  const handleStepComplete = async () => {
    if (!detail?.id) return;
    if (!completeNextStepId) {
      Toast({ message: '请选择下一阶段', theme: 'warning' });
      return;
    }
    if (!completeNextEndTime) {
      Toast({ message: '请选择下一阶段结束时间', theme: 'warning' });
      return;
    }
    setSubmittingComplete(true);
    setCompleting(true);
    try {
      await request(`/${detail.id}/complete-step`, {
        method: 'POST',
        body: JSON.stringify({
          next_step_id: completeNextStepId,
          curr_step_endtime: completeNextEndTime,
        }),
      });
      await refreshDetail();
      Toast({ message: '已推进到下一阶段，等待创建人确认', theme: 'success' });
      setShowCompleteStepPopup(false);
      setCompleteNextStepId(null);
      setCompleteNextEndTime(null);
    } catch (err) {
      Toast({ message: `操作失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmittingComplete(false);
      setCompleting(false);
    }
  };

  // 设置节点时间（已升级工单，处理人一锤定音）
  const handleSetStepTime = async () => {
    if (!detail?.id) return;
    if (!setStepTimeValue) {
      Toast({ message: '请选择节点时间', theme: 'warning' });
      return;
    }
    setSubmittingSetStepTime(true);
    try {
      await request(`/${detail.id}/set-step-time`, {
        method: 'POST',
        body: JSON.stringify({ curr_step_endtime: setStepTimeValue }),
        headers: { 'Content-Type': 'application/json' },
      });
      Toast({ message: '已设置节点时间', theme: 'success' });
      setShowSetStepTimePopup(false);
      setSetStepTimeValue(null);
      await refreshDetail();
    } catch (e) {
      Toast({ message: (e as Error)?.message || '设置失败', theme: 'error' });
    } finally {
      setSubmittingSetStepTime(false);
    }
  };

  // 协商节点：可调整节点（前/后均可）+ 设置节点结束时间，理由必填
  const handleNegotiateStep = async () => {
    if (!detail?.id) return;
    if (!negotiateEndTime) {
      Toast({ message: '请选择协商节点时间', theme: 'warning' });
      return;
    }
    if (!negotiateReason.trim()) {
      Toast({ message: '请填写协商理由', theme: 'warning' });
      return;
    }
    setSubmittingNegotiate(true);
    try {
      await request(`/${detail.id}/negotiate-step`, {
        method: 'POST',
        body: JSON.stringify({
          curr_step_endtime: negotiateEndTime,
          curr_step_id: negotiateStepId,
          reason: negotiateReason.trim(),
        }),
      });
      await refreshDetail();
      Toast({ message: '协商节点已更新', theme: 'success' });
      setNegotiateReason('');
      setNegotiateEndTime(null);
      setNegotiateStepId(null);
      setShowNegotiateStepPopup(false);
    } catch (err) {
      Toast({ message: `设置失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmittingNegotiate(false);
    }
  };

  // 未解决打回：提单人选择重新开始的阶段 + 时间，工单回到处理中，阶段性处理从头开始
  const handleReopenStep = async () => {
    if (!detail?.id) return;
    if (!reopenStepId) {
      Toast({ message: '请选择重新开始的阶段', theme: 'warning' });
      return;
    }
    if (!reopenEndTime) {
      Toast({ message: '请选择节点结束时间', theme: 'warning' });
      return;
    }
    setSubmittingReopen(true);
    try {
      await request(`/${detail.id}/reopen-step`, {
        method: 'POST',
        body: JSON.stringify({
          curr_step_id: reopenStepId,
          curr_step_endtime: reopenEndTime,
        }),
      });
      await refreshDetail();
      Toast({ message: '工单已打回到处理中，阶段性处理从头开始', theme: 'success' });
      setShowReopenPopup(false);
      setReopenStepId(null);
      setReopenEndTime(null);
    } catch (err) {
      Toast({ message: `打回失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmittingReopen(false);
    }
  };

  const openNegotiate = () => {
    setNegotiateStepId(detail?.curr_step_id ?? null);
    setNegotiateEndTime(detail?.curr_step_endtime ?? null);
    setNegotiateReason('');
    setShowNegotiateStepPopup(true);
  };

  const openCompleteStep = () => {
    const currIdx = stepTemplate.findIndex((s) => s.id === detail?.curr_step_id);
    const currSeqLocal = currIdx >= 0 ? stepTemplate[currIdx].sequence : null;
    const nextStep = currSeqLocal === null
      ? stepTemplate[0]
      : stepTemplate.find((s) => s.sequence > currSeqLocal);
    setCompleteNextStepId(nextStep ? nextStep.id : null);
    setCompleteNextEndTime(null);
    setShowCompleteStepPopup(true);
  };

  return {
    stepTemplate,
    responding,
    completing,
    showNegotiateStepPopup,
    setShowNegotiateStepPopup,
    negotiateStepId,
    setNegotiateStepId,
    negotiateEndTime,
    setNegotiateEndTime,
    negotiateReason,
    setNegotiateReason,
    submittingNegotiate,
    showReopenPopup,
    setShowReopenPopup,
    reopenStepId,
    setReopenStepId,
    reopenEndTime,
    setReopenEndTime,
    submittingReopen,
    showCompleteStepPopup,
    setShowCompleteStepPopup,
    completeNextStepId,
    setCompleteNextStepId,
    completeNextEndTime,
    setCompleteNextEndTime,
    submittingComplete,
    showSetStepTimePopup,
    setShowSetStepTimePopup,
    setStepTimeValue,
    setSetStepTimeValue,
    submittingSetStepTime,
    handleRespond,
    handleStepComplete,
    handleSetStepTime,
    handleNegotiateStep,
    handleReopenStep,
    openNegotiate,
    openCompleteStep,
  };
}
