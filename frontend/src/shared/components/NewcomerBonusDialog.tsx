// 新人红包弹窗（ORS-1086）—— 进「我要摇人」时弹出，领取后跳「我的奖励」由用户自行提现。
//
// 极简风：粗体小标题 + 大字金额 + 一行说明 + 一个主按钮，不加花哨图标。
// 金额单位是分，展示用 fenToYuan 转换；领取只记账，真正的转账在「我的奖励」页调起微信确认页。
import { useState } from 'react';
import { Button, Popup, Toast } from 'tdesign-mobile-react';
import { claimNewcomerBonus, fenToYuan, type NewcomerBonusStatus } from '@/api/wxpay';

export interface NewcomerBonusDialogProps {
  /** 弹窗是否可见 */
  visible: boolean;
  /** 弹窗内容（金额、标题、说明由后端下发，前端不写死） */
  status: NewcomerBonusStatus | null;
  /** 用户关闭弹窗（不领取） */
  onClose: () => void;
  /** 领取成功（带奖励记录 ID） */
  onClaimed: (rewardId: string) => void;
}

export default function NewcomerBonusDialog({ visible, status, onClose, onClaimed }: NewcomerBonusDialogProps) {
  const [busy, setBusy] = useState(false);

  async function handleClaim() {
    if (busy) return;
    setBusy(true);
    try {
      const result = await claimNewcomerBonus();
      if (result.ok) {
        Toast({ message: '已领取，去「我的奖励」提现到微信零钱', theme: 'success' });
        onClaimed(result.reward_id);
      } else {
        Toast({ message: result.message || '领取失败，请稍后重试', theme: 'warning' });
        onClose();
      }
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '领取失败，请稍后重试', theme: 'error' });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Popup visible={visible} onClose={onClose} placement="center" showOverlay>
      <div className="newcomer-bonus" role="dialog" aria-label="新人红包">
        <h3 className="newcomer-bonus__title">{status?.title || '新人福利'}</h3>
        <p className="newcomer-bonus__amount">
          <span className="newcomer-bonus__currency">¥</span>
          {fenToYuan(status?.amount_fen || 0)}
        </p>
        <p className="newcomer-bonus__desc">{status?.desc || ''}</p>
        <div className="newcomer-bonus__actions">
          <Button block theme="primary" loading={busy} onClick={() => void handleClaim()}>
            去领取
          </Button>
          <button type="button" className="newcomer-bonus__later" onClick={onClose} disabled={busy}>
            稍后再说
          </button>
        </div>
      </div>
    </Popup>
  );
}
