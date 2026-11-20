/**
 * 剪贴板 HTML → Markdown 轻量转换器（问题文档编辑器粘贴富文本用）。
 *
 * 支持常见结构：标题 / 段落 / 粗斜删除线 / 链接 / 图片 / 列表（嵌套）/
 * 引用 / 代码块 / 行内代码 / 表格 / 换行。
 *
 * 安全：仅做结构提取（DOMParser 解析，不执行脚本）；script/style/iframe 等直接
 * 丢弃；产出 markdown 后由 react-markdown 渲染（React 虚拟 DOM + urlTransform
 * 协议白名单），危险 URL 在渲染层二次清洗，双保险。
 */

/** 直接丢弃的标签（内容与标签一并不保留） */
const DROP_TAGS = new Set(['script', 'style', 'iframe', 'object', 'embed', 'noscript', 'template']);

/** 链接 href 安全协议（其余只保留文字，不产出链接） */
const SAFE_HREF_RE = /^(?:https?:|mailto:|#)/i;
/** 图片 src 允许：http(s) 绝对链接 或 data:image 内联（渲染层已放行 data:image） */
const SAFE_IMG_SRC_RE = /^(?:https?:\/\/|data:image\/)/i;

/** 折叠行内连续空白（HTML 源码换行缩进无意义） */
function collapseWs(text: string): string {
  return text.replace(/\s+/g, ' ');
}

/** 交替文本是否值得包标记（空白内容包 ** 会产生孤零零的星号） */
function wrapMark(mark: string, inner: string): string {
  const t = inner.trim();
  if (!t) return inner;
  return `${mark}${t}${mark}`;
}

/** 递归渲染节点为 markdown 行内片段 */
function renderInline(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) {
    return collapseWs(node.textContent || '');
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  const el = node as HTMLElement;
  const tag = el.tagName.toLowerCase();
  if (DROP_TAGS.has(tag)) return '';

  const inner = Array.from(el.childNodes).map(renderInline).join('');

  switch (tag) {
    case 'br':
      return '\n';
    case 'strong':
    case 'b':
      return wrapMark('**', inner);
    case 'em':
    case 'i':
      return wrapMark('*', inner);
    case 'del':
    case 's':
    case 'strike':
      return wrapMark('~~', inner);
    case 'code': {
      const t = (el.textContent || '').trim();
      return t ? `\`${t}\`` : '';
    }
    case 'a': {
      const href = (el.getAttribute('href') || '').trim();
      const text = inner.trim();
      if (!text) return '';
      if (!href || !SAFE_HREF_RE.test(href)) return text;
      return `[${text}](${href})`;
    }
    case 'img': {
      const src = (el.getAttribute('src') || '').trim();
      if (!src || !SAFE_IMG_SRC_RE.test(src)) return '';
      const alt = collapseWs(el.getAttribute('alt') || '').trim() || '图片';
      return `![${alt}](${src})`;
    }
    default:
      // 其余行内/未知标签：保留内容
      return inner;
  }
}

/** 列表上下文：depth 缩进层级 + 有序序号 */
interface ListCtx {
  depth: number;
}

/** 块级渲染：返回带首尾空行的 markdown 片段 */
function renderBlock(node: Node, list: ListCtx): string {
  if (node.nodeType === Node.TEXT_NODE) {
    const t = collapseWs(node.textContent || '').trim();
    return t ? `\n\n${t}\n\n` : '';
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  const el = node as HTMLElement;
  const tag = el.tagName.toLowerCase();
  if (DROP_TAGS.has(tag)) return '';

  switch (tag) {
    case 'h1':
    case 'h2':
    case 'h3':
    case 'h4':
    case 'h5':
    case 'h6': {
      const level = Number(tag[1]);
      const text = renderInline(el).trim();
      if (!text) return '';
      return `\n\n${'#'.repeat(level)} ${text}\n\n`;
    }
    case 'p':
    case 'div':
    case 'section':
    case 'article':
    case 'main': {
      // 容器类标签可能混合块级子节点，递归块级渲染
      const hasBlockChild = Array.from(el.children).some((c) =>
        ['P', 'DIV', 'UL', 'OL', 'TABLE', 'PRE', 'BLOCKQUOTE', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HR', 'SECTION', 'ARTICLE'].includes(c.tagName),
      );
      if (hasBlockChild) {
        return Array.from(el.childNodes).map((n) => renderBlock(n, list)).join('');
      }
      const text = renderInline(el).trim();
      return text ? `\n\n${text}\n\n` : '';
    }
    case 'hr':
      return '\n\n---\n\n';
    case 'pre': {
      const code = el.textContent || '';
      if (!code.trim()) return '';
      return `\n\n\`\`\`\n${code.replace(/\n+$/, '')}\n\`\`\`\n\n`;
    }
    case 'blockquote': {
      const inner = Array.from(el.childNodes)
        .map((n) => renderBlock(n, list))
        .join('')
        .trim();
      if (!inner) return '';
      const quoted = inner
        .split('\n')
        .map((line) => (line.trim() ? `> ${line}` : '>'))
        .join('\n');
      return `\n\n${quoted}\n\n`;
    }
    case 'ul':
    case 'ol': {
      const ordered = tag === 'ol';
      const items = Array.from(el.children).filter(
        (c) => c.tagName.toLowerCase() === 'li',
      );
      const indent = '  '.repeat(list.depth);
      const lines: string[] = [];
      items.forEach((li, i) => {
        // li 内容 = 行内渲染；嵌套列表另起块（li 内 ul/ol）
        const nested: string[] = [];
        const inlineParts: string[] = [];
        Array.from(li.childNodes).forEach((n) => {
          if (n.nodeType === Node.ELEMENT_NODE) {
            const t = (n as HTMLElement).tagName.toLowerCase();
            if (t === 'ul' || t === 'ol') {
              nested.push(renderBlock(n, { depth: list.depth + 1 }));
            } else if (t === 'p' || t === 'div') {
              const t2 = renderInline(n).trim();
              if (t2) inlineParts.push(t2);
            } else {
              const t2 = renderInline(n);
              if (t2.trim()) inlineParts.push(t2);
            }
          } else if (n.nodeType === Node.TEXT_NODE) {
            const t2 = collapseWs(n.textContent || '').trim();
            if (t2) inlineParts.push(t2);
          }
        });
        const marker = ordered ? `${i + 1}. ` : '- ';
        const text = inlineParts.join('').trim();
        lines.push(`${indent}${marker}${text}`);
        nested.forEach((n) => lines.push(n.trimEnd()));
      });
      if (!lines.length) return '';
      return `\n\n${lines.join('\n')}\n\n`;
    }
    case 'table': {
      const rows = Array.from(el.querySelectorAll('tr'));
      if (!rows.length) return '';
      const toCells = (tr: Element) =>
        Array.from(tr.querySelectorAll('th,td')).map(
          (cell) => renderInline(cell).replace(/\|/g, '\\|').replace(/\n/g, ' ').trim() || ' ',
        );
      const lines = rows.map((tr) => `| ${toCells(tr).join(' | ')} |`);
      // 第一行后补分隔行（无论 thead 与否，markdown 表格必须有）
      const sep = `| ${toCells(rows[0]).map(() => '---').join(' | ')} |`;
      lines.splice(1, 0, sep);
      return `\n\n${lines.join('\n')}\n\n`;
    }
    default:
      // 行内元素出现在块级位置：行内渲染成段
      return renderBlockInline(el);
  }
}

/** 行内元素兜底成段（如顶层裸 <span> 文本） */
function renderBlockInline(el: HTMLElement): string {
  const text = renderInline(el).trim();
  return text ? `\n\n${text}\n\n` : '';
}

/** HTML → Markdown（不支持的复杂结构退化为纯文本内容） */
export function htmlToMarkdown(html: string): string {
  if (!html || typeof html !== 'string') return '';
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const body = doc.body;
  if (!body) return '';

  let md = Array.from(body.childNodes)
    .map((n) => renderBlock(n, { depth: 0 }))
    .join('');

  // 规整：3+ 连续换行压成 2（段落间距），首尾空白去掉
  md = md.replace(/\n{3,}/g, '\n\n').trim();
  return md;
}
