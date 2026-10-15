import { describe, expect, it } from 'vitest';
import globalStyles from '@/shared/styles/global.css?raw';

function ruleStyle(selector: string): CSSStyleDeclaration {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = globalStyles.match(new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]+)\\}`));
  if (!match) throw new Error(`Missing vehicle style: ${selector}`);
  const sheet = new CSSStyleSheet();
  sheet.insertRule(`${selector} {${match[1]}}`);
  return (sheet.cssRules[0] as CSSStyleRule).style;
}

describe('车型入口气泡主题配色', () => {
  it('车型选择按钮使用马卡龙主蓝底和白字', () => {
    const style = ruleStyle('.chat-veh-chip--intro');
    expect(style.getPropertyValue('background')).toBe('var(--primary)');
    expect(style.getPropertyValue('border-color')).toBe('var(--primary)');
    expect(style.getPropertyValue('color')).toBe('#fff');
  });

  it('报障选项使用主题浅蓝底、蓝色文字和同色系描边', () => {
    const style = ruleStyle('.chat-veh-chip');
    expect(style.getPropertyValue('background')).toBe('var(--primary-soft)');
    expect(style.getPropertyValue('color')).toBe('var(--sky)');
    expect(style.getPropertyValue('border')).toContain('var(--primary)');
  });

  it('报障选项按下时仍使用主题蓝色', () => {
    const style = ruleStyle('.chat-veh-chip:active');
    expect(style.getPropertyValue('background')).toBe('var(--accent)');
    expect(style.getPropertyValue('border-color')).toBe('var(--primary)');
  });

  it.each(['.chat-veh-chip--selected', '.chat-veh-chip--intro.chat-veh-chip--selected'])(
    '选中态使用加深主题蓝底白字：%s', (selector) => {
      const style = ruleStyle(selector);
      expect(style.getPropertyValue('background')).toBe('var(--sky)');
      expect(style.getPropertyValue('border-color')).toBe('var(--sky)');
      expect(style.getPropertyValue('color')).toBe('#fff');
    },
  );

  it('车型按下态和展开全部按钮保持同一蓝色色调', () => {
    expect(ruleStyle('.chat-veh-chip--intro:active').getPropertyValue('background')).toBe('var(--sky)');
    expect(ruleStyle('.chat-veh-expand').getPropertyValue('color')).toBe('var(--sky)');
  });

  it('保留未选项弱化和车型按钮两列布局', () => {
    expect(ruleStyle('.chat-veh-chip--done').getPropertyValue('opacity')).toBe('0.45');
    expect(ruleStyle('.chat-veh-choices--intro').getPropertyValue('grid-template-columns')).toBe('repeat(2, minmax(0, 1fr))');
  });
});
