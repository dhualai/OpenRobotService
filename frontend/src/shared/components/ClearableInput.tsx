// 可清空输入框 —— 修复 tdesign-mobile 内置 clearable 在电脑端失效的问题
//
// tdesign-mobile-react v0.23.1 的 Input `clearable` 清除图标只绑定了 onTouchEnd（移动端触摸事件），
// 电脑端鼠标点击 × 不会触发，导致输入框无法清空。本组件基于 tdesign Input，用 suffix 插槽渲染
// 自定义 × 按钮，绑定 onClick（PC/移动端均有效）+ CSS touch-action: manipulation（消除移动端
// 300ms 点击延迟，点击即时响应），并带弹出动画 + 悬停/按压动态效果。清空后重新聚焦 input，
// 方便继续输入（PC/移动端一致）。
// 用本组件替换业务代码中的 `<Input clearable ...>` 即可全局生效，样式复用 global.css 的
// .project-picker-clear 类（弹出/悬停/按压动画）。
import { forwardRef, useRef } from 'react';
import { Input } from 'tdesign-mobile-react';
import { CloseCircleFilledIcon } from 'tdesign-icons-react';
import type { InputProps } from 'tdesign-mobile-react/es/input';
import type { InputRefProps } from 'tdesign-mobile-react/es/input/Input';

export interface ClearableInputProps extends Omit<InputProps, 'clearable' | 'suffix'> {
  /** 输入框有值时才显示清空按钮（默认 true）。false 时始终隐藏 × */
  showClear?: boolean;
}

const ClearableInput = forwardRef<InputRefProps, ClearableInputProps>(
  function ClearableInput({ showClear = true, onChange, ...rest }, ref) {
    const inputRef = useRef<InputRefProps | null>(null);
    const hasValue = rest.value != null && String(rest.value).length > 0;

    const handleClear = () => {
      onChange?.('', { trigger: 'clear' });
      // 清空后重新聚焦，键盘保持弹出，方便继续输入（移动端必须，PC 端也无副作用）
      inputRef.current?.focus?.();
    };

    return (
      <Input
        ref={(node) => {
          inputRef.current = node;
          if (typeof ref === 'function') ref(node);
          else if (ref) ref.current = node;
        }}
        {...rest}
        onChange={onChange}
        suffix={
          showClear && hasValue ? (
            <span
              className="project-picker-clear"
              role="button"
              aria-label="清空输入"
              onMouseDown={(e) => e.preventDefault()}
              onClick={handleClear}
            >
              <CloseCircleFilledIcon />
            </span>
          ) : undefined
        }
      />
    );
  }
);

export default ClearableInput;
