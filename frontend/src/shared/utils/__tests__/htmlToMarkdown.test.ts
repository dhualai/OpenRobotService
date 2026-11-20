/**
 * htmlToMarkdown 单元测试：剪贴板富文本 → markdown 结构转换。
 * 覆盖：标题/段落/行内标记/链接/图片/列表嵌套/引用/代码块/表格/安全过滤。
 */
import { describe, expect, it } from 'vitest';
import { htmlToMarkdown } from '../htmlToMarkdown';

describe('htmlToMarkdown', () => {
  it('标题与段落', () => {
    const md = htmlToMarkdown('<h1>大标题</h1><p>第一段。</p><p>第二段。</p>');
    expect(md).toContain('# 大标题');
    expect(md).toContain('第一段。');
    expect(md).toContain('第二段。');
    expect(md).toMatch(/第一段。\n\n第二段。/);
  });

  it('行内标记：粗体/斜体/删除线/行内代码', () => {
    const md = htmlToMarkdown(
      '<p><strong>粗</strong> <em>斜</em> <del>删</del> <code>x=1</code></p>',
    );
    expect(md).toContain('**粗**');
    expect(md).toContain('*斜*');
    expect(md).toContain('~~删~~');
    expect(md).toContain('`x=1`');
  });

  it('链接：安全协议保留，javascript: 丢弃只留文字', () => {
    const md = htmlToMarkdown(
      '<p><a href="https://example.com">官网</a> <a href="javascript:alert(1)">危险</a></p>',
    );
    expect(md).toContain('[官网](https://example.com)');
    expect(md).toContain('危险');
    expect(md).not.toContain('javascript:');
  });

  it('图片：http 与 data:image 保留，相对路径与危险协议丢弃', () => {
    const md = htmlToMarkdown(
      '<p><img src="https://cdn.example.com/a.png" alt="架构图">' +
        '<img src="data:image/png;base64,iVBOR">' +
        '<img src="/static/b.png">' +
        '<img src="javascript:alert(1)"></p>',
    );
    expect(md).toContain('![架构图](https://cdn.example.com/a.png)');
    expect(md).toContain('![图片](data:image/png;base64,iVBOR)');
    expect(md).not.toContain('/static/b.png');
    expect(md).not.toContain('javascript:');
  });

  it('无序/有序列表与嵌套', () => {
    const md = htmlToMarkdown(
      '<ul><li>甲</li><li>乙<ul><li>乙一</li><li>乙二</li></ul></li></ul>' +
        '<ol><li>第一步</li><li>第二步</li></ol>',
    );
    expect(md).toContain('- 甲');
    expect(md).toContain('- 乙');
    expect(md).toContain('  - 乙一');
    expect(md).toContain('  - 乙二');
    expect(md).toContain('1. 第一步');
    expect(md).toContain('2. 第二步');
  });

  it('引用块', () => {
    const md = htmlToMarkdown('<blockquote><p>引用内容</p></blockquote>');
    expect(md).toContain('> 引用内容');
  });

  it('代码块保留换行与缩进', () => {
    const md = htmlToMarkdown('<pre>def f():\n    return 1</pre>');
    expect(md).toContain('```\ndef f():\n    return 1\n```');
  });

  it('表格：首行后补分隔行，竖线转义', () => {
    const md = htmlToMarkdown(
      '<table><tr><th>列A</th><th>列B</th></tr><tr><td>1</td><td>a|b</td></tr></table>',
    );
    expect(md).toContain('| 列A | 列B |');
    expect(md).toContain('| --- | --- |');
    expect(md).toContain('| 1 | a\\|b |');
  });

  it('安全：script/style 内容整体丢弃', () => {
    const md = htmlToMarkdown(
      '<p>正文</p><script>alert(1)</script><style>.x{}</style>',
    );
    expect(md).toContain('正文');
    expect(md).not.toContain('alert(1)');
    expect(md).not.toContain('.x{}');
  });

  it('br 转换行，hr 转分隔线', () => {
    const md = htmlToMarkdown('<p>第一行<br>第二行</p><hr>');
    expect(md).toContain('第一行\n第二行');
    expect(md).toContain('---');
  });

  it('空输入与纯文本', () => {
    expect(htmlToMarkdown('')).toBe('');
    expect(htmlToMarkdown('<p></p>')).toBe('');
  });
});
