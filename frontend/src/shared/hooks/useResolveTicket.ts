import { useMemo, useRef, useState } from 'react';
import { Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

/** 结束工单弹窗展示所需的最小工单信息 */
export interface ResolveTicketInfo {
  id?: string;
  title?: string;
  description?: string;
}

/**
 * 结束工单（确认完成）逻辑 hook：系统任务详情页与历史工单详情页复用。
 * 封装：解决方式弹窗状态、AI 生成解决方式（POST /{id}/resolution-summary）、轮询回读、
 * 确认完成（PATCH /{id}/status → resolved）。afterResolve 用于确认完成后触发全局列表刷新。
 */
export function useResolveTicket(
  taskId: string | number,
  detail: ResolveTicketInfo | null,
  refreshDetail: () => Promise<void>,
  afterResolve?: () => void,
) {
  const request = useMemo(() => createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务'), []);

  const [showResolutionPopup, setShowResolutionPopup] = useState(false);
  const [resolutionText, setResolutionText] = useState('');
  const [resolutionLoading, setResolutionLoading] = useState(false);
  const [resolutionFailed, setResolutionFailed] = useState(false);
  const [resolutionPolling, setResolutionPolling] = useState(false);
  // AI 判定当前无解决方案（仅占位提示，不填入输入框）
  const [resolutionNoSolution, setResolutionNoSolution] = useState(false);
  // 确认完成提交中（防重复提交）
  const [resolutionSubmitting, setResolutionSubmitting] = useState(false);
  // 标记是否已"确认完成"成功（成功后关闭弹窗不应清除草稿；取消/遮罩关闭才清除）
  const resolveConfirmedRef = useRef(false);
  // 轮询停止标志：取消/关闭时置 true，让异步轮询循环及时退出（state 无法中断 while 循环）
  const resolvePollStopRef = useRef(false);

  // 结束工单：轮询读取 metadata_info.resolution_summary（worker 后台生成后回填）
  const pollResolutionSummary = async (force = false) => {
    if (!taskId) return;
    let attempts = 0;
    const maxAttempts = 20; // 约 20 * 1.5s ≈ 30s 上限
    setResolutionPolling(true);

    // 重试时先带 force 强制重新入队（清除"无内容"标记）
    if (force) {
      try {
        await request<{ status: string; resolution_summary?: string }>(`/${taskId}/resolution-summary`, {
          method: 'POST',
          body: JSON.stringify({ force: true }),
          skipCache: true,
        });
      } catch {
        setResolutionLoading(false);
        setResolutionPolling(false);
        setResolutionFailed(true);
        return;
      }
    }

    while (attempts < maxAttempts) {
      attempts += 1;
      await new Promise((r) => setTimeout(r, 1500));
      // 取消/关闭时立即停止轮询
      if (resolvePollStopRef.current) {
        setResolutionPolling(false);
        return;
      }
      try {
        const res = await request<{ status: string; resolution_summary?: string }>(`/${taskId}/resolution-summary`, {
          method: 'POST',
          skipCache: true,
        });
        // 已取消 → 不再回填，直接退出
        if (resolvePollStopRef.current) {
          setResolutionPolling(false);
          return;
        }
        const text = (res?.resolution_summary || '').trim();
        // 已生成完成（有值、无内容、或 empty）→ 结束轮询
        if (res?.status === 'empty') {
          // AI 判定无解决方案
          setResolutionLoading(false);
          setResolutionFailed(false);
          setResolutionNoSolution(true);
          setResolutionPolling(false);
          if (text) setResolutionText(text);
          return;
        }
        if (res?.status === 'done' || res?.status === 'confirmed') {
          if (text) setResolutionText(text);
          setResolutionLoading(false);
          setResolutionFailed(false);
          setResolutionNoSolution(false);
          setResolutionPolling(false);
          return;
        }
        // status === 'pending' → 仍在生成中，继续轮询
      } catch {
        // 单次请求失败继续轮询
      }
    }
    // 轮询超时仍未完成 → 置失败，提示用户手动补充/重试
    setResolutionLoading(false);
    setResolutionPolling(false);
    setResolutionFailed(true);
  };

  const handleResolveClick = () => {
    if (!detail?.id) return;
    resolvePollStopRef.current = false; // 重置轮询停止标志
    resolveConfirmedRef.current = false; // 重置确认标志（新一次打开）
    setShowResolutionPopup(true);
    setResolutionText('');
    setResolutionLoading(false);
    setResolutionFailed(false);
    setResolutionPolling(false);
    setResolutionNoSolution(false);
  };

  // 手动触发 AI 生成解决方式（点"帮我生成"时调用）
  const handleGenerateResolution = async () => {
    if (!detail?.id) return;
    resolvePollStopRef.current = false;
    setResolutionLoading(true);
    setResolutionFailed(false);
    setResolutionPolling(false);
    setResolutionNoSolution(false);
    try {
      const res = await request<{ status: string; resolution_summary?: string }>(`/${detail.id}/resolution-summary`, {
        method: 'POST',
        body: JSON.stringify({ force: true }),
        skipCache: true,
      });
      if (res) {
        const text = (res.resolution_summary || '').trim();
        if (text) {
          setResolutionText(text);
          setResolutionLoading(false);
          setResolutionFailed(false);
        } else if (res.status === 'pending') {
          pollResolutionSummary();
        } else if (res.status === 'empty') {
          setResolutionLoading(false);
          setResolutionFailed(false);
          setResolutionNoSolution(true);
        } else if (res.status === 'done') {
          setResolutionLoading(false);
          setResolutionFailed(false);
          setResolutionNoSolution(false);
        } else {
          setResolutionLoading(false);
          setResolutionFailed(true);
        }
      } else {
        setResolutionLoading(false);
        setResolutionFailed(true);
      }
    } catch {
      setResolutionLoading(false);
      setResolutionFailed(true);
    }
  };

  const handleRetryResolution = () => {
    resolvePollStopRef.current = false;
    setResolutionFailed(false);
    setResolutionLoading(true);
    setResolutionText('');
    setResolutionNoSolution(false);
    pollResolutionSummary(true);
  };

  // 取消：停止轮询 + 清掉已保存的解决方式草稿，关闭弹窗（下次点击重新生成）
  const handleResolveCancel = async () => {
    resolvePollStopRef.current = true;
    setResolutionPolling(false);
    setResolutionLoading(false);
    if (resolveConfirmedRef.current) {
      setShowResolutionPopup(false);
      resolveConfirmedRef.current = false;
      return;
    }
    setShowResolutionPopup(false);
    try {
      if (detail?.id) {
        await request<{ status: string }>(`/${detail.id}/resolution-summary`, {
          method: 'POST',
          body: JSON.stringify({ clear: true }),
          skipCache: true,
        });
      }
    } catch {
      // 清除失败不影响：下次点击仍会重新生成
    }
  };

  const handleConfirmResolve = async () => {
    if (!detail?.id) return;
    const finalText = resolutionText.trim();
    if (!finalText) {
      Toast({ message: '请填写解决方式', theme: 'warning' });
      return;
    }
    if (resolutionSubmitting) return;
    setResolutionSubmitting(true);
    try {
      await request(`/${detail.id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status: 'resolved', resolution_summary: finalText }),
      });
      afterResolve?.();
      await refreshDetail();
      resolveConfirmedRef.current = true;
      setShowResolutionPopup(false);
      setResolutionText('');
      Toast({ message: '工单已处理完成', theme: 'success' });
    } catch (err) {
      Toast({ message: `处理完成失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setResolutionSubmitting(false);
    }
  };

  return {
    showResolutionPopup,
    setShowResolutionPopup,
    resolutionText,
    setResolutionText,
    resolutionLoading,
    resolutionFailed,
    resolutionPolling,
    resolutionNoSolution,
    resolutionSubmitting,
    handleResolveClick,
    handleGenerateResolution,
    handleRetryResolution,
    handleResolveCancel,
    handleConfirmResolve,
  };
}
