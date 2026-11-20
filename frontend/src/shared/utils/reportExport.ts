/**
 * 日报/周报导出工具：Word（HTML 封装的 .doc，Word 可直接打开且可继续编辑）。
 *
 * 重型依赖（react-dom/server）在本模块内部动态 import，
 * 调用方通过 `await import('@/shared/utils/reportExport')` 懒加载，不进首屏 bundle。
 *
 * markdown → HTML 与页面 MarkdownRenderer 同源（react-markdown + remark-gfm），
 * 导出样式与页面上渲染的排版保持一致（标题/表格/列表/代码块/引用）。
 */
import { createElement } from 'react';

// ---------------------------------------------------------------------------
// markdown → HTML 字符串
// ---------------------------------------------------------------------------

/** 把 markdown 文本渲染为 HTML 字符串（与页面 MarkdownRenderer 同一套解析器） */
export async function markdownToHtml(markdown: string): Promise<string> {
  const { renderToStaticMarkup } = await import('react-dom/server');
  const ReactMarkdown = (await import('react-markdown')).default;
  const remarkGfm = (await import('remark-gfm')).default;
  return renderToStaticMarkup(
    createElement(ReactMarkdown, { remarkPlugins: [remarkGfm] }, markdown),
  );
}

// ---------------------------------------------------------------------------
// 表格列宽自适应 + 对齐：列数相同的表格统一各列宽度
// ---------------------------------------------------------------------------
// 日报/周报中表格默认自动布局，内容超宽时会撑破容器出现横向滚动条，
// 且列数相同的多张表格列线参差不齐。这里把所有表格统一为固定布局
// （table-layout: fixed + 100% 宽），列宽按测量出的自然比例换算为
// 百分比分配：表格永远铺满容器、随页面尺寸自适应，不会产生横向滚动条；
// 列数相同的表格组每列取组内最大自然宽度，列线对齐。

/** 对齐 root 内所有表格：直接操作 DOM（同步完成，供页面渲染/离屏容器复用） */
export function applyTableAlign(root: HTMLElement | null): void {
  if (!root) return;
  const tables = Array.from(root.querySelectorAll<HTMLTableElement>('table'));
  // 按列数分组（列数取首个 tr 的单元格数；react-markdown 表格为 thead>tr>th）
  const groups = new Map<number, HTMLTableElement[]>();
  for (const t of tables) {
    const firstRow = t.querySelector('tr');
    const cols = firstRow ? firstRow.children.length : 0;
    if (cols > 0) {
      const list = groups.get(cols) ?? [];
      list.push(t);
      groups.set(cols, list);
    }
  }

  for (const [cols, list] of groups) {
    // 1) 重置上次对齐状态，回到自动布局测自然列宽
    //    （inline auto 覆盖 CSS 兜底的 fixed，否则测不到内容驱动的列宽）
    for (const t of list) {
      t.style.tableLayout = 'auto';
      t.style.width = '';
      const cg = t.querySelector('colgroup');
      if (cg) cg.remove();
    }
    root.offsetWidth; // 强制回流，让重置后的自动布局生效

    // 2) 每列取组内最大值（th 表头参与测量；offsetWidth 含 padding/边框）
    const widths = new Array<number>(cols).fill(0);
    for (const t of list) {
      const cells = Array.from((t.querySelector('tr') as HTMLTableRowElement).children);
      for (let i = 0; i < cols; i++) {
        const w = (cells[i] as HTMLElement).offsetWidth;
        if (w > widths[i]) widths[i] = w;
      }
    }

    // 3) 换算为百分比列宽并应用：单张表格同样固定，保证每张表都 100% 铺满
    //    容器、随页面尺寸自适应；列宽按比例分配 → 列数相同的表格列线对齐，
    //    且表格宽度永远不会超出容器（无横向滚动条）
    const total = widths.reduce((a, b) => a + b, 0) || 1;
    const colHtml = widths
      .map((w) => `<col style="width:${((w / total) * 100).toFixed(2)}%" />`)
      .join('');
    for (const t of list) {
      t.style.tableLayout = 'fixed';
      t.style.width = '100%';
      t.insertAdjacentHTML('afterbegin', `<colgroup>${colHtml}</colgroup>`);
    }
  }
}

