import { describe, expect, it } from 'vitest';
import { completeArchiveReportTemplate, KNOWLEDGE_REPORT_SECTIONS } from '../archiveReport';

describe('归档报告章节模板', () => {
  it('生成八个必要章节但不编造正文', () => {
    const result = completeArchiveReportTemplate('');
    KNOWLEDGE_REPORT_SECTIONS.forEach((heading) => expect(result).toContain(`## ${heading}`));
    expect(result).not.toContain('已验证');
  });

  it('保留已有正文和编号章节，不重复补齐', () => {
    const original = '## 1. 问题概述\n电量不足\n\n## 解决方案\n更换电池';
    const result = completeArchiveReportTemplate(original);
    expect(result).toContain(original);
    expect(result.match(/问题概述/g)).toHaveLength(1);
    expect(result.match(/解决方案/g)).toHaveLength(1);
    expect(completeArchiveReportTemplate(result)).toBe(result);
  });
});
