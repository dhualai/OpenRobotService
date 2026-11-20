import { archiveSkeleton, completeArchiveReportTemplate } from '../archiveReport';

describe('archiveSkeleton（两节骨架）', () => {
  it('故障类：问题描述+解决方案', () => {
    const s = archiveSkeleton('problem');
    expect(s).toContain('## 问题描述');
    expect(s).toContain('## 解决方案');
  });

  it('咨询类（support/other）：第二节是 回答', () => {
    const s = archiveSkeleton('support');
    expect(s).toContain('## 问题描述');
    expect(s).toContain('## 回答');
    const other = archiveSkeleton('other');
    expect(other).toContain('## 回答');
  });
});

describe('completeArchiveReportTemplate（补缺）', () => {
  it('缺哪节补哪节', () => {
    const result = completeArchiveReportTemplate('## 问题描述\n车不动', 'problem');
    expect(result).toContain('车不动');
    expect(result).toContain('## 解决方案');
  });

  it('两节齐全不动原文', () => {
    const src = '## 问题描述\n phenomenon\n\n## 解决方案\n fix';
    const result = completeArchiveReportTemplate(src, 'problem');
    expect(result).toContain(' phenomenon');
    expect(result).toContain('## 解决方案');
  });
});
