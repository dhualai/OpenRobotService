// 代他人提单 —— 工单详情页关系横幅。
//
// 文案统一口径（不再区分代理人 / 被代理人 / 接单人视角）：
//  「代 X 提交」+ 关系状态胶囊（待确认 / 已跟进 / 已拒绝）。
//  X = 被代提人姓名；未注册 / 未实名的 wechat id（后端不下发裸 id）缺省「未知用户」。
// 视角标记（后端按 token 判定下发，前端不自行拼身份判定）仅用于：
//  - 被代理人 pending：附加「确认跟进 / 与我无关」操作按钮（唯一可操作分支）
//  - 非参与人：不渲染
//
// 视觉：实底白卡（背景不透明，避免页面底色透出）+ 左侧主色竖条（::before 伪元素，
// 详见 global.css .proxy-banner），配色全部走 oklch token。
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
  // 统一文案：X = 被代提人姓名；未注册 / 未实名（后端不下发裸 id）缺省「未知用户」
  const principalDisplayName = relation.principal_name || '未知用户';

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

  // ── 统一渲染（不区分视角）──：非参与人不渲染（脱敏：后端无视角标记时这里拦住）。
  if (!isPrincipal && !relation.is_agent && !relation.is_assignee) {
    return null;
  }

  // 被代理人 pending：待办态，统一文案 + 「确认跟进 / 与我无关」操作按钮
  if (isPrincipal && status === 'pending') {
    return (
      <>
        <div className="proxy-banner proxy-banner--action">
          <div className="proxy-banner__text">
            代 <strong>{principalDisplayName}</strong> 提交，请确认是否跟进
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

  // 其余所有视角（代理人 / 已确认或已拒绝的被代理人 / 接单人）：弱化只读态
  return (
    <div className="proxy-banner proxy-banner--muted">
      <div className="proxy-banner__text">
        代 <strong>{principalDisplayName}</strong> 提交
      </div>
      <span className={`proxy-pill proxy-pill--${status}`}>{STATUS_TEXT[status] || status}</span>
    </div>
  );
};

export default ProxyRelationBanner;
