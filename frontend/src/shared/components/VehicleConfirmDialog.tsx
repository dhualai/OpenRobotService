// 车辆信息确认弹窗（车体扫码进入链路）
//
// 纯受控：只负责展示 项目名称/客户名称/车型信息 三行，以及承接「暂不 / 确认」两个动作，
// 自己不发任何请求。确认过程中的 loading 由父组件传入，期间两个按钮都禁用，防重复提交。
// 弹层复用全站统一的 tdesign-mobile-react Dialog，与既有弹窗（如 SubscriptionReminder）观感一致。
import type { CSSProperties } from 'react';
import { Dialog } from 'tdesign-mobile-react';

import type { VehicleContext } from '@/stores/workbench';

/** 缺值统一显示占位符，避免出现空白行 */
const PLACEHOLDER = '—';

const withPlaceholder = (value?: string | null): string => (value ?? '').trim() || PLACEHOLDER;

const bodyStyle: CSSProperties = { padding: '4px 0 8px' };

const rowStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'flex-start',
  justifyContent: 'space-between',
  gap: 16,
  padding: '10px 0',
  fontSize: 14,
  lineHeight: '20px',
};

const labelStyle: CSSProperties = { flex: '0 0 auto', color: '#8B8F94' };

const valueStyle: CSSProperties = { color: '#1F2329', textAlign: 'right', wordBreak: 'break-word' };

export interface VehicleConfirmDialogProps {
  visible: boolean;
  /** 待确认的车辆信息（取自扫码查到的那条码记录）；为 null 时三行都显示占位符 */
  info: VehicleContext | null;
  /** 确认提交中：主按钮转 loading，两个按钮都禁用 */
  confirming?: boolean;
  onConfirm: () => void;
  /** 点「暂不」或点遮罩：关闭弹窗并保持当前对话 */
  onCancel: () => void;
}

export default function VehicleConfirmDialog({
  visible,
  info,
  confirming = false,
  onConfirm,
  onCancel,
}: VehicleConfirmDialogProps) {
  const rows: Array<{ label: string; value: string }> = [
    { label: '项目名称', value: withPlaceholder(info?.projectName) },
    { label: '客户名称', value: withPlaceholder(info?.customerName) },
    { label: '车型信息', value: withPlaceholder(info?.vehicleModel) },
  ];

  return (
    <Dialog
      visible={visible}
      title="车辆信息确认"
      // 宽 360px 封顶 + maxWidth 88vw：窄屏自适应收窄，大屏不被拉伸
      width={360}
      style={{ maxWidth: '88vw' }}
      // 只挂 onCancel（取消按钮）与 onOverlayClick（遮罩）：
      // Dialog 内部点「取消」时会同时回调 onCancel 和 onClose，两个都接会把「暂不」重复触发一次，
      // 故这里刻意不接 onClose。
      cancelBtn={{ content: '暂不', disabled: confirming }}
      confirmBtn={{ content: '确认', loading: confirming, disabled: confirming }}
      onCancel={onCancel}
      onOverlayClick={onCancel}
      onConfirm={onConfirm}
    >
      <div style={bodyStyle}>
        {rows.map((row) => (
          <div key={row.label} style={rowStyle}>
            <span style={labelStyle}>{row.label}</span>
            <span style={valueStyle}>{row.value}</span>
          </div>
        ))}
      </div>
    </Dialog>
  );
}
