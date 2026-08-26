// 微信经典表情选择器：点击插入 shortcode 到输入框光标处，面板可连续选择，点击外部关闭。
// 极简版：白底 + 自适应列表情网格，无标题/tab/最近使用/hover预览。
// 渲染层 + shortcode 协议完全不动（参见 ./shared/emoji/wechat.ts）。
import { useEffect, useRef } from 'react';
import { WECHAT_EMOJIS } from '@/shared/emoji/wechat';

interface EmojiPickerProps {
  /** 选中表情（code 不含方括号，如「微笑」） */
  onSelect: (code: string) => void;
  onClose: () => void;
}

export default function EmojiPicker({ onSelect, onClose }: EmojiPickerProps) {
  const ref = useRef<HTMLDivElement>(null);

  // 点击外部关闭
  useEffect(() => {
    const onDocDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener('mousedown', onDocDown);
    return () => document.removeEventListener('mousedown', onDocDown);
  }, [onClose]);

  return (
    <div className="emoji-picker" ref={ref}>
      <div className="emoji-picker__grid">
        {WECHAT_EMOJIS.map((e) => (
          <button
            type="button"
            key={e.code}
            className="emoji-picker__item"
            onClick={() => onSelect(e.code)}
            aria-label={e.code}
            title={e.code}
          >
            <img src={e.url} alt={e.code} loading="lazy" decoding="async" draggable={false} />
          </button>
        ))}
      </div>
    </div>
  );
}
