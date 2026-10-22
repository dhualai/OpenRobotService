// ORS-1035 两节制：问题描述 + 解决方案/回答（咨询问答类第二节用「回答」）。
// 老 8 节报告（问题概述/原因分析…）在机械入库与审核侧仍兼容。

export const REPORT_QUESTION_SECTIONS = ['问题描述', '问题'];
export const REPORT_SOLUTION_SECTIONS = ['解决方案', '回答'];

/** 按工单类型给补齐模板：support/other（咨询问答类）第二节用「回答」，其余用「解决方案」 */
export function completeArchiveReportTemplate(content: string, taskType?: string): string {
  const answerStyle = taskType === 'support' || taskType === 'other';
  const sections = answerStyle ? ['问题描述', '回答'] : ['问题描述', '解决方案'];
  const missing = sections.filter(
    (heading) => !new RegExp(`^#{1,6}\\s+(?:\\d+[.、]\\s*)?${heading}\\s*$`, 'm').test(content),
  );
  return [content.trim(), ...missing.map((heading) => `## ${heading}\n\n`)].filter(Boolean).join('\n\n').trim();
}
