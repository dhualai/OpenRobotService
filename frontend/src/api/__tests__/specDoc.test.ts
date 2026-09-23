/**
 * specDoc 内嵌图片治理单测（纯函数部分）。
 * externalizeInlineImages 依赖上传接口，其网络行为由后端 pytest 与页面实测覆盖。
 */
import { describe, it, expect } from 'vitest';
import { foldHugeInlineImages, INLINE_IMAGE_FOLD_LIMIT } from '../specDoc';

describe('foldHugeInlineImages', () => {
  it('超阈值的 data URI 折叠为 1x1 占位', () => {
    const big = 'data:image/png;base64,' + 'A'.repeat(INLINE_IMAGE_FOLD_LIMIT + 1000);
    const r = foldHugeInlineImages('![图](' + big + ')');
    expect(r.folded).toBe(1);
    expect(r.content.length).toBeLessThan(120);
  });

  it('阈值内的 data URI 原样保留', () => {
    const small = 'data:image/png;base64,' + 'A'.repeat(2048);
    const md = '![图](' + small + ')';
    const r = foldHugeInlineImages(md);
    expect(r.folded).toBe(0);
    expect(r.content).toBe(md);
  });

  it('无内联图片时零改动', () => {
    const md = '普通正文';
    expect(foldHugeInlineImages(md)).toEqual({ content: md, folded: 0 });
  });
});
