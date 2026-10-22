import { completeArchiveReportTemplate } from '../archiveReport';

describe('completeArchiveReportTemplate（两节制）', () => {
  it('故障类：补 问题描述+解决方案', () => {
    const result = completeArchiveReportTemplate('', 'problem');
    expect(result).toContain('## 问题描述');
    expect(result).toContain('## 解决方案');
  });

  it('咨询类（support/other）：第二节是 回答', () => {
    const result = completeArchiveReportTemplate('', 'support');
    expect(result).toContain('## 问题描述');
    expect(result).toContain('## 回答');
    expect(result).not.toContain('## 解决方案');
    const other = completeArchiveReportTemplate('', 'other');
    expect(other).toContain('## 回答');
  });

  it('已有内容：只补缺失的节', () => {
    const result = completeArchiveReportTemplate('## 问题描述\n车不动', 'problem');
    expect(result).toContain('车不动');
    expect(result).toContain('## 解决方案');
    expect(result).not.toContain('## 问题描述\n\n');
  });
});
