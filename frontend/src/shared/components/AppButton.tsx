import { Button } from 'tdesign-mobile-react';
import type { ComponentProps } from 'react';
import './AppButton.css';

export type AppButtonTone = 'primary' | 'muted' | 'blue' | 'blue-deep';

export type AppButtonProps = ComponentProps<typeof Button> & {
  /** 品牌设计变体；不传则透传 TDesign 原生 theme，仅统一禁用 ::after 边框 */
  tone?: AppButtonTone;
};

/**
 * 工程级根治 TDesign 按钮 ::after 边框留白。
 * 根因：TDesign Mobile 的 Button 边框不是 border 属性，而是 ::after 伪元素
 * （width/height:200% + transform:scale(0.5) 高清 1px 边框），border-color 由各
 * theme 规则设置。只要业务自定义了按钮背景色，原 ::after 灰边框就会残留在背景
 * 外形成「留白」。所有走 AppButton 的业务按钮统一禁用 ::after，背景即按钮边界。
 *
 * 用法：
 *   <AppButton tone="primary">催办</AppButton>          品牌蓝实心胶囊（详情页主操作）
 *   <AppButton tone="muted">撤回</AppButton>            灰底 muted（详情页撤回）
 *   <AppButton tone="blue">催办</AppButton>             列表页蓝实心
 *   <AppButton tone="blue-deep">重新派单</AppButton>    列表页深蓝
 *   <AppButton theme="primary">确定</AppButton>         不传 tone：透传 TDesign 原生主题，仅禁用 ::after
 */
export default function AppButton({ tone, className, ...rest }: AppButtonProps) {
  const cls = ['app-btn', tone ? `app-btn--${tone}` : '', className]
    .filter(Boolean)
    .join(' ');
  return <Button {...rest} className={cls} />;
}
