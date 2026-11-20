import { describe, it, expect } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import { useRef } from 'react';
import { useHorizontalScroll } from '../useHorizontalScroll';

/**
 * 测试宿主：渲染一个横向滚动容器，内含若干 tab 按钮（记录点击）。
 * 用 enabled: true 强制启用 hook（绕过 jsdom matchMedia 默认 matches:false），
 * 直接验证 hook 核心逻辑：滚轮横滑、拖拽横滑、点击不被吞。
 *
 * 事件派发注意：jsdom 不支持 PointerEvent 构造器，fireEvent.pointerMove 会丢失
 * clientX/pointerId 等属性。这里用 MouseEvent 构造器手动派发（type 仍为 pointerdown/
 * pointermove/pointerup），保证坐标正确注入，hook 的 document 级监听照常收到。
 */
function firePointer(el: Element | Document, type: 'pointerdown' | 'pointermove' | 'pointerup', init: { clientX?: number; button?: number } = {}) {
  el.dispatchEvent(new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    clientX: init.clientX ?? 0,
    button: init.button ?? 0,
  }));
}

function Harness({ onTabClick }: { onTabClick: (v: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useHorizontalScroll(ref, { enabled: true });
  return (
    <div
      ref={ref}
      data-testid="scroll-container"
      style={{ overflowX: 'auto', width: 200, whiteSpace: 'nowrap' }}
    >
      {['a', 'b', 'c', 'd', 'e'].map((v) => (
        <button key={v} data-testid={`tab-${v}`} onClick={() => onTabClick(v)}
          style={{ display: 'inline-block', width: 80 }}>
          {v}
        </button>
      ))}
    </div>
  );
}

// 让容器「可横向滚动」：mock 只读尺寸属性（jsdom 无真实布局）
function mockScrollGeometry(container: HTMLElement, contentWidth: number, clientWidth: number) {
  Object.defineProperty(container, 'scrollWidth', { configurable: true, value: contentWidth });
  Object.defineProperty(container, 'clientWidth', { configurable: true, value: clientWidth });
}

describe('useHorizontalScroll', () => {
  it('点击 tab 正常触发，不被 hook 吞掉', () => {
    const clicks: string[] = [];
    const { getByTestId } = render(<Harness onTabClick={(v) => clicks.push(v)} />);
    fireEvent.click(getByTestId('tab-b'));
    fireEvent.click(getByTestId('tab-c'));
    expect(clicks).toEqual(['b', 'c']);
  });

  it('鼠标滚轮（deltaY）转为横向 scrollLeft', () => {
    const { getByTestId } = render(<Harness onTabClick={() => {}} />);
    const el = getByTestId('scroll-container');
    mockScrollGeometry(el, 500, 200);
    el.scrollLeft = 0;
    fireEvent.wheel(el, { deltaY: 40, deltaX: 0, deltaMode: 0 });
    expect(el.scrollLeft).toBeGreaterThan(0);
  });

  it('容器不可横滑时 wheel 不接管（scrollLeft 不变，不卡死页面）', () => {
    const { getByTestId } = render(<Harness onTabClick={() => {}} />);
    const el = getByTestId('scroll-container');
    mockScrollGeometry(el, 100, 200); // content 比 client 窄 → 不可滚
    el.scrollLeft = 0;
    fireEvent.wheel(el, { deltaY: 40, deltaX: 0, deltaMode: 0 });
    expect(el.scrollLeft).toBe(0);
  });

  it('鼠标左键拖拽横滑（pointerdown + pointermove）', () => {
    const { getByTestId } = render(<Harness onTabClick={() => {}} />);
    const el = getByTestId('scroll-container');
    mockScrollGeometry(el, 500, 200);
    el.scrollLeft = 100;

    firePointer(el, 'pointerdown', { clientX: 200 });
    firePointer(document, 'pointermove', { clientX: 120 }); // 左移 80px → scrollLeft 增加
    firePointer(document, 'pointerup', { clientX: 120 });
    expect(el.scrollLeft).toBeGreaterThan(100);
  });

  it('拖拽位移后松手，不误触发 tab 点击（click 被抑制）', () => {
    const clicks: string[] = [];
    const { getByTestId } = render(<Harness onTabClick={(v) => clicks.push(v)} />);
    const el = getByTestId('scroll-container');
    mockScrollGeometry(el, 500, 200);
    el.scrollLeft = 0;

    firePointer(el, 'pointerdown', { clientX: 100 });
    firePointer(document, 'pointermove', { clientX: 40 }); // 超过 3px → 视为拖拽
    firePointer(document, 'pointerup', { clientX: 40 });
    fireEvent.click(getByTestId('tab-a')); // 拖拽松手后的 click 应被抑制
    expect(clicks).toEqual([]);
  });

  it('拖拽之后的下一次正常点击不受残留状态影响', async () => {
    const clicks: string[] = [];
    const { getByTestId } = render(<Harness onTabClick={(v) => clicks.push(v)} />);
    const el = getByTestId('scroll-container');
    mockScrollGeometry(el, 500, 200);

    // 第一次：拖拽（产生 moved 标志）
    firePointer(el, 'pointerdown', { clientX: 100 });
    firePointer(document, 'pointermove', { clientX: 30 });
    firePointer(document, 'pointerup', { clientX: 30 });
    fireEvent.click(getByTestId('tab-a')); // 抑制本次 click

    // 等待 moved 标志被 setTimeout(0) 复位
    await new Promise((r) => setTimeout(r, 10));

    // 第二次：正常点击 tab，应正常触发
    fireEvent.click(getByTestId('tab-a'));
    expect(clicks).toEqual(['a']);
  });
});
