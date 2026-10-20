export const KNOWLEDGE_REPORT_SECTIONS = [
  '问题概述', '问题场景', '复现条件', '原因分析', '解决方案', '验证结果', '适用范围', '遗留问题',
];

export function completeArchiveReportTemplate(content: string) {
  const missing = KNOWLEDGE_REPORT_SECTIONS.filter((heading) =>
    !new RegExp(`^#{1,6}\\s+(?:\\d+[.、]\\s*)?${heading}\\s*$`, 'm').test(content));
  return [content.trim(), ...missing.map((heading) => `## ${heading}\n\n`)].filter(Boolean).join('\n\n').trim();
}
