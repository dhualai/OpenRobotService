// ORS-1035 两节制：问题描述 + 解决方案/回答（咨询问答类第二节用「回答」）。
// 开始归档即预填骨架（空节），手写往里填或点 AI 初稿代笔——提交校验只拦空白。

/** 按工单类型出两节骨架：support/other（咨询问答类）第二节用「回答」，其余用「解决方案」 */
export function archiveSkeleton(taskType?: string): string {
  const answerStyle = taskType === 'support' || taskType === 'other';
  const second = answerStyle ? '回答' : '解决方案';
  return `## 问题描述\n\n## ${second}\n`;
}

/** 补齐骨架中缺的节（保留历史兼容：手写自由文本时只补缺失标题） */
export function completeArchiveReportTemplate(content: string, taskType?: string): string {
  const answerStyle = taskType === 'support' || taskType === 'other';
  const sections = answerStyle ? ['问题描述', '回答'] : ['问题描述', '解决方案'];
  const missing = sections.filter(
    (heading) => !new RegExp(`^#{1,6}\s+(?:\d+[.、]\s*)?${heading}\s*$`, 'm').test(content),
  );
  return [content.trim(), ...missing.map((heading) => `## ${heading}\n\n`)].filter(Boolean).join('\n\n').trim();
}
