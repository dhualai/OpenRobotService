// 代他人提单 —— 工单详情页关系横幅。
//
// 三种视角三套内容（后端通过 is_agent / is_principal / is_assignee 下发视角标记，
// 前端**不自行拼身份判定**）：
//  - 代理人视角：你代 X 提交 + 关系状态胶囊（待确认 / 已跟进 / 已拒绝）
//  - 被代理人视角：
//      · pending      → 「X 代你提交了本工单」+ 操作按钮（确认跟进 / 与我无关）
//      · acknowledged → 弱化只读态「你已确认跟进」
//      · declined     → 弱化只读态「你已表示与本单无关」
//  - 接单人视角：X 代 Y 提交 + 关系状态胶囊（只读，便于处理人判断该找谁对接）
//
// 视觉：复用 .surface-card 毛玻璃 + 左侧主色竖条，配色全部走 global.css 的 oklch token。
import { useState } from 'react';
import { Button, Popup, Textarea, Toast } from 'tdesign-mobile-react';
import { ackProxyRelation, declineProxyRelation } from '@/api/ticket';
import type { ProxyRelation } from '@/api/ticket';

interface Props {
  ticketId: number | string;
  relation: ProxyRelation;
  /** 操作成功（确认/拒绝）后回调，供详情页刷新数据 */
  onChanged?: (relation: ProxyRelation) => void;
}

const STATUS_TEXT: Record<string, string> = {
  pending: '待确认',
  acknowledged: '已跟进',
  declined: '已拒绝',
};

const ProxyRelationBanner = ({ ticketId, relation, onChanged }: Props) => {
  const [declineVisible, setDeclineVisible] = useState(false);
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const status = relation.relation_status;
  const isPrincipal = relation.is_principal;
  const isAgent = relation.is_agent;
  const otherName = isPrincipal ? relation.agent_name : relation.principal_name;

  const handleAck = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      const updated = await ackProxyRelation(ticketId, relation.id);
      Toast({ theme: 'success', message: '已确认跟进' });
      onChanged?.(updated);
    } catch (err) {
      console.error('确认跟进失败', err);
      Toast({ theme: 'error', message: '确认失败，请重试' });
    } finally {
      setSubmitting(false);
    }
  };

  const handleDecline = async () => {
    const text = reason.trim();
    if (!text) {
      Toast({ theme: 'warning', message: '请填写与本单无关的原因' });
      return;
    }
    setSubmitting(true);
    try {
      const updated = await declineProxyRelation(ticketId, relation.id, text);
      Toast({ theme: 'success', message: '已反馈' });
      setDeclineVisible(false);
      setReason('');
      onChanged?.(updated);
    } catch (err) {
      console.error('拒绝跟进失败', err);
      Toast({ theme: 'error', message: '提交失败，请重试' });
    } finally {
      setSubmitting(false);
    }
  };

  // ── 被代理人视角 ──
  if (isPrincipal) {
    if (status === 'pending') {
      return (
        <>
          <div className="proxy-banner proxy-banner--action">
            <div className="proxy-banner__text">
              <strong>{otherName || '他人'}</strong> 代你提交了本工单，请确认是否跟进
            </div>
            <div className="proxy-banner__actions">
              <Button
                theme="primary"
                size="small"
                loading={submitting}
                onClick={handleAck}
              >
                确认跟进
              </Button>
              <Button
                theme="default"
                variant="outline"
                size="small"
                disabled={submitting}
                onClick={() => setDeclineVisible(true)}
              >
                与我无关
              </Button>
            </div>
          </div>

          <Popup
            visible={declineVisible}
            placement="bottom"
            destroyOnClose
            preventScrollThrough={false}
            onVisibleChange={(v) => !v && setDeclineVisible(false)}
          >
            <div className="proxy-decline">
              <div className="proxy-decline__title">与本单无关</div>
              <div className="proxy-decline__hint">
                请说明原因，提交人会收到通知；工单仍由他继续推进，不会中断。
              </div>
              <Textarea
                className="proxy-decline__input"
                placeholder="例如：该问题由另一位同事对接"
                value={reason}
                maxlength={500}
                autosize={{ minRows: 3, maxRows: 5 }}
                onChange={(v) => setReason(String(v ?? ''))}
              />
              <div className="proxy-decline__actions">
                <Button theme="default" variant="outline" onClick={() => setDeclineVisible(false)}>
                  取消
                </Button>
                <Button theme="primary" loading={submitting} onClick={handleDecline}>
                  提交
                </Button>
              </div>
            </div>
          </Popup>
        </>
      );
    }

    // acknowledged / declined：弱化只读态
    return (
      <div className="proxy-banner proxy-banner--muted">
        <div className="proxy-banner__text">
          {status === 'acknowledged'
            ? `你已确认跟进本工单（${otherName || '他人'} 代你提交）`
            : `你已表示与本单无关（${otherName || '他人'} 代你提交）`}
        </div>
        <span className={`proxy-pill proxy-pill--${status}`}>{STATUS_TEXT[status] || status}</span>
      </div>
    );
  }

  // ── 代理人视角 ──
  if (isAgent) {
    return (
      <div className="proxy-banner">
        <div className="proxy-banner__text">
          你代 <strong>{otherName || '对方'}</strong> 提交
        </div>
        <span className={`proxy-pill proxy-pill--${status}`}>{STATUS_TEXT[status] || status}</span>
      </div>
    );
  }

  // ── 接单人视角 ──：处理人也能看到「谁代谁提单」，便于判断该找谁对接（只读信息态）。
  // 姓名对非参与人不下发，故此处同样以「双方姓名齐备」为前提，缺则与其他视角一致地不渲染。
  if (relation.is_assignee && relation.agent_name && relation.principal_name) {
    return (
      <div className="proxy-banner proxy-banner--muted">
        <div className="proxy-banner__text">
          <strong>{relation.agent_name}</strong> 代 <strong>{relation.principal_name}</strong> 提交
        </div>
        <span className={`proxy-pill proxy-pill--${status}`}>{STATUS_TEXT[status] || status}</span>
      </div>
    );
  }

  return null;
};

export default ProxyRelationBanner;
