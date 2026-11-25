/**
 * MarkdownRenderer 流式稳定性回归：带图回复在流式期间不得每帧重载图片 DOM。
 *
 * 回归背景（2026-11-25 线上实锤）：「我要摇人」AI 流式回复一旦带图片，页面持续
 * 一闪一闪（手机/电脑同复现）。根因：components 对象内联新建 → react-markdown
 * 每次渲染全量重建元素树时 React 按函数引用判定「不同类型」→ 整棵 markdown DOM
 * （含 <img>）每帧（80~90ms）卸载重挂 → 浏览器反复重新加载图片。
 *
 * 本用例断言修复后的行为契约：
 *   ① streaming=true：图片占位节点跨帧保留同一 DOM 引用（不重载）；
 *   ② streaming=false：真实 <img> 跨帧保留同一 DOM 引用（不重新加载）；
 *   ③ 流式期间不渲染真实 <img>（占位机制保持）；
 *   ④ 定稿后正常渲染真实 <img>。
 */
import { describe, it, expect, vi } from 'vitest';
import { render, act } from '@testing-library/react';

// ---- mock：与既有 ChatPanel 测试同口径 ----
vi.mock('@/stores/auth', () => ({
  useAuthStore: Object.assign(
    () => ({ token: 'tok', username: 'tester' }),
    { getState: () => ({ token: 'tok', username: 'tester' }) },
  ),
}));

vi.mock('@/shared/utils/authImageFetch', () => ({
  loadAuthImage: async () => ({ status: 'loaded', blobUrl: 'blob:fake-1' }),
  getAuthImgCache: () => undefined,
  setAuthImgCache: () => undefined,
}));

import MarkdownRenderer from '../MarkdownRenderer';

/** 模拟流式逐帧增长的回复（图片在第 3 帧成形，之后文字继续增长） */
const FRAMES = [
  '手机里目前收到的验收项有两块：充电验收，以及输送线对接验收。',
  '手机里目前收到的验收项有两块：充电验收，以及输送线对接验收。整台车本身上下两层。',
  '手机里目前收到的验收项有两块：充电验收，以及输送线对接验收。整台车本身上下两层。\n\n![截图](https://example.com/a.png)',
  '手机里目前收到的验收项有两块：充电验收，以及输送线对接验收。整台车本身上下两层。\n\n![截图](https://example.com/a.png)\n\n充电验收：',
  '手机里目前收到的验收项有两块：充电验收，以及输送线对接验收。整台车本身上下两层。\n\n![截图](https://example.com/a.png)\n\n充电验收：先记下目标充电桩对应的点位编号。',
  '手机里目前收到的验收项有两块：充电验收，以及输送线对接验收。整台车本身上下两层。\n\n![截图](https://example.com/a.png)\n\n充电验收：先记下目标充电桩对应的点位编号，发送。',
];

/** 按文本找图片占位节点（不依赖类名/实现） */
function findPlaceholder(root: HTMLElement): HTMLElement | null {
  return (
    [...root.querySelectorAll('span')].find((s) => (s.textContent || '').includes('图片加载中')) ?? null
  );
}

describe('MarkdownRenderer 流式稳定性：带图回复不重载', () => {
  it('streaming=true：图片占位节点跨帧保留同一 DOM 引用，且流式期间不出现真实 img', async () => {
    const { container, rerender } = render(
      <MarkdownRenderer content={FRAMES[0]} compact streaming />,
    );
    const body = container.querySelector('.markdown-body') as HTMLElement;
    const removedImgs: Node[] = [];
    const obs = new MutationObserver((recs) => {
      for (const r of recs) {
        if (r.type !== 'childList') continue;
        for (const n of Array.from(r.removedNodes)) {
          if (n.nodeName === 'IMG') removedImgs.push(n);
        }
      }
    });
    obs.observe(body, { childList: true, subtree: true });

    let placeholderRef: HTMLElement | null = null;
    let remounts = 0;
    for (const f of FRAMES) {
      await act(async () => {
        rerender(<MarkdownRenderer content={f} compact streaming />);
      });
      const ph = findPlaceholder(body);
      if (ph) {
        if (placeholderRef && ph !== placeholderRef) remounts += 1;
        placeholderRef = ph;
      }
      // 流式期间任何一帧都不允许出现真实图片
      expect(body.querySelectorAll('img')).toHaveLength(0);
    }
    obs.disconnect();

    expect(placeholderRef).not.toBeNull(); // 占位机制生效
    expect(remounts).toBe(0); // 占位节点跨帧复用，未被销毁重建
    expect(removedImgs).toHaveLength(0);
  });

  it('streaming=false：真实 img 元素跨帧保留同一引用（不会被浏览器重新加载）', async () => {
    const { container, rerender } = render(
      <MarkdownRenderer content={FRAMES[0]} compact streaming={false} />,
    );
    const body = container.querySelector('.markdown-body') as HTMLElement;
    let imgRef: HTMLImageElement | null = null;
    let remounts = 0;
    const removedImgs: Node[] = [];
    const obs = new MutationObserver((recs) => {
      for (const r of recs) {
        if (r.type !== 'childList') continue;
        for (const n of Array.from(r.removedNodes)) {
          if (n.nodeName === 'IMG') removedImgs.push(n);
        }
      }
    });
    obs.observe(body, { childList: true, subtree: true });

    for (const f of FRAMES) {
      await act(async () => {
        rerender(<MarkdownRenderer content={f} compact streaming={false} />);
      });
      const img = body.querySelector('img');
      if (img) {
        if (imgRef && img !== imgRef) remounts += 1;
        imgRef = img;
      }
    }
    obs.disconnect();

    expect(imgRef).not.toBeNull(); // 定稿路径正常渲染真实图片
    expect(imgRef?.getAttribute('src')).toBe('https://example.com/a.png');
    expect(remounts).toBe(0); // 关键：img 元素跨帧复用，不重载
    expect(removedImgs).toHaveLength(0);
  });

  it('定稿切换：streaming 由 true 转 false 后渲染真实 img', async () => {
    const content = FRAMES[FRAMES.length - 1];
    const { container, rerender } = render(
      <MarkdownRenderer content={content} compact streaming />,
    );
    const body = container.querySelector('.markdown-body') as HTMLElement;
    expect(body.querySelectorAll('img')).toHaveLength(0);

    await act(async () => {
      rerender(<MarkdownRenderer content={content} compact streaming={false} />);
    });
    const img = body.querySelector('img');
    expect(img).not.toBeNull();
    expect(img?.getAttribute('src')).toBe('https://example.com/a.png');
  });
});