/** 离屏对齐 HTML 字符串中的表格列宽（导出 Word 前调用，960px 模拟 Word 页宽） */
function alignHtmlTables(html: string): string {
  if (typeof document === 'undefined' || !html) return html;
  const host = document.createElement('div');
  // visibility:hidden 保留布局计算（display:none 会量不到列宽）
  host.style.cssText =
    'position:fixed;left:-9999px;top:0;width:960px;visibility:hidden;pointer-events:none;';
  host.innerHTML = html;
  document.body.appendChild(host);
  try {
    applyTableAlign(host);
    return host.innerHTML;
  } finally {
    host.remove();
  }
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    c === '&' ? '&amp;' : c === '<' ? '&lt;' : c === '>' ? '&gt;' : c === '"' ? '&quot;' : '&#39;',
  );
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // 延迟 revoke：部分浏览器（含微信 WebView）异步取流，立即释放会下载失败
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

// ---------------------------------------------------------------------------
// 导出排版样式（Word 用；字体在容器级统一切到微软雅黑/苹方）
// ---------------------------------------------------------------------------

const EXPORT_CSS = `
.report-export { color: #1f2329; font-size: 14px; line-height: 1.75; word-break: break-word; }
.report-export__title { font-size: 20px; font-weight: 600; text-align: center; margin: 0 0 4px; }
.report-export__meta { text-align: center; font-size: 12px; color: #8a919f; margin-bottom: 18px; }
.report-export h1 { font-size: 17px; margin: 20px 0 8px; border-bottom: 1px solid #e5e8ec; padding-bottom: 6px; }
.report-export h2 { font-size: 16px; margin: 16px 0 8px; }
.report-export h3 { font-size: 15px; margin: 14px 0 6px; }
.report-export h4 { font-size: 14px; margin: 12px 0 6px; }
.report-export p { margin: 8px 0; }
.report-export ul, .report-export ol { margin: 8px 0; padding-left: 22px; }
.report-export li { margin: 3px 0; }
.report-export table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 13px; }
.report-export th, .report-export td { border: 1px solid #d8dce2; padding: 6px 10px; text-align: left; }
.report-export th { background: #f2f4f7; font-weight: 600; }
.report-export code { background: #f2f4f7; border-radius: 4px; padding: 1px 5px; font-size: 12.5px; font-family: Consolas, "Courier New", monospace; }
.report-export pre { background: #f6f8fa; border: 1px solid #e5e8ec; border-radius: 8px; padding: 12px; overflow-x: auto; margin: 10px 0; }
.report-export pre code { background: none; padding: 0; }
.report-export blockquote { margin: 10px 0; padding: 6px 12px; border-left: 3px solid #d0d5dd; color: #5b6472; background: #f8f9fb; }
.report-export hr { border: none; border-top: 1px solid #e5e8ec; margin: 14px 0; }
.report-export a { color: #3697c3; text-decoration: none; }
.report-export img { max-width: 100%; }
.report-export strong { font-weight: 600; }
`;

// ---------------------------------------------------------------------------
// Word 导出：HTML 封装的 .doc（Word 可直接打开，保留标题/表格/加粗排版）
// ---------------------------------------------------------------------------

/** 导出 HTML 正文为 Word（.doc）。bodyHtml 为渲染后的报告 HTML（含富文本编辑结果）。 */
export async function exportReportWordFromHtml(
  bodyHtml: string,
  title: string,
  meta: string,
  filenameBase: string,
): Promise<void> {
  // 编辑后的 HTML 来自 contenteditable，导出前经 DOMPurify 清洗防注入
  const { default: DOMPurify } = await import('dompurify');
  const cleanBody = DOMPurify.sanitize(bodyHtml);
  // 列数相同的表格统一列宽（与页面预览一致），Word 中列线同样对齐
  const alignedBody = alignHtmlTables(cleanBody);
  const html =
    '<!DOCTYPE html><html><head><meta charset="utf-8">' +
    `<title>${escapeHtml(title)}</title>` +
    `<style>${EXPORT_CSS}</style></head><body class="report-export">` +
    `<h1 class="report-export__title">${escapeHtml(title)}</h1>` +
    `<div class="report-export__meta">${escapeHtml(meta)}</div>` +
    `${alignedBody}</body></html>`;
  // BOM 前缀让 Word 正确识别 UTF-8 中文
  const blob = new Blob(['\ufeff', html], { type: 'application/msword;charset=utf-8' });
  downloadBlob(blob, `${filenameBase}.doc`);
}

/** 导出 markdown 报告为 Word（.doc）。filenameBase 不含扩展名。 */
export async function exportReportWord(
  markdown: string,
  title: string,
  meta: string,
  filenameBase: string,
): Promise<void> {
  const bodyHtml = await markdownToHtml(markdown);
  return exportReportWordFromHtml(bodyHtml, title, meta, filenameBase);
}
