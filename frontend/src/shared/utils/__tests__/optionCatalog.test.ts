// 模板页「一键填选项」按钮的目录判定：按钮文案跟着节点走，不是所有下拉都叫车型目录。
import { describe, it, expect } from 'vitest';
import { optionCatalogFor } from '../optionCatalog';

describe('optionCatalogFor（下拉节点 → 可选目录）', () => {
  it('「车型N」标题的节点给车型目录', () => {
    const catalog = optionCatalogFor({ title: '车型1' });
    expect(catalog?.label).toBe('填入车型目录');
    expect(catalog?.hint).toContain('66 款');
    expect(catalog?.values).toHaveLength(66);
    expect(catalog?.values).toContain('XC1051');
  });

  it('改过名但选项里已经是车型型号的，仍认得出是车型目录', () => {
    const catalog = optionCatalogFor({ title: 'AGV 型号', options: ['XC1051', 'XCD061'] });
    expect(catalog?.label).toBe('填入车型目录');
  });

  it('普通下拉（项目类型 / 载具类型）没有对应目录：不显示按钮，选项自己敲', () => {
    expect(optionCatalogFor({ title: '项目类型', options: ['试点项目', 'PK项目'] })).toBeNull();
    expect(optionCatalogFor({ title: '载具类型', options: [] })).toBeNull();
  });
});
