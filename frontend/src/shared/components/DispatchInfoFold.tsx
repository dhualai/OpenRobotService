import { useState } from 'react';
import { ChevronDown } from 'lucide-react';

/**
 * 派单信息折叠卡（共享组件）：系统任务详情页 TaskDetailPage 与历史工单详情页 TicketDetailPage 复用。
 * 承载「派单说明」(variant="tip") 与「派单理由」(variant="reason") 两种展示。
 * 角色判定（派单理由仅接单人/管理员可见）由父组件负责，本组件只负责折叠展示。
 */
interface DispatchInfoFoldProps {
  /** 折叠卡标题，如「派单说明」「派单理由」 */
  label: string;
  /** 完整正文内容 */
  content: string;
  /** 视觉变体，对应 global.css 的 .dispatch-fold--tip / .dispatch-fold--reason */
  variant?: 'tip' | 'reason';
}

export default function DispatchInfoFold({ label, content, variant = 'tip' }: DispatchInfoFoldProps) {
  const [open, setOpen] = useState(false);
  if (!content) return null;

  const cls = `dispatch-fold dispatch-fold--${variant}${open ? ' is-open' : ''}`;
  return (
    <div className={cls}>
      <button
        type="button"
        className="dispatch-fold__header"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="dispatch-fold__preview">
          <span className="dispatch-fold__label">{label}</span>
          <span className="dispatch-fold__sep">：</span>
          <span className="dispatch-fold__clip">{content.replace(/\s+/g, ' ').trim()}</span>
        </span>
        <ChevronDown size={14} className="dispatch-fold__chevron" aria-hidden />
      </button>
      <div className="dispatch-fold__bodywrap">
        <div className="dispatch-fold__body">{content}</div>
      </div>
    </div>
  );
}
